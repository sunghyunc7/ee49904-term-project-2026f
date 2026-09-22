# Starter kit — Twin-Trained Control

## Quick start

```bash
bash setup.sh          # CPU-only PyTorch, then Sionna   (3–10 min)
export TWIN_VENV="${TWIN_VENV:-$HOME/twin-venv}"   # where setup.sh put the environment
bash smoke_test.sh     # 5 checks — all must pass

$TWIN_VENV/bin/python build_twin.py --scene simple_street_canyon --tx-dbm 20 --out twin.npz
$TWIN_VENV/bin/python run_policy.py --twin twin.npz --delay 0,5,20,50 --est-std 0,2 --speed 20
```

`setup.sh` runs in its own shell, so it cannot set `TWIN_VENV` for you — hence the `export` line.
Without it `$TWIN_VENV/bin/python` expands to `/bin/python` and the command is not found.

Then read `../BRIEF.md` and start with Question 1.

## What is in here

| File | Purpose |
|---|---|
| `setup.sh` | Virtualenv + **CPU-only** PyTorch, then Sionna — all at **pinned versions** (Sionna 2.1.0, Sionna RT 2.1.0, PyTorch 2.14.0; needs Python 3.11+). Ubuntu/Debian (incl. WSL2) and macOS. |
| `smoke_test.sh` | Five checks: runtime, geometry layer, link layer (with a monotonicity check on the BLER table), policy on the twin, and the core phenomenon — that delay and estimation error push BLER above target. |
| `twinlib.py` | MCS definitions, radio-map → SNR, trajectory walk, BLER table builder, policy derivation, evaluation. Add an MCS here. |
| `build_twin.py` | Builds and caches both twin layers into a `.npz`. Run once per scene. |
| `run_policy.py` | Derives the policy from a cached twin and evaluates it under deployment conditions. Writes `results.csv`. |

No root, no kernel features, no Docker, and no GPU required. The link layer always runs on CPU
PyTorch. The ray tracer runs through Mitsuba, which takes the `cuda_*` variant when the machine has
a CUDA GPU and the `llvm_*` (CPU) variant otherwise. Both are supported and the difference is small,
but it is not nothing — `smoke_test.sh` prints which one you got, so record it with your results.

## The twin has two layers

**Geometry** — `build_twin.py` ray-traces a scene and produces SNR over a grid of positions.
Under a second, even for Munich (70k cells).

**Link** — the same script simulates BLER against SNR for each MCS. This is a real Monte-Carlo
simulation and takes a few minutes. It is cached, so you only pay once.

Because the link layer does not depend on the scene, `--reuse-link` lets you build a second scene's
twin in seconds:

```bash
$TWIN_VENV/bin/python build_twin.py --scene etoile --reuse-link twin.npz --out twin_etoile.npz
```

That reuse is convenient *and* it is an assumption. Question 2 asks you to examine it.

## Choosing a link budget that makes the problem interesting

`build_twin.py` prints the SNR percentiles of your scene and the 10% BLER threshold of every MCS.
**Compare them.** If your SNR percentiles sit above the highest threshold, the policy will pick the
top MCS everywhere and no amount of delay or estimation error will change anything — you will
measure nothing.

Lower `--tx-dbm` until the percentiles straddle the ladder. For `simple_street_canyon`,
`--tx-dbm 20` gives SNR percentiles of roughly [-10, 2, 10, 19, 28] dB against thresholds of
2…22 dB, which is a good working range.

## Choosing a speed that makes delay matter

Feedback delay only costs you something if the channel changed while the report was in flight.
At 3 m/s with 10 ms steps a user moves 3 cm and the channel does not move at all. Use vehicular
speeds (`--speed 20` or more) to see the effect, and treat the relationship between speed, delay
and BLER as a measurement — that is Question 1.

## Typical commands

These assume the environment is active (`source $TWIN_VENV/bin/activate`). Otherwise write
`$TWIN_VENV/bin/python` in place of `python3`.

