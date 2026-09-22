# Term Project Brief — LLM NetOps with a Verifier in the Loop

**Stack:** open-weight LLM + [Batfish](https://batfish.org), both on the department's shared server
**Team:** 2–3 students · **Where it runs:** the department's shared server — an SSH client is all you need (no GPU of your own, no root, no Docker).
**Track link:** AI for Network (observe → bound → rehearse → learn → **operate**)

> Weights, deadlines, and submission mechanics are in the syllabus and on the LMS.
> This brief defines *what you must produce*, not when.

---

## 1. The problem

Ask a language model to configure a router and it will. It will produce something that looks
exactly like what a competent engineer produces: right vendor syntax, plausible addresses, an ACL
whose comment matches the ticket. The question this project is about is not whether it can write
configuration — it can — but **what has to be true before anyone is allowed to apply it.**

The answer this course keeps arriving at is: something other than the author must decide. So you
build the loop where the deciding is done by a formal verifier:

```
   ticket  ──▶  model proposes  ──▶  apply to a copy  ──▶  Batfish verifies  ──┬─▶ accepted
   (English)     (config lines)      (never the live         (reachability,    │
                        ▲              network)               ACL semantics,   │
                        │                                     references)      │
                        └────────────── findings ─────────────────────────────┘
                                                                               └─▶ budget spent,
                                                                                   nothing applied
```

Batfish reads configuration files and computes what the network *would* do: which flows would be
delivered, which ACL lines can ever match, which BGP sessions would come up, which references
point at nothing. It sends no packets and touches no device. It is, in this project, the
acceptance test — the thing standing between a plausible answer and a production network.

You will find that the loop closes, that it converges on some tasks and not others, and — this is
the part worth writing down — that it can converge on a configuration which passes every check and
still does not do what the ticket asked.

### The network

A small campus (AS2) with two upstream providers, thirteen Cisco IOS devices, taken verbatim from
Batfish's own example network so that parsing is never your problem. All changes happen on
`as2dept1`, the one device with host-facing interfaces. See `starter/snapshot/NOTICE.md`.

Two properties of that network are not accidents and you should know them before you start:

1. `RESTRICT_HOST_TRAFFIC_OUT` is **defined but applied nowhere.** The protection it describes is
   not in force.
2. Both host ACLs contain a **line that can never match**, because an earlier line already matches
   the same packets. A rule that never runs is a rule whose intent is not implemented — and the
   verifier will say so, about the network as you received it, before you change anything.

### A disclosure you need before you start

The model does not hand you a file; it hands you the lines an operator would type. Something has
to merge those into a configuration, and that something makes choices — `starter/netops/cfg.py`
documents them at the top of the file. Two matter:

- On an **interface**, proposed lines are *merged*, so the model cannot delete an address it was
  never shown.
- In an **ACL**, proposed lines *replace the whole body*, because in an access list the order of
  the lines is the semantics and a rule appended after `deny ip any any` is a rule that never runs.

A generous merge hides model errors; a strict one invents them. Ours is a judgment call sitting
between your measurement and the thing you claim to be measuring, and Q1 asks you to quantify how
often it has to work hard.

---

## 2. Getting started

This runs on the department's shared server, and only there. You will be given an account on one
of those machines; it needs no Docker and no root of yours, and it is the configuration the course
verified end to end.

```bash
ssh <your-account>@<the server you were assigned>
cd starter/
bash setup.sh                      # venv + pybatfish + Batfish under udocker  (5–10 min, once)
source ~/ee49904-netops/env.sh     # every new shell
bash smoke_test.sh                 # 9 checks — all must pass
```

Both halves of the loop live on that machine: the model is served locally and Batfish runs in
your own account under **udocker**, which needs neither a daemon nor root. There is no tunnel.

Accounts and server assignments are announced on the LMS. Until yours exists, any machine with
Python can run the offline checks — `python3 -m netops.selftest` — which exercise the merge rules,
the answer parser and the budget without a model and without a verifier. Everything else waits for
the account: the verifier needs a container runtime and the model needs a GPU, and the server is
where the course provides both.

Three consequences of the server being shared, worth knowing on day one:

- **Home directories are not shared between servers.** Work on the one you were assigned; a
  model copied onto one server is not visible from another.
- **Batfish files your snapshots under a network name**, and `setup.sh` makes yours
  `ee49904-<your-username>`. Leave it alone. If you hard-code a shared name, another team's run
  can overwrite your snapshot and you will not be told.
- **Batfish is a process in your account, not a system service.** When the server reboots it is
  gone, and `smoke_test.sh` check 2 fails. Run `bash setup.sh` again — the second time takes
  seconds — and `source` the env file.

That `source` line is per-shell, not once-ever: a new terminal, a reboot, a fresh `tmux` pane
all need it again. `smoke_test.sh` activates the venv for you if you forget (and says so), but
`run_loop.py` will not — it stops with the same instruction instead.

Then:

```bash
python3 run_loop.py --intent enforce-egress                    # the model tries
python3 run_loop.py --intent block-guest --model gemma4        # a smaller model tries
python3 run_loop.py --intent block-guest --replay              # rerun free, no server
python3 run_loop.py --intent enforce-egress \
        --proposal fixtures/wrong_direction.txt                # verify lines you wrote yourself
python3 run_loop.py --intent block-guest --temperature 0.7 --seed 7 \
        --out runs/block-guest-t07-s7.json                     # one draw of a sample, kept
```

Every run writes `runs/<intent>-<model>.json`, and the next run with the same intent and model
overwrites it. Name the runs you mean to keep with `--out`.

**You edit `propose_prompt`, `repair_prompt`, and `format_findings` in `netops/loop.py`.**
Everything else — snapshots, budget accounting, run logs — is done. The shipped prompts are the
least clever thing that works, so that whatever you do to them shows up as a difference rather
than as noise on top of something already tuned.

`format_findings` looks like plumbing and is not. It decides how much of the verifier's output the
model is shown, and that one choice moves convergence more than most prompt wording does.

### Two things about cost

**The server is shared and it has eight slots.** Measured on the department server: a 512-token
answer takes about **4 s** when the server is quiet and about **12 s** with eight generations in
flight at once — and on the assignment below, most of those eight are your own. Each run has a
call ceiling (default 40) and stops when it hits it.

**Every call is recorded, so most of your work should cost nothing.** `--replay` re-runs an
experiment out of the transcript with the server switched off. You will spend most of your time on
the verifier side and on the feedback format; neither needs a live model. A replay that misses is
a hard error, never a silent live call — if the prompt changed, the transcript cannot cover it and
you should know that rather than be quietly billed for it.

---

## 3. Required questions

Answer all three. Each is tied to a tool this course has been building.

### Q1 — What is contended, on what timescale, and with what information?
> *Course tool: the four persistent control problems.*

This is a control loop whose actuator is a shared inference server and whose sensor is a formal
model. Say first whether this is one of the four wireless control problems or none of them — and
if none, name the analog you would defend and the loop the work actually closes. Apply the lens
with measurements:

- **What is contended.** Two things are, and they are not the same thing. One is the **server**:
  measure the loop's wall-clock cost with one team generating and with several — run your own
  concurrent instances if you want the curve (each process keeps its own snapshots, so they
  do not disturb each other's verdicts), and say where the knee is. The other is the
  **network's configuration state**: only one change can be in flight at a time, and your loop
  holds a candidate for as long as it iterates. State the change-window your loop would need.
- **On what timescale.** Three clocks: **generation** (seconds per proposal), **verification**
  (seconds to tens of seconds per candidate — measure it, it is in every run log), and the
  **ticket** (a human waiting). Report all three from your own runs. Which one dominates, and does
  the answer change between the two intents?
- **With what information.** The model sees a configuration file and a list of findings. It does
  not see the reference network, the assertions it is being judged against, or any live state. Say
  what it is inferring rather than observing — and measure one consequence: how often does its
  answer need work from the merge in `cfg.py` (`apply_report`, `unapplied`) before it is even a
  configuration?

### Q2 — What did the verifier actually verify, and what did it not?
> *Course tool: the acceptance test.*

Batfish is the acceptance test, so audit the acceptance test.

1. **Audit the check suite.** Six checks run (`netops/verify.py` names them). For each, say in one
   sentence what class of defect it can catch and what it cannot. Then find at least two classes of
   defect that **none** of them would catch, and say how you would catch those.
2. **Find a pass that is not a success.** There exists at least one change that satisfies every
   check in this kit — including the intent assertions — and still does not accomplish what the
   ticket asked. Find one, demonstrate it, and explain what the assertion set is actually asserting
   as opposed to what it appears to assert. (It is not a trick question about wording. It is about
   the difference between testing a filter and testing a network.)
3. **Find a rejection that is not a defect.** Run enough proposals to hit a finding you judge to be
   a false alarm — a warning that blocks nothing real, or a check that is stricter than the intent
   requires. Proposals you write yourself and feed in with `--proposal` count. Argue the case,
   then say what it costs the loop: an alarm that fires on correct work is a loop that spends
   its budget arguing.
4. **Verifier off versus verifier on.** Run each intent with the loop's feedback disabled (accept
   the first proposal) and with it enabled. `--max-iters 1` gives you the first condition: one
   proposal, the verifier's verdict on it, nothing fed back. Report the acceptance rate under
   each, and — separately — the rate of *defects that reach acceptance*, which is not the same
   number and is the one that matters. **A rate needs a sample.** At the default temperature 0
   decoding is greedy, so a different `--seed` normally returns the same answer and still costs
   a call. Run several seeds at a non-zero `--temperature`, and report the temperature and the
   number of runs behind every rate.

**Honest declaration of what you did not verify earns credit.** "Our loop never checks that the
change is minimal, so a proposal that rewrites the whole device would pass" is a good sentence to
have in a report.

### Q3 — Where does your finding sit on the gap ladder?
> *Course tool: the four gaps from cloud-native to AI-native.*

- **Gap 1 — GPU/radio-aware orchestration.** Your loop needs an inference server and a verifier,
  both with real resource footprints. Where do they run, and what does your Q1 timescale answer
  say about whether that placement can serve a whole operations team?
- **Gap 2 — telco-grade data plane.** Batfish reasons from configuration alone. Name one property
  of the running network that your loop's decisions depend on and that no amount of configuration
  analysis can establish.
- **Gap 3 — MLOps × NetOps.** Your loop has a model, a prompt, a feedback format, and an assertion
  set, and all four drift. Who owns each? How would anyone notice that the model's acceptance rate
  had fallen — before a change went out, rather than after?
- **Gap 4 — bounded, verifiable autonomy.** This brief's most direct target. The verifier is the
  bound. Say exactly what autonomy you would grant this loop today — which classes of change, on
  which devices, with what human involvement — and defend the boundary with your own numbers. Then
  state what evidence would let you move it out one notch.

Gap 4 is where this brief lives, and the marks are for the boundary you draw, the numbers you
defend it with, and the evidence you name — not for naming the gap. If you argue that your result
narrows another gap more, say what evidence would move *that* one to the next rung.

---

## 4. Optional extension — pick at most one

- **A. Feedback design.** `format_findings` currently sends the model up to eight blocking
  findings, most specific first. As shipped, most runs are accepted at the first attempt, so first
  find a setting in which the loop iterates at all — with no second iteration there is no feedback
  to design. Then build two alternatives — say, one finding at a time, and all of
  them with full traces — and measure iterations-to-acceptance and tokens-to-acceptance for each.
  Report the trade-off, including cases where more information made it worse.
- **B. Model comparison.** The server keeps two models resident. Run both intents on both, several
  seeds each at a non-zero `--temperature` (at 0 the seed changes nothing), and report
  acceptance rate, iterations, and the *kind* of mistake each makes. The
  interesting result is not which wins; it is whether the verifier's findings are enough to rescue
  the weaker one.
- **C. Grow the acceptance test.** Add checks that catch what §Q2.1 says the current suite misses —
  a minimality check, a check that the ACL is applied where the intent implies, a policy assertion
  of your own. Re-run your earlier experiments against the stronger suite and report which of your
  previous conclusions did not survive.
- **D. A third intent.** Write one: a ticket, its assertions, and a reference solution, in the
  style of `netops/intents.py`. Then hand your teammates the ticket only, and report whether the
  loop's behavior on it matched what you predicted when you wrote the assertions.

---

## 5. Deliverables

| Stage | What you hand in |
|---|---|
| **Selection + plan** | 1 page: that the smoke test passes on the server you were assigned, which intents you will study, your chosen extension if any. |
| **Progress note** | 1 page: acceptance rates so far, the verification-time number from your run logs, what surprised you. |
| **Workshop** | Presentation/demo. Show one loop running live if you can, and the Q2.2 case — the change that passed and should not have. |
| **Final report** | 6 pages, IEEE format. Q1/Q2/Q3, and your extension if you take one, must be identifiable sections. Include your run-log JSONs, your `loop.py`, and the exact commands. |

Reproducibility is graded, and this kit makes it cheap: ship your transcript and another team can
replay your experiments without a model server. A report whose numbers cannot be regenerated from
its own run logs has not met the bar.

**AI tool use:** declare which tools you used and for what. Disclosure is required and is not
penalized; undisclosed use is an integrity matter. Note that in this project the model's output
*is* the experimental data — quoting it is expected; passing off its analysis as yours is not.

---

## 6. Traps worth knowing in advance

- **`smoke_test.sh` check 4 is the one that matters.** It feeds the verifier a defect that is known
  to be there and requires it to be rejected. "Batfish answered" and "Batfish caught it" are
  different facts, and only the second means your measurements mean anything.
- **The base network already has findings.** The verifier reports only what is *new* against the
  reference, or you would learn to skim its output — and skimming is how the one finding that was
  yours gets missed. Run `--show-reference-findings` once so you know what is being subtracted.
- **`in` and `out` are relative to the interface**, not to the campus. This is the single most
  common wrong answer, from models and from humans, and the verifier catches it as a reachability
  regression rather than as a syntax error — which is exactly why the check suite is not a linter.
- **Adding a line does not remove the previous one.** If an earlier iteration applied a filter in
  the wrong direction, the repair must emit `no ip access-group … in`. A proposal that only adds
  leaves both in place, and the loop can iterate forever fixing something it never undoes.
- **A proposal that changes nothing stops the loop.** That is deliberate: a model that has run out
  of ideas repeats itself, and spending the remaining budget on identical answers teaches you
  nothing.
- **`--replay` misses are fatal by design.** Change a prompt and the recorded answers no longer
  apply. The alternative — quietly calling the server — would make "replay" both expensive and
  irreproducible.
- **Do not assume where the time goes.** It is tempting to blame the model, and equally tempting
  to blame the verifier. On one reference machine a full iteration took 8 s, of which snapshot
  initialization was under a second — but that was one machine, with the model served locally and
  nobody else on it. Yours is a shared department machine, with your own concurrent runs
  on it and other people's work beside them. The split is yours
  to measure, and Q1 asks for *your* numbers, not these.
- **The shared server is shared in ways that can bite you.** Your home directory does not follow
  you to another server, so a model or a snapshot staged on one is invisible from the next. And
  the verifier's HTTP port cannot be remapped under udocker: if someone else on that machine got
  there first, `setup.sh` reuses their Batfish and says so, and the answer is to use that
  endpoint (`EE_BF_HOST`) rather than to start a second one. The process then belongs to
  whoever started it: if it disappears in the middle of your run, they stopped it or the server
  rebooted, and `bash setup.sh` starts one under your account.
- **An answer cut off by the token allowance is never applied.** gpt-oss reasons before it
  answers, and the reasoning is spent out of the same allowance as the answer
  (`--num-predict`, default 4096). When the allowance runs out the run stops with
  `answer truncated` and nothing is verified: half a configuration line is not the model's
  proposal, and applied it makes the verifier report defects the model never wrote. The run log
  records `done_reason: length`, and the transcript keeps the reasoning text (`thinking`) —
  which is also evidence for Q1's third bullet. How long the model reasons is not a constant:
  on the course server the same ticket was answered in 619 tokens six times running, and on
  another occasion the model was still reasoning at 2,048. The allowance sits on Q1's generation
  clock, so say what you set it to.
- **Do the setup on day one, not the night before the progress note.** `setup.sh` downloads a
  container image and builds a virtual environment; it is the step most likely to meet something
  unexpected about your account, and it is the cheapest thing in the project to do early.
- **Determinism is not a sample.** `temperature 0` is greedy decoding: it makes the seed
  irrelevant, and a repeated call normally returns the same answer — five seeds at temperature 0
  are one observation made five times. *Normally* is not *always*: on a shared GPU server the
  same call can take a different path depending on what the server had cached when it arrived,
  and a long chain of reasoning amplifies the difference. What makes your result reproducible is
  the transcript, not the temperature. And none of this makes two models, or two prompt
  phrasings, comparable on one run each. Before you report a rate or a difference, raise
  `--temperature`, vary `--seed`, keep every run with `--out`, and say how many runs stand
  behind each number.

---

## 7. Open source used

Everything below is third-party work. Cite it in your report the way you would cite a paper, and
respect the license if you publish your code.

| Project | What it does here | Repository | License |
|---|---|---|---|
| **Batfish** | the verifier — computes what the configuration would do | [github.com/batfish/batfish](https://github.com/batfish/batfish) — image `batfish/allinone` | Apache-2.0 |
| **pybatfish** | the Python client the kit drives Batfish with | [github.com/batfish/pybatfish](https://github.com/batfish/pybatfish) | Apache-2.0 |
| **ollama** | serves the open-weight models on the shared server | [github.com/ollama/ollama](https://github.com/ollama/ollama) | MIT |
| **udocker** | runs the Batfish image inside your own account, with no daemon and no root | [github.com/indigo-dc/udocker](https://github.com/indigo-dc/udocker) | Apache-2.0 |

The network in `starter/snapshot/configs/` is **Batfish's own example network**, copied verbatim under Apache-2.0 — see `starter/snapshot/NOTICE.md` for provenance.

The **model weights** are not covered by the licenses above, and the two are not alike. **gpt-oss** is Apache-2.0, weights included. **gemma4** is a Gemma-family model, and Gemma is not open source in the OSI sense: Google releases it under its own Gemma Terms of Use, with a Prohibited Use Policy that follows the model to whoever uses it. You reach both through the lab server's API and never redistribute them, so what binds you is the terms on use rather than on distribution — read Gemma's before you build anything beyond this course on it. Either way, if you quote model output at length in your report, say which model produced it.

**Versions are not pinned yet.** The kit installs whatever is current when you run `setup.sh`, so record the versions you actually used — `smoke_test.sh` prints most of them — and report them with your results. Two teams on different versions can get different numbers, and that is worth knowing rather than arguing about.

---

## Revision history

| Rev | Date | What changed |
|---|---|---|
| **r10** | 2026-09-21 | **Q1** opens with one sentence: say first whether this loop is one of the four wireless control problems or none of them, and if none, name the analog you would defend and the loop the work actually closes. **Q3**'s closing sentence now says that Gap 4 is where this brief lives and that the marks are for the boundary, the numbers and the evidence, not for naming the gap; arguing for another gap is still allowed. No requirement, command or number changed. |
| **r9** | 2026-09-21 | **§7 only, license accuracy.** The two models were named on one line as carrying "its own terms", which flattened a real difference: gpt-oss is Apache-2.0 down to the weights, while Gemma is not an OSI open-source license at all but Google's own Terms of Use with a Prohibited Use Policy that follows the model. Q2.4 and extension B have you compare the two, so the distinction is one you meet in the work. No requirement, command or number changed. |
| **r8** | 2026-09-20 | **One place to run this.** The laptop-with-Docker route (r2's path B) is gone: §2, §5, §6 and §7 now describe the shared server only. It was a second environment to support for no gain — the model has to come from the server in either case, and the course verified the server path end to end. Teams without an account yet can still run the offline checks (`python3 -m netops.selftest`). **§4 extension A** also gains one sentence: as shipped, most runs are accepted at the first attempt, so the first job is to find a setting in which the loop iterates at all — measured across models, intents and seeds, a second iteration is rare, and without this the extension asks you to compare feedback formats that are never used. The three required questions are unchanged from r7. |
| **r7** | 2026-09-20 | **§5:** the Final report row now says where an extension goes, for the teams that take the optional one. Nothing else changed. |
| **r6** | 2026-09-19 | **Token allowance.** Measured on the course server: with the allowance this kit first shipped with (768 tokens per call) gpt-oss never finished the second intent — it reasons longer on that server than on the machine the value was chosen on. The default is now **4096**, exposed as `run_loop.py --num-predict`, and **an answer that hits the allowance is no longer applied**: a truncated answer used to replace an ACL body with half a line, and the verifier then reported defects the model had not written. §6: the trap on empty proposals is rewritten around this, and the last trap now says that temperature 0 is reproducible *normally*, not always — the transcript is what makes a result reproducible. Q2.4: one word (“normally”). The three required questions ask for the same work as in r5. |
| **r5** | 2026-09-19 | Spelling only: American spelling throughout the brief and the kit (behavior, modeled, license, serialization, …), to match the lecture decks and notes. No requirement, command or number changed. The ticket text and assertion names in `netops/intents.py` are deliberately left as they were (they are model input and replay keys). |
| **r4** | 2026-09-19 | Kit only — the text of this brief is unchanged. `setup.sh` now writes the model endpoint it was run with into `env.sh`, as it already did for Batfish, so a tunnel on a port other than 11434 survives a new shell (it used to fall back to 11434 and find your own ollama, with no course model on it). It prints the recovery command when amd64 emulation is missing on arm64 Linux, and it no longer says that no tunnel is needed when the model was reached through one. |
| **r3** | 2026-09-19 | **Sampling.** `run_loop.py` gains `--temperature` (default 0, unchanged). Q2.4, extension B and the last trap in §6 now say what r2 left implicit: at temperature 0 the seed changes nothing, so a rate needs runs at a non-zero temperature, and the report states how many. Q2.4 names `--max-iters 1` as the verifier-off condition; Q2.3 says hand-written `--proposal` files count. **Concurrency.** Each process now files its Batfish snapshots under its own names and removes them on exit, so concurrent runs under one account no longer overwrite each other (Q1 invites them). **Run logs** record `done_reason` and the transcript keeps the model's reasoning text; new trap in §6 on empty proposals caused by the token limit. §2: what to run before you have an account, `--out` for runs you keep, Batfish after a server reboot. §6: the WSL2 entry no longer claims this kit was verified there — it was not. Kit messages and `starter/README.md` brought into line with path A (they still described the tunnel-only setup of r1). The three required questions ask for the same work as in r2. |
| **r2** | 2026-09-11 | The department's shared server becomes the recommended place to run this (§2 path A); the laptop-with-Docker route stays as path B. Batfish now runs under **udocker** there — no daemon, no root — so Docker is no longer a requirement of the project. Serving numbers in §2 remeasured on that server: 512-token answer **9 s → 4 s** alone, **23 s → 12 s** with eight teams. Snapshots are now filed under a per-user Batfish network name, so a shared verifier cannot let one team overwrite another's. New traps in §6 on non-shared home directories and on the verifier port. udocker added to §7. |
| **r1** | 2026-08-22 | First release. Smoke test 9/9 on two reference machines. |
