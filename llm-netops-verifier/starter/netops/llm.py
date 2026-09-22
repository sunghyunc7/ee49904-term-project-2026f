"""LLM client for the NetOps loop — deterministic, budgeted, and replayable.

Three properties matter here, and none of them is about prompt quality:

1. **Deterministic by default** (temperature 0, fixed seed). Two runs of the same experiment
   must differ because you changed something, not because the sampler did. At temperature 0
   decoding is greedy and the seed changes nothing; `temperature` is a parameter for the day
   you need a sample rather than a point (`run_loop.py --temperature`). Greedy is not
   bit-exact on a shared GPU server: what the server happens to have cached can move a long
   answer onto another path. That is rare, and it is one more reason for property 3.
2. **Budgeted.** The shared server has eight slots for the whole class. A loop with a bug can
   emit a thousand calls before you notice; this refuses to.
3. **Recorded, therefore replayable.** Every call is appended to a transcript. `--replay` reruns
   the same experiment out of that file with the server switched off. Use it whenever you are
   debugging the *verifier* side of the loop rather than the *generation* side — which is most
   of the time, and costs the class nothing.

Only the standard library is used, so this file has no install step of its own.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.error
import urllib.request

DEFAULT_HOST = os.environ.get("EE_LLM_HOST", "http://localhost:11434")
DEFAULT_MODEL = os.environ.get("EE_LLM_MODEL", "gpt-oss")


class BudgetExceeded(RuntimeError):
    pass


class ReplayMiss(RuntimeError):
    """Asked to replay a call that is not in the transcript.

    This is deliberately fatal. Silently falling through to the live server would make a
    'replay' run quietly cost tokens and quietly stop being reproducible."""


class Budget:
    """A call and token ceiling for one run.

    Measured on the department's shared server (8 slots): a 512-token completion takes about
    4 s alone and about 12 s when eight teams are generating at once. A loop that needs 40
    calls is a 3-to-8-minute loop by itself; one that needs 400 is an hour and it is not a
    better loop."""

    def __init__(self, max_calls=40, max_output_tokens=40_000):
        self.max_calls = max_calls
        self.max_output_tokens = max_output_tokens
        self.calls = 0
        self.output_tokens = 0
        self.input_tokens = 0
        self.seconds = 0.0
        self.replayed = 0

    def check(self):
        if self.calls >= self.max_calls:
            raise BudgetExceeded(
                f"call budget exhausted ({self.calls}/{self.max_calls}). "
                "Raise --budget-calls only if you can say what the extra calls are for.")
        if self.output_tokens >= self.max_output_tokens:
            raise BudgetExceeded(
                f"token budget exhausted ({self.output_tokens}/{self.max_output_tokens})")

    def account(self, meta, replayed=False):
        if replayed:
            self.replayed += 1
            return
        self.calls += 1
        self.output_tokens += meta.get("output_tokens", 0)
        self.input_tokens += meta.get("input_tokens", 0)
        self.seconds += meta.get("wall_s", 0.0)

    def as_dict(self):
        return {"calls": self.calls, "replayed": self.replayed,
                "output_tokens": self.output_tokens, "input_tokens": self.input_tokens,
                "seconds": round(self.seconds, 1)}

    def __str__(self):
        d = self.as_dict()
        return (f"{d['calls']}/{self.max_calls} calls, {d['output_tokens']} out-tok, "
                f"{d['seconds']}s" + (f", {d['replayed']} replayed" if d['replayed'] else ""))


def _key(model, system, prompt, num_predict, seed, temperature=0.0):
    parts = [model, system or "", prompt, str(num_predict), str(seed)]
    if temperature:
        # Left out at 0 so that the key of a deterministic call does not depend on this knob.
        parts.append(f"temperature={temperature:g}")
    h = hashlib.sha256()
    for part in parts:
        h.update(part.encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()[:16]


class LLMClient:
    """mode='live' calls the server and records; mode='replay' reads the transcript only."""

    def __init__(self, host=DEFAULT_HOST, model=DEFAULT_MODEL, transcript="transcript.jsonl",
                 mode="live", seed=1234, budget=None, timeout=600, temperature=0.0):
        self.host = host if host.startswith("http") else "http://" + host
        self.model = model
        self.transcript = transcript
        self.mode = mode
        self.seed = seed
        self.budget = budget or Budget()
        self.timeout = timeout
        self.temperature = float(temperature)
        self._cache = {}
        if os.path.exists(transcript):
            self._load()

    # ---------------------------------------------------------------- transcript

    def _load(self):
        with open(self.transcript, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if "key" in rec:
                    self._cache[rec["key"]] = rec

    def _append(self, rec):
        with open(self.transcript, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        self._cache[rec["key"]] = rec

    # ---------------------------------------------------------------- the call

    def complete(self, prompt, system=None, num_predict=512, tag=""):
        """Returns (text, meta). Identical arguments return the identical recorded answer."""
        key = _key(self.model, system, prompt, num_predict, self.seed, self.temperature)

        if key in self._cache:
            rec = self._cache[key]
            self.budget.account(rec.get("meta", {}), replayed=True)
            return rec["response"], dict(rec.get("meta", {}), replayed=True)

        if self.mode == "replay":
            raise ReplayMiss(
                f"no recorded answer for this prompt (key {key}, tag={tag!r}).\n"
                "The prompt — or the model, seed, or temperature — differs from anything in the\n"
                "transcript, so replay cannot cover it. Run once without --replay to record it,\n"
                "or restore the earlier prompt and settings.")

        self.budget.check()
        payload = {
            "model": self.model, "prompt": prompt, "stream": False,
            "options": {"temperature": self.temperature, "seed": self.seed,
                        "num_predict": num_predict},
        }
        if system:
            payload["system"] = system

        t0 = time.time()
        req = urllib.request.Request(
            self.host.rstrip("/") + "/api/generate",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                d = json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            # The body carries the real reason; the status code alone never does.
            try:
                body = e.read().decode("utf-8", "replace")
                body = json.loads(body).get("error", body)
            except Exception:
                body = "(no body)"
            raise RuntimeError(f"LLM HTTP {e.code}: {body[:300]}") from None
        except urllib.error.URLError as e:
            raise RuntimeError(
                f"cannot reach the LLM at {self.host}: {e.reason}.\n"
                "The model is served on the assigned server itself, so this usually means the "
                "serving process is down — after a reboot it has to be started again. Ask on "
                "the KLMS Q&A board or your TA rather than starting one yourself.") from None

        wall = time.time() - t0
        # A reasoning model (gpt-oss) thinks before it answers. The thinking arrives in its own
        # field, is counted in eval_count, and is spent out of the same num_predict allowance —
        # so an empty `response` with done_reason "length" means the allowance ran out mid-thought.
        thinking = d.get("thinking") or ""
        meta = {
            "model": self.model,
            "temperature": self.temperature,
            "done_reason": d.get("done_reason") or "",
            "thinking_chars": len(thinking),
            "output_tokens": d.get("eval_count") or 0,
            "input_tokens": d.get("prompt_eval_count") or 0,
            "gen_s": round((d.get("eval_duration") or 0) / 1e9, 2),
            "prefill_s": round((d.get("prompt_eval_duration") or 0) / 1e9, 2),
            "wall_s": round(wall, 2),
        }
        self.budget.account(meta)
        self._append({"key": key, "tag": tag, "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                      "model": self.model, "seed": self.seed, "num_predict": num_predict,
                      "temperature": self.temperature,
                      "system": system, "prompt": prompt,
                      "response": d.get("response", ""), "thinking": thinking, "meta": meta})
        return d.get("response", ""), meta

    # ---------------------------------------------------------------- helpers

    def available(self):
        """(reachable, [model names]) — never raises."""
        try:
            with urllib.request.urlopen(self.host.rstrip("/") + "/api/tags", timeout=15) as r:
                tags = json.loads(r.read().decode())
            return True, [m["name"] for m in tags.get("models", [])]
        except Exception as e:  # noqa: BLE001
            return False, [f"{type(e).__name__}: {e}"]


# -------------------------------------------------------------------- extraction

def extract_config_lines(text):
    """Pull configuration out of an LLM answer.

    Models wrap configuration in prose, in ```code fences```, in numbered lists, or in nothing at
    all, and the same model does different things on different days. Everything this function
    throws away is a place where a real deployment pipeline would need a human, so keep an eye on
    how often it has to work hard — Q1 asks you to quantify exactly that."""
    lines = text.replace("\r\n", "\n").split("\n")
    out, in_fence, seen_fence = [], False, False
    for raw in lines:
        s = raw.rstrip()
        if s.strip().startswith("```"):
            in_fence = not in_fence
            seen_fence = seen_fence or in_fence
            continue
        if in_fence:
            out.append(s)
    if not seen_fence:
        # No fence: keep lines that look like IOS configuration and drop the prose.
        keep = ("interface ", "ip access-list", "ip access-group", "permit ", "deny ",
                "no ip access-group", "access-list ", "router ", "neighbor ", "network ",
                "exit", "!", " ")
        for raw in lines:
            s = raw.rstrip()
            if not s.strip():
                continue
            if s.startswith(" ") or s.strip().lower().startswith(keep):
                out.append(s)
    # Drop leading/trailing blanks and stray markdown bullets.
    cleaned = []
    for s in out:
        if s.strip().startswith(("- ", "* ", "#")):
            continue
        cleaned.append(s)
    while cleaned and not cleaned[0].strip():
        cleaned.pop(0)
    while cleaned and not cleaned[-1].strip():
        cleaned.pop()
    return cleaned
