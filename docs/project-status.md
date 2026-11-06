# Development status

As of 2026-11-04. Verify the actual branch, working tree and `pyproject.toml` version before reporting progress.
[Backlog](backlog.md) holds the detailed requirements and owner findings.

## Branch state

| Branch | State |
| --- | --- |
| `main` | Local version `0.7.0-alpha.2`: the published 0.6.0, the upstream syncs to `6c61fdc8` and to v2.12.0 (`62ec9471`), the security policy, the fix that keeps files from a mod inside their output folder (`e8bee4c2`) and its own message for refused atlases (`ca44e051`), BL-065 through BL-069, BL-070 steps 1 to 3, BL-071, BL-072, BL-073, BL-076, BL-077, the BL-078 fixes (`1a2afd84`, `b186e93b`, `4841e30e`), BL-079. Not pushed. Upstream published its fix for the same issue in v2.12.0 (2026-11-04), so 0.6.1 is no longer held back. |

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
| BL-083 through BL-085 | Implemented by the lead (2026-11-06, a Windows user's 0.6.0 report): the Spine licence notice on the message frame, 3D model mods identified by their asset paths, the Official File Overwritten message instead of "3D model unavailable"; owner review pending |
| BL-086 | Open: a missing official copy must not keep a mod from being imported; the owner's decision on the behaviour pending |
| BL-057, BL-058 | Accepted by the owner on macOS (alpha.12); Windows not reviewed |
| BL-055 | Accepted by the owner on macOS (0.6.0 pre-review) |
| BL-014 | Candidate, not scheduled |
| BL-016 through BL-018 | Deferred |

Implementation, automated checks, owner acceptance and release are separate states. Do not mark an item accepted
from passing tests or the presence of earlier code.

## Review artifact and validation

`dist/Blue-Archive-Modding-and-Patching-Tool-0.7.0-alpha.2-macos-arm64.dmg`, MD5 `ec923fd8f15e96ad62d4af26a4953ccc`,
built from `3639a953` (`chore(release): 0.7.0-alpha.2`): alpha.1, the update offer's centred version box (`65e901da`) and
BL-082, the App in its own package `bmpt` (`8465e1ee`..`013a536b`); the bundle carries `bmpt`'s front end, strings, assets
(the 0.7.0 notes) and catalog data and the engine's strings. BL-081, BL-082 and the release notes' wording await the
owner's review.

On the split (`013a536b`, same tree as `main` before the docs and the bump): 4327 passed, 33 skipped (Python), 2370
JavaScript tests passed (isolated runs with the official development view). Native Windows behavior is not verified on the macOS host.
