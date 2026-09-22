#!/usr/bin/env python3
"""
EE49904 Term Project — build the digital twin (run once; the result is cached).

Builds the two layers and saves them into a single .npz:

  (1) Geometry layer — Sionna RT radio map: the per-position SNR grid inside the scene
  (2) Link layer — Sionna PHY: per-MCS BLER(SNR) table (link-to-system mapping)

    python3 build_twin.py --scene simple_street_canyon --tx-dbm 20 --out twin_canyon.npz

The link layer takes a few minutes (about 3 min on 2 cores). The geometry layer takes under 1 second.
With --reuse-link you reuse the link layer of another scene and quickly build a twin with only the scene changed —
this works because **the link layer does not depend on the scene**, and that fact is itself one assumption of the twin design.
"""
import argparse, os, sys, time
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import twinlib as T

SCENES = ["simple_street_canyon", "simple_street_canyon_with_cars",
          "munich", "etoile", "florence", "san_francisco", "box"]


def main():
    ap = argparse.ArgumentParser(formatter_class=argparse.RawDescriptionHelpFormatter,
                                 description=__doc__)
    ap.add_argument("--scene", default="simple_street_canyon",
                    help=f"Sionna RT built-in scene. E.g.: {', '.join(SCENES[:4])}")
    ap.add_argument("--freq-ghz", type=float, default=3.5)
    ap.add_argument("--cell-size", type=float, default=2.0, help="radio map cell size (m)")
    ap.add_argument("--samples", type=float, default=1e6, help="ray samples per TX")
    ap.add_argument("--max-depth", type=int, default=3, help="maximum number of reflections")
    ap.add_argument("--tx-dbm", type=float, default=T.DEFAULT_TX_DBM)
    ap.add_argument("--bw-mhz", type=float, default=T.DEFAULT_BW_HZ / 1e6)
    ap.add_argument("--snr-min", type=float, default=-4.0)
    ap.add_argument("--snr-max", type=float, default=30.0)
    ap.add_argument("--snr-step", type=float, default=1.0)
    ap.add_argument("--k", type=int, default=512, help="number of information bits (block length)")
    ap.add_argument("--batch-size", type=int, default=200)
    ap.add_argument("--max-mc-iter", type=int, default=15)
    ap.add_argument("--target-block-errors", type=int, default=50)
    ap.add_argument("--reuse-link", default=None,
                    help="reuse the link layer (BLER table) of an existing twin .npz")
    ap.add_argument("--out", default="twin.npz")
    a = ap.parse_args()

    print(f"Building twin — scene '{a.scene}' @ {a.freq_ghz} GHz")

    # ---------------- (1) Geometry layer ----------------
    t0 = time.time()
    snr_map, centers, meta = T.radio_map_snr(
        a.scene, freq_hz=a.freq_ghz * 1e9, cell_size=a.cell_size,
        samples_per_tx=int(a.samples), max_depth=a.max_depth,
        tx_dbm=a.tx_dbm, bw_hz=a.bw_mhz * 1e6)
    valid = np.isfinite(snr_map)
    print(f"\n[1/2] Geometry layer — radio map  {time.time()-t0:.1f}s")
    print(f"  grid {snr_map.shape}  valid cells {valid.sum()}/{snr_map.size} "
          f"({valid.mean()*100:.0f}%)")
    q = np.nanpercentile(snr_map, [5, 25, 50, 75, 95])
    print(f"  SNR percentiles (dB)  5%={q[0]:.1f}  25%={q[1]:.1f}  50%={q[2]:.1f}  "
          f"75%={q[3]:.1f}  95%={q[4]:.1f}")

    # ---------------- (2) Link layer ----------------
    grid = np.arange(a.snr_min, a.snr_max + 1e-9, a.snr_step)
    if a.reuse_link:
        z = np.load(a.reuse_link)
        grid, bler = z["snr_grid_db"], z["bler_table"]
        print(f"\n[2/2] Link layer — reused from {a.reuse_link} (assumed scene-independent)")
    else:
        print(f"\n[2/2] Link layer — {len(T.MCS_TABLE)} MCS × {len(grid)} SNR points "
              f"(takes a few minutes)")
        t1 = time.time()
        bler = T.build_bler_table(grid, k=a.k, batch_size=a.batch_size,
                                  max_mc_iter=a.max_mc_iter,
                                  num_target_block_errors=a.target_block_errors)
        print(f"  took {time.time()-t1:.1f}s")

    np.savez_compressed(a.out, snr_map=snr_map, cell_centers=centers,
                        snr_grid_db=grid, bler_table=bler,
                        scene=a.scene, freq_hz=a.freq_ghz * 1e9,
                        cell_size=a.cell_size, bw_hz=a.bw_mhz * 1e6,
                        tx_dbm=a.tx_dbm, k=a.k)
    size = os.path.getsize(a.out)
    print(f"\nTwin saved to {a.out} "
          f"({size/1e6:.1f} MB)." if size >= 1e6 else f"\nTwin saved to {a.out} ({size/1e3:.0f} kB).")
    print(f"Next: {sys.executable} run_policy.py --twin {a.out}")


if __name__ == "__main__":
    main()
