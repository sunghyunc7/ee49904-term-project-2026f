# Term Project Brief — Collective Communication and Fabric Design

**Stack:** [ASTRA-sim](https://github.com/astra-sim/astra-sim) (analytical network backend)
**Team:** 2–3 students · **Hardware:** any laptop (no GPU, no root, no VM)
**Track link:** Network for AI (AI workloads → parallelism → collectives → fabric)

> Weights, deadlines, and submission mechanics are in the syllabus and on the LMS.
> This brief defines *what you must produce*, not when.

---

## 1. The problem

Every parallelism choice a training job makes is a communication choice. Data parallelism emits an
**all-reduce** of gradients each step. ZeRO-style sharding splits that into **reduce-scatter** plus
**all-gather**. Tensor parallelism emits an all-reduce of *activations* — many times per step.
Mixture-of-Experts emits an **all-to-all** that routes every token to its expert.

So a question that sounds like a machine-learning question — *which parallelism layout should we use?* —
is answered by the network. Your job in this project is to answer it **with numbers**, for a fabric
you specify, and then to say honestly how far your numbers can be trusted.

ASTRA-sim is the instrument. It takes three inputs — a **workload** (which collective, how big, how many
NPUs), a **system** configuration (which collective *algorithm*: ring, direct, tree, halving-doubling),
and a **network** configuration (topology, bandwidth, latency) — and returns the time the collective takes.
It ships two network backends: one that ignores link contention (**congestion-unaware**) and one that
models it (**congestion-aware**). The difference between those two numbers will matter more than you expect.

---

## 2. Getting started

```bash
cd starter/
bash setup.sh                 # deps + clone + build   (about 5–10 min)
bash smoke_test.sh            # 5 checks — all must pass before you go further
python3 run_sweep.py --help
```

`run_sweep.py` runs ASTRA-sim across a grid of settings and writes one `results.csv`:

```bash
python3 run_sweep.py \
    --collectives all_reduce,all_to_all \
    --npus 4,8,16 \
    --sizes-mb 1,8,64 \
    --topologies Ring:50,Switch:400 \
    --impls ring,direct \
    --congestion both
```

Columns: `collective, npus, size_mb, topology, bandwidth_GBps, latency_ns, impl, congestion,
cycles_ns, us, achieved_GBps`. Times are in **nanoseconds**; `us` is microseconds; `achieved_GBps`
is payload bytes divided by elapsed time (**not** the link rate — the ratio between them is
information, not an error).

`--topologies` takes `Ring`, `Switch`, or `FullyConnected`, each with a per-link bandwidth in GB/s.
`--sizes-mb` takes whole megabytes, minimum 1 — a limit of the upstream workload generator.

A full sweep of a few dozen points takes seconds at the NPU counts you will mostly use — but the
cost per point grows steeply with `--npus`, and a single point at 256 NPUs can take minutes. Add
large NPU counts deliberately rather than by widening every axis at once. Otherwise you are not
compute-limited on this project. You are limited by how carefully you choose what to measure and
how honestly you read it.

### Read before you plan

The lectures on parallelism and collectives come after your selection + plan is due. These are
enough to design a sweep:

- R. Thakur, R. Rabenseifner, and W. Gropp, "Optimization of Collective Communication Operations in
  MPICH," *Int. J. High Performance Computing Applications*, vol. 19, no. 1, 2005 — the ring,
  recursive halving/doubling, and tree algorithms, and the latency–bandwidth cost model behind Q1.
- W. Won et al., "ASTRA-sim2.0: Modeling Hierarchical Networks and Disaggregated Systems for
  Large-model Training at Scale," *IEEE ISPASS*, 2023 — what the simulator models, and what its
  analytical backend leaves out.
- For extension A only: D. Narayanan et al., "Efficient Large-Scale Language Model Training on GPU
  Clusters Using Megatron-LM," *SC '21*; S. Rajbhandari et al., "ZeRO: Memory Optimizations Toward
  Training Trillion Parameter Models," *SC '20*.

---

## 3. Required questions

Answer all three. Each is tied to a tool this course has been building, and your report is graded
against those tools — not against getting a particular number.

### Q1 — Which resource is actually scarce, and on what timescale?
> *Course tool: the four persistent control problems.*

Track 1 taught you to read a wireless system by asking which resource is contended, on what timescale,
and with what information the controller has. That lens is not about radios — it is about contention.
Apply it to a fabric:

- **What is contended** — link bandwidth, switch capacity, or the endpoint itself?
- **On what timescale** — per collective, per step, per chunk?
- **With what information** — does the collective algorithm know about congestion, or is it open-loop?

None of the four wireless problems — channel, spectrum sharing, medium access, mobility — applies to
a fabric literally, and saying so is the correct first line. Then name the one you would defend as
the closest analog for a collective on a shared fabric, and say where the analogy stops holding.

Concretely: sweep message size across at least three decades for one collective, at **three or more**
link bandwidths. Two bandwidths are not enough: a two-parameter line passes through any two points
exactly, so the fit would tell you nothing. Sizes are whole megabytes, so three decades means going
from 1 MB to 1 GB or beyond. At each size, fit your measurements to `T = a + b/BW`. Report `a` and
`b`, say **what physical quantity each term corresponds to**, and say what the quality of the fit
tells you about the instrument you are using. Report **where the crossover `a = b/BW` sits**, and say
on which axis you are reporting it — as a bandwidth at each size, or as a size at each bandwidth.
Then state which of your design knobs is useless below the crossover and which is useless above it.

This is the question that decides whether "buy a faster fabric" is the right answer at all.

### Q2 — What did your model actually verify, and what did it not?
> *Course tool: the acceptance test.*

ASTRA-sim's analytical backend is a **model**, and you are being asked to make a claim on the strength
of it. The acceptance test asks: for a component whose behavior you did not derive from first
principles, what conditions did you check, and what remains unchecked?

Do this concretely:

1. Run the same configuration under **both** the congestion-unaware and congestion-aware backends.
2. Find where the two backends **rank the design choices differently** — not merely differ in
   magnitude, but disagree about which option is best. One such configuration is an anecdote; report
   the **boundary**: over message size, NPU count, and link bandwidth, where the disagreement appears
   and where it disappears. It does not appear everywhere. It depends on the collective *algorithm*
   and on the *topology* it runs on, so a study confined to one topology may find none — and that
   absence needs an explanation as much as a disagreement does.
3. Explain the mechanism: what does the unaware backend assume, and which assumption breaks? Relate
   the boundary to your Q1 crossover.
4. State plainly which of your conclusions survive under both backends and which depend on one of them.

**Honest declaration of what you did not verify earns credit.** A report that recommends an algorithm
on the strength of one backend, without noticing that the other backend reverses the recommendation,
has failed this question, even if every number in it is correct.

### Q3 — Where does your finding sit on the gap ladder?
> *Course tool: the four gaps from cloud-native to AI-native.*

Your result is a statement about infrastructure. Locate it:

- **Gap 1 — GPU/radio-aware orchestration.** Does your finding say something about how jobs should be
  *placed* (which NPUs are grouped, which collectives stay inside the scale-up domain)?
- **Gap 2 — telco-grade data plane.** Does it depend on properties the data plane must guarantee?
- **Gap 3 — MLOps × NetOps.** Would acting on your finding require the training stack and the network
  to share state they do not share today?
- **Gap 4 — bounded, verifiable autonomy.** Q2's disagreement is a worst-case-versus-average story.
  Say which of your numbers is an average and which is a worst case, and what would have to be true
  for a system to *automatically* choose a parallelism layout using numbers like yours.

Pick the gap your work speaks to most directly, argue for it in a paragraph, and say what evidence
you would need to move it to the next rung.

---

## 4. Required extension — pick exactly one

In this brief the extension is **part of the assignment, not a bonus**. The starter kit makes the
required questions quick to run, so the extension is where your team shows depth. Choose one — not
zero, not two — name it in your selection + plan, and report it as its own section after Q1–Q3.

- **A. Parallelism layout, end to end.** Take a real model shape (layer count, hidden size, batch,
  expert count) and derive the per-step collective sizes and counts for two candidate layouts — two
  ways of dividing the same NPUs among data, tensor, and pipeline or expert parallelism. Take the
  derivation from the published analyses and cite what you used. Simulate both. Recommend one and
  quantify the margin. State two limits of the kit rather than hiding them: it simulates collectives
  only, so pipeline point-to-point transfers have to be estimated analytically; and it simulates one
  collective at a time, so a step time is a sum that you construct — say what your sum assumes about
  overlap between collectives, and between communication and computation.
- **B. Topology comparison under a fixed budget.** Define the bandwidth budget first — total link
  capacity in the fabric, or capacity per NPU port. The two definitions do not give the same
  comparison, so say which you chose and why. Hold it constant and compare Ring, Switch, and
  FullyConnected across collectives and scales. Report where each wins and why the crossover sits
  where it does.
- **C. Algorithm selection policy.** Build a rule that picks the collective algorithm from
  (collective, size, npus, topology, bandwidth). Derive it from a cost model you can write down — a
  rule read off your results table is a lookup, not a policy. Validate it on configurations you did
  not use to build it. Report how often it picks the wrong algorithm and its **regret** — the time
  lost relative to the best algorithm at each point — under both backends, including the cases where
  it picks the option that the congestion-aware backend says is worst.
- **D. Scale-up versus scale-out.** Model a two-tier fabric (fast intra-node, slower inter-node). The
  congestion-aware backend accepts one-dimensional topologies only, so under it you must run the
  tiers separately and compose the result yourself; `run_sweep.py` does not do the composition. The
  congestion-unaware backend accepts a two-dimensional topology directly, through hand-edited
  configuration files (`starter/configs/README.md` shows how). Use that run as the reference against
  which you validate your composition before you rely on it under congestion. Then determine the
  largest tensor-parallel group worth keeping inside one node under your parameters.

---

## 5. Deliverables

| Stage | What you hand in |
|---|---|
| **Selection + plan** | 1 page: the fabric and workload you will study, your chosen extension, and the specific sweeps you intend to run. |
| **Progress note** | 1 page: what you have measured, what surprised you, what changed in the plan. |
| **Workshop** | Presentation/demo. Show at least one plot where the two congestion backends disagree. |
| **Final report** | 6 pages, IEEE format. Q1/Q2/Q3 and the extension must be identifiable sections. Include `results.csv` and the exact `run_sweep.py` commands that produced it. |

Reproducibility is graded: another team must be able to regenerate your CSV from your commands.

**AI tool use:** declare which tools you used and for what. Disclosure is required and is not penalized;
undisclosed use is an integrity matter.

---

## 6. Traps worth knowing in advance

- **Small messages do not care about your bandwidth.** If your whole sweep sits below the Q1 crossover,
  every topology will look the same and you will conclude nothing. Sweep wide.
- **`achieved_GBps` will exceed the link rate** for some collectives. That is not a bug — think about
  how many bytes the *payload* is versus how many bytes actually cross links.
- **Not every algorithm runs at every scale.** `doubleBinaryTree`, `halvingDoubling` and
  `oneHalvingDoubling` need a power-of-two NPU count; `halvingDoubling` has no all-to-all
  implementation at all. `run_sweep.py` names these and carries on, but plan around them: a
  comparison across 4, 6 and 8 NPUs silently loses two of its three points for those algorithms,
  and a gap in a table is not a measurement.
- **`size_mb` is not the same quantity for every collective.** For all_reduce, reduce_scatter, and
  all_to_all it is the full buffer each NPU starts with. For all_gather it is the shard each NPU
  contributes, so the gathered result is `npus` times larger. Check this before you add collective
  times together: under the ring algorithm, reduce_scatter at size S plus all_gather at size S/N
  reproduces all_reduce at size S. `achieved_GBps` is computed from `size_mb`, so it carries the same
  difference.
- **The two backends are not "approximate" and "exact".** Both are models. The congestion-aware one
  models contention; neither models your real cluster. Q2 is about knowing the difference.
- **Ring is suspiciously stable across backends.** Ask yourself why before you use it as your baseline.
- **Which Linux, if you are on WSL2:** install the verified distribution —
  `wsl --install -d Ubuntu-24.04`. Newer releases work too (the kit carries the fixes that
  GCC 15 and glibc 2.41 made necessary), but 24.04 is the combination this kit is verified on
  and it is the shortest path to a green smoke test.
- **WSL2 users:** keep the repository under your Linux home (`~/`), never under `/mnt/c/`, and raise
  the memory limit in `.wslconfig`. `setup.sh` warns you about both.

---

## 7. Open source used

Everything below is third-party work. Cite it in your report the way you would cite a paper, and
respect the license if you publish your code.

| Project | What it does here | Repository | License |
|---|---|---|---|
| **ASTRA-sim** | the simulator itself — workload, system and network models | [github.com/astra-sim/astra-sim](https://github.com/astra-sim/astra-sim) | MIT (its submodules differ — see below) |
| **Chakra** | execution-trace schema ASTRA-sim consumes (bundled as a submodule) | [github.com/mlcommons/chakra](https://github.com/mlcommons/chakra) | Apache-2.0 |
| **Protocol Buffers** | `protoc` compiles the trace schema; the Python runtime reads it | [github.com/protocolbuffers/protobuf](https://github.com/protocolbuffers/protobuf) | BSD-3-Clause |

The kit clones ASTRA-sim at setup time; nothing from it is redistributed here. Its submodules come
with it, and they are not all MIT — the ns-3 network backend is **GPL-2.0**. This kit builds and uses
the analytical backend only, so that never enters your results, but if you publish code that ships or
links one of the backends, respect that submodule's license rather than ASTRA-sim's.

**ASTRA-sim is pinned.** `setup.sh` installs commit `518bd51` (2026-03-26), the revision this kit is verified on, so that every team measures on the same simulator; `smoke_test.sh` prints it. If you deliberately use a different one — `ASTRA_SIM_REF=<commit> bash setup.sh` — say so in your report, because the numbers move with it. Everything else (compiler, protobuf, Python) comes from your own machine and is not pinned. `setup.sh` prints those versions as it runs, so record them with your results.

---

## Revision history

| Rev | Date | What changed |
|---|---|---|
| **r10** | 2026-09-23 | Kit only, nothing in this brief changed: `setup.sh` now tests for `ensurepip` rather than the `venv` module when it decides whether Python needs installing, and rebuilds a virtual environment that has no `pip` instead of reusing it. On Debian and Ubuntu the `venv` module ships in the base Python package while `python3-venv` adds `ensurepip`, so the old test passed on machines where `python3 -m venv` could not actually work — and the environment it left behind made the second attempt fail too. |
| **r9** | 2026-09-20 | **§7 only, license accuracy.** ASTRA-sim itself is MIT, but `setup.sh` fetches its submodules with `--recursive` and the ns-3 network backend among them is GPL-2.0. The table said MIT without qualification. Nothing is redistributed either way, and this kit builds only the analytical backend — but the brief tells you to respect the license if you publish your code, so it should say which license. No requirement, command or number changed. |
| **r8** | 2026-09-20 | Kit only, nothing in this brief changed: on Linux `setup.sh` now checks what is missing before it runs `apt-get`, so a machine that already has the toolchain installs nothing and is never asked for a password — which is what the "no root" line at the top of this brief has always promised. If something is missing and the account cannot install it, the kit names the packages to ask for instead of failing inside `apt`. |
| **r7** | 2026-09-20 | §6: a new trap — `doubleBinaryTree` and `halvingDoubling` need a power-of-two NPU count, and `halvingDoubling` has no all-to-all. §2: a sweep is seconds only at modest NPU counts — the cost per point grows steeply with `--npus`. Kit: `run_sweep.py` now checks its arguments before simulating anything and names what is wrong, prints the whole error for a failed combination instead of the first 60 characters, exits non-zero if any combination failed, and widens the console columns; `smoke_test.sh` counts its first step as one check, so the total matches the five steps it prints. No question or extension changed. |
| **r6** | 2026-09-20 | §7: ASTRA-sim is pinned to commit `518bd51`. `setup.sh` fetches that revision instead of whatever HEAD happens to be, overridable with `ASTRA_SIM_REF`, and says so when an existing install sits on a different one; `smoke_test.sh` prints the commit. No question or extension changed. |
| **r5** | 2026-09-20 | **§5:** the Final report row now names the extension as its own section, as briefs 1–3 all require one (this matches split-inference r4). Nothing else changed. |
| **r4** | 2026-09-19 | Spelling only: American spelling throughout the brief and the kit (behavior, modeled, license, serialization, …), to match the lecture decks and notes. No requirement, command or number changed. |
| **r3** | 2026-09-19 | Pre-release review, checked by running the kit. **Q1:** three or more bandwidths (was two); the crossover axis must be stated; sizes are whole megabytes; one paragraph links the question to the four wireless problems. **Q2:** item 2 asks for the boundary of the disagreement rather than one configuration, and names topology as well as algorithm; item 3 relates the boundary to Q1. **§2:** `FullyConnected` documented; "Read before you plan" added. **§4:** A states what the kit cannot simulate; B asks for a budget definition and adds FullyConnected; C asks for a derived rule, validation on unused configurations, and regret; D states that the congestion-aware backend is one-dimensional and how to validate a composition. **§6:** `size_mb` is a different quantity for all_gather. Starter: README example, `configs/` notes, and `run_sweep.py --help` text updated to match; no code logic changed. |
| **r2** | 2026-09-19 | §4: the extension changes from optional ("pick at most one") to **required ("pick exactly one")**. The kit makes Q1–Q3 fast to run, so depth is shown in the extension. No other section changed. |
| **r1** | 2026-08 | First release (kit confirmed by 2026-08-22). |
