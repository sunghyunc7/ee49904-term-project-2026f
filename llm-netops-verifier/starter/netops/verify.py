"""The verifier — Batfish as the acceptance test.

Batfish reads configuration files and computes what the network *would* do: which ACL lines can
ever match, which references point at nothing, which BGP sessions would come up, which flows
would be delivered. It never touches the network and never sends a packet. That is the whole
point, and it is also the whole limitation — Q2 in the brief is about the second half of that
sentence.

Six checks run against every candidate. Three are **structural** and are reported as a *delta*
against the reference snapshot; three are **behavioural** and are absolute.

| check | question | delta? | why |
|---|---|---|---|
| `parse` | `initIssues` | delta | the base snapshot has its own warnings; only new ones are yours |
| `refs` | `undefinedReferences` | delta | same reason |
| `acl_lines` | `filterLineReachability` | delta | **the base snapshot already contains unreachable lines** |
| `reachability` | `differentialReachability` | inherently differential | did you break a flow that used to work |
| `bgp` | `bgpSessionStatus` | delta | did you break an adjacency |
| `intent` | `testFilters` | absolute | does the filter do what the intent asked |

The delta rule is not a convenience. A verifier that reports the network's pre-existing sins on
every run trains you to skim its output, and skimming is how the one line that *was* yours gets
missed. (The same failure mode as an alarm that is always on.)
"""
from __future__ import annotations

import itertools
import os
import shutil
import tempfile
import time

BASE_NODE = "as2dept1"
_INSTANCE = itertools.count()      # two Verifiers in one process must not share a tag either


def venv_problem():
    """Return an actionable message if pybatfish is missing, else None.

    A missing venv and a stopped Batfish produce the same symptom — "the verifier does not
    work" — and opposite fixes. Naming the two apart here keeps every entry point from
    guessing, and keeps students from restarting a container that was never the problem."""
    try:
        import pybatfish  # noqa: F401
    except ImportError as e:
        root = os.environ.get("EE_ROOT", os.path.join(os.path.expanduser("~"), "ee49904-netops"))
        return (f"pybatfish is not importable ({e}) — the virtual environment is not active.\n"
                f"This is not a Batfish problem. Run:\n"
                f"    source {root}/env.sh\n"
                f"(setup.sh writes that file; every new shell needs it.)")
    return None


def only_set(**kw):
    """Drop the arguments that were not given.

    Batfish's parameters are typed, and an optional one that is *absent* is not the same as one
    that is present and null: `startLocation=None` comes back as
    `Expected type: 'locationSpec' … Got error: 'A Batfish locationSpec must be a string'`,
    which reads like a bad location rather than a missing one. Passing only what was set keeps
    optional parameters optional."""
    return {k: v for k, v in kw.items() if v is not None}


class Finding:
    __slots__ = ("check", "severity", "where", "detail")

    def __init__(self, check, severity, where, detail):
        self.check = check          # parse | refs | acl_lines | reachability | bgp | intent
        self.severity = severity    # blocking | warning
        self.where = where
        self.detail = detail

    def signature(self):
        return f"{self.check}|{self.where}|{self.detail}"

    def __repr__(self):
        return f"[{self.severity}] {self.check}: {self.where} — {self.detail}"

    def as_dict(self):
        return {"check": self.check, "severity": self.severity,
                "where": self.where, "detail": self.detail}


class VerifyResult:
    def __init__(self, findings, timings, snapshot):
        self.findings = findings
        self.timings = timings
        self.snapshot = snapshot

    @property
    def blocking(self):
        return [f for f in self.findings if f.severity == "blocking"]

    @property
    def accepted(self):
        return not self.blocking

    def summary(self):
        if self.accepted:
            warn = len(self.findings)
            return "ACCEPTED" + (f" ({warn} warning{'s' if warn != 1 else ''})" if warn else "")
        by = {}
        for f in self.blocking:
            by[f.check] = by.get(f.check, 0) + 1
        return "REJECTED — " + ", ".join(f"{k}:{v}" for k, v in sorted(by.items()))

    def as_dict(self):
        return {"accepted": self.accepted, "summary": self.summary(),
                "findings": [f.as_dict() for f in self.findings], "timings": self.timings}


