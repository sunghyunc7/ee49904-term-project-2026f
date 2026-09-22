#!/usr/bin/env python3
"""
EE49904 Term Project — ASTRA-sim collective/fabric sweep driver.

This script runs ASTRA-sim (analytical backend) repeatedly over many configurations and
collects the results into a single results.csv. There are five experiment axes:

  --collectives   all_reduce, all_gather, reduce_scatter, all_to_all
                  (= DP / ZeRO all-gather / ZeRO reduce-scatter / MoE all-to-all)
  --npus          number of NPUs taking part in the collective
  --sizes-mb      collective size in whole MB, minimum 1. For all_reduce, reduce_scatter and
                  all_to_all this is the full buffer each NPU starts with; for all_gather it is the
                  shard each NPU contributes (the gathered result is npus times larger).
  --topologies    topology:bandwidth(GB/s), e.g. "Ring:50". Ring, Switch or FullyConnected.
  --impls         ring | oneRing | direct | oneDirect | doubleBinaryTree | halvingDoubling |
                  oneHalvingDoubling
  --congestion    unaware | aware | both

A point takes well under a second at the NPU counts you will mostly use, but the cost grows
steeply with --npus: one point at 256 NPUs can take minutes. Add large NPU counts deliberately.

Uses only the standard library (no pandas or numpy needed).

Examples:
  python3 run_sweep.py --sizes-mb 1,8,64 --topologies Ring:50,Switch:400
  python3 run_sweep.py --collectives all_to_all --npus 8,16 --impls ring,direct

Output: results.csv  (columns: collective,npus,size_mb,topology,bandwidth_GBps,latency_ns,
                        impl,congestion,cycles_ns,us,achieved_GBps)
"""
import argparse, csv, itertools, os, re, subprocess, sys, tempfile, shutil, json, time

HERE = os.path.dirname(os.path.abspath(__file__))

BIN = {
    "unaware": "build/astra_analytical/build/bin/AstraSim_Analytical_Congestion_Unaware",
    "aware": "build/astra_analytical/build/bin/AstraSim_Analytical_Congestion_Aware",
}
GEN = "examples/workload/microbenchmarks/generator_scripts/{coll}.py"
WL_DIR = "examples/workload/microbenchmarks"

# What this kit knows how to run. Checked before anything is simulated, so that a typo stops the
# run with an explanation instead of failing one combination at a time inside the simulator.
COLLECTIVES = ("all_reduce", "all_gather", "reduce_scatter", "all_to_all")
TOPOLOGIES = ("Ring", "Switch", "FullyConnected")
IMPLS = ("ring", "oneRing", "direct", "oneDirect", "doubleBinaryTree",
         "halvingDoubling", "oneHalvingDoubling")
# Two limits of this simulator build, found by running every algorithm against every collective at
# 2-32 NPUs. Checked here so the reason is one line rather than a page of the simulator's internals.
POWER_OF_TWO_ONLY = ("doubleBinaryTree", "halvingDoubling", "oneHalvingDoubling")
NO_ALL_TO_ALL = ("halvingDoubling", "oneHalvingDoubling")

SYS_TEMPLATE = {
    "scheduling-policy": "LIFO",
    "endpoint-delay": 10,
    "active-chunks-per-dimension": 1,
    "preferred-dataset-splits": 4,
    "all-reduce-implementation": ["ring"],
    "all-gather-implementation": ["ring"],
    "reduce-scatter-implementation": ["ring"],
    "all-to-all-implementation": ["ring"],
    "collective-optimization": "localBWAware",
    "local-mem-bw": 1600,
    "boost-mode": 0,
    "roofline-enabled": 0,
    "peak-perf": 900,
}
NO_MEM = {"remote-mem-latency": 0, "remote-mem-bw": 0}


def bad(msg):
    """Stop with an explanation rather than a traceback."""
    sys.exit("[ERROR] " + msg)


def parse_choice(text, flag, valid):
    items = [x.strip() for x in text.split(",") if x.strip()]
    if not items:
        bad(f"{flag} is empty.")
    for x in items:
        if x not in valid:
            bad(f"{flag}: '{x}' is not one of {', '.join(valid)}.")
    return items


def parse_ints(text, flag, minimum, why):
    items = [x.strip() for x in text.split(",") if x.strip()]
    if not items:
        bad(f"{flag} is empty.")
    out = []
    for x in items:
        if not x.isdigit() or int(x) < minimum:
            bad(f"{flag} takes whole numbers of {minimum} or more, not '{x}'. {why}")
        out.append(int(x))
    return out


