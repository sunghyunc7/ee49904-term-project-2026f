# Term Project Brief — Twin-Trained Control: Rehearsing a Policy Before It Touches the Network

**Stack:** [Sionna](https://github.com/NVlabs/sionna) 2.1 (RT ray tracing + PHY link-level), CPU by default
**Team:** 2–3 students · **Hardware:** any laptop (no GPU required, no root, no VM)
**Track link:** AI for Network (observe → bound → **rehearse** → learn → operate)

> Weights, deadlines, and submission mechanics are in the syllabus and on the LMS.
> This brief defines *what you must produce*, not when.

---

## 1. The problem

A digital twin is where learned control gets rehearsed before it is allowed near a live network.
The promise is attractive: build a faithful replica, train a policy in it, validate the policy
against a target, deploy with confidence.

This project tests the last step of that sentence.

You will build a two-layer twin of a radio cell:

- **Geometry layer** — Sionna RT ray-traces a real 3D scene and produces a radio map: SNR as a
  function of position. Walk a user through it and you have SNR over time.
- **Link layer** — Sionna PHY simulates each modulation-and-coding scheme (MCS) over a range of
  SNR and produces BLER curves. This is a *link-to-system mapping*, the same abstraction a real
  scheduler uses.

Then you will derive a **link adaptation** policy from that twin: given the SNR you observe, pick
the MCS that maximizes spectral efficiency subject to a block error rate target. This is the
canonical channel control problem — the first of the four persistent control problems, in its
learned form.

On the title: here *trained* means derived from, tuned on and validated against the twin. The
starter policy is a rule read off the link layer, and what you tune on a scene is its margin.
Extension B is where the policy itself is learned.

On the twin, your policy will hit its BLER target. It has to: it was derived from that exact table.
Then you will deploy it, and it will not.

---

## 2. Getting started

```bash
cd starter/
bash setup.sh                 # CPU-only PyTorch, then Sionna   (3–10 min)
export TWIN_VENV="${TWIN_VENV:-$HOME/twin-venv}"    # setup.sh cannot set this for you
bash smoke_test.sh            # 5 checks — all must pass

$TWIN_VENV/bin/python build_twin.py --scene simple_street_canyon --tx-dbm 20 --out twin.npz
$TWIN_VENV/bin/python run_policy.py --twin twin.npz \
      --delay 0,5,20,50 --est-std 0,2 --margin 0 --speed 20
```

`build_twin.py` runs once and caches both layers into a `.npz`. The geometry layer takes under a
second; the link layer takes a few minutes because it is a real Monte-Carlo BLER simulation.
`run_policy.py` then reuses the cache, so sweeps are fast.

Four knobs control the experiment. Three model the ways deployment differs from the twin;
`--margin` is what the policy can do about it:

| Knob | Meaning |
|---|---|
| `--delay N` | The policy acts on an SNR report that is N steps old (feedback delay). |
| `--est-std X` | The reported SNR carries X dB of estimation error. |
| `--margin M` | The policy backs off M dB before choosing — a conservative safety margin. |
| `--deploy-twin F` | The field is a *different twin*, F: the policy — and the margin you tuned — runs on F's geometry and F's link layer. Build F with `--reuse-link` and only the geometry differs. |

The policy always transmits at the **true** SNR. It just does not always know what that is.

---

## 3. Required questions

Answer all three. Each is tied to a tool this course has been building.

### Q1 — Which resource is contended, on what timescale, and with what information?
> *Course tool: the four persistent control problems.*

Link adaptation is the channel problem in its purest form, and the course's lens asks three things.
Answer them with measurements, not prose:

- **What is contended** — spectral efficiency against reliability. Show the frontier: sweep the
  BLER target and report the throughput you get at each.
- **On what timescale** — the channel decorrelates as the user moves. Sweep `--speed` and
  `--delay` together and find, for your scene, the **staleness budget**: how old a channel report
  can be before the policy's BLER target is lost. Report it in milliseconds *and* in meters.
- **With what information** — sweep `--est-std`. Compare the damage done by 2 dB of estimation
  error against the damage done by an equivalent amount of delay. Which is worse in your scene,
  and why?

The deliverable is a boundary in the (delay, estimation error) plane separating "target met" from
"target violated". That boundary is the honest specification of what your twin-trained policy needs
from the measurement system in order to be safe.

### Q2 — What did the twin verify, and what did it not?
> *Course tool: the acceptance test.*

Your policy passes on the twin by construction. The acceptance test asks what that passing actually
certified.

1. Report the twin-condition result (delay 0, error 0) and at least four deployment conditions.
   Give BLER as a multiple of the target, not just as a number.
2. **Name the assumption each knob breaks.** `--delay` and `--est-std` break a specific belief the
   twin holds about the relationship between what the controller sees and what the channel does.
   State that belief precisely.
3. Run `--deploy-twin` with a different scene, built with `--reuse-link`. The link layer is
   unchanged — only the geometry differs. Report what happens and explain why a policy derived from a BLER table can fail when
   only the *geometry* changed.
4. **Find the margin that restores the guarantee**, and report its price. Sweep `--margin` under a
   fixed harsh condition until BLER returns below target. Report the throughput and the silent
   fraction you paid for it. Then check whether that same margin still works in the other scene.
5. State plainly which parts of your Q1 boundary are properties of the physics and which are
   artifacts of this twin's construction.

**Honest declaration of what you did not verify earns credit.** The twin models AWGN at the link
layer and static geometry at the ray-tracing layer. Neither is your network. Saying so, precisely,
is part of the answer.

### Q3 — Where does your finding sit on the gap ladder?
> *Course tool: the four gaps from cloud-native to AI-native.*

- **Gap 1 — GPU/radio-aware orchestration.** Does your staleness budget constrain where the control
  loop can be *placed*?
- **Gap 2 — telco-grade data plane.** Your delay budget is a latency requirement on a measurement
  path. State it as one.
- **Gap 3 — MLOps × NetOps.** The twin and the live network must agree about the channel. What
  would keep them in agreement, and how would you detect that they have drifted apart?
- **Gap 4 — bounded, verifiable autonomy.** Your margin sweep is a bounded-delegation experiment:
  it buys a guarantee with throughput. Argue what evidence would justify letting this policy run
  unattended, and what monitor you would require alongside it.

Pick the **one** gap your result narrows — not the one it merely imposes a requirement on — argue
for it from your Q1 boundary and your Q2 verdicts, and state what would still be missing to close it
end-to-end.

---

## 4. Required extension — pick exactly one

In this brief the extension is **part of the assignment, not a bonus**. The starter kit makes the
required questions quick to run, so the extension is where your team shows depth. Choose one — not
zero, not two — name it in your selection + plan, and report it as its own section after Q1–Q3.

- **A. Outer-loop link adaptation.** Real systems do not use a fixed margin; they adapt it from
  observed ACK/NACK. Implement an outer loop that adjusts the margin online toward the target BLER.
  Compare against the best fixed margin and report how fast it converges after a scene change.
  *Where to start:* the ACK/NACK is the block-error flag inside the loop of `evaluate()` in
  `twinlib.py`; copy that loop and let the margin change from step to step. For the scene change,
  join trajectories from two twins built with `--reuse-link`.
- **B. Learned policy.** Replace the table-derived rule with a policy learned from twin rollouts
  (contextual bandit or small RL). Show whether it beats the rule on the twin, and whether that
  advantage survives deployment. A learned policy that overfits the twin harder is a legitimate and
  interesting result.
- **C. Twin fidelity budget.** What a twin produces is not only the rule but everything you tune
  on it — the margin, the staleness budget. Build a reference twin at the highest fidelity you can
  afford and treat it as the field (`--deploy-twin`). Then degrade the twin deliberately — fewer
  ray bounces (`--max-depth`), coarser cells (`--cell-size`), fewer ray samples (`--samples`), a
  cheaper link layer (`--target-block-errors`, `--snr-step`, `--k`) — tune on the cheap twin,
  deploy on the reference, and find how cheap a twin can be before what it produces stops being
  good enough. This is the "how much twin do I need" question.
- **D. Multi-user / interference.** Add a second transmitter and derive SINR instead of SNR.
  Re-run the analysis and report what changes when the contended resource becomes shared.
  *Where to start:* `radio_map_snr()` in `twinlib.py` places a single transmitter; add a second one
  and the solver returns one received-power map per transmitter, from which SINR follows. Give the
  interferer an on/off load, so that SINR changes even when the user stands still.

---

## 5. Deliverables

| Stage | What you hand in |
|---|---|
| **Selection + plan** | 1 page: the scene and link budget you will use and why, the deployment conditions you will sweep, your chosen extension. |
| **Progress note** | 1 page: your twin-vs-deployment gap so far, the staleness budget you have measured, what changed in the plan. |
| **Workshop** | Presentation/demo. Show the (delay, error) boundary and the margin/throughput trade-off. |
| **Final report** | 6 pages, IEEE format. Q1/Q2/Q3 and the extension must be identifiable sections. Include your CSVs and the exact commands that produced them. |

Reproducibility is graded: another team must be able to rebuild your twin and regenerate your
numbers from your commands.

**AI tool use:** declare which tools you used and for what. Disclosure is required and is not
penalized; undisclosed use is an integrity matter.

---

## 6. Traps worth knowing in advance

- **Pick a link budget where the MCS ladder actually matters.** If your scene's SNR sits above the
  highest MCS threshold everywhere, the policy saturates and nothing you do to delay or estimation
  error will change the answer. `build_twin.py` prints the SNR percentiles — compare them against
  the BLER thresholds it also prints. If the percentiles are far above the top threshold, lower
  `--tx-dbm` until they overlap.
- **Move fast enough that delay means something.** At walking speed with 10 ms steps, a user moves
  3 cm — the channel does not change and delay costs nothing. Vehicular speeds make the effect
  visible. This is not a trick: delay costs something only once the user has moved far enough for
  the SNR to change, and Q1 asks you to measure that distance. Note what this twin does and does
  not contain — it has no small-scale fading, so what you measure is how fast the
  position-averaged SNR changes along the path, not the coherence time.
- **The link layer is scene-independent by construction.** `build_twin.py --reuse-link` exists
  because of that. It is also an assumption, and Q2 step 3 is where you examine it.
- **BLER thresholds must increase with MCS index.** The smoke test checks this. If they do not,
  your table is undersampled — raise `--target-block-errors`.
- **Random seeds matter.** Trajectories are random walks. Use `--trials` > 1 and report spread, not
  a single run.
- **Which Linux, if you are on WSL2:** `wsl --install -d Ubuntu-24.04` — the distribution the
  course kits are verified on. Newer releases work, but 24.04 is the shortest path to a green
  smoke test.
- **WSL2 users:** keep this folder under your Linux home (`~/`), not `/mnt/c/`, and raise the
  memory limit in `.wslconfig`. The link layer is CPU-bound and benefits from cores.

---

## 7. Open source used

Everything below is third-party work. Cite it in your report the way you would cite a paper, and
respect the license if you publish your code.

| Project | What it does here | Repository | License |
|---|---|---|---|
| **Sionna** | ray tracing (RT) and link-level PHY — the twin itself | [github.com/NVlabs/sionna](https://github.com/NVlabs/sionna) | Apache-2.0 |
| **Mitsuba 3** | the renderer Sionna RT traces rays with | [github.com/mitsuba-renderer/mitsuba3](https://github.com/mitsuba-renderer/mitsuba3) | BSD-3-Clause |
| **Dr.Jit** | just-in-time compiler under Mitsuba; needs an LLVM backend on CPU | [github.com/mitsuba-renderer/drjit](https://github.com/mitsuba-renderer/drjit) | BSD-3-Clause |
| **PyTorch** | Sionna 2.x computes on it | [github.com/pytorch/pytorch](https://github.com/pytorch/pytorch) | BSD-3-Clause style (see repo `LICENSE`) |

On macOS, and on a Linux system that has none, the kit installs LLVM (Apache-2.0 with LLVM exceptions) because Dr.Jit's CPU backend needs `libLLVM`. Scene assets shipped with Sionna carry their own terms — check them before republishing a figure made from one.

**Versions are pinned.** `setup.sh` installs Sionna 2.1.0, Sionna RT 2.1.0 and PyTorch 2.14.0 (Mitsuba 3.9.1 and Dr.Jit 1.5.0 follow from Sionna RT), and it needs Python 3.11 or newer. Where a pin cannot be honored — an older Python, a platform without that wheel — it installs what it can and says so in a line beginning `! UNPINNED install`. Report the versions `setup.sh` and `smoke_test.sh` print with your results either way: two teams on different versions can get different numbers, and that is worth knowing rather than arguing about. Record one more line from `smoke_test.sh`: the Mitsuba variant. The link layer always runs on CPU PyTorch, but the ray tracer takes the GPU when the machine has a CUDA one (`cuda_*` instead of `llvm_*`), and that is expected rather than a fault.

**Kit only.** Before it creates the environment, `setup.sh` now tests for `ensurepip` rather than `venv`, and it rebuilds an environment that has no pip. On Debian and Ubuntu `venv` is part of the standard library while the piece that installs pip ships in `python3.x-venv`, which a fresh WSL image often does not have; if the script asks you to install that package, install it and run the script again.

---

## Revision history

| Rev | Date | What changed |
|---|---|---|
| **r10** | 2026-09-23 | **Kit only, §7:** one paragraph on the environment check. `setup.sh` tested `import venv`, which always succeeds because `venv` is in the standard library, so a machine without `python3.x-venv` got past the check and then failed inside `python3 -m venv` with "ensurepip is not available", leaving an environment with no pip that broke every later run. The check now asks for `ensurepip`, an environment without pip is rebuilt, and a creation that fails is removed and reported with the package to install. Found by a TA on a fresh WSL Ubuntu-24.04. No question, command or number changed. |
| **r9** | 2026-09-21 | **§3 Q3:** the closing sentence now asks for the **one** gap your result *narrows* — not one it merely imposes a requirement on — argued from your Q1 boundary and your Q2 verdicts, together with what would still be missing to close it end-to-end. The wording now matches the Lens Guide (Tool 5). No requirement, command or number changed. |
| **r8** | 2026-09-20 | **Header and §7:** the stack is described as *CPU by default* rather than *CPU only*. Sionna RT asks Mitsuba for the `cuda_*` variant first and falls back to `llvm_*`, so a machine with a CUDA GPU ray-traces on it; the link layer is CPU either way. §7 now asks for the variant alongside the versions. **Kit:** `setup.sh` and `starter/README.md` say the same. Measured difference between the two variants on one scene: 0.1 dB at the 5th SNR percentile. No requirement, command or number changed. |
| **r7** | 2026-09-20 | **§4:** extensions A and D each gain a one-sentence *Where to start* pointer. A names where the ACK/NACK signal lives, because `run_policy.py` does not expose it; D names the function that places the transmitter, and asks for an on/off interferer so that SINR varies in time rather than being a second static map. No requirement changed, and B and C already name their entry points. |
| **r6** | 2026-09-20 | **§5:** the Final report row now names the extension as its own section, as briefs 1–3 all require one (this matches split-inference r4). Nothing else changed. |
| **r5** | 2026-09-19 | **§7:** the kit now installs LLVM on Linux too when it is missing. **Kit:** on Linux `setup.sh` checks whether Dr.Jit finds `libLLVM`; if not, it looks for one, installs it (`apt`, or unpacked into the venv when there is no root) and records the path in the venv. Nothing changes on a machine where the library is already found. No requirement, command or number changed. |
| **r4** | 2026-09-19 | Spelling only: American spelling throughout the brief and the kit (behavior, modeled, license, serialization, …), to match the lecture decks and notes. No requirement, command or number changed. |
| **r3** | 2026-09-19 | **§1:** one paragraph on what *trained* means in the title. **§2:** the example builds the twin with `--tx-dbm 20` (with the default link budget the policy saturates and every condition reads `met`); `export TWIN_VENV` line added; "three knobs" → four; `--deploy-twin` now takes the field's link layer as well as its geometry from the other twin (identical for `--reuse-link` twins, so Q2 step 3 is unaffected). **§3 Q2 step 3:** says to build the second scene with `--reuse-link`. **§4 C:** rewritten — the rule does not depend on the geometry layer, so the extension is defined on what you *tune* on a cheap twin and deploy on a reference twin; link-layer fidelity knobs named. **§6:** the delay trap no longer calls the effect coherence time — the twin has no small-scale fading. **§7:** versions are now pinned. **Kit:** `results.csv` gains `bler_min`, `bler_max`, `speed_mps`, `dt_s`, `steps`, `trials`. Q1–Q3 requirements and the extension menu (A–D, exactly one) are unchanged. |
| **r2** | 2026-09-19 | §4: the extension changes from optional ("pick at most one") to **required ("pick exactly one")**. The kit makes Q1–Q3 fast to run, so depth is shown in the extension. No other section changed. |
| **r1** | 2026-08 | First release (kit confirmed by 2026-08-22). |
