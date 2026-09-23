"""The loop: propose → verify → repair, with the verifier holding the pen.

The only authority to accept a change belongs to the verifier. The model proposes; if anything
blocking comes back, the findings are handed to the model and it tries again; when the budget or
the iteration limit runs out, the change is **not** applied. That last clause is the design: a
loop that gives up is safe, a loop that gives up *quietly* is not, so every run writes a record
of what it tried and why it stopped.

**You edit `propose_prompt`, `repair_prompt`, and `format_findings`.** Everything else — snapshot
handling, budget accounting, the run log — is done. The shipped prompts are the least clever
thing that works; that is on purpose, so that whatever you do to them shows up as a measurable
difference rather than as noise on top of something already tuned.

`format_findings` deserves a second look before you decide it is plumbing. It decides how much of
the verifier's output the model gets to see, and that single choice moves the convergence rate
more than most prompt wording does (extension A in the brief asks you to measure it).
"""
from __future__ import annotations

import json
import os
import time

from netops.cfg import apply_edits, unified_diff
from netops.llm import BudgetExceeded, ReplayMiss, extract_config_lines

SYSTEM = (
    "You are a network engineer editing Cisco IOS configuration. "
    "Answer with configuration lines only, exactly as they would be typed in configuration mode, "
    "inside a single fenced code block. No explanation, no 'configure terminal', no 'end'. "
    "Change as little as possible: emit only the sections you are modifying."
)


def propose_prompt(intent, device_name, device_cfg):
    return (
        f"Here is the running configuration of {device_name}:\n\n"
        f"```\n{device_cfg}\n```\n\n"
        f"Task:\n{intent.request}\n\n"
        "Emit the configuration lines that implement this task."
    )


def format_findings(findings, max_items=8):
    """What the model is told about its own failure.

    Blocking findings first, most specific first, truncated. Truncation is a choice with a cost —
    see extension A in the brief."""
    if not findings:
        return "(no findings)"
    order = {"intent": 0, "acl_lines": 1, "refs": 2, "parse": 3, "reachability": 4, "bgp": 5}
    blocking = sorted([f for f in findings if f.severity == "blocking"],
                      key=lambda f: order.get(f.check, 9))
    lines = []
    for f in blocking[:max_items]:
        lines.append(f"- [{f.check}] {f.where}: {f.detail}")
    if len(blocking) > max_items:
        lines.append(f"- … and {len(blocking) - max_items} more findings of the same kind")
    return "\n".join(lines)


def repair_prompt(intent, device_name, device_cfg, findings):
    return (
        f"Here is the running configuration of {device_name} after your change:\n\n"
        f"```\n{device_cfg}\n```\n\n"
        f"The original task was:\n{intent.request}\n\n"
        f"A formal verifier rejected this configuration. Its findings:\n\n"
        f"{format_findings(findings)}\n\n"
        "Emit corrected configuration lines. Address every finding. "
        "Remember that in an access list the order of the lines decides which lines can ever match."
    )


class RunLog:
    def __init__(self, intent, model, mode):
        self.intent = intent.key
        self.model = model
        self.mode = mode
        self.iterations = []
        self.started = time.strftime("%Y-%m-%dT%H:%M:%S")
        self.outcome = "not run"
        self.wall_s = 0.0

    def add(self, **kw):
        self.iterations.append(kw)

    def as_dict(self, budget=None):
        return {"intent": self.intent, "model": self.model, "mode": self.mode,
                "started": self.started, "outcome": self.outcome,
                "wall_s": round(self.wall_s, 1),
                "iterations": self.iterations,
                "budget": budget.as_dict() if budget else None}

    def write(self, path):
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self._payload, f, indent=2, ensure_ascii=False)

    def finish(self, budget, path=None):
        self._payload = self.as_dict(budget)
        if path:
            self.write(path)
        return self._payload


# Tokens the model may emit per call — reasoning included, for a model that reasons before it
# answers. The value is measured, not chosen: on the course server gpt-oss answered the second
# intent in 619 tokens six times running, and on another occasion was still reasoning at 2,048.
# At 768 (the value this kit first shipped with) it never finished that intent at all. 4096 plus the
# longest repair prompt still fits the server's 8192-token context. It is part of the replay key,
# so changing it means recording again.
NUM_PREDICT = 4096


