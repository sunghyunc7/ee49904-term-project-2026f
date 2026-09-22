# Term Project Brief — Split Inference: Placing Computation Across a Network

**Stack:** [PyTorch](https://github.com/pytorch/pytorch) / [torchvision](https://github.com/pytorch/vision) (CPU only) + a userspace link emulator included in the kit
**Team:** 2–3 students · **Hardware:** any laptop (no GPU, no root, no VM, no kernel features)
**Track link:** Network for AI (edge AI, split inference, placement under network constraints)

> Weights, deadlines, and submission mechanics are in the syllabus and on the LMS.
> This brief defines *what you must produce*, not when.

---

## 1. The problem

A neural network does not have to run in one place. You can execute the first *k* layers on the
device, ship the intermediate tensor across a link, and finish on an edge server. Where you cut
decides the latency, and the right cut is not a property of the model — it moves with the network.

The classic result (Neurosurgeon, ASPLOS 2017) is that the optimal partition point is
layer-dependent and shifts with link conditions, and that intermediate activations can be *larger*
than the input you were trying to avoid sending. You will reproduce that reasoning, and then you
will find out how much of it survives contact with a real measurement.

Because that is the second half of this project. The kit gives you two tools:

- **`split_bench.py`** — the paper model. Measures per-stage compute, counts intermediate tensor
  bytes, and computes `T = T_device + (bytes / bandwidth + RTT) + T_remote` for every cut point.
- **`split_serve.py`** — the real thing. Runs the head in one process and the tail in another,
  serializes the intermediate tensor, ships it over TCP through a userspace bandwidth pacer,
  and reports what actually happened next to what the model predicted.

The gap between those two numbers is where this project gets interesting.

> **Read before you plan.** Y. Kang et al., "Neurosurgeon: Collaborative Intelligence Between the
> Cloud and Mobile Edge," ASPLOS 2017, pp. 615–629, doi:10.1145/3037697.3037698 — free at
> <https://ypkang.github.io/downloads/kang17neurosurgeon.pdf> (paper P33 in the course pool). The
> lecture on placement and split inference comes in the second half of the semester, after you
> have chosen and planned this project, so this brief and that paper have to carry you until then.
> The per-layer latency and data-size study and the partitioning algorithm are the parts you need.

---

## 2. Getting started

```bash
cd starter/
bash setup.sh                 # CPU-only PyTorch into a venv   (2–6 min)
export SPLIT_VENV=$HOME/split-inference-venv   # setup.sh prints this line; add it to your shell profile
bash smoke_test.sh            # 5 checks (6 results) — all must pass
```

The smoke test includes a **pacer calibration**: for a range of target link rates it prints the
rate the harness actually achieved on your machine. **Record that table — as a reference, not as a
ceiling.** The number that counts is the **effective bandwidth** printed in the report of every
live run. When it falls well short of the target, the harness did not emulate the link you asked
for — it emulated a slower one, and a conclusion drawn against the target will be wrong in a way
that looks like a real result. Checking how well your own machine tracks is the first measurement
of the project.

Then:

```bash
$SPLIT_VENV/bin/python split_bench.py --models resnet18,mobilenet_v3_large \
    --bandwidth 1,5,10,25,100 --rtt 20 --device-slowdown 8 --tail-speedup 20

$SPLIT_VENV/bin/python split_serve.py --selftest --model resnet18 --split layer2 \
    --bandwidth 10 --rtt 20
```

Two scenario knobs matter and you must justify your choice of both:
`--device-slowdown` (how much slower the real device is than your laptop — a phone is 5–20×) and
`--tail-speedup` (how much faster the server is — 10–50× for a GPU). Set both to 1.0 and offloading
never wins; that is arithmetic, not a finding.

To justify a factor, name the device and the server you have in mind and cite where the number
comes from: a published benchmark, a vendor specification, or your own measurement on the real
hardware. A round number with no source is a guess, and the report should call it one.

Both knobs belong to `split_bench.py`. The live harness runs the two halves on your own machine, so
it always measures at 1×/1×; leave the knobs at 1.0 there. Q2 explains what that comparison is for.

Cut points are `input` (send the raw image, compute nothing locally), each stage boundary, and
`all_local` (compute everything on the device, send nothing).

---

## 3. Required questions

Answer all three. Each is tied to a tool this course has been building.

### Q1 — Which resource is scarce, on what timescale, and with what information?
> *Course tool: the four persistent control problems.*

Track 1 taught you to read a system by asking what is contended, over what timescale, and what the
controller knows when it decides. Say first whether this is one of the four wireless control
problems or none of them — and if none, name the analog you would defend and the loop the work
actually closes. Apply it to placement:

- **What is contended** — device compute, link bandwidth, or the server?
- **On what timescale** — per inference, per video frame, per session?
- **With what information** — does the split decision know the *current* link rate, or a rate
  measured minutes ago?

Concretely: for at least two models with different compute profiles, sweep bandwidth across at
least two decades and report the optimal cut point at each. Identify every **regime boundary** —
the bandwidth at which the winner changes — and explain each in terms of which term dominates.
Then answer the control question: if link rate varies over a session, how often must the split
decision be revisited, and what does the device need to measure to make it?

### Q2 — What did your model verify, and what did it not?
> *Course tool: the acceptance test.*

`split_bench.py` produces a prediction. `split_serve.py` produces a measurement. They will not
agree. Your job is to account for the difference honestly, not to make it disappear.

Be clear about what is being compared. The live harness runs the head and the tail on the same
machine and always ships fp32, so it measures at `--device-slowdown 1 --tail-speedup 1 --dtype fp32`
whatever scenario you chose in Q1 — those options rescale only the predicted side, and the harness
warns you if you set them. It therefore tests the *structure* of the paper model — which terms
exist, and how large the ones it leaves out are — not your scenario.

1. Pick at least three cut points and compare predicted against measured. Report the error for
   each, and report the **effective bandwidth** the harness printed alongside it — a prediction
   compared against a link that did not actually run at the target rate is not a comparison.
2. **Decompose the error.** The harness breaks the measurement into device compute, serialization,
   RTT, remote deserialization, remote compute, and effective bandwidth. At least one line item in
   that breakdown has no counterpart in the paper model. Name every unmodeled cost you find and
   quantify it.
3. Now attack your own measurement. Re-run one configuration with `--channels-last` and compare the
   stage profile. If the optimal cut point changes, say so plainly and explain what that means: the
   number you called "device compute time" was never a property of the model alone.
4. State which of your Q1 regime boundaries survive both the model and the measurement, and which
   are artifacts of one of them. The harness cannot run your scenario, so do this by correction:
   put what you measured in items 1–3 — the unmodeled terms, the effective rather than the target
   bandwidth, the NHWC stage profile — back into the scenario model, recompute the boundaries, and
   report which ones move and by how much.

**Honest declaration of what you did not verify earns credit.** "We could not emulate above
X Mb/s on our hardware, so the high-bandwidth regime is model-only" is a good sentence to have in
your report. Silence about it is not.

### Q3 — Where does your finding sit on the gap ladder?
> *Course tool: the four gaps from cloud-native to AI-native.*

- **Gap 1 — GPU/radio-aware orchestration.** Does your result constrain *where* inference should be
  scheduled, and on what evidence?
- **Gap 2 — telco-grade data plane.** Which of your assumptions is really a demand on the data plane?
- **Gap 3 — MLOps × NetOps.** An adaptive split needs the serving stack to know the link state.
  What exactly would have to be shared, at what rate, and who owns it today?
- **Gap 4 — bounded, verifiable autonomy.** Your numbers are averages over repeated runs. A device
  that re-plans its split automatically will sometimes choose wrong. What would have to be true
  before you would let it?

Pick the gap your work speaks to most directly, argue for it, and state what evidence would move it
to the next rung.

---

## 4. Required extension — pick exactly one

In this brief the extension is **part of the assignment, not a bonus**. The starter kit makes the
required questions quick to run, so the extension is where your team shows depth. Choose one — not
zero, not two — name it in your selection + plan, and report it as its own section after Q1–Q3.

- **A. Adaptive split policy.** Build a policy that picks the cut point from measured link state.
  Drive it with a varying bandwidth trace, compare against the best fixed split and against an
  oracle that always knows the current rate. Report the regret and the cost of re-planning.
- **B. Feature compression.** The kit models fp16/int8 as *byte counts only*. Implement real
  quantization of the intermediate tensor, measure the accuracy cost (use `--pretrained` and a
  small labeled sample), and place the accuracy/latency trade-off on the same axes as Q1.
- **C. Early exit.** Add a classifier head partway through the network. Route confident inputs to
  the local exit and the rest to the server. Report the exit rate, the accuracy cost, and the
  effective latency distribution — not just the mean.
- **D. Batch and concurrency.** Everything above is batch-1, one request at a time. Re-run with
  several concurrent clients against one server and report where the bottleneck moves.

**What the kit gives you for each, and what it does not**

- **A.** The server takes the cut point with every request, and `send_msg()` takes the link rate
  with every message, so a bandwidth trace is a list of rates that your own client loop steps
  through. The only honest input to the policy is what the client can measure — for example, the
  effective bandwidth of the requests it has already sent. Because the harness measures at 1×/1×
  (see Q2), you may evaluate the policy on the scenario model driven by the trace, on the live
  harness, or on both. Say which, and what the one you skipped would have added.
- **B and C.** You need labeled images, and ImageNet itself requires registration. **Imagenette**, a
  freely downloadable 10-class subset of ImageNet, is enough:
  `torchvision.datasets.Imagenette(root, split="val", size="320px", download=True)` (roughly
  330 MB; `"160px"` is roughly 100 MB but has to be upsampled to the 224-pixel input). Preprocess
  with `weights.transforms()`, map the ten class names to ImageNet indices with
  `weights.meta["categories"].index(name)`, and when you compare against a 10-class head restrict
  the full model's output to the same ten classes.
- **B.** The server must dequantize before it runs the tail, so you will change `split_serve.py`
  on both sides. Report the bytes actually sent, not `numel × 1`.
- **C.** Freeze the backbone, cache the intermediate features once, and train a small head on the
  cache. On a laptop CPU that is minutes, not hours: about 17 ms per image to reach `layer2` of
  ResNet-18 on two cores, and seconds to fit a linear head on the cached features. The exit
  criterion is yours to choose and to defend (BranchyNet, arXiv:1709.01686, is the usual start).
- **D.** The kit's server handles **one connection at a time**. A second client waits in the accept
  queue, and because every client discards its first iteration as warm-up, that wait never reaches
  its report: run two clients against the unmodified server and both look normal. Making the
  server concurrent is the first step of this extension, not a detail. Then decide and state two
  things: whether your clients share one bottleneck link or each have their own (the pacer is per
  client), and how you keep client and server compute from contending for the same cores — pin
  threads, and use two machines if you can (`starter/README.md` shows how).

---

## 5. Deliverables

| Stage | What you hand in |
|---|---|
| **Selection + plan** | 1 page: your device/server scenario with justification, the models you will study, your chosen extension, and what `--calibrate` reported on your machine (a reference figure, not a limit). |
| **Progress note** | 1 page: measurements so far, the biggest predicted-vs-measured gap, what changed in the plan. |
| **Workshop** | Presentation/demo. Show one plot where the optimal cut point moves, and one predicted-vs-measured comparison. |
| **Final report** | 6 pages, IEEE format. Q1/Q2/Q3 and the extension must be identifiable sections. Include your CSVs and the exact commands that produced them. |

Reproducibility is graded: another team must be able to re-run your commands on their own machine,
after their own calibration, and either recover the same *structure* — the order of the regimes and
the term that dominates each — or explain from their own stage profile why theirs differs. Absolute
milliseconds differ between machines; that is expected and is not a reproducibility failure.

**AI tool use:** declare which tools you used and for what. Disclosure is required and is not
penalized; undisclosed use is an integrity matter.

---

## 6. Traps worth knowing in advance

- **A target bandwidth is a request, not a fact.** The pacer runs in userspace, so on a busy
  machine the link may not reach the rate you asked for. Every run prints the **effective
  bandwidth** it achieved; quote that number, not the one you typed, and the harness tells you when
  it fell below 80 % of the target. `--calibrate` gives you a *reference* for how well pacing
  tracks on your machine — a hint, not a ceiling: it measures one payload size with nothing else
  running.
- **`split_serve.py` always measures 1×/1× and fp32.** `--device-slowdown`, `--tail-speedup` and
  `--dtype` rescale only the prediction there. See Q2.
- **The first stage can make the data bigger.** Check the tensor sizes before you assume that
  computing more locally always means sending less.
- **Timing on a busy laptop is not timing.** The kit reports a max/min spread per stage and warns
  you when it exceeds 3×. Close your browser and re-run; the optimal cut point can flip.
- **Random weights are fine — until they are not.** The kit defaults to untrained weights because
  latency does not depend on weight values, and this keeps results offline and reproducible. Only
  extension B and C need `--pretrained`.
- **`fp16`/`int8` in `split_bench.py` change byte counts only.** No quantization is performed and no
  accuracy is lost, because no accuracy is computed. Saying so in your report is part of Q2.
- **Which Linux, if you are on WSL2:** install the verified distribution —
  `wsl --install -d Ubuntu-24.04`. Newer releases work too (the kit carries the fixes that
  GCC 15 and glibc 2.41 made necessary), but 24.04 is the combination this kit is verified on
  and it is the shortest path to a green smoke test.
- **WSL2 users:** keep this folder under your Linux home (`~/`), not `/mnt/c/`, and raise the memory
  limit in `.wslconfig`. Both affect measurement stability, not just speed.

---

## 7. Open source used

Everything below is third-party work. Cite it in your report the way you would cite a paper, and
respect the license if you publish your code.

| Project | What it does here | Repository | License |
|---|---|---|---|
| **PyTorch** | runs the model and gives per-stage timings | [github.com/pytorch/pytorch](https://github.com/pytorch/pytorch) | BSD-3-Clause style (see repo `LICENSE`) |
| **torchvision** | the ResNet you split | [github.com/pytorch/vision](https://github.com/pytorch/vision) | BSD-3-Clause |

The link emulator (`splitlib.py`, `split_serve.py`) is course material, not upstream code. The partitioning idea comes from Neurosurgeon (Kang et al., ASPLOS 2017) — a paper to cite, not software to install.

**Versions are not pinned.** The kit installs whatever is current when you run `setup.sh`. It was verified on 2026-09-19 with torch 2.13.0 / torchvision 0.28.0 (macOS and Linux) and with torch 2.14.0 / torchvision 0.29.0 (Linux). Record the versions you actually used — `smoke_test.sh` prints most of them — and report them with your results. Two teams on different versions can get different numbers, and that is worth knowing rather than arguing about.

---

## Revision history

| Rev | Date | What changed |
|---|---|---|
| **r5** | 2026-09-21 | Q1: one sentence added before "Apply it to placement:" — say first whether this is one of the four wireless control problems or none of them, and if none, name the analog and the loop the work closes. `split_serve.py`: report annotations trimmed; every printed number and column is unchanged. No measurement requirement, command or number changed. |
| **r4** | 2026-09-19 | Pre-release review, checked by running the kit. **Harness (`split_serve.py`):** Nagle's algorithm is now off on every socket — on Linux the delayed-ACK stall had been booked as transfer time, so effective bandwidth stopped near 10 Mb/s there while macOS tracked 100 Mb/s; the pacer now waits before it sends a chunk instead of after; `all_local` is measured like every other cut point (warm-up discarded, mean of the rest); a warning is printed when `--device-slowdown`, `--tail-speedup` or `--dtype` is set in the live harness. **`split_bench.py`:** `link_ms` is 0 for `all_local`. **§1:** "Read before you plan" added. **§2:** the calibration result is a reference, not a ceiling (the r1 sentence "stay below it" is removed); `export SPLIT_VENV`; what counts as justifying a scenario knob; the knobs belong to `split_bench.py`. **Q2:** states that the live harness measures at 1×/1× and fp32, and item 4 says how to carry the measurement back into the scenario. **§4:** a note per extension on what the kit gives and does not give — trace input (A), Imagenette as the labeled sample (B, C), CPU cost of training a head (C), the server handles one connection at a time (D). **§5:** the extension is a section of the final report; reproducibility means the same structure, not the same milliseconds. **§6:** first trap rewritten, the 16-core anecdote removed (it was the socket artifact), one trap added. **§7:** verified versions stated. Starter `README.md` updated to match, including a working two-machine example. |
| **r3** | 2026-09-19 | Spelling only: American spelling throughout the brief and the kit (behavior, modeled, license, serialization, …), to match the lecture decks and notes. No requirement, command or number changed. |
| **r2** | 2026-09-19 | §4: the extension changes from optional ("pick at most one") to **required ("pick exactly one")**. The kit makes Q1–Q3 fast to run, so depth is shown in the extension. No other section changed. |
| **r1** | 2026-08 | First release (kit confirmed by 2026-08-22). |
