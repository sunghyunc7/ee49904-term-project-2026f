#!/usr/bin/env python3
"""Run one generate → verify → repair experiment.

    python3 run_loop.py --intent enforce-egress
    python3 run_loop.py --intent block-guest --model gemma4 --max-iters 6
    python3 run_loop.py --intent block-guest --replay          # no server, no tokens
    python3 run_loop.py --intent enforce-egress --proposal fixtures/…​.txt   # no model at all
    python3 run_loop.py --intent block-guest --temperature 0.7 --seed 7 \
            --out runs/block-guest-t07-s7.json                  # one draw of a sample, kept

`--replay` reruns from the transcript. `--proposal FILE` skips the model entirely and verifies
lines you wrote yourself — the way to check whether a finding is real without spending a call,
and the way the smoke test proves the verifier detects a defect it is *known* to have.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from netops import intents                                     # noqa: E402
from netops.llm import DEFAULT_HOST, DEFAULT_MODEL, Budget, LLMClient   # noqa: E402
from netops.loop import NUM_PREDICT, run                        # noqa: E402
from netops.verify import Verifier                              # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    from netops.verify import venv_problem
    problem = venv_problem()
    if problem:
        print(problem, file=sys.stderr)
        return 2

    p = argparse.ArgumentParser()
    p.add_argument("--intent", default="enforce-egress",
                   help="enforce-egress | block-guest")
    p.add_argument("--device", default="as2dept1")
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument("--llm-host", default=DEFAULT_HOST)
    p.add_argument("--bf-host", default=os.environ.get("EE_BF_HOST", "localhost"))
    p.add_argument("--snapshot", default=os.path.join(HERE, "snapshot"))
    p.add_argument("--max-iters", type=int, default=4)
    p.add_argument("--budget-calls", type=int, default=40)
    p.add_argument("--seed", type=int, default=1234,
                   help="only matters at a non-zero --temperature; at 0 decoding is greedy")
    p.add_argument("--temperature", type=float, default=0.0,
                   help="0 (default) is deterministic. Raise it, and vary --seed, when you "
                        "need a sample of answers rather than one")
    p.add_argument("--num-predict", type=int, default=NUM_PREDICT,
                   help="token allowance per model call. A reasoning model spends it on its "
                        "reasoning first; when it runs out, done_reason in the run log is 'length'")
    p.add_argument("--transcript", default="runs/transcript.jsonl")
    p.add_argument("--replay", action="store_true", help="use the transcript only; never call")
    p.add_argument("--proposal", default=None,
                   help="verify these configuration lines instead of asking a model")
    p.add_argument("--out", default=None,
                   help="run log JSON (default runs/<intent>-<model>.json — overwritten by the "
                        "next run with the same intent and model, so name the runs you keep)")
    p.add_argument("--show-reference-findings", action="store_true",
                   help="print what the verifier already says about the untouched network")
    a = p.parse_args()

    intent = intents.get(a.intent)
    cfg_path = os.path.join(a.snapshot, "configs", f"{a.device}.cfg")
    base_cfg = open(cfg_path, encoding="utf-8").read()

    print(f"intent   {intent.key} — {intent.title}")
    print(f"device   {a.device}")
    print(f"verifier Batfish at {a.bf_host}")

    v = Verifier(a.snapshot, host=a.bf_host)
    ref = v.init_reference()
    print(f"reference snapshot loaded in {ref['init_s']}s "
          f"({ref['pre_existing_findings']} pre-existing findings, not counted against you)")

    if a.show_reference_findings:
        print("\n  the untouched network's own findings:")
        for f in v.reference_findings():
            print(f"    {f}")
        print()

    proposal_lines = None
    llm = None
    if a.proposal:
        proposal_lines = [l.rstrip("\n") for l in open(a.proposal, encoding="utf-8")]
        print(f"proposal {a.proposal} ({len(proposal_lines)} lines) — no model will be called")
    else:
        os.makedirs(os.path.dirname(os.path.abspath(a.transcript)), exist_ok=True)
        llm = LLMClient(host=a.llm_host, model=a.model, transcript=a.transcript,
                        mode="replay" if a.replay else "live", seed=a.seed,
                        budget=Budget(max_calls=a.budget_calls), temperature=a.temperature)
        if not a.replay:
            ok, models = llm.available()
            if not ok:
                print(f"\n!! cannot reach the LLM at {a.llm_host}: {models[0]}")
                print("   The model is served on the assigned server itself, so this usually")
                print("   means the serving process is down — after a reboot it has to be")
                print("   started again. Ask on the KLMS Q&A board or your TA.")
                v.close()
                return 2
            if not any(m.split(":")[0] == a.model.split(":")[0] for m in models):
                print(f"\n!! the server has no model {a.model!r}. It has: {', '.join(models)}")
                v.close()
                return 2
        print(f"model    {a.model} ({'replay' if a.replay else 'live'}"
              + (f", temperature {a.temperature:g}, seed {a.seed}" if a.temperature else "") + ")")

    out = a.out or os.path.join("runs", f"{intent.key}-{(a.model if llm else 'scripted')}.json")
    print()
    try:
        payload, accepted = run(intent, v, llm, base_cfg, device=a.device,
                                max_iters=a.max_iters, proposal_lines=proposal_lines,
                                out_json=out, num_predict=a.num_predict)
    except KeyboardInterrupt:
        # run() has already written the log and removed its snapshots; say so rather than
        # printing a traceback at someone who pressed Ctrl-C on purpose.
        print(f"\n!! interrupted. The run log up to that point is in {out}.")
        return 130
    except Exception as e:                                          # noqa: BLE001
        print(f"\n!! the run stopped: {type(e).__name__}: {e}")
        print(f"   The run log is in {out} and the verifier's snapshots were removed.")
        print("   If this came from the model server, check that it is up before running again.")
        return 3

    print(f"\noutcome  {payload['outcome']}")
    if payload.get("budget"):
        b = payload["budget"]
        print(f"cost     {b['calls']} calls, {b['output_tokens']} output tokens, {b['seconds']}s"
              + (f", {b['replayed']} replayed" if b["replayed"] else ""))
    print(f"log      {out}")

    if accepted:
        # Named after the run log, not after the intent: two runs of the same intent side by side
        # (what Q1 asks you to do) would otherwise write the same file and one would lose its
        # result. --out names the run; this follows it.
        acc_path = os.path.join(os.path.dirname(out) or ".",
                                f"{os.path.splitext(os.path.basename(out))[0]}"
                                f"-accepted-{a.device}.cfg")
        with open(acc_path, "w", encoding="utf-8") as f:
            f.write(accepted)
        print(f"config   {acc_path}")
    v.close()
    return 0 if accepted else 1


if __name__ == "__main__":
    sys.exit(main())