def run(intent, verifier, llm, base_cfg, device="as2dept1", max_iters=4,
        proposal_lines=None, verbose=True, out_json=None, num_predict=NUM_PREDICT):
    """One experiment. Returns (RunLog payload, accepted_cfg_or_None).

    Whatever happens inside — a timeout talking to the model, an HTTP error from the server, a
    Ctrl-C — the run log is written and the verifier's snapshots are deleted before the exception
    leaves this function. Both matter to you directly: the log is the only record of what the run
    did, and snapshots that are never removed pile up in the verifier you keep using."""
    log = RunLog(intent, llm.model if llm else "(scripted)", "scripted" if proposal_lines else "llm")
    t_start = time.time()
    cfg = base_cfg
    accepted = None
    findings = []

    try:
        for i in range(1, max_iters + 1):
            t0 = time.time()
            # ---- propose -------------------------------------------------------
            if proposal_lines is not None:
                lines, meta = list(proposal_lines), {"scripted": True}
                proposal_lines = None       # scripted mode gets exactly one shot
                if i > 1:
                    break
            else:
                prompt = (propose_prompt(intent, device, cfg) if i == 1
                          else repair_prompt(intent, device, cfg, findings))
                try:
                    text, meta = llm.complete(prompt, system=SYSTEM, num_predict=num_predict,
                                              tag=f"{intent.key}/iter{i}")
                except (BudgetExceeded, ReplayMiss) as e:
                    log.outcome = f"stopped: {type(e).__name__}"
                    if verbose:
                        print(f"  iter {i}: {e}")
                    break
                lines = extract_config_lines(text)

                if meta.get("done_reason") == "length":
                    # The answer ends where the allowance ended, not where the model would have. Its
                    # last line is usually cut mid-word, and a cut line is not the model's proposal:
                    # applied, it makes the verifier report defects the model never wrote (half an
                    # ACL line replaces the ACL body and derails the parser for the rest of the file —
                    # measured: a truncated answer came back as "2 undefined route-maps"). So a
                    # truncated answer is recorded and NOT applied. Asking again at temperature 0
                    # would spend the same allowance the same way, so the loop stops.
                    if lines:
                        why = (f"answer truncated at the token limit (num_predict={num_predict}) after "
                               f"{len(lines)} configuration line(s) — not applied. Last line: "
                               f"{lines[-1].strip()[:60]!r}. Raise --num-predict.")
                    else:
                        why = (f"answer truncated at the token limit (num_predict={num_predict}) before "
                               f"any configuration appeared ({meta.get('thinking_chars', 0)} characters "
                               "of reasoning came first). Raise --num-predict.")
                    log.add(iter=i, error=why, proposed_lines=lines, meta=meta)
                    log.outcome = "stopped: answer truncated"
                    if verbose:
                        print(f"  iter {i}: {why}")
                    break

            if not lines:
                why = "model produced no configuration lines"
                log.add(iter=i, error=why, meta=meta)
                log.outcome = "stopped: empty proposal"
                if verbose:
                    print(f"  iter {i}: {why}")
                break

            # ---- apply ---------------------------------------------------------
            candidate, report = apply_edits(cfg, lines)
            if candidate == cfg:
                # Same keys as an applied iteration (`proposed_lines`, `apply_report`), so that a count
                # of `unapplied` lines over a set of run logs does not skip the runs where the merge
                # refused everything — which are exactly the runs in which it matters most.
                log.add(iter=i, error="proposal changed nothing", proposed_lines=lines,
                        apply_report=report, meta=meta)
                log.outcome = "stopped: proposal changed nothing"
                if verbose:
                    print(f"  iter {i}: the proposal did not change the configuration")
                break

            # ---- verify --------------------------------------------------------
            res = verifier.verify(candidate, name=f"cand-{intent.key}-{i}", intent=intent, node=device)
            findings = res.findings
            log.add(iter=i, proposed_lines=lines, apply_report=report,
                    diff=unified_diff(cfg, candidate, f"{device}.cfg"),
                    result=res.as_dict(), meta=meta, iter_s=round(time.time() - t0, 1))

            if verbose:
                print(f"  iter {i}: {res.summary()}   "
                      f"({meta.get('output_tokens', 0)} tok, {round(time.time() - t0, 1)}s)")
                for f in res.blocking[:5]:
                    print(f"        {f}")

            if res.accepted:
                accepted = candidate
                log.outcome = f"accepted at iteration {i}"
                break
            cfg = candidate     # repair from the rejected candidate, not from scratch
        else:
            log.outcome = f"not accepted within {max_iters} iterations"

        if log.outcome == "not run":
            log.outcome = log.outcome if accepted else (log.outcome or "stopped")
    except BaseException as e:                                      # noqa: BLE001
        # Anything the two handled cases above do not cover: a socket timeout, an HTTP 500 from
        # the model server, a bug in your own code, Ctrl-C. Record why the run ended, then let it
        # through — the finally block below still writes the log and frees the snapshots.
        log.outcome = f"stopped: {type(e).__name__}: {e}"[:300]
        raise
    finally:
        log.wall_s = time.time() - t_start
        try:
            verifier.close()          # snapshots first: they are the shared resource
        except Exception:             # noqa: BLE001
            pass
        payload = log.finish(llm.budget if llm else None, out_json)
    return payload, accepted
