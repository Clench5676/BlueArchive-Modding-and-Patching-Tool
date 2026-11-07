# Development status

As of 2026-11-07. Verify the actual branch, working tree and `pyproject.toml` version before reporting progress.
[Backlog](backlog.md) holds the detailed requirements and owner findings.

## Branch state

| Branch | State |
| --- | --- |
| `main` | Local version `0.7.0-alpha.9` (built from `80d8cf53`); since then BL-101 and BL-102 merged (`aab0b22a`), not built; the owner holds the full suite, version bump and build until told: the published 0.6.0, the upstream syncs to `6c61fdc8` and to v2.12.0 (`62ec9471`), the security policy, the fix that keeps files from a mod inside their output folder (`e8bee4c2`) and its own message for refused atlases (`ca44e051`), BL-065 through BL-069, BL-070 steps 1 to 3, BL-071, BL-072, BL-073, BL-076, BL-077, the BL-078 fixes (`1a2afd84`, `b186e93b`, `4841e30e`), BL-079, BL-081 through BL-100 (BL-092 step 1). Not pushed. Upstream published its fix for the same issue in v2.12.0 (2026-11-04), so 0.6.1 is no longer held back. |

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
| BL-096 | Implemented (Codex, reviewed and merged, `2218bdcc`, prototype accepted by the owner): Import Mod tells which files of a mod are missing and adds them; owner review pending |
| BL-097 | Implemented (2026-11-07): the design system (`docs/design-system.md`) and the popups, Import Mod, Settings, switch cards, toolbars and the detail page's composed parts on it; owner review pending |
| BL-098 through BL-100 | Implemented by the lead (2026-11-07, research `docs/asset-ecosystem.md`): older releases' parts found by asset path over every date of the core, a group rebuilt in place under a new CRC taken for its part's successor, Unity's built-in resources never missing; owner review pending |
| BL-101, BL-102 | Implemented by the lead (2026-11-07): each bundle of a packed generic part its own slot (CAB), the configuration migrated; Steam's file list judges official files, read with the Check Game Files consent, which now covers Steam's records (Detect, the Steam watch); on `main`, not built; owner review pending |
| BL-019 | Planned, rescoped by the owner (2026-11-07): game files that differ from Steam's list go into the error report only; handoff notes in the backlog |
| BL-057, BL-058 | Accepted by the owner on macOS (alpha.12); Windows not reviewed |
| BL-055 | Accepted by the owner on macOS (0.6.0 pre-review) |
| BL-014 | Candidate, not scheduled |
| BL-016 through BL-018 | Deferred |

Implementation, automated checks, owner acceptance and release are separate states. Do not mark an item accepted
from passing tests or the presence of earlier code.

## Review artifact and validation

`dist/Blue-Archive-Modding-and-Patching-Tool-0.7.0-alpha.9-macos-arm64.dmg`, MD5 `c3b5316500403010e9e209430d14db6e`,
built from `80d8cf53` (`chore(release): 0.7.0-alpha.9`): alpha.8 and BL-098 to BL-100 (merge `bada2a43`). The Windows
bundle of `main` is `Blue-Archive-Modding-and-Patching-Tool-0.7.0-alpha.9-main.bundle`.

On `bada2a43` (same code as the build but the version): 4444 passed, 34 skipped (`python -m pytest`), the roster
preflight run on its own as well (`tests/test_library_model.py`, 81 passed), 2569 JavaScript tests passed. BL-098 to
BL-100 change no screen; they show in what Enable, Apply, previews and the entry states do with mods of older releases
and after game updates. Native Windows is not verified on the macOS host.
