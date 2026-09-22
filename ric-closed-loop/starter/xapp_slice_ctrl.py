#!/usr/bin/env python3
"""
EE49904 Term Project — closed-loop slice control xApp (skeleton).

This is the file you modify. The rest of the wiring (nearRT-RIC, E2 node, subscription) is already done.

Loop:
    MAC indication  →  decide()  →  SLICE control  →  next indication …

    Observed:   per-UE backlog (BSR, bytes), throughput, PRBs, CQI
    Controlled: PRB share (%) of slices 0/1

    UE rnti 0x1000, 0x1001 → slice 0
    UE rnti 0x1002, 0x1003 → slice 1
    (This mapping is not carried in the MAC indication. Think about why it is not.)

Run (one fresh RIC + E2 node per run — see run_experiment.sh for why):
    bash run_experiment.sh --gain 2.0 --period 100 --duration 60
    bash run_experiment.sh --open-loop --duration 60          # observe only, no control
    bash run_experiment.sh --ind-period 2 --gain 2.0          # indications every 2 ms instead of 10

Output: one line per decision + a summary on exit + a CSV if --out is given.
"""
import argparse, csv, faulthandler, os, statistics, sys, time

# The SDK is a C library. If an assert or a heap error fires inside it, Python vanishes with
# SIGABRT and no traceback. With faulthandler enabled, the Python line where it died is left
# on stderr — without it, a single "Aborted" line is all you get.
faulthandler.enable()

# xapp_sdk lives inside the FlexRIC build tree. setup.sh tells you XAPP_SDK_PATH.
_sdk = os.environ.get("XAPP_SDK_PATH")
if _sdk and _sdk not in sys.path:
    sys.path.insert(0, _sdk)
try:
    import xapp_sdk as ric
except ImportError:
    sys.exit("[ERROR] xapp_sdk not found.\n"
             "      export XAPP_SDK_PATH=<flexric>/build/src/xApp/swig\n"
             "      or run starter/setup.sh first.")

SLICE0_RNTIS = {0x1000, 0x1001}
SLICE1_RNTIS = {0x1002, 0x1003}
MIN_SHARE = 5.0          # same value as the lower bound enforced by the agent
EE_NUM_SLICES = 2
# Subscription periods the Python SDK exposes (swig_wrapper.h, enum Interval). The C API also has
# 100 and 1000 ms; the Python wrapper asserts on them. For slower observation, subsample in handle().
IND_PERIODS = {1: "Interval_ms_1", 2: "Interval_ms_2", 5: "Interval_ms_5", 10: "Interval_ms_10"}


class Observer(ric.mac_cb):
    """Called whenever a MAC indication arrives. Only updates state; does no control."""

    def __init__(self):
        ric.mac_cb.__init__(self)
        self.backlog = {0: 0.0, 1: 0.0}      # bytes
        self.tput = {0: 0.0, 1: 0.0}         # bytes served in the last indication period — NOT per second
        self.n_ind = 0
        self.last_ts = None
        self.gaps_ms = []

    def handle(self, ind):
        if not ind.ue_stats:
            return
        b = {0: 0.0, 1: 0.0}
        t = {0: 0.0, 1: 0.0}
        for ue in ind.ue_stats:
            s = 0 if ue.rnti in SLICE0_RNTIS else (1 if ue.rnti in SLICE1_RNTIS else None)
            if s is None:
                continue
            b[s] += float(ue.bsr)
            t[s] += float(ue.dl_curr_tbs)
        self.backlog, self.tput = b, t
        self.n_ind += 1
        now = time.time()
        if self.last_ts is not None:
            self.gaps_ms.append((now - self.last_ts) * 1000.0)
        self.last_ts = now


# ---------------------------------------------------------------------------
#  The part you modify
# ---------------------------------------------------------------------------
def decide(backlog_bytes, current_share0, gain, dt_s):
    """
    Decide the new PRB share (%) of slice 0 from the observed per-slice backlog.

    The default implementation is a very simple rule that pushes share toward the side with more backlog.

    Mind the name. What is proportional to the error is **not the share itself but its rate of change**
    (new0 = current + gain x err x dt). So this is **integral control** of the error, which is why
    the share does not return to 50 when the error reaches 0 but stays at its last value.
    In the log you will see share0 settle near 69%; that is not a bug but
    the definition of an integrator. Keep this property in mind when you raise the gain (Q2.3).
    The unit of gain is "share points per second / **Mbit** of backlog imbalance".

    Mind the units. `bsr` in the MAC indication is in **bytes**, while the plant and the offline reference
    (`patch/test_plant.c`) measure the error in **bits**. If you divide the bytes by 1e6 as they are,
    the same gain value becomes 8 times weaker — the loop runs but seems to have almost no effect.
    (This mistake was actually made once while building this kit. It is also material for Question 2.2.)

    Room for improvement (covered in Questions 1 and 3):
      * integral and derivative terms, or using throughput error instead of backlog
      * a rate limit on how far the share may move in one step
      * stating an SLA (minimum throughput per slice) and treating it as a constraint
      * a safe default behavior when observations are stale or missing
    """
    err_mbit = (backlog_bytes[0] - backlog_bytes[1]) * 8.0 / 1e6
    new0 = current_share0 + gain * err_mbit * dt_s
    return max(MIN_SHARE, min(100.0 - MIN_SHARE, new0))


