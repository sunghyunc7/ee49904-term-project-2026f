#!/usr/bin/env python3
"""
EE49904 Term Project — evaluate a policy derived from the twin under field conditions.

Procedure:
  1. Derive the link adaptation policy from the BLER table of the twin
     ("the MCS with the highest spectral efficiency that keeps the target BLER")
  2. Evaluate **on the twin** — the SNR the policy sees = the actual transmission SNR
  3. Evaluate **under field conditions** — the SNR the policy sees is stale (feedback delay)
     and inaccurate (estimation error). The transmission still happens at the true SNR.

    python3 run_policy.py --twin twin.npz --delay 0,2,5,10 --est-std 0,1,2 \\
                          --margin 0,1,2,3 --out results.csv

In the twin evaluation the policy meets the target. By definition — the policy came from that table.
Whether it meets it in the field is the question of this assignment.
"""
import argparse, csv, itertools, os, sys
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import twinlib as T


def main():
    ap = argparse.ArgumentParser(formatter_class=argparse.RawDescriptionHelpFormatter,
                                 description=__doc__)
    ap.add_argument("--twin", default="twin.npz", help="file produced by build_twin.py")
    ap.add_argument("--deploy-twin", default=None,
                    help="take the field from another twin: its geometry and its link layer "
                         "(twins built with --reuse-link share the link layer, so only the geometry differs)")
    ap.add_argument("--target-bler", type=float, default=0.1)
    ap.add_argument("--delay", default="0", help="feedback delay (steps), comma-separated")
    ap.add_argument("--est-std", default="0", help="SNR estimation error std (dB), comma-separated")
    ap.add_argument("--margin", default="0", help="conservative backoff margin (dB), comma-separated")
    ap.add_argument("--steps", type=int, default=4000, help="trajectory length")
    ap.add_argument("--speed", type=float, default=3.0, help="user speed (m/s)")
    ap.add_argument("--dt", type=float, default=0.01, help="step interval (s)")
    ap.add_argument("--trials", type=int, default=3, help="number of different trajectories")
    ap.add_argument("--out", default="results.csv")
    a = ap.parse_args()

    z = np.load(a.twin, allow_pickle=True)
    grid, bler = z["snr_grid_db"], z["bler_table"]
    scene_train = str(z["scene"])
    zz = np.load(a.deploy_twin, allow_pickle=True) if a.deploy_twin else z
    snr_map, centers = zz["snr_map"], zz["cell_centers"]
    scene_deploy = str(zz["scene"])
    # The field is the deployment twin in full: the policy is derived from the link layer of --twin,
    # but whether a block is lost is decided by the link layer of --deploy-twin. For twins built with
    # --reuse-link the two tables are identical and only the geometry differs.
    truth_grid, truth_bler = zz["snr_grid_db"], zz["bler_table"]
    if truth_bler.shape[0] != bler.shape[0]:
        sys.exit(f"--deploy-twin has {truth_bler.shape[0]} MCS rows but --twin has {bler.shape[0]}: "
                 f"both twins must be built with the same MCS_TABLE.")
    same_link = (truth_grid.shape == grid.shape and np.array_equal(truth_grid, grid)
                 and np.array_equal(truth_bler, bler))

    print(f"policy-training twin: {scene_train}   field geometry: {scene_deploy}")
    if a.deploy_twin:
        print("field link layer: " + ("identical to the policy twin's" if same_link else
              "DIFFERENT from the policy twin's — the policy's BLER table is not the truth here"))
    print(f"target BLER {a.target_bler}  ·  trajectory {a.steps} steps × {a.trials} trials  "
          f"·  {a.speed} m/s, {a.dt*1000:.0f} ms/step")

    delays = [int(x) for x in a.delay.split(",")]
    ests = [float(x) for x in a.est_std.split(",")]
    margins = [float(x) for x in a.margin.split(",")]

    rows = []
    hdr = (f"{'margin':>7}{'delay':>7}{'est σ':>7}  "
           f"{'throughput':>11}{'BLER':>8}{'vs tgt':>9}{'sil%':>7}  verdict")
    print("\n" + hdr); print("-" * len(hdr))

    for margin in margins:
        policy = T.derive_policy(grid, bler, a.target_bler, margin_db=margin)
        for delay, est in itertools.product(delays, ests):
            th, bl, sil = [], [], []
            for tr in range(a.trials):
                snr_true, _ = T.walk_trajectory(snr_map, centers, steps=a.steps,
                                                speed_mps=a.speed, dt_s=a.dt, seed=tr)
                obs = T.stale_and_noisy(snr_true, delay_steps=delay,
                                        est_std_db=est, seed=100 + tr)
                r = T.evaluate(snr_true, obs, grid, bler, policy,
                               target_bler=a.target_bler, seed=200 + tr,
                               truth_grid_db=truth_grid, truth_table=truth_bler)
                th.append(r["throughput"]); bl.append(r["bler"]); sil.append(r["silent_frac"])
            thr, blr, slf = float(np.mean(th)), float(np.mean(bl)), float(np.mean(sil))
            viol = blr > a.target_bler
            tag = "VIOLATED" if viol else "met"
            print(f"{margin:>7.1f}{delay:>7d}{est:>7.1f}  {thr:>11.3f}{blr:>8.3f}"
                  f"{blr/a.target_bler:>8.1f}x{slf*100:>6.1f}  {tag}")
            rows.append(dict(scene_train=scene_train, scene_deploy=scene_deploy,
                             margin_db=margin, delay_steps=delay,
                             delay_ms=delay * a.dt * 1000, est_std_db=est,
                             target_bler=a.target_bler,
                             throughput_bps_hz=round(thr, 4),
                             bler=round(blr, 4),
                             bler_over_target=round(blr / a.target_bler, 3),
                             violates=viol, silent_frac=round(slf, 4),
                             # spread over the --trials trajectories (bler above is their mean)
                             bler_min=round(float(np.min(bl)), 4),
                             bler_max=round(float(np.max(bl)), 4),
                             # run settings, so that CSVs from different runs can be merged
                             speed_mps=a.speed, dt_s=a.dt, steps=a.steps, trials=a.trials))

    with open(a.out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)
    print(f"\nSaved {len(rows)} rows to {a.out}.")

    base = [r for r in rows if r["margin_db"] == 0 and r["delay_steps"] == 0
            and r["est_std_db"] == 0]
    if base:
        b = base[0]
        print(f"\nBaseline (delay 0 · error 0 · margin 0): "
              f"throughput {b['throughput_bps_hz']:.3f} b/s/Hz, BLER {b['bler']:.3f}")
        worst = max(rows, key=lambda r: r["bler"])
        if worst["bler"] > b["bler"]:
            print(f"Worst case (delay {worst['delay_ms']:.0f} ms · error {worst['est_std_db']:.0f} dB · "
                  f"margin {worst['margin_db']:.0f} dB): BLER {worst['bler']:.3f} "
                  f"= {worst['bler_over_target']:.1f}x the target")


if __name__ == "__main__":
    main()