```bash
# build a twin (once per scene)
python3 build_twin.py --scene simple_street_canyon --tx-dbm 20 --out twin.npz

# second scene, reusing the expensive link layer
python3 build_twin.py --scene etoile --tx-dbm 20 --reuse-link twin.npz --out twin_etoile.npz

# deployment sweep
python3 run_policy.py --twin twin.npz --delay 0,5,20,50 --est-std 0,2 --speed 20 --out sweep.csv

# how much margin buys back the guarantee, and at what cost
python3 run_policy.py --twin twin.npz --delay 20 --est-std 2 --margin 0,1,2,3,4,5,6 --speed 20

# policy derived on one scene, deployed on another
python3 run_policy.py --twin twin.npz --deploy-twin twin_etoile.npz \
        --delay 0,20 --est-std 0,2 --margin 0,5 --speed 20
```

## Reading the output

`run_policy.py` prints, per condition: throughput (bit/s/Hz, failed blocks count as zero), measured
BLER, BLER as a multiple of the target, the fraction of steps where the policy refused to transmit
at all, and a met/VIOLATED verdict. `results.csv` has the same columns plus metadata.

Every printed number is a **mean over the `--trials` trajectories**. The CSV also carries `bler_min`
and `bler_max` over those trajectories — a condition can be `met` on the mean and violated on one
trajectory — and the run settings (`speed_mps`, `dt_s`, `steps`, `trials`), so CSVs from runs at
different speeds can be merged without losing track of which row came from which run.

## What `--deploy-twin` does

With `--deploy-twin F` the field is twin `F` in full: the user walks through F's geometry, and
whether a block is lost is decided by F's link layer. The policy is still derived from the link
layer of `--twin`. Two consequences:

- Twins built with `--reuse-link` share one link layer, so only the geometry differs — this is the
  scene-mismatch experiment of Question 2. `run_policy.py` prints which case you are in.
- A twin with a deliberately cheaper link layer (`--target-block-errors`, `--max-mc-iter`,
  `--snr-step`, `--k`) can be judged against a reference twin instead of against itself.

## Platform notes

**Windows (WSL2).** Keep this folder under `~/`, not `/mnt/c/`. Raise memory and cores in
`%USERPROFILE%\.wslconfig`, then `wsl --shutdown`:

```ini
[wsl2]
memory=16GB
processors=12
```

The link layer is CPU-bound; more cores directly reduce the few minutes it takes.

**macOS.** `setup.sh` only needs `python3`. Sionna's ray tracer uses Mitsuba's LLVM (CPU) variant.

**Linux.** The ray tracer needs the LLVM shared library (`libLLVM`), and the Python wheels do not
include it. Many systems have it only because some other package pulled it in; a clean image (a
fresh WSL2 distribution, a shared server) often does not. `setup.sh` checks: if Dr.Jit finds the
library it changes nothing; if not, it looks for one, installs it with `apt` (it asks for your
`sudo` password) or, without root, unpacks the package into the venv, and records the path there.

**Disk.** With the CPU-only PyTorch index the venv is roughly 1 GB. If `setup.sh` fell back to the
default PyPI index on Linux it pulls the CUDA build and exceeds 5 GB. The script says which it used.

## Common problems

| Symptom | Cause / fix |
|---|---|
| Every SNR maps to the top MCS | Link budget too generous. Lower `--tx-dbm`. |
| Delay changes nothing | User too slow, or cells too coarse. Raise `--speed`. |
| BLER thresholds not increasing with MCS | Table undersampled. Raise `--target-block-errors` or `--max-mc-iter`. |
| `ImportError: … the LLVM backend is inactive … libLLVM` | Run `setup.sh` again: it finds or installs `libLLVM` and records its path in the venv. A venv built by hand skips that step. |
| `mitsuba variant` is `cuda_*`, not `llvm_*` | Expected on a machine with an NVIDIA GPU — the ray tracer uses it. Nothing to fix; note the variant in your report. |
| `mitsuba variant` reported as `None` | Normal before the first scene load; the smoke test loads one first. |
| Link layer takes very long | Expected on few cores. Build once and reuse with `--reuse-link`. |
| Unknown scene name | Use one of Sionna's built-ins: `simple_street_canyon`, `munich`, `etoile`, `florence`, `san_francisco`. |
