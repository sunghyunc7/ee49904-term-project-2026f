# Snapshot provenance

`configs/` is the **`live` snapshot of the Batfish example network**, copied verbatim from
[`batfish/batfish`](https://github.com/batfish/batfish) at `networks/example/live/configs/`.

Batfish is licensed under the Apache License 2.0. These files are redistributed here under that
license, unmodified, so that the starter kit has a network that is guaranteed to parse.

## What the network is

A small campus (**AS2**) with two upstream providers (**AS1**, **AS3**), 13 devices running Cisco IOS:

```
  AS1 (1.0.0.0/8)                AS3 (3.0.0.0/8)
   as1border1  as1border2          as3border1  as3border2
   as1core1                        as3core1
            \                     /
             as2border1   as2border2          AS2 = the campus (2.0.0.0/8)
             as2core1     as2core2
             as2dist1     as2dist2
                    as2dept1                   ← the host-facing device you will change
                      |
              2.128.0.0/24, 2.128.1.0/24       ← the host subnets
```

`as2dept1` is where this project's changes happen: it is the only device with host-facing
interfaces (`GigabitEthernet2/0`, `GigabitEthernet3/0`) and it already carries two extended
ACLs, `RESTRICT_HOST_TRAFFIC_IN` and `RESTRICT_HOST_TRAFFIC_OUT`.

## Two things about this snapshot that are not accidents

1. **`RESTRICT_HOST_TRAFFIC_OUT` is defined but applied nowhere.** The intent it encodes is
   therefore not in force. Making it take effect — in the right direction — is the first task.
2. **Both ACLs contain an unreachable line.** In `_IN`, `permit icmp any any` sits after
   `deny ip any any`. In `_OUT`, `deny ip 1.128.0.0 0.0.255.255 2.128.0.0 0.0.255.255` sits after
   a line that already permits the same traffic. A line that can never match is a line whose
   intent is not implemented, and the verifier will tell you so.

`../fixtures/wrong_direction.txt` reproduces the defect in the upstream `candidate` snapshot's
version of `as2dept1`: an ACL applied **in the wrong direction**. It is used by the smoke
test as a defect the verifier must catch, and it is the mistake this project is really about:
plausible-looking configuration that a reviewer would sign off on.
