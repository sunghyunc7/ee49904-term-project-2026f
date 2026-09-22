# Notice — third-party software and licenses

The starter kits in this repository install and drive third-party open-source software. None of it
is redistributed here except where stated. Cite what you use, and read the license before you
publish anything built on it. Each `BRIEF.md` §7 has the details for its own kit.

| Brief | Project | License | Redistributed here? |
|---|---|---|---|
| 1 | ASTRA-sim | MIT — its submodules differ, see below | no — cloned by `setup.sh` |
| 1 | Chakra (MLCommons) | Apache-2.0 | no — ASTRA-sim submodule |
| 1 | Protocol Buffers | BSD-3-Clause | no |
| 2, 3 | PyTorch · torchvision | BSD-3-Clause style | no |
| 3 | Sionna · Mitsuba 3 · Dr.Jit | Apache-2.0 · BSD-3-Clause · BSD-3-Clause | no |
| 4 | FlexRIC | **OAI Public License V1.1** | **partly — see below** |
| 4 | SWIG | GPL-3.0 — the tool only; what it generates is exempt, see below | no |
| 5 | Batfish · pybatfish | Apache-2.0 | example network only — see below |
| 5 | ollama · udocker | MIT · Apache-2.0 | no |

Two rows in that table need a sentence more.

**ASTRA-sim (brief 1).** The simulator itself is MIT, but `setup.sh` fetches its submodules
recursively and they are not all MIT — the ns-3 network backend is **GPL-2.0**. This kit builds and
uses the analytical backend only, and nothing from any of them is redistributed here. If you publish
code that ships or links one of those backends, the license to respect is that submodule's, not
ASTRA-sim's.

**SWIG (brief 4).** The tool is GPL-3.0 and is used only while building. Its own license carries an
exception for what it produces: the bindings SWIG generates on your machine are **not** covered by
the GPL, so building the Python xApp SDK does not put your code under it.

## Files in this repository that derive from third-party work

- `ric-closed-loop/patch/sm_mac.c` and `ric-closed-loop/patch/sm_slice.c` are **modified copies of
  FlexRIC source files** and remain under the **OAI Public License V1.1**. A copy of that license
  ships beside them as `ric-closed-loop/patch/LICENSE-OAI-PL-v1.1.txt`; the canonical text is at
  https://openairinterface.org/legal/oai-public-license/. Each of the two files carries a header
  stating what was changed from upstream. That license is not an OSI license — read it rather than
  assuming it behaves like MIT or Apache. Note in particular that its patent grant covers study,
  testing and research; any other purpose needs separately negotiated FRAND terms.
  `ee49904_plant.c`, `ee49904_plant.h` and `test_plant.c` are course material written from scratch.
- `llm-netops-verifier/starter/snapshot/configs/` is **Batfish's example network, copied verbatim**
  under Apache-2.0. Provenance is in `llm-netops-verifier/starter/snapshot/NOTICE.md`.

Everything else in this repository is course material for KAIST EE49904 (Fall 2026),
© AI-Native Networking Lab, KAIST. Students enrolled in the course may clone it, modify it, keep
their own copies, and include the parts they used in the repository they submit with their report.
Redistribution beyond that, or reuse in another course, needs written permission. The third-party
terms above apply to the third-party files regardless of this paragraph.

Model weights used in brief 5 are served from the course server and are not part of this repository,
and their terms differ enough that it matters which model produced a given result. **gpt-oss** is
Apache-2.0, weights included. **gemma4** is a Gemma-family model, and Gemma is not open source in the
OSI sense — it is released under Google's own Gemma Terms of Use, with a Prohibited Use Policy that
follows the model to whoever uses it. Name the model behind each number in your report.
