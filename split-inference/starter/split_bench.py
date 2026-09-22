#!/usr/bin/env python3
"""
EE49904 Term Project — split inference split point sweep.

Cuts the model at every stage boundary, computes
  (device compute time) + (intermediate tensor transfer time) + (remote compute time)
and finds the optimal split point for each link condition.

    python3 split_bench.py --models resnet18,mobilenet_v3_large \
                           --bandwidth 1,10,100 --rtt 5,50 --dtype fp32,fp16

Output: results.csv  +  a summary of the optimal split point per link condition

Notes:
  * The default is random weights (weights=None). Latency and tensor sizes do not depend on the
    weights, and this reproduces offline with no download. For accuracy experiments use --pretrained.
  * fp16/int8 reduce **only the number of bytes sent**. Real quantization error is not modeled —
    how you deal with that fact in the report is part of Question 2.
"""
import argparse, csv, itertools, os, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import splitlib as S


def main():
    ap = argparse.ArgumentParser(formatter_class=argparse.RawDescriptionHelpFormatter,
                                 description=__doc__)
    ap.add_argument("--models", default="resnet18")
    ap.add_argument("--bandwidth", default="1,10,100", help="Mb/s, comma-separated")
    ap.add_argument("--rtt", default="20", help="ms, comma-separated")
    ap.add_argument("--dtype", default="fp32", help="fp32,fp16,int8")
    ap.add_argument("--tail-speedup", type=float, default=20.0,
                    help="how many times faster the remote server is than this machine (default 20 = server GPU)")
    ap.add_argument("--device-slowdown", type=float, default=1.0,
                    help="how many times slower the real device is than this machine (5~20 for a phone)")
    ap.add_argument("--repeats", type=int, default=20)
    ap.add_argument("--threads", type=int, default=0, help="number of torch threads (0=default)")
    ap.add_argument("--pretrained", action="store_true")
    ap.add_argument("--channels-last", action="store_true",
                    help="use the NHWC memory layout — CPU depthwise conv gets several times faster")
    ap.add_argument("--out", default="results.csv")
    a = ap.parse_args()

    import torch
    if a.threads:
        torch.set_num_threads(a.threads)

    models = [m.strip() for m in a.models.split(",")]
    bws = [float(x) for x in a.bandwidth.split(",")]
    rtts = [float(x) for x in a.rtt.split(",")]
    dtypes = [d.strip() for d in a.dtype.split(",")]

    print(f"torch {torch.__version__} · threads {torch.get_num_threads()} · "
          f"weights {'pretrained' if a.pretrained else 'random'} · "
          f"layout {'NHWC(channels_last)' if a.channels_last else 'NCHW(default)'}")

    rows = []
    for mname in models:
        print(f"\n### {mname} — profiling stages…")
        stage_names, mods = S.build_model(mname, a.pretrained, a.channels_last)
        S.set_split_index(stage_names)
        t_s, shapes, numels, in_numel, spread = S.profile_stages(
            mods, repeats=a.repeats, channels_last=a.channels_last)

        print(f"  {'stage':<16}{'output shape':<22}{'out elements':>12}{'stage ms':>10}{'cum. ms':>10}{'jitter max/min':>16}")
        cum = 0.0
        worst = 1.0
        for n, sh, ne, t, sp in zip(stage_names, shapes, numels, t_s, spread):
            cum += t * 1000
            jitter = sp[2] / sp[0] if sp[0] > 0 else float("nan")
            worst = max(worst, jitter)
            print(f"  {n:<16}{str(sh):<22}{ne:>12,}{t*1000:>10.2f}{cum:>10.2f}{jitter:>15.1f}x")
        if worst > 3.0:
            print(f"  ! Measurement jitter is large (up to {worst:.1f}x). Close other programs and measure again —")
            print(f"    the optimal split point may change.")

        for bw, rtt, dt in itertools.product(bws, rtts, dtypes):
            best, best_t = None, float("inf")
            for sp in S.split_points(stage_names):
                tot = S.total_latency_s(sp, t_s, numels, in_numel, dt, bw, rtt,
                                        a.tail_speedup, a.device_slowdown)
                if sp == "input":
                    nb = S.payload_bytes(in_numel, dt); dev = 0.0
                elif sp == "all_local":
                    nb = 0; dev = sum(t_s) * a.device_slowdown
                else:
                    k = S.SPLIT_INDEX[sp]
                    nb = S.payload_bytes(numels[k], dt)
                    dev = sum(t_s[: k + 1]) * a.device_slowdown
                rows.append(dict(model=mname, layout="NHWC" if a.channels_last else "NCHW",
                                 split=sp, dtype=dt,
                                 bandwidth_mbps=bw, rtt_ms=rtt,
                                 tail_speedup=a.tail_speedup,
                                 device_slowdown=a.device_slowdown,
                                 tx_bytes=nb,
                                 device_ms=round(dev * 1000, 3),
                                 # all_local sends nothing, so it pays no link time at all — not even the RTT
                                 link_ms=0.0 if sp == "all_local"
                                         else round(S.link_time_s(nb, bw, rtt) * 1000, 3),
                                 total_ms=round(tot * 1000, 3)))
                if tot < best_t:
                    best_t, best = tot, sp
            print(f"  best @ {bw:>6.0f} Mb/s · RTT {rtt:>4.0f} ms · {dt:<5} → "
                  f"{best:<16} {best_t*1000:>8.1f} ms")

    with open(a.out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)
    print(f"\nSaved {len(rows)} rows to {a.out}.")


if __name__ == "__main__":
    main()
