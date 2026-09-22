# Starter kit — LLM NetOps + verifier

```bash
bash setup.sh                      # venv + pybatfish + Batfish   (5–10 min, once)
source ~/ee49904-netops/env.sh     # every new shell
bash smoke_test.sh                 # 9 checks — all must pass
```

This runs on the department's shared server you were assigned, and `BRIEF.md` §2 is the longer
version. In short: the model is served on that machine and `setup.sh` starts Batfish in your own
account under udocker — no Docker of yours, no root, no tunnel.

`smoke_test.sh --no-llm` runs the six checks that need no model server. It is the fastest way to
tell a broken kit from an unreachable model, and it is what you can run before you have an
account on the server. `python3 -m netops.selftest` runs the offline checks alone — no Batfish,
no model, no venv.

**After the machine reboots, Batfish is gone** — it is a process in your account, not a system
service. `bash setup.sh` starts it again; the second time takes seconds.

## What is here

| Path | What it is |
|---|---|
| `snapshot/` | the network, 13 IOS devices (see `snapshot/NOTICE.md` for what it is and where it came from) |
| `netops/llm.py` | model client — deterministic, budgeted, records every call |
| `netops/cfg.py` | turns proposed lines into a configuration file; **read the rules at the top** |
| `netops/verify.py` | the six Batfish checks and how findings are counted |
| `netops/intents.py` | the two tasks, and the assertions that decide whether they are done |
| `netops/loop.py` | the loop — **this is the file you edit** |
| `netops/selftest.py` | offline checks (no Batfish, no model) |
| `fixtures/*.txt` | hand-written proposals, for verifying without spending a call |
| `run_loop.py` | the CLI |

## Running it

```bash
python3 run_loop.py --intent enforce-egress                  # the model tries
python3 run_loop.py --intent block-guest --model gemma4
python3 run_loop.py --intent block-guest --replay            # rerun, no server, no tokens
python3 run_loop.py --intent enforce-egress \
        --proposal fixtures/wrong_direction.txt              # no model at all
python3 run_loop.py --intent enforce-egress --show-reference-findings
python3 run_loop.py --intent block-guest --temperature 0.7 --seed 7 \
        --out runs/block-guest-t07-s7.json                   # one draw of a sample, kept
```

Every run writes `runs/<intent>-<model>.json`: each iteration's proposal, the diff it produced,
every finding, the tokens it cost, and why the loop stopped. That file is your data — the report
is written from it, not from what you remember happening.

**The next run with the same intent and model overwrites that file.** Name the runs you mean to
keep with `--out`. The transcript (`runs/transcript.jsonl`) is append-only, so a log you did
overwrite can be regenerated with `--replay`, as long as the prompts have not changed since.

**At temperature 0 the seed changes nothing** — decoding is greedy, so five seeds are one answer
asked for five times, each one a live call. When you need a rate rather than a single outcome,
raise `--temperature`, vary `--seed`, and keep each run with `--out`.

**Several runs at once are safe.** Each process files its Batfish snapshots under names of its
own and removes them when it ends, so your concurrent instances — or a teammate on the same
account — cannot be handed each other's verdicts.

## Three things that will save you an evening

**Use `--replay`.** Once a run is recorded, replaying it costs nothing and takes seconds. Most of
your work is on the verifier and the feedback format, and neither needs a live model. The
server's eight slots are shared; a team that replays is a team that is never queued. A live run
also replays any call already in the transcript — time a run only from a fresh `--transcript`, and
read the `replayed` count in the log.

**`--proposal` is how you check whether a finding is real.** Write the lines yourself, verify
them, and you have separated *the model got it wrong* from *your loop mangled a correct answer*.
The two look identical in a run log and have completely different fixes.

**Read `--show-reference-findings` once, before you blame anything.** The untouched network
already has findings, including an ACL line that can never match. The verifier subtracts those
from yours, but you should know what they are — one of them is the subject of the second intent.

## Budget

The default ceiling is 40 calls per run. A 512-token answer takes about 4 s when you are alone on
the server is quiet and about 12 s with eight generations in flight at once. If you find yourself raising `--budget-calls`, first check that you are not paying
for iterations that `--replay` would have given you free.

gpt-oss reasons before it answers, and the reasoning is spent out of the same token allowance as
the answer (`--num-predict`, default 4096). **An answer that hits the allowance is never
applied**: the run stops with `answer truncated`, because a configuration line cut in half is not
the model's proposal and would make the verifier report defects the model never wrote. The run
log says `done_reason: length`; the transcript keeps the reasoning text (`thinking`). The
allowance is part of the replay key, so a run recorded at one value does not replay at another.
