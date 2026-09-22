"""Phases of the smoke test that need Python (Batfish and LLM). Driven by ../smoke_test.sh.

Each phase prints one line starting with RESULT: pass|fail followed by a message, so the shell
script can count without parsing prose. Phases are separate processes on purpose — a phase that
hangs on a Batfish query does not take the rest of the run with it.
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from netops import intents                                    # noqa: E402
from netops.cfg import apply_edits                             # noqa: E402
from netops.llm import DEFAULT_HOST, DEFAULT_MODEL, Budget, LLMClient  # noqa: E402
from netops.loop import run                                    # noqa: E402
from netops.verify import Verifier                             # noqa: E402

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SNAP = os.path.join(HERE, "snapshot")
CFG = os.path.join(SNAP, "configs", "as2dept1.cfg")
FIX = os.path.join(HERE, "fixtures")
BF_HOST = os.environ.get("EE_BF_HOST", "localhost")


def result(ok, msg):
    print(f"RESULT: {'pass' if ok else 'fail'} {msg}")
    return 0 if ok else 1


def lines_of(name):
    with open(os.path.join(FIX, name), encoding="utf-8") as f:
        return [l.rstrip("\n") for l in f]


def base_cfg():
    return open(CFG, encoding="utf-8").read()


# ------------------------------------------------------------------ phases

def phase_service():
    """Batfish is up and answers."""
    from netops.verify import venv_problem
    problem = venv_problem()
    if problem:
        return result(False, problem.replace("\n", "\n        "))
    from pybatfish.client.session import Session
    try:
        s = Session(host=BF_HOST)
        # Per-user, for the same reason as in verify.py: one Batfish may be serving several
        # teams on this server, and snapshots are filed by network name.
        net = os.environ.get("EE_BF_NETWORK", "ee49904-" + (os.environ.get("USER") or "anon"))
        s.set_network(f"{net}-smoke")
        return result(True, f"Batfish at {BF_HOST} answered (network {net}-smoke)")
    except Exception as e:  # noqa: BLE001
        return result(False, f"cannot talk to Batfish at {BF_HOST}: {type(e).__name__}: {e}\n"
                             "        run setup.sh again — it starts Batfish under Docker or,\n"
                             "        where there is no Docker, under udocker.")


def phase_reference():
    """The base network parses, and we learn what it already complains about."""
    v = Verifier(SNAP, host=BF_HOST)
    t0 = time.time()
    info = v.init_reference()
    pre = v.reference_findings()
    blocking = [f for f in pre if f.severity == "blocking"]
    parse_errors = [f for f in pre if f.check == "parse" and f.severity == "blocking"]
    v.close()
    if parse_errors:
        return result(False, "the base snapshot does not parse cleanly: "
                             + "; ".join(str(f) for f in parse_errors[:3]))
    return result(True, f"13 devices parsed in {info['init_s']}s; the untouched network already "
                        f"has {len(pre)} findings ({len(blocking)} of them blocking) — these are "
                        f"subtracted from yours")


def phase_detects():
    """The verifier must reject a defect it is known to contain.

    This is the check that matters most. 'Batfish answered' and 'Batfish caught the bug' are two
    different facts, and only the second one means the acceptance test is doing any work."""
    v = Verifier(SNAP, host=BF_HOST)
    v.init_reference()
    cand, _ = apply_edits(base_cfg(), lines_of("wrong_direction.txt"))
    res = v.verify(cand, name="smoke-wrong-direction", intent=intents.get("enforce-egress"))
    v.close()
    reach = [f for f in res.blocking if f.check == "reachability"]
    if not res.blocking:
        return result(False, "the wrong-direction ACL was ACCEPTED. The verifier is running but "
                             "not detecting; every later result would be meaningless.")
    if not reach:
        return result(False, "rejected, but not for the right reason — no reachability finding. "
                             f"Got: {res.summary()}")
    return result(True, f"wrong-direction ACL rejected with {len(reach)} reachability findings "
                        f"(e.g. {reach[0].where[:60]})")


def phase_accepts():
    """And it must accept the correct change — a verifier that rejects everything is also useless."""
    v = Verifier(SNAP, host=BF_HOST)
    v.init_reference()
    cand, _ = apply_edits(base_cfg(), lines_of("correct_direction.txt"))
    res = v.verify(cand, name="smoke-correct-direction", intent=intents.get("enforce-egress"))
    v.close()
    if res.accepted:
        return result(True, f"correct-direction ACL accepted ({res.summary()})")
    return result(False, "the correct change was REJECTED — the check suite is too strict or an "
                         f"assertion is wrong: {'; '.join(str(f) for f in res.blocking[:3])}")


def phase_intent():
    """The second intent's reference solution must satisfy its assertions."""
    v = Verifier(SNAP, host=BF_HOST)
    v.init_reference()
    intent = intents.get("block-guest")
    before = v.verify(base_cfg(), name="smoke-untouched", intent=intent)
    cand, _ = apply_edits(base_cfg(), lines_of("reorder_out_acl.txt"))
    after = v.verify(cand, name="smoke-reordered", intent=intent)
    v.close()
    unmet = [f for f in before.findings if f.check == "intent"]
    if not unmet:
        return result(False, "the untouched network already satisfies block-guest — the intent "
                             "asserts nothing, so the task is not a task")
    still = [f for f in after.findings if f.check == "intent"]
    if still:
        return result(False, "the reference fix does not satisfy block-guest: "
                             + "; ".join(str(f) for f in still[:3]))
    return result(True, f"block-guest is unmet before the fix ({len(unmet)} assertion"
                        f"{'s' if len(unmet) != 1 else ''}) and met after it")


