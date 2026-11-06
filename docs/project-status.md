# Development status

As of 2026-11-06. Verify the actual branch, working tree and `pyproject.toml` version before reporting progress.
[Backlog](backlog.md) holds the detailed requirements and owner findings.

## Branch state

| Branch | State |
| --- | --- |
| `main` | Local version `0.7.0-alpha.4`: the published 0.6.0, the upstream syncs to `6c61fdc8` and to v2.12.0 (`62ec9471`), the security policy, the fix that keeps files from a mod inside their output folder (`e8bee4c2`) and its own message for refused atlases (`ca44e051`), BL-065 through BL-069, BL-070 steps 1 to 3, BL-071, BL-072, BL-073, BL-076, BL-077, the BL-078 fixes (`1a2afd84`, `b186e93b`, `4841e30e`), BL-079, BL-081 through BL-087. Not pushed. Upstream published its fix for the same issue in v2.12.0 (2026-11-04), so 0.6.1 is no longer held back. |

0.6.0 is published (tag `0.6.0` at `817ebeda`, 2026-10-30): macOS build uploaded, Windows build by the release workflow.

## Feature state

| Items | State |
| --- | --- |
| BL-001 through BL-008 | Included in the published 0.5.0 baseline |
| BL-009 through BL-011 | Accepted by the owner (2026-10-27); Windows not reviewed |
| BL-012 | Accepted by the owner on the lead's evidence (2026-10-27) |
| BL-015 | Initial photography feature accepted by the owner; later requirements are separate entries |
| BL-022 through BL-024, BL-028, BL-029 | Accepted by the owner (2026-10-27); Windows not reviewed |
| BL-026 | Accepted by the owner on Windows (alpha.12) |
| BL-025, BL-027 | Implemented; Windows-only. BL-025 needs a real in-app update; BL-027 see BL-062 |
| BL-031 | Accepted by the owner (2026-10-27) |
| BL-033 through BL-036, BL-038, BL-039 | Accepted by the owner on macOS (alpha.2); Windows not reviewed |
| BL-032 | Closed: superseded by BL-040 |
| BL-037 | Accepted by the owner on macOS (alpha.7); Windows not reviewed |
| BL-040, BL-041, BL-043, BL-045 | Accepted by the owner on macOS (alpha.3); Windows not reviewed |
| BL-042 | Accepted by the owner on macOS (alpha.4) |
| BL-044 | Review 1 (runtime-sliced badges) accepted on macOS (alpha.6); review 2 closed as won't fix by the owner. |
| BL-047 | Accepted by the owner on macOS (alpha.7); Windows not reviewed |
| BL-049 | Accepted by the owner on macOS (alpha.7): page titles removed; Windows not reviewed |
| BL-050 | Accepted by the owner on macOS (alpha.6); Windows not reviewed |
| BL-051 | Accepted by the owner on macOS (alpha.6); Windows not reviewed |
| BL-052 | Accepted by the owner on macOS (alpha.8); Windows not reviewed |
| BL-053 | Accepted by the owner on macOS (alpha.7); Windows not reviewed |
| BL-046 | Closed by the owner without UAT (CLI-only) |
| BL-048 | Accepted: CLI no longer built, installed or released |
| BL-020 | Accepted by the owner on macOS (alpha.9, simulated prompt); Windows not reviewed |
| BL-021 | Accepted by the owner on macOS (alpha.12); Windows not reviewed |
| BL-013, BL-019, BL-030 | Planned, not implemented |
| BL-054 | Deferred by the owner for research; design proposal on file |
| BL-056 | Funnel: AI-assisted portrait refinement, being refined |
| BL-059 through BL-063 | Accepted by the owner on Windows (alpha.14) |
| BL-064 | Accepted by the owner on Windows (alpha.15) |
| BL-065 through BL-069 | Accepted by the owner on macOS (alpha.7); Windows not reviewed |
| BL-070 | Closed by the owner (2026-11-04): steps 1 to 3 accepted on macOS (alpha.5, alpha.7, alpha.20); step 4 not started; Windows not reviewed |
| BL-071, BL-072 | Accepted by the owner on macOS (alpha.7); Windows not reviewed |
| BL-073 | Accepted by the owner (alpha.8) |
| BL-074 | Closed by the owner: working as intended |
| BL-076, BL-077 | Accepted by the owner on macOS (alpha.13, 2026-11-04); Windows not reviewed |
| BL-075 | Closed by the owner (2026-11-04) |
| BL-078 | Accepted by the owner on macOS (alpha.19); Windows not reviewed |
| BL-079 | Accepted by the owner on macOS (alpha.19); Windows not reviewed |
| BL-080 | Closed by the owner (2026-11-04): flicker fixed (`5e6b3d56`); stuck pointer not seen again, cause not identified, logging kept |
| BL-081 | In-app release notes draw Markdown in the UI language; the 0.7.0 notes built in; owner review pending |
| BL-082 | The App in its own package `bmpt`, upstream's package as the engine; owner review pending |
| BL-083 | Superseded by BL-087 |
| BL-084, BL-085 | Implemented by the lead (2026-11-06, a Windows user's 0.6.0 report): 3D model mods identified by their asset paths, the missing-official message instead of "3D model unavailable"; owner review pending |
| BL-086 | Implemented (Codex, merged): mods import and apply where no official copy is left, outside files kept first, "Mod incomplete"; owner review pending |
| BL-087 | Implemented (Codex, merged): the Spine licence as a one-time notice for users who never answered; owner review pending, wording in five languages included |
| BL-089, BL-090, BL-093 | Implemented (Codex, reviewed and merged, `e3e47b63`): mod files map to the game files they replace, dependencies resolve through the install's CAB index, one identification for Add and the batch import; owner review pending |
| BL-088 | Implemented (Codex, reviewed and merged, `452858d4`): a 3D slot spans the prefab and the student's own model cores (Hoshino (Armed), Wakamo (Swimsuit)); owner review pending |
| BL-091, BL-094 | Implemented by the lead (2026-11-06); owner review pending; native Windows not verified on the macOS host |
| BL-092 | Step 1 implemented by the lead (2026-11-06): lock waits and preview times in the error report; Windows measurement and fix open |
| BL-095 | Implemented by the lead (2026-11-06): a student's add buttons open Import Mod (Batch Import renamed); owner review pending |
| BL-096 | Asked by the owner (2026-11-06): telling which files of a mod are missing; proposal, not scheduled |
| BL-057, BL-058 | Accepted by the owner on macOS (alpha.12); Windows not reviewed |
| BL-055 | Accepted by the owner on macOS (0.6.0 pre-review) |
| BL-014 | Candidate, not scheduled |
| BL-016 through BL-018 | Deferred |

Implementation, automated checks, owner acceptance and release are separate states. Do not mark an item accepted
from passing tests or the presence of earlier code.

## Review artifact and validation

`dist/Blue-Archive-Modding-and-Patching-Tool-0.7.0-alpha.4-macos-arm64.dmg`, MD5 `c7c17e4168263650771427eecce3703a`,
built from `4cf02350` (`chore(release): 0.7.0-alpha.4`): alpha.3, BL-087 (`9bec8dc8`, merge `da204ee8`), BL-086
(`a4ecd147`, merge `88c2408f`), the Official File Missing title (`5d9653ad`) and the merge's test fix (`bf5bd60f`).
BL-081, BL-082, BL-084 to BL-087 and the release notes' wording await the owner's review.

On `a98278af` (same tree as the build but the version): 4356 passed, 33 skipped (Python, `python -m pytest`), 2383
JavaScript tests passed (isolated runs with the official development view). The start notice's look, the "Mod
incomplete" and "Current game files added to the library" results and an install without official copies are for the
owner's review. Native Windows behavior is not verified on the macOS host.