def parse_topologies(text):
    items = [x.strip() for x in text.split(",") if x.strip()]
    if not items:
        bad("--topologies is empty.")
    out = []
    for t in items:
        if ":" not in t:
            bad(f"--topologies takes topology:bandwidth pairs, for example Ring:50 — got '{t}'.")
        topo, _, bw = t.partition(":")
        if topo not in TOPOLOGIES:
            bad(f"--topologies: '{topo}' is not one of {', '.join(TOPOLOGIES)}.")
        try:
            value = float(bw)
        except ValueError:
            bad(f"--topologies: '{bw}' in '{t}' is not a bandwidth in GB/s.")
        if value <= 0:
            bad(f"--topologies: the bandwidth in '{t}' must be greater than zero.")
        out.append((topo, bw))       # bandwidth kept as written, so it reaches the CSV unchanged
    return out


def unsupported(coll, npus, impl):
    """Return why this combination cannot be simulated, or None if it can."""
    if impl in POWER_OF_TWO_ONLY and npus & (npus - 1):
        return (f"{impl} needs a power-of-two NPU count, and {npus} is not one "
                f"(ring and direct run at any count)")
    if coll == "all_to_all" and impl in NO_ALL_TO_ALL:
        return (f"{impl} has no all_to_all implementation "
                f"(ring, direct and doubleBinaryTree do)")
    return None


def sh(cmd, cwd=None, timeout=900):
    return subprocess.run(cmd, cwd=cwd, shell=isinstance(cmd, str),
                          capture_output=True, text=True, timeout=timeout)


def ensure_workload(root, coll, npus, size_mb):
    """Generate the required Chakra ET workload if it does not exist."""
    wl = os.path.join(root, WL_DIR, coll, f"{npus}npus_{size_mb}MB", coll)
    if os.path.exists(wl + ".0.et"):
        return wl
    env = dict(os.environ, PYTHONPATH=root)
    r = subprocess.run(
        [sys.executable, os.path.join(root, GEN.format(coll=coll)),
         "--npus-count", str(npus), "--coll-size", str(size_mb)],
        cwd=os.path.join(root, WL_DIR), env=env, capture_output=True, text=True, timeout=600)
    if not os.path.exists(wl + ".0.et"):
        raise RuntimeError(f"workload generation failed ({coll} {npus}npus {size_mb}MB):\n"
                           f"{r.stdout[-400:]}\n{r.stderr[-400:]}")
    return wl


def write_net(path, topology, npus, bw, lat):
    with open(path, "w") as f:
        f.write(f"topology: [ {topology} ]\nnpus_count: [ {npus} ]\n"
                f"bandwidth: [ {bw} ]  # GB/s\nlatency: [ {lat} ]  # ns\n")


def write_sys(path, impl):
    cfg = dict(SYS_TEMPLATE)
    for k in ("all-reduce-implementation", "all-gather-implementation",
              "reduce-scatter-implementation", "all-to-all-implementation"):
        cfg[k] = [impl]
    json.dump(cfg, open(path, "w"), indent=2)


WALL = re.compile(r"sys\[(\d+)\],\s*Wall time:\s*(\d+)")


def run_one(root, binpath, workload, sysf, netf, memf):
    r = sh([binpath,
            f"--workload-configuration={workload}",
            f"--system-configuration={sysf}",
            f"--network-configuration={netf}",
            f"--remote-memory-configuration={memf}"], cwd=root)
    times = [int(m.group(2)) for m in WALL.finditer(r.stdout + r.stderr)]
    if not times:
        raise RuntimeError("'Wall time' not found in the ASTRA-sim output:\n"
                           + (r.stdout + r.stderr)[-600:])
    return max(times)          # a collective finishes only when the slowest NPU finishes


