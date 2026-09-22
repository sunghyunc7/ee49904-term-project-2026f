"""Applying proposed configuration lines to a device file.

A model does not hand you a file; it hands you the lines an operator would type. Something has to
turn those into a configuration that a parser will accept, and that something makes choices. The
choices are written down here, in one place, because **they are part of what you are evaluating** —
a generous merge hides model errors, a strict one invents them.

The rules:

* Lines with no leading whitespace open a **section** (`interface GigabitEthernet2/0`,
  `ip access-list extended FOO`). Indented lines belong to the open section.
* For an **interface** section that already exists, proposed children are **merged**: a child
  replaces an existing child with the same head (`ip access-group ... in` replaces
  `ip access-group ... in`, but not `... out`), otherwise it is appended. Merging is right here
  because the model is not shown the whole interface and must not be able to delete an address it
  never saw.
* For an **ACL** section (`ip access-list ...`), proposed children **replace the whole body**.
  Merging would be meaningless: in an ACL the order of the lines *is* the semantics, and a rule
  appended after `deny ip any any` is a rule that never runs.
* `no <something>` inside a section removes matching children.
* A section the base file does not have is appended before the first `ip forward-protocol` line,
  or at the end.
* Anything else is **refused, not guessed** — it comes back in `report["unapplied"]`, and a high
  unapplied count is a finding about the model, not a bug in this file.
"""
from __future__ import annotations

import re

INTERFACE_RE = re.compile(r"^interface\s+\S+", re.I)
ACL_RE = re.compile(r"^ip\s+access-list\s+", re.I)
SECTION_RE = re.compile(r"^(interface|ip access-list|router|route-map|line|class-map|policy-map)\b", re.I)


def _head(child_line, words=3):
    """The part of a child line that identifies 'the same setting'.

    `ip access-group X in` and `ip access-group Y in` are the same setting with different values;
    `ip access-group X in` and `ip access-group X out` are two different settings, because IOS
    applies one filter per direction."""
    t = child_line.strip().split()
    if not t:
        return ""
    low = [w.lower() for w in t]
    if low[:2] == ["ip", "access-group"]:
        direction = low[-1] if low[-1] in ("in", "out") else ""
        return f"ip access-group {direction}"
    if low[:2] == ["ip", "address"]:
        return "ip address"
    return " ".join(low[:words])


def split_sections(text):
    """[(header or None, [lines])] preserving order and the file's own formatting."""
    out, cur = [], (None, [])
    for line in text.replace("\r\n", "\n").split("\n"):
        if line and not line[0].isspace() and SECTION_RE.match(line):
            out.append(cur)
            cur = (line, [])
        elif cur[0] is not None and (line.startswith(" ") or line.startswith("\t")):
            cur[1].append(line)
        else:
            if cur[0] is not None:
                out.append(cur)
                cur = (None, [])
            cur[1].append(line)
    out.append(cur)
    return [(h, ls) for h, ls in out if h is not None or ls]


def parse_proposal(lines):
    """Group proposed lines into {section header: [children]} plus loose lines."""
    sections, loose, cur = [], [], None
    for raw in lines:
        s = raw.rstrip()
        if not s.strip() or s.strip() == "!":
            continue
        if s.startswith(" ") or s.startswith("\t"):
            if cur is None:
                loose.append(s.strip())
            else:
                cur[1].append(s.rstrip())
            continue
        if SECTION_RE.match(s):
            cur = (s.strip(), [])
            sections.append(cur)
        elif s.strip().lower() in ("exit", "end"):
            cur = None
        elif cur is not None and s.strip().lower().startswith(("permit ", "deny ", "remark ")):
            # ACL bodies are frequently emitted flush-left.
            cur[1].append(" " + s.strip())
        else:
            cur = None
            loose.append(s.strip())
    return sections, loose


def apply_edits(base_text, proposed_lines):
    """Returns (new_text, report). Never raises on bad input — it reports instead."""
    report = {"sections_changed": [], "sections_added": [], "unapplied": [], "removed": []}
    sections, loose = parse_proposal(proposed_lines)
    report["unapplied"].extend(loose)

    base = split_sections(base_text)
    index = {h.strip().lower(): i for i, (h, _) in enumerate(base) if h}

    for header, children in sections:
        key = header.strip().lower()
        if not children:
            report["unapplied"].append(header + "   (no body)")
            continue

        if key in index:
            i = index[key]
            h, existing = base[i]
            if ACL_RE.match(header):
                # Order is the semantics: replace the body wholesale.
                base[i] = (h, [c if c.startswith(" ") else " " + c.strip() for c in children])
                report["sections_changed"].append(header + "  (body replaced)")
            else:
                merged = list(existing)
                for c in children:
                    body = c.strip()
                    if body.lower().startswith("no "):
                        target = _head(body[3:])
                        before = len(merged)
                        merged = [m for m in merged if _head(m) != target]
                        if len(merged) != before:
                            report["removed"].append(f"{header}: {body}")
                        continue
                    hd = _head(body)
                    for j, m in enumerate(merged):
                        if _head(m) == hd:
                            merged[j] = " " + body
                            break
                    else:
                        merged.append(" " + body)
                base[i] = (h, merged)
                report["sections_changed"].append(header)
        else:
            body = [c if c.startswith(" ") else " " + c.strip() for c in children]
            insert_at = len(base)
            for i, (h, ls) in enumerate(base):
                if h is None and any(l.startswith("ip forward-protocol") for l in ls):
                    insert_at = i
                    break
            base.insert(insert_at, (header, body))
            index = {h.strip().lower(): i for i, (h, _) in enumerate(base) if h}
            report["sections_added"].append(header)

    out = []
    for h, ls in base:
        if h is not None:
            out.append(h)
        out.extend(ls)
    text = "\n".join(out)
    if not text.endswith("\n"):
        text += "\n"
    return text, report


def unified_diff(a, b, name="as2dept1.cfg"):
    import difflib
    return "".join(difflib.unified_diff(
        a.splitlines(keepends=True), b.splitlines(keepends=True),
        fromfile=f"a/{name}", tofile=f"b/{name}"))
