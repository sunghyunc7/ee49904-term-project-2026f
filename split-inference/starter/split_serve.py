#!/usr/bin/env python3
"""
EE49904 Term Project — split inference **live measurement** harness.

split_bench.py is the paper model: (compute) + (bytes/bandwidth + RTT) + (remote compute).
This script checks that prediction by **actually running two processes**.

  client process : run the head stages → serialize the intermediate tensor → send over TCP
  server process : receive → run the tail stages → return the result

The link is **emulated in userspace** — data is sent in chunks with sleeps to match the target
bandwidth, and RTT is produced by delaying the response. No kernel qdisc (netem) is used, so no
root and no particular kernel is needed (works the same on Windows WSL2, macOS and Linux).

Easiest usage — start server and client automatically and compare against the prediction:

    python3 split_serve.py --selftest --model resnet18 --split layer2 \\
                           --bandwidth 25 --rtt 20

Manual run:
    python3 split_serve.py --role server --port 8471
    python3 split_serve.py --role client --port 8471 --model resnet18 --split layer2 \\
                           --bandwidth 25 --rtt 20

The output shows the **predicted latency** and the **measured latency** side by side. Explaining why
they differ is the core of Question 2. (Hint: there is one step the paper model does not price.)
"""
import argparse, io, os, pickle, socket, struct, subprocess, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import splitlib as S

CHUNK = 16 * 1024
PACE_CHUNK = 64 * 1024


# ----------------------------- socket utilities ----------------------------
def _wait_until(deadline):
    """
    With time.sleep alone, scheduler error makes us slower than the target bandwidth (the gap
    reaches 20~30% on a loaded machine). Yield most of the remaining time with sleep and spin
    only for the last 1 ms, which brings the pacing error below a millisecond.
    """
    while True:
        remain = deadline - time.perf_counter()
        if remain <= 0:
            return
        if remain > 0.0010:
            time.sleep(remain - 0.0005)
        # spin for the last 0.5 ms (kept short so we do not hog a core)


def _nodelay(sock):
    """
    Turn Nagle's algorithm off on this socket.

    send_msg writes a length prefix and then the body, and the peer answers: write-write-read.
    With Nagle on, the second write waits for the ACK of the first, and the peer holds that ACK
    back for up to ~40 ms (delayed ACK). On Linux loopback this stall landed on every request and
    was booked as transfer time, so the "effective bandwidth" read far below target — a socket
    artifact, not a property of the emulated link. Measured 2026-09-19 on a 2-core Linux box,
    resnet18/layer2: target 50 Mb/s gave 23.7 Mb/s effective without this call, 49.6 Mb/s with it.
    macOS loopback did not show the stall, so until this fix the two platforms disagreed.
    """
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)