def main():
    ap = argparse.ArgumentParser(formatter_class=argparse.RawDescriptionHelpFormatter,
                                 description=__doc__)
    ap.add_argument("--root", default=os.environ.get("ASTRA_SIM_ROOT",
                    os.path.join(os.path.expanduser("~"), "astra-sim")),
                    help="path to the astra-sim repository")
    ap.add_argument("--collectives", default="all_reduce")
    ap.add_argument("--npus", default="8")
    ap.add_argument("--sizes-mb", default="1,8,64")
    ap.add_argument("--topologies", default="Ring:50", help="e.g. Ring:50,Switch:400")
    ap.add_argument("--latency-ns", type=float, default=500.0)
    ap.add_argument("--impls", default="ring")
    ap.add_argument("--congestion", default="unaware", choices=["unaware", "aware", "both"])
    ap.add_argument("--out", default="results.csv")
    a = ap.parse_args()

    root = os.path.abspath(os.path.expanduser(a.root))
    for m, p in BIN.items():
        if not os.path.exists(os.path.join(root, p)):
            sys.exit(f"[ERROR] build outputs not found: {os.path.join(root,p)}\n"
                     f"       run starter/setup.sh first.")

    colls = parse_choice(a.collectives, "--collectives", COLLECTIVES)
    npus_l = parse_ints(a.npus, "--npus", 2, "A collective needs at least two NPUs.")
    sizes = parse_ints(a.sizes_mb, "--sizes-mb", 1,
                       "The workload generator only makes whole-megabyte collectives.")
    tops = parse_topologies(a.topologies)
    impls = parse_choice(a.impls, "--impls", IMPLS)
    cong = ["unaware", "aware"] if a.congestion == "both" else [a.congestion]
    if a.latency_ns < 0:
        bad("--latency-ns cannot be negative.")

    tmp = tempfile.mkdtemp(prefix="astra_sweep_")
    memf = os.path.join(tmp, "no_mem.json"); json.dump(NO_MEM, open(memf, "w"))

    rows = []
    combos = list(itertools.product(colls, npus_l, sizes, tops, impls, cong))
    print(f"{len(combos)} combinations to run  (results → {a.out})\n")
    hdr = (f"{'collective':<15}{'npus':>5}{'MB':>7}{'topology':>16}{'GB/s':>8}"
           f"{'impl':>20}{'cong':>9}{'us':>13}{'achieved GB/s':>15}")
    print(hdr); print("-" * len(hdr))
    t_start = time.time()
    failed = skipped = 0
    for coll, npus, size, (topo, bw), impl, cg in combos:
        why = unsupported(coll, npus, impl)
        if why:
            skipped += 1
            print(f"{coll:<15}{npus:>5}{size:>7}{topo:>16}{bw:>8}{impl:>20}{cg:>9}{'skip':>13}")
            print(f"      {why}")
            continue
        try:
            wl = ensure_workload(root, coll, npus, size)
            netf = os.path.join(tmp, f"net_{topo}_{npus}_{bw}.yml")
            write_net(netf, topo, npus, bw, a.latency_ns)
            sysf = os.path.join(tmp, f"sys_{impl}.json"); write_sys(sysf, impl)
            ns = run_one(root, os.path.join(root, BIN[cg]), wl, sysf, netf, memf)
            us = ns / 1000.0
            # bytes actually moved / time (payload-based, since total traffic differs per implementation)
            achieved = (size * 1024 * 1024) / (ns * 1e-9) / 1e9
            rows.append(dict(collective=coll, npus=npus, size_mb=size, topology=topo,
                             bandwidth_GBps=bw, latency_ns=a.latency_ns, impl=impl,
                             congestion=cg, cycles_ns=ns, us=round(us, 2),
                             achieved_GBps=round(achieved, 2)))
            print(f"{coll:<15}{npus:>5}{size:>7}{topo:>16}{bw:>8}{impl:>20}{cg:>9}{us:>13.2f}{achieved:>15.2f}")
        except Exception as e:
            failed += 1
            print(f"{coll:<15}{npus:>5}{size:>7}{topo:>16}{bw:>8}{impl:>20}{cg:>9}{'FAIL':>13}")
            for line in str(e).strip().splitlines():       # the whole message, not the first 60 characters
                print(f"      {line}")
    if rows:
        with open(a.out, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)
        print(f"\nSaved {len(rows)} rows to {a.out}.  (total {time.time()-t_start:.1f} s)")
    else:
        print(f"\nEvery combination failed — {a.out} was not written.")
    shutil.rmtree(tmp, ignore_errors=True)
    if skipped:
        print(f"{skipped} of {len(combos)} combinations were skipped as unsupported; "
              f"those points are missing from {a.out}.")
    if failed:
        print(f"{failed} of {len(combos)} combinations failed; the messages above say why.")
        sys.exit(1)


if __name__ == "__main__":
    main()
