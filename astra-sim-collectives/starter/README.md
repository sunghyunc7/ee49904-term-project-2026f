# Starter kit — Collective Communication and Fabric Design (ASTRA-sim)

## Quick start

```bash
bash setup.sh          # deps + clone + build ASTRA-sim   (~5–10 min)
bash smoke_test.sh     # 5 checks — all must pass
python3 run_sweep.py --help
```

Then read `../BRIEF.md` and start with Question 1.

## What is in here

| File | Purpose |
|---|---|
| `setup.sh` | Installs dependencies, clones ASTRA-sim, builds the analytical backend, creates a Python venv for the workload generator. Ubuntu/Debian (incl. WSL2) and macOS. |
| `smoke_test.sh` | Five checks, including a **model-sanity check** (doubling bandwidth must roughly halve a large-message collective). Exit code = number of failures. |
| `run_sweep.py` | Sweeps ASTRA-sim over collectives × NPU counts × message sizes × topologies × algorithms × congestion backends, writes `results.csv`. Standard library only. |
| `configs/` | Reference `network/` and `system/` config files, for when you want to hand-edit rather than sweep. |

Install location defaults to `~/astra-sim`. Override with `ASTRA_SIM_ROOT`:

```bash
ASTRA_SIM_ROOT=~/work/astra-sim bash setup.sh
export ASTRA_SIM_ROOT=~/work/astra-sim      # add to ~/.bashrc
```

## Platform notes

**Windows (WSL2) — read this before you start.**

1. Keep everything under your **Linux home** (`~/`). Building under `/mnt/c/...` is 5–10× slower.
2. Raise WSL's memory limit. Create or edit `%USERPROFILE%\.wslconfig` in Windows:
   ```ini
   [wsl2]
   memory=16GB
   processors=12
   ```
   Then in PowerShell: `wsl --shutdown`, and reopen your terminal. A default WSL2 install often
   gets ~7 GB regardless of how many cores your laptop has, which makes builds crawl.
3. A fresh Ubuntu WSL image has no compiler at all. `setup.sh` installs what is needed.

**macOS.** Requires [Homebrew](https://brew.sh). `setup.sh` installs `cmake`, `ninja`, `protobuf`.

**No GPU is used anywhere in this project.** The analytical backend is pure CPU and each simulation
finishes in well under a second.

## Manual dependency list

If `setup.sh` cannot run on your system, install these and then run
`./build/astra_analytical/build.sh` from the ASTRA-sim repository root:

- C++17 compiler, `cmake` ≥ 3.15, `make` (or `ninja`), `git`
- `protobuf-compiler` and `libprotobuf-dev` (macOS: `protobuf`)
- Python 3.8+ with the `protobuf` package (workload generator only)

## Common problems

| Symptom | Cause / fix |
|---|---|
| `build outputs not found` from `run_sweep.py` | `setup.sh` did not finish, or `ASTRA_SIM_ROOT` points elsewhere. |
| Workload generation fails with `ModuleNotFoundError: google.protobuf` | Use the venv: `$ASTRA_SIM_ROOT/.venv/bin/python`, or `pip install protobuf`. |
| `Wall time` not found in output | The simulator crashed. Run the command from `smoke_test.sh` step 3 by hand to see the error. |
| Build takes more than 20 minutes | You are almost certainly on WSL2 with default memory, or building under `/mnt/c/`. See above. |

## Regenerating a result

Every number in your report must be reproducible from a single command. Record them, e.g.:

```bash
python3 run_sweep.py --collectives all_reduce --npus 8 --sizes-mb 1,16,256 \
        --topologies Ring:50,Ring:100,Ring:200 --impls ring \
        --congestion unaware --out q1_bandwidth_sweep.csv
```