# ---------------------------------------------------------------------------
#  Building the control message
#
#  ★ Mind the lifetime. An array made through SWIG is C memory that is valid **only while the
#    Python object is alive**. Yet slice_ctrl_msg_t holds that memory **by pointer only**
#    (ul_dl_slice_conf_t.slices is an fr_slice_t*). Therefore
#
#        def build(...):
#            arr = ric.slice_array(2)
#            ...
#            dl.slices = arr
#            return msg              # <-- arr is garbage-collected here
#        ric.control_slice_sm(node.id, build(...))   # <-- reads memory that is already freed
#
#    is wrong. It is worse because it does not die right away — as long as the freed block is not
#    reused it works fine for a while, and the moment the heap layout changes it trips
#    assert(s->len_sched != 0) in the SWIG wrapper and the xApp dies with SIGABRT.
#
#    So, as below, the array and its elements are **kept attached to an object.** As a bonus,
#    the allocation is no longer repeated every 100 ms.
# ---------------------------------------------------------------------------
class SliceCtrl:
    """Build the SLICE ADD control message once and reuse it, changing only the shares."""

    def __init__(self, n_slices=EE_NUM_SLICES):
        self.n = n_slices
        self.msg = ric.slice_ctrl_msg_t()
        self.msg.type = ric.SLICE_CTRL_SM_V0_ADD
        self.arr = ric.slice_array(self.n)        # ← you must keep this reference
        self.items = []                           # ← owner of the label/sched buffers
        for i in range(self.n):
            s = ric.fr_slice_t()
            s.id = i
            s.label = f"s{i}"
            s.len_label = len(s.label)
            s.sched = "PF"
            s.len_sched = len("PF")
            s.params.type = ric.SLICE_ALG_SM_V0_NVS
            s.params.u.nvs.conf = ric.SLICE_SM_NVS_V0_CAPACITY
            s.params.u.nvs.u.capacity.u.pct_reserved = 50.0
            self.items.append(s)
        self.dl = ric.ul_dl_slice_conf_t()
        self.dl.len_slices = self.n
        self.dl.sched_name = "NVS"
        self.dl.len_sched_name = len("NVS")

    def message(self, pcts):
        """Return a control message carrying the list of PRB shares (%)."""
        assert len(pcts) == self.n
        for i, pct in enumerate(pcts):
            self.items[i].params.u.nvs.u.capacity.u.pct_reserved = float(pct)
            self.arr[i] = self.items[i]           # copied by value — must be re-assigned each time to take effect
        self.dl.slices = self.arr.cast()
        self.msg.u.add_mod_slice.dl = self.dl
        return self.msg


