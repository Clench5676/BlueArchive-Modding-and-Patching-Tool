# Development status

As of 2026-11-03. Verify the actual branch, working tree and `pyproject.toml` version before reporting progress.
[Backlog](backlog.md) holds the detailed requirements and owner findings.

## Branch state

| Branch | State |
| --- | --- |
| `main` | Local version `0.6.1-alpha.9`: the published 0.6.0, the upstream sync to `6c61fdc8`, the security policy, the fix that keeps files from a mod inside their output folder (`e8bee4c2`), BL-065 through BL-069, BL-070 steps 1 and 2, BL-071, BL-072, BL-073, BL-076. Not pushed: held until upstream publishes its fix for the same issue. |

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
| BL-070 | Steps 1 and 2 of 4 accepted by the owner on macOS (alpha.5, alpha.7); Windows not reviewed; steps 3 and 4 not started |
| BL-071, BL-072 | Accepted by the owner on macOS (alpha.7); Windows not reviewed |
| BL-073 | Accepted by the owner (alpha.8) |
| BL-074 | Closed by the owner: working as intended |
| BL-076 | In alpha.9; owner acceptance pending |
| BL-075 | Open, minor |
| BL-057, BL-058 | Accepted by the owner on macOS (alpha.12); Windows not reviewed |
| BL-055 | Accepted by the owner on macOS (0.6.0 pre-review) |
| BL-014 | Candidate, not scheduled |
| BL-016 through BL-018 | Deferred |

Implementation, automated checks, owner acceptance and release are separate states. Do not mark an item accepted
from passing tests or the presence of earlier code.

## Review artifact and validation

`dist/Blue-Archive-Modding-and-Patching-Tool-0.6.1-alpha.9-macos-arm64.dmg`, MD5 `6ee145277ca44be9ac4a6d900a380185`,
built from `5a656cac` (`chore(release): 0.6.1-alpha.9`): alpha.8 (all accepted) and BL-076, which awaits the owner's review.

On `main` before the build (`95959624`, tested in its worktree): 4249 passed, 26 skipped (Python); 2297 JavaScript tests passed (isolated runs with the official development
view). Native Windows behavior is not verified on the macOS host.
