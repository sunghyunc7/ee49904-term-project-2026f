# Term Project Brief — RIC Closed-Loop Control: Delegating a Decision to an xApp

**Stack:** [FlexRIC](https://gitlab.eurecom.fr/mosaic5g/flexric) (near-RT RIC + emulated E2 node) + Python xApp SDK, CPU only
**Team:** 2–3 students · **Hardware:** Ubuntu 24.04 with root on it — WSL2, a VM, or a server (no GPU, no radio)
**Track link:** AI for Network (observe → **bound** → rehearse → learn → operate)

> Weights, deadlines, and submission mechanics are in the syllabus and on the LMS.
> This brief defines *what you must produce*, not when.

---

## 1. The problem

O-RAN's near-real-time RIC exists so that control decisions can be taken *outside* the base station,
by software someone else wrote, over a standard interface. That is a remarkable thing to allow.
This project makes you build one of those loops and then asks what you would need to know before
letting it run unattended.

You get a real near-RT RIC (FlexRIC), a real E2 node, and a real E2AP interface: subscriptions,
indications, control requests, acknowledgments, ASN.1 on the wire. You write a **Python xApp**
that closes this loop:

```
   MAC indication  ──▶  your decide()  ──▶  SLICE control  ──▶  the RAN changes  ──▶  next indication
   (per-UE backlog,      (choose PRB          (over E2AP)
    throughput, CQI)      shares)
```

Two slices share one cell. Their traffic demand swings **in anti-phase**: when slice 0 wants 75% of
the cell, slice 1 wants 15%, and thirty seconds later it is the other way round. Total demand stays
near 90% of capacity, so the cell can always serve everyone — **but only if the PRB split follows
the demand.** A fixed 50/50 split starves whichever slice is currently heavy while the other wastes
resources it cannot use. Your xApp's job is to keep up.

### A disclosure you need before you start

FlexRIC's emulated E2 node, as shipped upstream, has **no state**. Every service model returns
randomly generated indications, and control messages are parsed, logged, and discarded. The E2AP
path is real; the RAN behind it is not. A control loop built on the stock emulator cannot close —
the KPIs never respond to the control.

This kit therefore **patches the emulator** (`patch/ee49904_plant.c` and the two service-model
files) to add a deliberately simple downlink scheduler: SLICE control sets PRB shares, MAC
indications report what that allocation produced. That patch is why your loop closes.

It is also the single most important thing to be skeptical about, and Question 2 is about exactly
that. Read `patch/ee49904_plant.h` before you write your report — it tells you what the plant does
and does not model, and you are expected to go further than that list.

---

## 2. Getting started

```bash
cd starter/
bash setup.sh                 # deps → clone → patch → build → sudo make install  (3–8 min)
source ~/ee49904-ric/env.sh   # every new shell
bash smoke_test.sh            # 8 checks — all must pass
```

`setup.sh` ends with `sudo make install`. That is not optional and not laziness: FlexRIC loads its
service-model shared libraries and its config file at runtime from `/usr/local/lib/flexric/` and
`/usr/local/etc/flexric/`. The C binaries can be pointed elsewhere with `-p`, but **the Python xApp
SDK cannot** — its `init()` is compiled with no argument handling at all. Without the install every
process dies immediately with `Error finding path /usr/local/lib/flexric/`.

Then run your first two experiments:

```bash
bash run_experiment.sh --open-loop --duration 60 --out open.csv                       # watch the problem
bash run_experiment.sh --gain 2.0 --period 100 --duration 60 --out closed.csv         # close the loop
```

`run_experiment.sh` hands every argument to `xapp_slice_ctrl.py` unchanged. What it adds is the
session around the xApp: it kills leftovers from an earlier run, starts a fresh `nearRT-RIC` and a
fresh `emu_agent_gnb`, runs the xApp once, and stops both. Their logs go to `logs/<timestamp>/` —
`agent.log` is where you see what the RAN did with your control. A sweep is then an ordinary shell
loop, and you will need several (Q1, Q2.3):

```bash
for g in 1 2 5 10 20 50; do bash run_experiment.sh --gain $g --period 100 --out g$g.csv; done
```

**One xApp per RIC session — this is not a convenience.** Upstream FlexRIC has a bug in which a
pending event left behind by one xApp is touched by the next xApp's first control acknowledgment
(`assoc_rb_tree_extract: Assertion ... failed`, then `Aborted`). Running the open-loop xApp and then
the closed-loop xApp against the same RIC hits it in roughly six runs out of ten. A fresh session
per run avoids it. You can still start the three processes by hand, each in its own terminal after
`source env.sh`, when you want to watch the logs scroll —

```bash
$RIC_BUILD/examples/ric/nearRT-RIC                       # 1. the near-RT RIC
$RIC_BUILD/examples/emulator/agent/emu_agent_gnb         # 2. the emulated E2 node
python3 xapp_slice_ctrl.py --gain 2.0 --period 100       # 3. your xApp
```

— but restart terminals 1 and 2 before every new xApp run.

**You edit `decide()` in `xapp_slice_ctrl.py`.** Everything else — subscription, control message
construction, CSV logging — is done. The shipped `decide()` is the crudest rule that closes the
loop at all; it works, badly, and improving it is part of the assignment.

Read what it actually does before you name it. What is proportional to the backlog error is not the
share but the **rate of change** of the share — so this is *integral* action on the error, and when
the error reaches zero the share stops where it is rather than returning to 50/50. You will see
`share0` sit at some value like 69% at the end of a run. That is the definition of an integrator,
not a bug. Keep it in mind when you raise the gain (Q2.3).

### Three things about the plant you must know before you compare any two runs

**It has state, and the state survives your xApp.** The plant is one object created when
`emu_agent_gnb` starts. If you run an experiment and then run another, the second one inherits the
first one's queues and its position in the demand cycle. The two runs are then not comparable, and
the difference you measure is an artifact.

The xApp therefore resets the plant at startup (`--no-reset` turns it off). The reset travels as
a **SLICE DEL** control message, which this kit reinterprets as "return the plant to tick 0, empty
queues, 50/50". That is a kit invention, not O-RAN semantics — real DEL removes a slice from the
configuration. Restarting `emu_agent_gnb` does the same thing and is what you would have to do on
real hardware. Note that `--open-loop` therefore still sends exactly one control message; it never
sends a share change.

**Its clock is the indication, not the wall.** The plant advances one tick every time a MAC
indication is generated, and a tick is worth a fixed amount of plant time, set once when
`emu_agent_gnb` starts (`EE49904_PLANT_DT_MS`, default 10). `--ind-period` sets how often the xApp
asks for indications: 1, 2, 5 or 10 ms — the only values the Python SDK exposes. The two must
agree, or plant time and wall time drift apart: subscribe at 1 ms against a 10 ms tick and the 30 s
demand cycle passes in 3 s. `run_experiment.sh` starts the agent with the tick that matches your
`--ind-period`; if you start the agent by hand, that is your job. Even when they agree, plant time
follows wall time only as well as indications arrive on time — the xApp prints the indication gap it
measured at the end of every run. Look at it (Q2.2).

**Watch your units.** The MAC indication reports `bsr` in **bytes**. The plant and the offline
reference (`patch/test_plant.c`) both express the backlog error in **megabits**. The shipped
`decide()` converts (`* 8 / 1e6`) and `--gain` is calibrated in *share points per second per Mbit
of imbalance*. Divide bytes by `1e6` and call it the error and your gain is silently 8× weaker —
the loop still runs, the log still scrolls, and the controller does almost nothing. Check the agent's
`SLICE ADD: ... -> applied` lines: if `share0` never leaves the neighborhood of 50%, your control
is arriving and being ignored by your own arithmetic.

---

## 3. Required questions

Answer all three. Each is tied to a tool this course has been building.

### Q1 — What is contended, on what timescale, and with what information?
> *Course tool: the four persistent control problems.*

This is the spectrum-sharing problem wearing a slicing costume. Answer the three parts with
measurements:

- **What is contended** — PRBs. Show the frontier: sweep your controller and report the
  achievable (peak backlog, throughput) pairs. Is there a setting that improves both, or does every
  gain in one cost the other?
- **On what timescale** — three clocks matter here: the **indication period** (how often you
  learn), your **control period** (how often you act), and the **demand period** (how fast the
  problem moves). Vary the first two independently — `--ind-period` (1, 2, 5, 10 ms) and
  `--period` — and report how performance depends on each. To observe more slowly than 10 ms,
  discard callbacks in `Observer.handle()`; the subscription itself cannot go slower from Python.
  State which clock actually limits you and why.
- **With what information** — the MAC indication gives you `bsr` (backlog), `dl_curr_tbs`
  (throughput), `wb_cqi`, `dl_aggr_prb`. It does **not** tell you which slice a UE belongs to; the
  starter code hardcodes that mapping. Say what else your controller would need that E2 does not
  carry, and what it is inferring rather than observing.

### Q2 — What did this experiment verify, and what did it not?
> *Course tool: the acceptance test.*

You are running a real protocol against a fake RAN. Both halves of that sentence need auditing.

1. **Audit the plant.** Read `patch/ee49904_plant.c`. List what it does not model — the header
   names several, but it is not a complete list; find at least two more it does not mention. For
   each, say whether your conclusions would survive if it were modeled.
2. **Model versus measurement.** `patch/test_plant.c` runs the same plant *offline*, with no E2AP,
   no SCTP, no process boundary — and it steps the plant on a perfect 10 ms clock while the real
   loop steps it whenever an indication happens to be generated. Run both at the same gain and
   control period and report both: `./test_plant <gain> [delay_ms] [period_ms] [trace.csv]` runs
   the offline plant at your operating point and writes a trace with the same columns as the xApp's
   CSV (build line in the file's header; with no arguments it runs its four fixed scenarios). Do not assume in advance which way the difference goes, or that
   there is one: state what you measured, then account for it. Candidate mechanisms — loop delay
   (indication → xApp → control → next indication), indication jitter, the fact that the plant's
   clock is driven by the subscription rather than by wall time — are for you to test, not to
   assert. The xApp measures the indication gap for you; the loop delay it does not — time the
   `control_slice_sm()` round trip, or read it off the lag between a share step and the backlog's
   response. Whichever metric you compare, say why that metric and not another; peak, mean, and
   settling time do not have to agree.
3. **Find the stability boundary.** Increase `--gain` at a fixed control period until the loop
   becomes unstable, then repeat at a different control period. Report the boundary as a curve, not
   a number. Then explain the mechanism. The instability you measured is a property of what,
   exactly — the plant, the controller, or something that is neither? Design an experiment that
   separates the candidates and run it. The offline plant lets you change one thing at a time,
   which the live loop does not.
4. **Check that your control was applied.** The agent clamps any share below 5% and renormalizes
   pairs summing over 100%. When it does, your request was **not** honored. The SLICE indication
   reports the shares actually in force. Demonstrate a case where what you asked for and what you
   got differ, and say how an xApp that never reads back its own configuration would behave.

**Honest declaration of what you did not verify earns credit.** "Our controller is tuned for this
plant's demand period and we did not test it against a different one" is a good sentence to have.

### Q3 — Where does your finding sit on the gap ladder?
> *Course tool: the four gaps from cloud-native to AI-native.*

- **Gap 1 — GPU/radio-aware orchestration.** Your loop has a deadline. Where can the xApp be
  placed, and what does your timescale answer from Q1 say about it?
- **Gap 2 — telco-grade data plane.** State your indication-period requirement as a demand on the
  path between the RAN and the RIC.
- **Gap 3 — MLOps × NetOps.** Your controller has a gain. Who tunes it, against what, and how would
  anyone know it had drifted out of tune in production?
- **Gap 4 — bounded, verifiable autonomy.** The 5% floor and the renormalization are a guardrail
  written by someone who did not trust the xApp. Design the guardrail *you* would require before
  letting your loop run unattended, state what it costs, and say what monitor you would run beside
  it. This is the most direct target for this brief.

Gap 4 is where this brief lives, and the marks are for the guardrail you design, the cost you
measure, and the monitor you specify — not for naming the gap. If you argue that your result
narrows another gap more, say what evidence would move *that* one to the next rung.

---

## 4. Optional extension — pick at most one

- **A. A controller worth the name.** Replace the shipped rule with something defensible —
  PI with anti-windup, a rate limiter on share changes, or a model-predictive step that uses the
  demand periodicity. Compare against the shipped controller on the same trace and report both the
  performance and the added failure modes.
- **B. Degraded observability.** Drop or delay indications deliberately (subscribe less often, or
  ignore a fraction of callbacks) and find where your controller stops being safe. Produce the
  operating envelope: the worst observability under which you would still allow the loop to run.
- **C. Two xApps, one RAN.** Run two instances that both issue SLICE control with different
  objectives. Document what happens, and propose the arbitration the RIC would need. (Conflict
  mitigation is an open O-RAN problem — treat it as one.)
- **D. Make the plant harder.** Modify `ee49904_plant.c` — add a third slice, an SLA floor per
  slice, or bursty rather than sinusoidal demand — and show which of your Q1/Q2 conclusions were
  properties of the controller and which were properties of the old plant.

---

## 5. Deliverables

| Stage | What you hand in |
|---|---|
| **Selection + plan** | 1 page: the machine you will run on (and its SCTP status), the controller you intend to build, your chosen extension. |
| **Progress note** | 1 page: open-loop versus closed-loop numbers so far, the stability boundary you have found, what changed in the plan. |
| **Workshop** | Presentation/demo. Show the loop running live if you can, and one plot of the stability boundary. |
| **Final report** | 6 pages, IEEE format. Q1/Q2/Q3, and your extension if you take one, must be identifiable sections. Include your CSVs, your `decide()` implementation, and the exact commands. |

Reproducibility is graded: another team must be able to apply the patch, build, and regenerate your
numbers.

**AI tool use:** declare which tools you used and for what. Disclosure is required and is not
penalized; undisclosed use is an integrity matter.

---

## 6. Traps worth knowing in advance

- **`Error finding path /usr/local/lib/flexric/`** means `sudo make install` did not run or did not
  finish. Re-run `setup.sh`. This is the first thing to check when nothing works, and it will hit
  you again if you ever rebuild from a fresh clone.
- **The patch must be applied *and rebuilt*.** If you rebuild from a clean clone without
  `apply_patch.sh`, indications go back to random numbers and your controller will appear to do
  nothing. Smoke test check 2 catches this; believe it.
- **Which machine.** Ubuntu 24.04 is what this kit is verified on, x86_64 and arm64 both. On
  Windows: `wsl --install -d Ubuntu-24.04`. On macOS: a Linux VM (UTM, multipass) running the same
  release — macOS cannot run this natively, because E2AP needs kernel SCTP. Either way you may need
  `sudo modprobe sctp` once. Newer releases work too; the kit carries the fixes that GCC 15 and
  glibc 2.41 made necessary. **Ubuntu 22.04 will not build this kit as it comes:** its GCC crashes
  on FlexRIC's headers and its SWIG is too old for the Python SDK, and while `gcc-12` plus SWIG 4.2
  gets through the build, SWIG 4.2 is not in the 22.04 repositories and the result has never been
  run here. Do not start there.
- **You need root on that machine, and the machine to yourself while a run is going.** `setup.sh`
  installs packages and ends in `sudo make install`; `modprobe sctp` needs root as well. A server
  where you have only an ordinary account will not work, and neither will one where someone else is
  running this kit at that moment — see the next trap for why. **If no machine you have fits, say so
  in the sign-up form and you will be assigned one.**
- **`assoc_rb_tree_extract: Assertion ... failed` / `Aborted` at the start of a closed-loop run**
  is the upstream bug described in §2, not your controller. One xApp per RIC session:
  use `run_experiment.sh`, or restart the RIC and the E2 node yourself before each run.
- **The plant remembers.** Two runs back to back are not two independent experiments unless one of
  them reset. See §2. If your "improvement" is 5% and you did not reset, you measured nothing.
- **Bytes are not bits.** `bsr` is bytes; `--gain` is per Mbit. See §2.
- **Indication period ≠ control period.** The starter subscribes at 10 ms (`--ind-period`) and
  decides every 100 ms (`--period`) by default. Changing one does not change the other, and
  confusing them will make your Q1 timescale analysis wrong.
- **`dl_curr_tbs` is bytes per indication period, not per second.** At `--ind-period 10` a slice
  serving 40 Mb/s reports about 50 kB; at `--ind-period 2` the same slice reports about 10 kB. The
  `tput` columns in the CSV are those raw values. Convert to a rate before you compare runs with
  different indication periods, or you will report a fivefold throughput loss that did not happen.
  The xApp's closing summary prints the mean cell throughput in Mb/s.
- **Indication period = plant tick, or time bends.** See §2. If a run with a different
  `--ind-period` looks ten times better or worse, check that the agent was started with the
  matching tick before you believe it.
- **Slice membership is hardcoded.** UEs `0x1000`,`0x1001` → slice 0; `0x1002`,`0x1003` → slice 1.
  The MAC indication does not carry it — a real limit of that indication, not a kit bug.
- **Only one RIC per host.** The near-RT RIC binds fixed SCTP ports (36421, 36422) and loads its
  service models from a fixed path, neither of which the Python SDK can be told to change. So a host
  runs one RIC, for one team, at a time — on a machine you share, a second RIC dies at `bind()` and,
  worse, a second xApp attaches to the *first* team's RIC and controls their plant. Within your own
  work the same limit applies: If a previous run's
  `nearRT-RIC` or `emu_agent_gnb` is still alive, the next one dies at `bind()` with
  `endpoint_ric.c: Assertion 'rc != -1' failed` — which looks like a broken build but is just a busy
  port. `pkill -f nearRT-RIC; pkill -f emu_agent_gnb` before you start. The smoke test and
  `run_experiment.sh` do this for you; your own three-terminal runs do not.
- **`swig/python detected a memory leak of type 'ngran_node_t *'`** is printed by the SDK on every
  startup. It is upstream's, it is harmless, and it is not your leak. Ignore it.
- **`dl_bler` is always zero.** This plant does not model errors. Do not build a controller on it
  and do not report it as a result.
- **The offline plant test is not the loop.** `test_plant.c` has no E2AP, no process boundary, no
  scheduling jitter. It is a useful reference precisely because it lacks those things — see Q2.2.

---

## 7. Open source used

Everything below is third-party work. Cite it in your report the way you would cite a paper, and
respect the license if you publish your code.

| Project | What it does here | Repository | License |
|---|---|---|---|
| **FlexRIC** | the near-RT RIC, the emulated E2 node, and the Python xApp SDK | [eurecom GitLab](https://gitlab.eurecom.fr/mosaic5g/flexric) (canonical) · [GitHub mirror](https://github.com/openaicellular/flexric) | **OAI Public License V1.1** |
| **SWIG** | generates the Python bindings for the xApp SDK during the build | [github.com/swig/swig](https://github.com/swig/swig) | GPL-3.0 (the tool) |

**Read this before you publish anything.** The OAI Public License is not one of the usual OSI licenses — read it rather than assuming it behaves like MIT or Apache. A copy ships with the kit as `patch/LICENSE-OAI-PL-v1.1.txt`. Note in particular that its patent grant covers study, testing and research; any other purpose needs separately negotiated FRAND terms. The kit's `patch/sm_mac.c` and `patch/sm_slice.c` are **replacements for FlexRIC's own files**, so they are derivative works under that same license, and each carries a header saying what was changed; `patch/ee49904_plant.c` is course material written from scratch. Whatever you build on top inherits the same question, and your report should say which of your code is which.

SWIG is only a build-time tool here: the kit ships no generated bindings — your build produces them on your machine. Its license exempts what it generates from the GPL, so those bindings do not put your own code under it.

**Versions are not pinned.** The kit installs whatever is current when you run `setup.sh`. For FlexRIC, `setup.sh` prints the commit it cloned and tells you if it is not the one the kit is verified on (`1f04cc55`, the mirror's head since December 2023). Record the versions you actually used — `smoke_test.sh` prints most of them — and report them with your results. Two teams on different versions can get different numbers, and that is worth knowing rather than arguing about.

---

## Revision history

| Rev | Date | What changed |
|---|---|---|
| r10 | 2026-09-22 | **Kit only:** `setup.sh` no longer calls apt when every build dependency is already installed. When it does need apt and another package manager holds the lock (often the automatic updates on a freshly booted Ubuntu), it now waits up to 10 minutes instead of stopping with `Could not get lock` (exit code 100). Nothing in this brief changed. |
| r9 | 2026-09-21 | **§6:** the slice-membership trap no longer says E2 as a whole lacks the mapping — it says the MAC indication does, which is what is true; the header comment in `patch/sm_mac.c` now says the same. **Kit:** `env.sh` now also puts the xApp SDK on `PYTHONPATH`, so an xApp you write from scratch can `import xapp_sdk` (the starter xApp already found it on its own; re-run `setup.sh` to regenerate `env.sh`). No requirement, command or number changed. |
| r8 | 2026-09-21 | **Q2.2:** one sentence after the candidate mechanisms — the xApp measures the indication gap but not the loop delay, and two ways to measure the latter. **Q3:** the closing sentence now says the marks are for the guardrail, its measured cost and the monitor, not for choosing a gap; arguing for another gap is still allowed. **Q2.4** unchanged — the Python SDK does expose the SLICE indication (`report_slice_sm`). No requirement, command or number changed. |
| r7 | 2026-09-20 | **§7 only, license accuracy.** A copy of the OAI Public License V1.1 now ships with the kit (`patch/LICENSE-OAI-PL-v1.1.txt`) — the license requires that recipients of a derivative work get one, and the kit was distributing two derivative files without it. §7 points at that copy, names the patent grant's split between research and other use, and says the two patched files carry modification notices. Added the SWIG output exception, so nobody fears the generated bindings are GPL. No requirement, command or number changed. |
| r6 | 2026-09-20 | **§5:** the Final report row now says where an extension goes, for the teams that take the optional one. Nothing else changed. |
| r5 | 2026-09-20 | **§6:** the machine guidance is now concrete — Ubuntu 24.04 (WSL2, or a Linux VM on macOS), root required, 22.04 ruled out, and "no machine of your own → say so in the sign-up form". The earlier "the shared host your TA points you at" is gone. "Only one RIC at a time" is now "per host" and says what happens when two teams share one. Header hardware line matches. |
| r4 | 2026-09-19 | Spelling only: American spelling throughout the brief and the kit (behavior, modeled, license, serialization, …), to match the lecture decks and notes. No requirement, command or number changed. |
| r3 | 2026-09-19 | **§6:** new trap — `dl_curr_tbs` and the CSV `tput` columns are bytes per indication period, so their scale changes with `--ind-period`. **Kit:** the xApp's summary now prints mean cell throughput in Mb/s; `run_experiment.sh` hides the SDK's per-control chatter on the terminal (`QUIET=0` shows it; `xapp.log` always has everything). |
| r2 | 2026-09-19 | **§2:** experiments now run through the new `starter/run_experiment.sh` (one fresh RIC + E2 node per xApp run); the upstream abort that hits a second xApp in the same RIC session is documented, and added to §6. **§2:** third "thing about the plant" — the plant tick must equal the indication period (`EE49904_PLANT_DT_MS`, set by `run_experiment.sh`). **Q1:** the indication period is now an xApp argument, `--ind-period {1,2,5,10}`; slower observation is done by discarding callbacks. **Q2.2:** `test_plant` takes `<gain> [delay_ms] [period_ms] [trace.csv]`. **Q2.3:** the sentence "without loop delay, higher gain is simply better" is removed — it holds only when the control period is one tick — and the question now asks for an experiment that separates the candidate causes; the matching sentence in §2 is shortened. **§7:** `setup.sh` now reports the FlexRIC commit against the verified one. **Kit:** `setup.sh` no longer dies intermittently with exit code 141 at the compiler check. |
| r1 | 2026-08-26 | First version. |