def phase_llm():
    """The model server is reachable and has the model."""
    host = os.environ.get("EE_LLM_HOST", DEFAULT_HOST)
    model = os.environ.get("EE_LLM_MODEL", DEFAULT_MODEL)
    cli = LLMClient(host=host, model=model, transcript=os.devnull, mode="live")
    ok, models = cli.available()
    if not ok:
        return result(False, f"cannot reach the LLM at {host}: {models[0]}\n"
                             "        the model is served on this machine — ask whether the "
                             "serving process is up")
    if not any(m.split(":")[0] == model.split(":")[0] for m in models):
        return result(False, f"the server has no model {model!r}; it has {', '.join(models)}")
    return result(True, f"{host} has {model} (of {len(models)} models)")


def phase_end_to_end():
    """One real iteration: model proposes, verifier answers, transcript records."""
    host = os.environ.get("EE_LLM_HOST", DEFAULT_HOST)
    model = os.environ.get("EE_LLM_MODEL", DEFAULT_MODEL)
    tpath = os.path.join("runs", "smoke-transcript.jsonl")
    os.makedirs("runs", exist_ok=True)
    if os.path.exists(tpath):
        os.remove(tpath)

    v = Verifier(SNAP, host=BF_HOST)
    v.init_reference()
    llm = LLMClient(host=host, model=model, transcript=tpath, mode="live",
                    budget=Budget(max_calls=2))
    t0 = time.time()
    payload, accepted = run(intents.get("enforce-egress"), v, llm, base_cfg(),
                            max_iters=1, verbose=False,
                            out_json=os.path.join("runs", "smoke-run.json"))
    v.close()
    took = time.time() - t0
    iters = payload["iterations"]
    if not iters:
        return result(False, f"the loop produced no iteration: {payload['outcome']}")
    it = iters[0]
    if "result" not in it:
        return result(False, f"iteration 1 never reached the verifier: {it.get('error')} "
                             f"({payload['outcome']})")
    b = payload["budget"]
    return result(True, f"one full iteration in {took:.0f}s — {b['calls']} call, "
                        f"{b['output_tokens']} output tokens, verdict "
                        f"'{it['result']['summary']}' "
                        f"(acceptance is not required here; reaching a verdict is)")


def phase_replay():
    """The recorded run reproduces with the server switched off."""
    tpath = os.path.join("runs", "smoke-transcript.jsonl")
    if not os.path.exists(tpath):
        return result(False, "no transcript from the previous phase")
    v = Verifier(SNAP, host=BF_HOST)
    v.init_reference()
    llm = LLMClient(host="http://127.0.0.1:1", model=os.environ.get("EE_LLM_MODEL", DEFAULT_MODEL),
                    transcript=tpath, mode="replay", budget=Budget(max_calls=0))
    payload, _ = run(intents.get("enforce-egress"), v, llm, base_cfg(),
                     max_iters=1, verbose=False)
    v.close()
    b = payload["budget"]
    if b["calls"] != 0:
        return result(False, f"replay made {b['calls']} live calls — it is not a replay")
    if b["replayed"] < 1:
        return result(False, f"replay used nothing from the transcript: {payload['outcome']}")
    return result(True, f"replayed {b['replayed']} call(s) with the server unreachable, "
                        f"0 tokens spent")


PHASES = {
    "service": phase_service, "reference": phase_reference, "detects": phase_detects,
    "accepts": phase_accepts, "intent": phase_intent, "llm": phase_llm,
    "end-to-end": phase_end_to_end, "replay": phase_replay,
}

if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in PHASES:
        print(f"usage: python3 -m netops.smoke [{' | '.join(PHASES)}]")
        sys.exit(2)
    try:
        sys.exit(PHASES[sys.argv[1]]())
    except Exception as e:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        sys.exit(result(False, f"{type(e).__name__}: {e}"))
