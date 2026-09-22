"""The intents — what an operator asks for, and what "done" means.

Each intent has two halves that are deliberately kept apart:

* `request` — the English an operator would write in a ticket. This is what the model sees.
  It is under-specified, because tickets are.
* `assertions` — example flows and the action the filter must take on them. This is what the
  **verifier** sees, and the model never does. If you show the model the assertions, you have
  built a system that passes its own test; Q2 in the brief is about why that matters.

Assertions are evaluated with Batfish's `testFilters`, which runs a synthetic packet through a
named filter and reports PERMIT or DENY. It needs no routing, so an assertion stays meaningful
even for a source address that does not exist anywhere in the network.
"""
from __future__ import annotations

HOST_SUBNETS = "2.128.0.0/16"
HOST_IFACES = ("GigabitEthernet2/0", "GigabitEthernet3/0")


class Intent:
    def __init__(self, key, title, request, assertions, hint=""):
        self.key = key
        self.title = title
        self.request = request
        self.assertions = assertions
        self.hint = hint

    def __repr__(self):
        return f"<Intent {self.key}: {self.title}>"


ENFORCE_EGRESS = Intent(
    key="enforce-egress",
    title="Put the host-protection filter into force",
    request=(
        "On device as2dept1, the extended ACL RESTRICT_HOST_TRAFFIC_OUT is defined but is not "
        "applied to any interface, so the protection it describes is not in force. Apply it to "
        "the two host-facing interfaces GigabitEthernet2/0 and GigabitEthernet3/0 so that it "
        "filters traffic being delivered towards the host subnets. Do not change which traffic "
        "the hosts themselves are allowed to originate, and do not disturb any other device or "
        "any routing configuration."
    ),
    # Placement only. Whether the ACL's *contents* express the intent is the next intent's problem.
    assertions=[
        dict(name="hosts can still originate traffic", node="as2dept1",
             filter="RESTRICT_HOST_TRAFFIC_IN", src="2.128.0.5", dst="3.0.1.5",
             expect="PERMIT"),
        dict(name="traffic towards hosts is still delivered", node="as2dept1",
             filter="RESTRICT_HOST_TRAFFIC_OUT", src="3.0.1.5", dst="2.128.0.5",
             expect="PERMIT"),
    ],
    hint=("The direction of an access-group is the whole task. `in` and `out` are relative to the "
          "interface, not to the campus."),
)


BLOCK_GUEST = Intent(
    key="block-guest",
    title="Make the block that is written down actually happen",
    request=(
        "On device as2dept1, traffic from the 1.128.0.0/16 range must never reach the host "
        "subnets 2.128.0.0/16. A line expressing this already exists in the ACL "
        "RESTRICT_HOST_TRAFFIC_OUT, but it has no effect as written. Make the block effective. "
        "Traffic from anywhere else towards the host subnets must keep being delivered, and "
        "traffic that the hosts originate must not be affected."
    ),
    assertions=[
        dict(name="1.128.0.0/16 is denied towards hosts", node="as2dept1",
             filter="RESTRICT_HOST_TRAFFIC_OUT", src="1.128.5.5", dst="2.128.0.5",
             expect="DENY"),
        dict(name="other sources still reach hosts", node="as2dept1",
             filter="RESTRICT_HOST_TRAFFIC_OUT", src="3.0.1.5", dst="2.128.0.5",
             expect="PERMIT"),
        dict(name="AS1's other prefixes still reach hosts", node="as2dept1",
             filter="RESTRICT_HOST_TRAFFIC_OUT", src="1.0.1.5", dst="2.128.0.5",
             expect="PERMIT"),
        dict(name="hosts still originate freely", node="as2dept1",
             filter="RESTRICT_HOST_TRAFFIC_IN", src="2.128.0.5", dst="1.128.5.5",
             expect="PERMIT"),
    ],
    hint=("A line that sits after a line matching the same packets never runs. The verifier's "
          "`acl_lines` check is telling you this before you even test a flow."),
)


ALL = {i.key: i for i in (ENFORCE_EGRESS, BLOCK_GUEST)}


def get(key):
    if key not in ALL:
        raise SystemExit(f"unknown intent {key!r}. Available: {', '.join(ALL)}")
    return ALL[key]