def send_msg(sock, payload, bandwidth_mbps=None):
    """Length prefix + body. If bandwidth_mbps is given, the send is paced in userspace."""
    sock.sendall(struct.pack("!Q", len(payload)))
    if not bandwidth_mbps:
        sock.sendall(payload); return
    bytes_per_s = bandwidth_mbps * 1e6 / 8.0
    # If pacing intervals are too fine, sleep/spin overhead eats into the bandwidth.
    # Grow the chunk so one interval is at least 5 ms, and cap the number of points at 64.
    total_s = len(payload) / bytes_per_s
    n_points = max(1, min(64, int(total_s / 0.005)))
    chunk = max(PACE_CHUNK, (len(payload) + n_points - 1) // n_points)
    # Pace like a serial link: a chunk is handed to the socket only at the moment a link of this
    # rate would have finished serializing it (wait, then send), so the last byte arrives at
    # bytes/bandwidth. Sending first and waiting afterward delivers every chunk one interval
    # early — the server starts working before the "link" is done, and at high rates (few
    # chunks) the effective bandwidth then reads above target.
    t0 = time.perf_counter()
    sent = 0
    while sent < len(payload):
        end = min(sent + chunk, len(payload))
        _wait_until(t0 + end / bytes_per_s)
        sock.sendall(payload[sent:end])
        sent = end


def recv_exact(sock, n):
    buf = bytearray()
    while len(buf) < n:
        b = sock.recv(min(CHUNK, n - len(buf)))
        if not b:
            raise ConnectionError("connection closed")
        buf += b
    return bytes(buf)


def recv_msg(sock):
    (n,) = struct.unpack("!Q", recv_exact(sock, 8))
    return recv_exact(sock, n)


# ------------------------------- server ------------------------------------
def run_server(port, host="127.0.0.1", threads=0):
    # --selftest runs server and client **on the same machine at the same time**. If both take
    # the default thread count (= all cores) they fight over cores, and that contention is a cost
    # the prediction model lacks, so it shows up as error. On a 16-core/7GB WSL2 box we saw device
    # compute 67 ms, remote compute 233 ms, error +63~83%; pinning both sides to 4 threads gave 32 ms,
    # 52 ms, +25% (measured 2026-08-26). On the Mac and Spark this difference was hidden inside 25%.
    if threads:
        import torch
        torch.set_num_threads(threads)
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((host, port)); srv.listen(4)
    print(f"[server] listening on {host}:{port} (Ctrl-C to stop)", flush=True)
    cache = {}
    while True:
        conn, _ = srv.accept()
        _nodelay(conn)
        try:
            while True:
                try:
                    req = pickle.loads(recv_msg(conn))
                except (ConnectionError, EOFError):
                    break
                if req.get("op") == "bye":
                    break
                key = (req["model"], req["pretrained"], req["channels_last"])
                if key not in cache:
                    cache[key] = S.build_model(*key)
                stage_names, mods = cache[key]
                S.set_split_index(stage_names)
                k = req["start_index"]
                import torch
                t_d0 = time.perf_counter()
                h = torch.load(io.BytesIO(req["tensor"]), weights_only=True)
                if req["channels_last"] and h.dim() == 4:
                    h = h.to(memory_format=torch.channels_last)
                deser_s = time.perf_counter() - t_d0
                t0 = time.perf_counter()
                with torch.no_grad():
                    for mod in list(mods)[k:]:
                        h = mod(h)
                compute_s = time.perf_counter() - t0
                rtt_actual = 0.0
                if req["rtt_ms"]:
                    # RTT is on the ms scale, so sleep accuracy is enough. Spinning here
                    # would disturb the client pacing on machines with few cores.
                    _r0 = time.perf_counter()
                    time.sleep(req["rtt_ms"] / 1000.0)
                    rtt_actual = time.perf_counter() - _r0   # time actually slept
                send_msg(conn, pickle.dumps(
                    {"compute_s": compute_s, "deser_s": deser_s,
                     "rtt_actual_s": rtt_actual, "out_shape": tuple(h.shape)}))
        finally:
            conn.close()


# ------------------------------ client --------------------------------------
def run_client(a):
    import torch
    if a.threads:
        torch.set_num_threads(a.threads)
    stage_names, mods = S.build_model(a.model, a.pretrained, a.channels_last)
    S.set_split_index(stage_names)
    points = S.split_points(stage_names)
    if a.split not in points:
        sys.exit(f"[ERROR] --split must be one of: {', '.join(points)}")

    t_s, shapes, numels, in_numel, _ = S.profile_stages(
        mods, repeats=a.repeats, channels_last=a.channels_last)

    x = torch.randn(*S.INPUT_SHAPE)
    if a.channels_last:
        x = x.to(memory_format=torch.channels_last)

    if a.split == "all_local":
        # Same protocol as the split path: --iters runs, the first discarded as warm-up, mean of
        # the rest. A single cold run reads tens of percent high and says nothing about the model.
        samples = []
        for _ in range(max(a.iters, 1)):
            t0 = time.perf_counter()
            with torch.no_grad():
                h = x
                for mod in mods:
                    h = mod(h)
            samples.append(time.perf_counter() - t0)
        samples = samples[1:] or samples
        measured = sum(samples) / len(samples)
        predicted = S.total_latency_s("all_local", t_s, numels, in_numel, a.dtype,
                                      a.bandwidth, a.rtt, a.tail_speedup, a.device_slowdown)
        report(a, 0, measured, predicted, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
        return

    k = -1 if a.split == "input" else S.SPLIT_INDEX[a.split]

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.connect((a.host, a.port))
    _nodelay(sock)

    dev_s = ser_s = wire_s = remote_s = deser_s = rtt_s = 0.0
    measured = None
    for it in range(a.iters):
        t_start = time.perf_counter()
        # --- device-side compute ---
        with torch.no_grad():
            h = x
            for mod in list(mods)[: k + 1]:
                h = mod(h)
        t_dev = time.perf_counter()
        # --- serialization ---
        buf = io.BytesIO(); torch.save(h.contiguous(), buf); payload = buf.getvalue()
        t_ser = time.perf_counter()
        # --- transfer + remote execution + response ---
        send_msg(sock, pickle.dumps({
            "model": a.model, "pretrained": a.pretrained,
            "channels_last": a.channels_last, "start_index": k + 1,
            "tensor": payload, "rtt_ms": a.rtt}), bandwidth_mbps=a.bandwidth)
        resp = pickle.loads(recv_msg(sock))
        t_end = time.perf_counter()
        if it == 0:
            continue                                   # discard the first iteration as warm-up
        dev_s += t_dev - t_start
        ser_s += t_ser - t_dev
        wire_s += t_end - t_ser
        remote_s += resp.get("compute_s", 0.0)
        deser_s += resp.get("deser_s", 0.0)
        rtt_s += resp.get("rtt_actual_s", a.rtt / 1000.0)
        measured = (measured or 0) + (t_end - t_start)
    n = max(a.iters - 1, 1)
    dev_s, ser_s, wire_s = dev_s / n, ser_s / n, wire_s / n
    remote_s, deser_s = remote_s / n, deser_s / n
    rtt_s, measured = rtt_s / n, measured / n
    send_msg(sock, pickle.dumps({"op": "bye"})); sock.close()

    predicted = S.total_latency_s(a.split, t_s, numels, in_numel, a.dtype,
                                  a.bandwidth, a.rtt, a.tail_speedup, a.device_slowdown)
    report(a, len(payload), measured, predicted, dev_s, ser_s, wire_s, remote_s, deser_s, rtt_s)


def _like_for_like_warnings(a):
    """
    The live harness runs the head and the tail on this one machine, and always ships fp32.
    Three options rescale only the PREDICTED side; with any of them set, the comparison printed
    below no longer describes one and the same system.
    """
    if a.device_slowdown != 1.0 or a.tail_speedup != 1.0:
        print(f"  ! --device-slowdown {a.device_slowdown:g} / --tail-speedup {a.tail_speedup:g} rescale the PREDICTED latency only.")
        print("    Both halves ran on this machine (1x/1x), so the two totals below are not like for like.")
        print("    Leave both at 1.0 here and apply your scenario in split_bench.py.")
    if a.dtype != "fp32":
        print(f"  ! --dtype {a.dtype} changes the byte count in the PREDICTED latency only.")
        print("    The harness always sends the tensor as fp32, so the two totals below are not like for like.")


def report(a, nbytes, measured, predicted, dev, ser, wire, remote=0.0, deser=0.0, rtt_s=None):
    print(f"\n  model {a.model} · split point {a.split} · "
          f"{'NHWC' if a.channels_last else 'NCHW'} · "
          f"link {a.bandwidth} Mb/s · RTT {a.rtt} ms")
    _like_for_like_warnings(a)
    print(f"  {'bytes sent':<22}{nbytes:>12,}")
    print(f"  {'device compute':<22}{dev*1000:>12.1f} ms")
    print(f"  {'serialization':<22}{ser*1000:>12.1f} ms")
    print(f"  {'transfer+remote+resp':<22}{wire*1000:>12.1f} ms")
    if nbytes:
        rtt_eff = a.rtt / 1000.0 if rtt_s is None else rtt_s
        transfer = wire - rtt_eff - remote - deser
        if transfer > 0:
            ach = nbytes * 8 / transfer / 1e6
            print(f"  {'  · RTT (measured)':<22}{rtt_eff*1000:>12.1f} ms")
            print(f"  {'  · remote deserial.':<22}{deser*1000:>12.1f} ms")
            print(f"  {'  · remote compute':<22}{remote*1000:>12.1f} ms")
            print(f"  {'  · actual transfer':<22}{transfer*1000:>12.1f} ms  "
                  f"→ effective bandwidth {ach:.1f} Mb/s (target {a.bandwidth:.0f})")
            if ach < 0.8 * a.bandwidth:
                print(f"  ! The userspace pacer did not reach the target bandwidth "
                      f"({ach/a.bandwidth*100:.0f}%).")
                print(f"    The pacer could not keep up at this rate (busy CPU, or too few cores) — "
                      f"close other programs, lower --bandwidth, or")
                print(f"    compare against the prediction using the effective bandwidth {ach:.1f} Mb/s.")
    print(f"  {'─'*36}")
    print(f"  {'measured end-to-end':<22}{measured*1000:>12.1f} ms")
    print(f"  {'predicted (bench)':<22}{predicted*1000:>12.1f} ms")
    err = (measured - predicted) / predicted * 100 if predicted else 0
    print(f"  {'error':<22}{err:>+11.1f} %")
    # Tolerance 35%: the remaining error is not a defect to remove but a **gap to explain**
    # (serialization, deserialization, RTT, effective bandwidth). After thread pinning the WSL2
    # measurement was +25.3%; allowing for machine-to-machine spread the limit is 35% (2026-08-26).
    if abs(err) > 35:
        print("  → The error exceeds 35%. Explain separately whether the effective bandwidth fell short")
        print("     of the target (pacing limit) or whether there is a cost the model does not price.")
        print("     Also check that no other program is using the CPU — this harness runs two")
        print("     processes on one machine, so it is sensitive to outside load.")


def run_calibrate(a):
    """
    **Measure** how well the userspace pacer tracks the target bandwidth on this machine.

    The result is a **reference value, not a guaranteed ceiling.** This calibration measures
    under conditions different from a real experiment — here short payloads are sent back to
    back, whereas in a real experiment model compute falls in between and gives the pacing slack.
    In the 2026-08-26 WSL2 measurement the calibrator said "about 5 Mb/s", yet the same machine
    delivered 9.8 Mb/s at a 10 Mb/s target. That disagreement was later traced to the socket, not
    to the pacer (see _nodelay above, fixed 2026-09-19); calibration now tracks far higher on Linux.
    It is still only a reference: one payload size, measured with nothing else running.

    How to use it: when the effective bandwidth falls far short of the target, use this as a
    reference for deciding "is it the pacer limit". Do not restrict your experiment design by it.
    """
    print("\n  Pacer calibration — effective bandwidth measured per target bandwidth")
    print(f"  {'target':>10}{'effective':>12}{'achieved':>9}")
    usable = None
    for bw in [float(x) for x in a.calibrate.split(",")]:
        a.bandwidth, a.split, a.rtt = bw, "layer2", 0.0
        got = _measure_bw(a)
        ratio = got / bw
        print(f"  {bw:>10.0f}{got:>12.1f}{ratio*100:>8.0f}%")
        if 0.9 <= ratio <= 1.15:
            usable = bw
        elif ratio > 1.15:
            print(f"             (pacing not engaged — the transfer is too short at this rate)")
    print()
    if usable:
        print(f"  → reference value: the highest target the pacer tracked in this calibration is about {usable:.0f} Mb/s.")
    else:
        print("  → reference value: no target landed within 90~115% in this calibration.")
    print("     This is a **reference value, not a guaranteed ceiling**. Calibration measures one payload")
    print("     size with nothing else running; a real run shares the machine with whatever else is open.")
    print("     The effective bandwidth is printed in the report of each run, so base your claims on that.")


def _measure_bw(a):
    """Get the effective bandwidth (Mb/s) from a single split transfer."""
    import torch
    if getattr(a, "threads", 0):
        torch.set_num_threads(a.threads)
    stage_names, mods = S.build_model(a.model, a.pretrained, a.channels_last)
    S.set_split_index(stage_names)
    k = S.SPLIT_INDEX[a.split]
    x = torch.randn(*S.INPUT_SHAPE)
    with torch.no_grad():
        h = x
        for mod in list(mods)[: k + 1]:
            h = mod(h)
    buf = io.BytesIO(); torch.save(h.contiguous(), buf); payload = buf.getvalue()
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.connect((a.host, a.port))
    _nodelay(sock)
    vals = []
    for _ in range(4):
        t0 = time.perf_counter()
        send_msg(sock, pickle.dumps({
            "model": a.model, "pretrained": a.pretrained,
            "channels_last": a.channels_last, "start_index": k + 1,
            "tensor": payload, "rtt_ms": 0.0}), bandwidth_mbps=a.bandwidth)
        resp = pickle.loads(recv_msg(sock))
        dt = time.perf_counter() - t0 - resp.get("compute_s", 0) - resp.get("deser_s", 0)
        if dt > 0:
            vals.append(len(payload) * 8 / dt / 1e6)
    send_msg(sock, pickle.dumps({"op": "bye"})); sock.close()
    vals = vals[1:] or vals              # first round is warm-up
    return sum(vals) / len(vals)         # mean — same statistic as in real use


def main():
    ap = argparse.ArgumentParser(formatter_class=argparse.RawDescriptionHelpFormatter,
                                 description=__doc__)
    ap.add_argument("--role", choices=["server", "client"], default="client")
    ap.add_argument("--selftest", action="store_true", help="start the server automatically and run the client")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8471)
    ap.add_argument("--model", default="resnet18")
    ap.add_argument("--split", default="layer2")
    ap.add_argument("--bandwidth", type=float, default=25.0, help="Mb/s")
    ap.add_argument("--rtt", type=float, default=20.0, help="ms")
    ap.add_argument("--dtype", default="fp32",
                    help="byte count used in the PREDICTION only — the harness always sends fp32, so keep fp32 here")
    ap.add_argument("--tail-speedup", type=float, default=1.0,
                    help="rescales the PREDICTION only — in the live harness the server is this same machine, so keep 1.0")
    ap.add_argument("--device-slowdown", type=float, default=1.0,
                    help="rescales the PREDICTION only — the live harness measures this machine as it is, so keep 1.0")
    # 4 iterations (3 after dropping 1 warm-up) are too few samples: a single scheduling hiccup
    # becomes the error as is. 20 iterations take about 20 s and the result is stable (measured 2026-08-26).
    ap.add_argument("--iters", type=int, default=20)
    ap.add_argument("--repeats", type=int, default=10)
    ap.add_argument("--threads", type=int, default=0,
                    help="number of torch threads (0=auto). --selftest pins it automatically — "
                         "so server and client do not fight over cores on the same machine.")
    ap.add_argument("--pretrained", action="store_true")
    ap.add_argument("--channels-last", action="store_true")
    ap.add_argument("--calibrate", nargs="?", const="5,10,25,50,100,250", default=None,
                    help="measure how well the pacer tracks the target bandwidth — the result is a reference value, "
                         "not a guaranteed ceiling (targets can be given as a comma-separated list)")
    a = ap.parse_args()

    # --selftest runs two processes on one machine → without thread pinning they contend.
    # (If --threads was given explicitly, we respect the student choice.)
    if a.selftest and not a.threads:
        a.threads = max(1, min(4, (os.cpu_count() or 4) // 2))
        print(f"  [selftest] Pinning server and client to {a.threads} threads each "
              f"(cores {os.cpu_count()}). If the two processes fight over the same cores, "
              f"a cost the prediction model lacks shows up as error.")

    if a.selftest:
        env = dict(os.environ)
        if a.threads:                      # BLAS/OpenMP must be pinned too for the pinning to take effect
            for k in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS"):
                env[k] = str(a.threads)
        srv = subprocess.Popen([sys.executable, os.path.abspath(__file__),
                                "--role", "server", "--port", str(a.port),
                                "--threads", str(a.threads)],
                               stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT,
                               env=env)
        try:
            for _ in range(60):                     # wait for the server to come up
                try:
                    s = socket.create_connection((a.host, a.port), timeout=0.5); s.close(); break
                except OSError:
                    time.sleep(0.25)
            else:
                sys.exit("[ERROR] server did not come up")
            run_calibrate(a) if a.calibrate else run_client(a)
        finally:
            srv.terminate(); srv.wait(timeout=10)
        return

    if a.role == "server":
        run_server(a.port, a.host, a.threads)
    elif a.calibrate:
        run_calibrate(a)
    else:
        run_client(a)


if __name__ == "__main__":
    main()
