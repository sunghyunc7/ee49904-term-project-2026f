"""Offline checks — no Batfish, no LLM, no network.

These cover the part of the kit that turns a model's answer into a configuration file. It is the
part most likely to break when you edit prompts (because the shape of the answer changes) and the
part where a silent failure is most expensive: a merge that quietly drops a line makes the
verifier report a defect that the model never produced, and you will spend an evening blaming the
model.

Run:  python3 -m netops.selftest
"""
from __future__ import annotations

import os
import sys

from netops.cfg import apply_edits, parse_proposal
from netops.intents import ALL as ALL_INTENTS
from netops.llm import Budget, BudgetExceeded, LLMClient, ReplayMiss, _key, extract_config_lines
from netops.verify import Verifier, only_set

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE_CFG = os.path.join(HERE, "snapshot", "configs", "as2dept1.cfg")

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    mark = "\033[32mok\033[0m  " if cond else "\033[31mFAIL\033[0m"
    print(f"  {mark} {name}" + (f"\n        {detail}" if (detail and not cond) else ""))


def main():
    base = open(BASE_CFG, encoding="utf-8").read()
    print("netops selftest — merge rules, answer extraction, budget\n")

    # --- extraction ---------------------------------------------------------
    fenced = ("Sure! Here is the configuration:\n\n```\ninterface GigabitEthernet2/0\n"
              " ip access-group RESTRICT_HOST_TRAFFIC_OUT out\n```\nLet me know if…")
    got = extract_config_lines(fenced)
    check("fenced answer: prose dropped, config kept",
          got == ["interface GigabitEthernet2/0", " ip access-group RESTRICT_HOST_TRAFFIC_OUT out"],
          repr(got))

    bare = ("interface GigabitEthernet2/0\n ip access-group RESTRICT_HOST_TRAFFIC_OUT out\n"
            "This applies the ACL outbound.")
    got = extract_config_lines(bare)
    check("unfenced answer: trailing prose dropped",
          "interface GigabitEthernet2/0" in got and not any("applies" in g for g in got),
          repr(got))

    # --- interface merge ----------------------------------------------------
    new, rep = apply_edits(base, ["interface GigabitEthernet2/0",
                                  " ip access-group RESTRICT_HOST_TRAFFIC_OUT out"])
    check("interface merge keeps the address the model never saw",
          "ip address 2.128.0.1 255.255.255.0" in new)
    check("interface merge keeps the inbound filter",
          new.count("ip access-group RESTRICT_HOST_TRAFFIC_IN in") == 2)
    check("interface merge adds the outbound filter once",
          new.count("ip access-group RESTRICT_HOST_TRAFFIC_OUT out") == 1, repr(rep))
    check("untouched lines are byte-identical",
          [l for l in base.split("\n") if "hostname" in l] ==
          [l for l in new.split("\n") if "hostname" in l])

    same_dir, _ = apply_edits(base, ["interface GigabitEthernet2/0",
                                     " ip access-group OTHER_ACL in"])
    check("same direction replaces, does not stack",
          same_dir.count("ip access-group OTHER_ACL in") == 1
          and "RESTRICT_HOST_TRAFFIC_IN in" not in same_dir.split("interface GigabitEthernet3/0")[0]
          .split("interface GigabitEthernet2/0")[1])

    # --- ACL body replacement ----------------------------------------------
    acl = ["ip access-list extended RESTRICT_HOST_TRAFFIC_OUT",
           " deny   ip 1.128.0.0 0.0.255.255 2.128.0.0 0.0.255.255",
           " permit ip any 2.128.0.0 0.0.255.255",
           " deny   ip any any"]
    new2, rep2 = apply_edits(base, acl)
    body = new2.split("ip access-list extended RESTRICT_HOST_TRAFFIC_OUT")[1].split("\n!")[0]
    check("ACL body is replaced wholesale (order is the semantics)",
          body.strip().startswith("deny   ip 1.128.0.0") and body.count("permit ip any") == 1,
          repr(body))
    check("replacing one ACL leaves the other alone",
          "permit ip 2.128.0.0 0.0.255.255 any" in new2)

    # --- removal ------------------------------------------------------------
    new3, rep3 = apply_edits(base, ["interface GigabitEthernet2/0",
                                    " no ip access-group RESTRICT_HOST_TRAFFIC_IN in"])
    check("`no` removes the matching child",
          new3.count("ip access-group RESTRICT_HOST_TRAFFIC_IN in") == 1 and rep3["removed"],
          repr(rep3))

    # --- refusal rather than guessing --------------------------------------
    junk = ["I would recommend applying the ACL outbound.",
            "configure terminal",
            "interface GigabitEthernet2/0", " ip access-group RESTRICT_HOST_TRAFFIC_OUT out"]
    new4, rep4 = apply_edits(base, junk)
    check("prose and mode commands are refused, not guessed",
          len(rep4["unapplied"]) == 2 and "ip access-group RESTRICT_HOST_TRAFFIC_OUT out" in new4,
          repr(rep4["unapplied"]))

    # --- new section --------------------------------------------------------
    new5, rep5 = apply_edits(base, ["ip access-list extended GUEST_BLOCK",
                                    " deny ip 2.128.1.0 0.0.0.255 any", " permit ip any any"])
    check("an ACL the file did not have is added",
          "ip access-list extended GUEST_BLOCK" in new5 and rep5["sections_added"])
    check("the added section lands before ip forward-protocol",
          new5.index("GUEST_BLOCK") < new5.index("ip forward-protocol"))

    # --- idempotence --------------------------------------------------------
    once, _ = apply_edits(base, ["interface GigabitEthernet2/0",
                                 " ip access-group RESTRICT_HOST_TRAFFIC_OUT out"])
    twice, _ = apply_edits(once, ["interface GigabitEthernet2/0",
                                  " ip access-group RESTRICT_HOST_TRAFFIC_OUT out"])
    check("applying the same edit twice changes nothing the second time", once == twice)

    # --- Batfish argument building -------------------------------------------
    # An optional Batfish parameter that is absent is not the same as one that is null:
    # startLocation=None is rejected as a malformed location, which sends you looking for a
    # bad location that does not exist. Every assertion here omits `start`, so the question
    # must be built without that parameter at all.
    check("only_set drops what was not given",
          only_set(a=1, b=None, c="x") == {"a": 1, "c": "x"},
          repr(only_set(a=1, b=None, c="x")))
    check("only_set keeps falsy values that were given",
          only_set(a=0, b="", c=False) == {"a": 0, "b": "", "c": False})
    missing = [(i.key, a["name"]) for i in ALL_INTENTS.values() for a in i.assertions
               if only_set(nodes=a["node"], filters=a["filter"],
                           startLocation=a.get("start")).get("startLocation") is not None
               and not isinstance(a.get("start"), str)]
    check("no assertion would pass a non-string startLocation", not missing, repr(missing))
    check("every assertion has the keys the verifier reads",
          all(set(("name", "node", "filter", "src", "dst", "expect")) <= set(a)
              for i in ALL_INTENTS.values() for a in i.assertions))

    # --- budget and replay --------------------------------------------------
    b = Budget(max_calls=2)
    b.account({"output_tokens": 10, "wall_s": 1.0})
    b.account({"output_tokens": 10, "wall_s": 1.0})
    try:
        b.check()
        check("budget stops the loop at the ceiling", False, "no exception raised")
    except BudgetExceeded:
        check("budget stops the loop at the ceiling", True)

    import tempfile
    with tempfile.TemporaryDirectory() as d:
        t = os.path.join(d, "t.jsonl")
        cli = LLMClient(transcript=t, mode="replay")
        try:
            cli.complete("never recorded")
            check("replay miss is fatal, not a silent live call", False)
        except ReplayMiss:
            check("replay miss is fatal, not a silent live call", True)

    # --- sampling and concurrency --------------------------------------------
    # At temperature 0 the key must not depend on the knob (a deterministic call is the same
    # call whether or not anyone mentioned temperature); above 0 it must, or a sample of five
    # draws would replay as one answer five times.
    k0 = _key("m", "s", "p", 768, 1)
    check("temperature 0 leaves the replay key unchanged", _key("m", "s", "p", 768, 1, 0.0) == k0)
    check("a non-zero temperature is part of the replay key",
          _key("m", "s", "p", 768, 1, 0.7) != k0
          and _key("m", "s", "p", 768, 1, 0.7) != _key("m", "s", "p", 768, 2, 0.7))

    # Batfish overwrites snapshots by name. Two processes under one account must therefore
    # never produce the same name, or each can be handed the other's verdict.
    va, vb = Verifier.__new__(Verifier), Verifier.__new__(Verifier)
    va._run, vb._run = "aaa", "bbb"
    check("two processes never share a snapshot name",
          va._snap("cand-x-1") != vb._snap("cand-x-1")
          and va._snap("reference").startswith("reference-"))

    print(f"\n  {len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("  failed: " + ", ".join(FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
