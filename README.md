# EE49904 AI-Native Networking — Term Project (Fall 2026)

Five guided labs on open-source stacks. Your team picks **one**. Each lab gives you a working
starter kit; the work is deciding what to measure, reading the result honestly, and writing it up.

> Weights and report deadlines are in the syllabus and on KLMS. This page covers what is common to
> all five briefs. Each `BRIEF.md` defines what that project must produce.

## The menu

| # | Brief | Folder | Track | Where it runs |
|---|---|---|---|---|
| 1 | Collective Communication and Fabric Design | `astra-sim-collectives/` | Network for AI | any laptop |
| 2 | Split Inference: Placing Computation Across a Network | `split-inference/` | Network for AI | any laptop |
| 3 | Twin-Trained Control: Rehearsing a Policy Before It Touches the Network | `twin-trained-control/` | AI for Network | any laptop |
| 4 | RIC Closed-Loop Control: Delegating a Decision to an xApp | `ric-closed-loop/` | AI for Network | **Ubuntu 24.04 with root** — WSL2, a Linux VM on macOS, or your own server |
| 5 | LLM NetOps with a Verifier in the Loop | `llm-netops-verifier/` | AI for Network | **shared course server only** — an SSH client is all you need |

No brief needs a GPU of your own. macOS, Linux, and Windows with WSL2 are supported unless the
table says otherwise. On WSL2, install `Ubuntu-24.04` — it is the combination the kits are verified on.

Two briefs constrain the machine. Brief 4 needs root and one host per team, so it does **not** run
on the shared course server, and Ubuntu 22.04 will not build it; if no machine you have fits, say so
in the sign-up form and you will be assigned one. Brief 5 is the opposite: it runs **only** on the
shared course server, and accounts are issued to the teams assigned to it.

Briefs 1 and 2 belong to the Network-for-AI track, which is lectured in the second half of the
semester. You can choose them now; the brief is self-contained, but expect to read ahead.

## What every brief asks

Three required questions, each tied to a tool from the lectures:

| | Question | Course tool |
|---|---|---|
| Q1 | What is contended, on what timescale, with what information — and which of the four it is, or none? | the four persistent control problems |
| Q2 | What did your experiment verify, and **what did it not**? | the acceptance test |
| Q3 | Which one gap does your finding narrow, and what is still missing? | the four gaps from cloud-native to AI-native |

**Q2 ends with a verdict.** Whatever your brief's Q2 asks you to run, close the section with the
acceptance test's five clauses (Lens Guide, Tool 2), one line each: **passes / not tested / fails** —
or **not applicable**, with the reason — and point to the part of your Q2 that is the evidence. An
honest *not tested* is credited; a *passes* without evidence is not. In brief 1 there is no learned
component: apply the clauses to what the simulator lets you measure and mark a clause *cannot be
measured here* with the reason — that is a full answer. In brief 5 the verifier is the acceptance test
*of a change*; the five clauses still judge the loop as a whole — model, prompt, feedback and verifier
together.

**Extensions.** In briefs 1–3 one extension is **required** (pick exactly one). In briefs 4–5 the
extension is optional (pick at most one). The difference is deliberate: kits 1–3 run in seconds, so
depth is shown in the extension; kits 4–5 already carry that load in their required questions.

## How projects are graded

- **There is no grading curve across briefs.** A team is assessed on the depth of its own Q1–Q3
  answers within its brief, not against teams that chose a different one. Choose the brief that
  interests you, not the one that looks cheapest.
- Grading looks at **what you measured and whether you know its limits**, not at whether you hit a
  particular number. An honest statement of what you did not verify earns credit.
- Reproducibility is graded: another team must be able to re-run your commands and arrive at your
  finding. Where the numbers depend on the machine, what has to reproduce is the *structure* of the
  result — the regimes, the boundaries, and what dominates each — not the absolute values. Your
  brief says which of the two it expects.

## Teams and brief selection

- Teams of **2–3**. Mixed undergraduate/graduate teams are encouraged.
- **At most 3 teams per brief.**
- By **Tue Oct 6, 23:59**, each team submits its members and its **1st–3rd choice** of brief: https://forms.gle/Q5o5jErRu8CiugHV8
- If a brief is over-subscribed, places are drawn by a **random draw with a published seed** — the
  same procedure used for the paper-presentation slots. First choices are honored before second.
- Assignments are announced **Thu Oct 8**. Selection + execution plan presentations follow on **Thu Oct 15**.
- If you have no team by Oct 6, submit the form alone; the teaching staff will match you.
- A free topic is possible only by prior approval of the instructor.

## Getting started

```bash
cd <brief-folder>/starter
bash setup.sh          # first install takes 5–10 min (longer on a first-time Homebrew/WSL setup)
bash smoke_test.sh     # every check must pass before you measure anything
```

Run the smoke test for your 1st and 2nd choice **before** you submit the form. A brief whose kit
does not go green on your machine is not a brief you should rank first.
Brief 5 is the exception — it cannot be tried before you have a server account, and the form has an
answer for that.

## AI Use Disclosure (required in the final report)

Generative AI tools are permitted for the project, including for code. Disclosure is required and is
not penalized; **undisclosed use is an integrity matter**. Include this table as the last section of
your report:

| Tool / model | Used for | Extent |
|---|---|---|
| e.g. Claude Fable 5.1 | debugging `decide()`; first draft of §2 | code reviewed and rewritten by us; text edited |
| | | |

All team members must take part in implementation, experiments, analysis, and writing.

## Questions

Post on the KLMS Q&A board, or contact the TAs: Bom Kim (spring@kaist.ac.kr) · Soonhyun Kwon
(soonhyun@kaist.ac.kr).