class Verifier:
    """Wraps one Batfish session and one reference snapshot."""

    # `network` names the workspace Batfish files your snapshots under, and init_snapshot()
    # overwrites by name. On a shared Batfish — one instance serving several teams on the same
    # server — a fixed name would let one team's snapshot silently replace another's, so the
    # default is per-user. EE_BF_NETWORK overrides it; setup.sh writes one into env.sh.
    def __init__(self, base_dir, host=None, network=None, quiet=True):
        problem = venv_problem()
        if problem:
            raise RuntimeError(problem)
        from pybatfish.client.session import Session  # imported late: needs the venv
        self.host = host or os.environ.get("EE_BF_HOST", "localhost")
        self.base_dir = os.path.abspath(base_dir)
        self.bf = Session(host=self.host)
        self.bf.set_network(network or os.environ.get(
            "EE_BF_NETWORK", "ee49904-" + (os.environ.get("USER") or "anon")))
        self.reference = None
        self._ref_signatures = set()
        self._tmp = tempfile.mkdtemp(prefix="ee49904-snap-")
        # Snapshot names are unique to this process. init_snapshot() overwrites by name, so two
        # runs under one account — your own concurrent instances, or a teammate on a shared
        # account — would otherwise swap candidates, and neither run would be told.
        self._run = f"{os.getpid():x}{int(time.time()) & 0xfffff:05x}{next(_INSTANCE):x}"
        self._created = []
        if quiet:
            import logging
            logging.getLogger("pybatfish").setLevel(logging.ERROR)

    # ------------------------------------------------------------------ setup

    def _snap(self, name):
        """The name Batfish files a snapshot under: the caller's name plus this process's tag."""
        return f"{name}-{self._run}"

    def init_reference(self, name="reference"):
        """Load the untouched network and remember its pre-existing findings."""
        name = self._snap(name)
        t0 = time.time()
        self.bf.init_snapshot(self.base_dir, name=name, overwrite=True)
        self._created.append(name)
        self.reference = name
        took = time.time() - t0
        self._ref_signatures = {f.signature() for f in self._structural(name)}
        return {"snapshot": name, "init_s": round(took, 1),
                "pre_existing_findings": len(self._ref_signatures)}

    def reference_findings(self):
        """The base network's own findings — worth reading once, before you blame the model."""
        return self._structural(self.reference)

    # ------------------------------------------------------------ candidates

    def make_candidate(self, node_cfg_text, name, node=BASE_NODE):
        """Write a full snapshot with one device file replaced."""
        d = os.path.join(self._tmp, name)
        if os.path.exists(d):
            shutil.rmtree(d)
        shutil.copytree(self.base_dir, d, ignore=shutil.ignore_patterns("NOTICE.md"))
        with open(os.path.join(d, "configs", f"{node}.cfg"), "w", encoding="utf-8") as f:
            f.write(node_cfg_text)
        return d

    def verify(self, node_cfg_text, name, intent=None, node=BASE_NODE):
        name = self._snap(name)
        d = self.make_candidate(node_cfg_text, name, node)
        timings = {}
        t0 = time.time()
        self.bf.init_snapshot(d, name=name, overwrite=True)
        if name not in self._created:
            self._created.append(name)
        timings["init_s"] = round(time.time() - t0, 1)

        findings = []
        t0 = time.time()
        for f in self._structural(name):
            if f.signature() not in self._ref_signatures:
                findings.append(f)
        timings["structural_s"] = round(time.time() - t0, 1)

        t0 = time.time()
        findings.extend(self._reachability(name))
        timings["reachability_s"] = round(time.time() - t0, 1)

        if intent is not None:
            t0 = time.time()
            findings.extend(self._intent(name, intent))
            timings["intent_s"] = round(time.time() - t0, 1)

        return VerifyResult(findings, timings, name)

    # -------------------------------------------------------------- the checks

    def _frame(self, question, **kw):
        try:
            return question.answer(**kw).frame()
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(f"Batfish question failed: {type(e).__name__}: {e}") from None

    def _structural(self, snapshot):
        bf, out = self.bf, []

        # initIssues Type is one of: "Parse error" · "Parse status" · "Parse warning[...]" ·
        # "Convert error" · "Convert warning [...]". Errors block; warnings do not — a config
        # that merely uses a feature Batfish has not implemented is still a config you can ship,
        # and treating that as a rejection would teach the loop to avoid legal syntax.
        df = self._frame(bf.q.initIssues(), snapshot=snapshot)
        for _, r in df.iterrows():
            issue_type = str(r.get("Type", ""))
            sev = "blocking" if "error" in issue_type.lower() else "warning"
            out.append(Finding("parse", sev,
                               f"{r.get('Nodes', '?')} {r.get('Source_Lines', '')}".strip()[:60],
                               f"{issue_type}: {str(r.get('Details', ''))[:140]}"))

        df = self._frame(bf.q.undefinedReferences(), snapshot=snapshot)
        for _, r in df.iterrows():
            out.append(Finding("refs", "blocking", f"{r.get('File_Name')}:{r.get('Lines')}",
                               f"{r.get('Struct_Type')} {r.get('Ref_Name')} used by "
                               f"{r.get('Context')}"))

        df = self._frame(bf.q.filterLineReachability(nodes=BASE_NODE), snapshot=snapshot)
        for _, r in df.iterrows():
            out.append(Finding("acl_lines", "blocking",
                               f"{r.get('Sources')}",
                               f"line {r.get('Unreachable_Line')} can never match "
                               f"({r.get('Reason')})"))

        df = self._frame(bf.q.bgpSessionStatus(), snapshot=snapshot)
        for _, r in df.iterrows():
            if str(r.get("Established_Status", "")).upper() not in ("ESTABLISHED", "UNIQUE_MATCH"):
                out.append(Finding("bgp", "blocking",
                                   f"{r.get('Node')} → {r.get('Remote_Node')}",
                                   str(r.get("Established_Status"))))
        return out

    def _reachability(self, snapshot):
        """Flows that succeeded in the reference and do not in the candidate.

        This is the check that catches the mistake a human reviewer makes: a filter that is
        correct about what it blocks and wrong about what it lets through."""
        df = self._frame(self.bf.q.differentialReachability(),
                         snapshot=snapshot, reference_snapshot=self.reference)
        out = []
        for _, r in df.iterrows():
            flow = r.get("Flow")
            out.append(Finding("reachability", "blocking", str(flow)[:110],
                               "worked in the reference network, does not here"))
        return out[:25]   # a broken filter can produce hundreds; the first 25 make the point

    def _intent(self, snapshot, intent):
        """Does the filter treat the intent's example flows the way the intent says?"""
        from pybatfish.datamodel.flow import HeaderConstraints
        out = []
        for a in intent.assertions:
            headers = HeaderConstraints(**only_set(
                srcIps=a["src"], dstIps=a["dst"],
                ipProtocols=a.get("protocols"), dstPorts=a.get("dst_ports")))
            df = self._frame(
                self.bf.q.testFilters(**only_set(
                    nodes=a["node"], filters=a["filter"], headers=headers,
                    startLocation=a.get("start"))),
                snapshot=snapshot)
            if df.empty:
                out.append(Finding("intent", "blocking", a["name"],
                                   f"filter {a['filter']} not found on {a['node']} — "
                                   "the intent cannot be evaluated"))
                continue
            action = str(df.iloc[0].get("Action", "")).upper()
            if action != a["expect"].upper():
                out.append(Finding("intent", "blocking", a["name"],
                                   f"{a['filter']} {action}s this flow; the intent requires "
                                   f"{a['expect']}  ({a['src']} → {a['dst']})"))
        return out

    def close(self):
        # Unique names do not overwrite each other, so they have to be removed instead. Best
        # effort: a snapshot left behind costs disk on a shared Batfish, not correctness.
        for name in self._created:
            try:
                self.bf.delete_snapshot(name)
            except Exception:  # noqa: BLE001
                pass
        self._created = []
        shutil.rmtree(self._tmp, ignore_errors=True)
