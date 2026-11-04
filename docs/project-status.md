# Development status

As of 2026-11-04. Verify the actual branch, working tree and `pyproject.toml` version before reporting progress.
[Backlog](backlog.md) holds the detailed requirements and owner findings.

## Branch state

| Branch | State |
| --- | --- |
| `main` | Local version `0.6.1-alpha.16`: the published 0.6.0, the upstream syncs to `6c61fdc8` and to v2.12.0 (`62ec9471`), the security policy, the fix that keeps files from a mod inside their output folder (`e8bee4c2`) and its own message for refused atlases (`ca44e051`), BL-065 through BL-069, BL-070 steps 1 and 2 and part of step 3, BL-071, BL-072, BL-073, BL-076, BL-077, the BL-078 close-time chrome fix (`1a2afd84`). Not pushed. Upstream published its fix for the same issue in v2.12.0 (2026-11-04), so 0.6.1 is no longer held back. |

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
| BL-070 | Steps 1 and 2 of 4 accepted by the owner on macOS (alpha.5, alpha.7); Windows not reviewed; step 3 partly on `main` (lobby dialogue balloons, Hall first-run buttons), owner review pending, stopped sites await the owner; step 4 not started |
| BL-071, BL-072 | Accepted by the owner on macOS (alpha.7); Windows not reviewed |
| BL-073 | Accepted by the owner (alpha.8) |
| BL-074 | Closed by the owner: working as intended |
| BL-076, BL-077 | Accepted by the owner on macOS (alpha.13, 2026-11-04); Windows not reviewed |
| BL-075 | Closed by the owner (2026-11-04) |
| BL-078 | Seen again on alpha.15; close-time failure fixed (`1a2afd84`), silent startup paths hardened and logged (`b186e93b`); root cause not confirmed |
| BL-057, BL-058 | Accepted by the owner on macOS (alpha.12); Windows not reviewed |
| BL-055 | Accepted by the owner on macOS (0.6.0 pre-review) |
| BL-014 | Candidate, not scheduled |
| BL-016 through BL-018 | Deferred |

Implementation, automated checks, owner acceptance and release are separate states. Do not mark an item accepted
from passing tests or the presence of earlier code.

## Review artifact and validation

`dist/Blue-Archive-Modding-and-Patching-Tool-0.6.1-alpha.16-macos-arm64.dmg`, MD5 `1cc1067379aee2c3ab176ecda87ca237`,
built from `f1d9b85d` (`chore(release): 0.6.1-alpha.16`): alpha.15, the upstream sync to v2.12.0 (`fee277eb`) with its
fork adaptations (`15c9dc42`, `bae2533f`) and the macOS reveal hardening (`62723651`). BL-078 awaits the owner's
confirmation.

On `main` before the build: 4273 passed, 33 skipped (`uv run python -m pytest`, JavaScript tests included, isolated
runs with the official development view; the seven new spine2dump tests skip without the tool), then the reveal
tests and the web UI suites (363 passed) after the reveal change. Native Windows behavior is not verified on the macOS host.
