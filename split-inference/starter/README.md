# Starter kit — Split Inference

## Quick start

```bash
bash setup.sh          # CPU-only PyTorch into a venv   (2–6 min)
export SPLIT_VENV=$HOME/split-inference-venv    # setup.sh prints this line — add it to your shell profile
bash smoke_test.sh     # 5 checks (6 results) — all must pass, and it calibrates your machine
```

Every command below starts with `$SPLIT_VENV/bin/python`. If the variable is not set in your shell,
that expands to `/bin/python` and fails — `setup.sh` cannot export it into your shell for you.

Then read `../BRIEF.md` and start with Question 1.

## What is in here

| File | Purpose |
|---|---|
| `setup.sh` | Creates a virtualenv and installs **CPU-only** PyTorch + torchvision. Ubuntu/Debian (incl. WSL2) and macOS. |
| `smoke_test.sh` | Five checks: runtime, model splitting, sweep driver, the live two-process harness, and a regime-crossover sanity check. Also runs the **pacer calibration** — read its output. |
| `splitlib.py` | Model → stage decomposition, tensor sizes, the analytical latency model. Add a model here. |
| `split_bench.py` | Sweeps cut points × bandwidth × RTT × dtype, writes `results.csv`, prints the optimum per link condition. |
| `split_serve.py` | The live harness: head in one process, tail in another, tensor over TCP through a userspace bandwidth pacer. Also `--calibrate`. |

No GPU, no root, no kernel features, no Docker. Everything runs identically on macOS, WSL2 and Linux.

## The one thing you must do first

```bash
$SPLIT_VENV/bin/python split_serve.py --selftest --calibrate
```

The link emulator paces bytes in userspace, so how well it tracks a target rate depends on your
machine's cores and current load. The calibration prints achieved-vs-target for a range of rates.

**Write the table down — as a reference, not as a ceiling.** It measures one payload size with
nothing else running. What counts is the **effective bandwidth** printed in the report of every
run: when it falls well short of the target, the harness emulated a slower link than you asked
for, and a prediction compared against the target will look plausible and be wrong.

## Typical commands

```bash
# stage profile + optimum per link condition, two models
$SPLIT_VENV/bin/python split_bench.py \
    --models resnet18,mobilenet_v3_large \
    --bandwidth 1,5,10,25,50,200 --rtt 20 \
    --device-slowdown 20 --tail-speedup 20 --out sweep.csv

# same, with NHWC memory layout — compare the optimum
$SPLIT_VENV/bin/python split_bench.py --models mobilenet_v3_large \
    --bandwidth 1,5,10,25,50,200 --rtt 20 \
    --device-slowdown 20 --tail-speedup 20 --channels-last --out sweep_nhwc.csv

# live measurement at one cut point (then check the effective bandwidth in the report)
$SPLIT_VENV/bin/python split_serve.py --selftest \
    --model resnet18 --split layer2 --bandwidth 10 --rtt 20

# server and client in separate terminals on one machine
$SPLIT_VENV/bin/python split_serve.py --role server --port 8471
$SPLIT_VENV/bin/python split_serve.py --role client --port 8471 \
    --model resnet18 --split layer2 --bandwidth 10 --rtt 20

# on two machines — the server listens on 127.0.0.1 only unless you tell it otherwise
$SPLIT_VENV/bin/python split_serve.py --role server --host 0.0.0.0 --port 8471       # machine A
$SPLIT_VENV/bin/python split_serve.py --role client --host <address-of-A> --port 8471 \
    --model resnet18 --split layer2 --bandwidth 10 --rtt 20                           # machine B
```

**Two machines: only on a network you trust.** The server unpickles whatever arrives on its port,
and unpickling data from an untrusted sender can run arbitrary code. Stop the server when you are
done. The pacer and the emulated RTT are added *on top of* the real link between the two machines,
so check the effective bandwidth here too.

Cut points: `input`, each stage name, `all_local`. Run `split_bench.py` once to see the stage names
for your model.

## Reading the numbers

`split_bench.py` prints a stage table with a **max/min jitter column**. If any stage exceeds ~3×,
your machine is too busy to measure on — close other programs and re-run. The optimal cut point
can and does flip under noise.

`split_serve.py` prints a breakdown: device compute, serialization, RTT (measured), remote
deserialization, actual transfer, and the **effective bandwidth** it managed. Compare effective
against target before you interpret the error.

The live harness runs both halves on your machine and always sends fp32, so it measures at
`--device-slowdown 1 --tail-speedup 1 --dtype fp32` whatever you type. Those three options rescale
only the *predicted* side; leave them at their defaults in `split_serve.py` (it warns you if you do
not) and apply your scenario in `split_bench.py`.

## Platform notes

**Windows (WSL2).** Keep this folder under your Linux home (`~/`), not `/mnt/c/`. The 9p filesystem
is slow and adds timing noise. Raise WSL's memory in `%USERPROFILE%\.wslconfig`:

```ini
[wsl2]
memory=16GB
processors=12
```

then `wsl --shutdown` in PowerShell. A fresh Ubuntu WSL image may not have `python3-venv`;
`setup.sh` installs it.

**macOS.** Nothing special — `setup.sh` only needs `python3`. The PyTorch wheel is CPU/MPS already.

**Disk.** With the CPU-only index the venv is roughly 500 MB. If `setup.sh` had to fall back to the
default PyPI index on Linux, it pulls the CUDA build and the venv exceeds 5 GB. The script tells you
which path it took.

## Common problems

| Symptom | Cause / fix |
|---|---|
| `PyTorch import` fails in smoke test | `setup.sh` did not finish, or `SPLIT_VENV` points elsewhere. |
| Effective bandwidth far below target | The pacer could not keep up — usually a busy CPU. Close other programs and re-run; if it persists, lower `--bandwidth`, or base the comparison on the effective number. |
| Live harness error is large, and a `!` line mentions PREDICTED | You passed `--device-slowdown`, `--tail-speedup` or `--dtype` to `split_serve.py`. They rescale the prediction only. |
| `/bin/python: No such file or directory` | `SPLIT_VENV` is not set in this shell: `export SPLIT_VENV=$HOME/split-inference-venv`. |
| A second client against one server shows nothing unusual | The server handles one connection at a time and the wait hides in the discarded warm-up iteration — see extension D in the brief. |
| Optimal cut point differs between runs | Measurement noise. Check the jitter column, close other programs. |
| `--split` rejected | Use a stage name from the table `split_bench.py` prints, or `input` / `all_local`. |
| Everything says `all_local` | You left `--device-slowdown` and `--tail-speedup` at 1.0. Offloading to a machine identical to yours cannot help. |
| Port already in use | Another run left a server behind: `--port 8472`, or kill the stray process. |