class SliceReset:
    """Experiment reset message (SLICE DEL).

    On SLICE DEL the agent of this kit returns the plant to tick 0 · empty queues · 50/50.
    **This is an extension of this kit, not the standard O-RAN meaning of DEL** (BRIEF §2).

    Why it is needed: the plant is a single global object in the emu_agent_gnb process. If you run
    a second experiment without a reset, it inherits the backlog and demand phase left by the
    first one, and the two runs cannot be compared. (Restarting the agent also works, and on
    real equipment that is the honest way.)

    It is a class for the same reason as SliceCtrl — del_dl_array must also keep its reference.
    """

    def __init__(self, n_slices=EE_NUM_SLICES):
        self.arr = ric.del_dl_array(n_slices)     # ← you must keep this reference
        for i in range(n_slices):
            self.arr[i] = i
        self.msg = ric.slice_ctrl_msg_t()
        self.msg.type = ric.SLICE_CTRL_SM_V0_DEL
        self.msg.u.del_slice.len_dl = n_slices
        self.msg.u.del_slice.dl = self.arr.cast()
        self.msg.u.del_slice.len_ul = 0

    def message(self):
        return self.msg


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gain", type=float, default=2.0,
                    help="gain (share points/s per Mbit of imbalance)")
    ap.add_argument("--period", type=int, default=100,
                    help="control period (ms). May differ from the indication period.")
    ap.add_argument("--ind-period", type=int, default=10, choices=sorted(IND_PERIODS),
                    help="MAC indication (subscription) period in ms. The Python SDK offers only these "
                         "four. The plant advances one tick per indication, so the agent must have been "
                         "started with the same EE49904_PLANT_DT_MS — run_experiment.sh does that.")
    ap.add_argument("--duration", type=float, default=60.0, help="run time (s)")
    ap.add_argument("--open-loop", action="store_true", help="observe only, no control")
    ap.add_argument("--init-share", type=float, default=50.0)
    ap.add_argument("--out", default=None, help="path of the CSV to write")
    ap.add_argument("--no-reset", dest="reset", action="store_false",
                    help="do not reset the plant at start (inherit the state of the previous run)")
    ap.set_defaults(reset=True)
    a = ap.parse_args()

    ric.init()
    nodes = ric.conn_e2_nodes()
    if not nodes:
        sys.exit("[ERROR] No E2 node is attached. Start nearRT-RIC and emu_agent_gnb first.")
    node = nodes[0]
    print(f"{len(nodes)} E2 node(s) connected. Using: {node.id.type}, "
          f"{'open loop (observe only)' if a.open_loop else f'closed loop gain={a.gain}'}, "
          f"indication period {a.ind_period} ms, control period {a.period} ms")

    # Reset comes **before the subscription**. The plant starts advancing ticks the moment a subscription attaches.
    resetter = SliceReset()          # a local variable, kept alive for the whole run
    if a.reset:
        ric.control_slice_sm(node.id, resetter.message())
        time.sleep(0.5)
        print("Plant reset request sent (SLICE DEL). "
              "The agent log should show 'plant reset'.")
    else:
        print("Continuing without a reset (--no-reset).")

    obs = Observer()
    hndlr = ric.report_mac_sm(node.id, getattr(ric, IND_PERIODS[a.ind_period]), obs)

    ctrl = SliceCtrl()               # likewise — must stay alive until the loop ends
    share0 = a.init_share
    if not a.open_loop:
        ric.control_slice_sm(node.id, ctrl.message([share0, 100.0 - share0]))

    rows, t0, dt = [], time.time(), a.period / 1000.0
    print(f"\n{'t(s)':>6}{'share0':>8}{'backlog0':>11}{'backlog1':>11}"
          f"{'tput0':>9}{'tput1':>9}{'#ind':>6}")
    try:
        while time.time() - t0 < a.duration:
            time.sleep(dt)
            b, t = dict(obs.backlog), dict(obs.tput)
            el = time.time() - t0
            if not a.open_loop:
                share0 = decide(b, share0, a.gain, dt)
                ric.control_slice_sm(node.id, ctrl.message([share0, 100.0 - share0]))
            print(f"{el:>6.1f}{share0:>8.1f}{b[0]/1e3:>10.1f}k{b[1]/1e3:>10.1f}k"
                  f"{t[0]/1e3:>8.1f}k{t[1]/1e3:>8.1f}k{obs.n_ind:>6}")
            rows.append(dict(t_s=round(el, 3), share0=round(share0, 2),
                             backlog0_bytes=int(b[0]), backlog1_bytes=int(b[1]),
                             tput0_bytes=int(t[0]), tput1_bytes=int(t[1]),
                             n_ind=obs.n_ind))
    except KeyboardInterrupt:
        print("\nInterrupted")
    finally:
        ric.rm_report_mac_sm(hndlr)
        while ric.try_stop == 0:
            time.sleep(0.1)

    if rows:
        tot = [r["backlog0_bytes"] + r["backlog1_bytes"] for r in rows]
        print(f"\n{obs.n_ind} indications · {len(rows)} decisions")
        if obs.gaps_ms:
            med = statistics.median(obs.gaps_ms)
            print(f"indication gap: median {med:.1f} ms, max {max(obs.gaps_ms):.1f} ms "
                  f"(asked for {a.ind_period} ms)")
            if not 0.5 * a.ind_period <= med <= 2.0 * a.ind_period:
                print("  ! The measured gap is far from the period you asked for. Plant time and wall time "
                      "have drifted apart in this run — say so when you report it (BRIEF §2, Q2.2).")
        print(f"total backlog: max {max(tot)/1e6:.2f} MB, mean {sum(tot)/len(tot)/1e6:.2f} MB")
        # tput*_bytes is bytes per indication period, so its scale changes with --ind-period.
        # Convert to a rate before you compare runs. (Assumes the plant tick equals --ind-period.)
        mbps = [(r["tput0_bytes"] + r["tput1_bytes"]) * 8.0 / (a.ind_period / 1000.0) / 1e6 for r in rows]
        print(f"cell throughput: mean {sum(mbps)/len(mbps):.2f} Mb/s "
              f"(tput columns are bytes per {a.ind_period} ms indication period)")
        if a.out:
            with open(a.out, "w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
                w.writeheader(); w.writerows(rows)
            print(f"Saved to {a.out}.")


if __name__ == "__main__":
    main()
