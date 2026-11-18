"""Adding Animations a mod's skeleton onto the official one, and the converter route of an animation transfer."""

from __future__ import annotations

from ..records import stored_target

import base64
import contextlib
import time
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Iterable, Mapping

from bmpt.spine import atlas_remap, add_animations as animation_transfer, rebake, converter as spine_converter
from bmpt.core import devmode
from bmpt.engine import (
    Bundle,
    LogFunc,
    get_skel_version,
    no_log,
)
from ...core.errors import ServiceError
from ...core.openfiles import temporary_directory
from .. import integrity
from ..judge import (
    core_of,
    resource_type_of_file,
)
from ..records import Pin
from .base import _serialized


class AddAnimationsMixin:
    """Adding Animations a mod's skeleton onto the official one, and the converter route of an animation transfer."""

    def _add_animations_rigs(
        self, record: dict, paths: list[Path], inputs: dict, available: Iterable[Path], resource_type: str = "portrait"
    ) -> dict[str, dict]:
        """Find the rigs an animation transfer adds the official rig's new animations to, per official atlas name.

        A rig is the mod's own binary skeleton and atlas where it carries them and the official skeleton has animations
        it lacks (``kind`` "own"; ``base``: the official rig the mod was made from, as the fingerprint recorded it or
        the official copy of the mod's date, where known), else the rig a texture-only portrait's textures were drawn
        for (``atlas_remap.old_rig_plan``; ``kind`` "oldRig", its own base). Each comes with the official ``skeleton``
        name, the ``animations`` it gains and the mod's ``pages`` (texture keys). ``available``: verified official files
        that may be of the mod's date (``_dated_rig``). A memorial lobby or Spine background (resource type
        memorialLobby) takes
        only its own rig, without a base: its animation transfer is ``add_animations.plain``.
        """
        mod, official = inputs["mod"], inputs["official"]
        gains = atlas_remap.rig_gains(mod.atlases, mod.skeletons, official.atlases, official.skeletons)
        skeleton_of = atlas_remap.pair_atlases(official.skeletons, official.atlases)
        own_skeletons = atlas_remap.pair_atlases(mod.skeletons, official.skeletons)
        own_atlases = atlas_remap.pair_atlases(mod.atlases, official.atlases)
        painted = (self._read_built_against(record) or {}).get("painted") or {}
        rigs: dict[str, dict] = {}

        def rig(resource_type: str, name: str, skeleton: str, text: str, data: bytes, base: tuple | None) -> None:
            try:
                pages = {atlas_remap.texture_key(page) for page in atlas_remap.parse_atlas(text, name).pages}
            except (ValueError, UnicodeError):
                return
            if not pages or not pages <= set(mod.textures) or not self._binary_42(data):
                return
            if base is not None and not self._binary_42(base[1]):
                base = None
            rigs[name] = {
                "resource_type": resource_type,
                "skeleton": skeleton,
                "atlas": text,
                "data": data,
                "base": base,
                "animations": gains["animations"].get(skeleton, []),
                "pages": sorted(pages),
            }

        for name, skeleton in skeleton_of.items():
            if skeleton not in gains["animations"] or skeleton not in own_skeletons or name not in own_atlases:
                continue
            entry = (painted.get(integrity.basename(name)) or {}) if resource_type == "portrait" else {}
            if resource_type == "portrait" and (
                not isinstance(entry.get("skeleton"), str) or not isinstance(entry.get("text"), str)
            ):
                entry = self._dated_rig(paths, [name], official.atlases, available).get(name) or {}
            base = None
            if isinstance(entry.get("skeleton"), str) and isinstance(entry.get("text"), str):
                with contextlib.suppress(ValueError):
                    base = (entry["text"], base64.b64decode(entry["skeleton"], validate=True))
            if base is not None and base[1] == official.skeletons[skeleton]:
                base = None  # an official copy that kept the mod's date is the current rig: it tells no origin
            try:
                text = mod.atlases[own_atlases[name]].decode("utf-8-sig")
            except UnicodeError:
                continue
            rig("own", name, skeleton, text, mod.skeletons[own_skeletons[skeleton]], base)
        if rigs or resource_type != "portrait":
            return rigs
        plan = atlas_remap.old_rig_plan(
            {**inputs["layout"], **inputs["dated"]},
            official.atlases,
            official.skeletons,
            mod.skeletons,
            mod.textures,
            official.keys,
        )
        if plan.complete:
            for name, (atlas, skeleton, data) in plan.atlases.items():
                if skeleton in plan.animations:
                    gains["animations"][skeleton] = plan.animations[skeleton]
                    text = atlas.decode("utf-8")
                    rig("oldRig", name, skeleton, text, data, (text, data))
        return rigs

    @staticmethod
    def _binary_42(data: bytes) -> bool:
        try:
            return (get_skel_version(data) or "").startswith("4.2.") and not data.lstrip()[:1] == b"{"
        except (ValueError, UnicodeError):
            return False

    def _add_animations_state(self, token: str, mod_id: str | None = None) -> dict:
        state = self._added_animations.get(token) if isinstance(token, str) else None
        if state is None or state["expires"] <= time.monotonic() or (mod_id and state["source"] != mod_id):
            self._added_animations.pop(token, None) if isinstance(token, str) else None
            raise ServiceError("entry_changed")
        return state

    @_serialized
    def add_animations(
        self, mod_id: str, log: LogFunc = no_log, progress: Callable[[dict], None] | None = None, pin: Pin | None = None
    ) -> dict:
        """Start an animation transfer and retain its input rigs under a temporary job token.

        Return rigs to pose, per official atlas name the mod's rig, the
        official rig it was made from (``base``, or None) and the official rig, each as ``atlas`` text and base64
        ``skeleton``; kept under a ``token`` for ``add_animations_build`` and ``add_animations_check``. Refused with
        ``add_animations_unavailable`` when no rig gains animations an animation transfer could carry
        (``_add_animations_rigs``).

        Args:
            mod_id: Unrepaired stored portrait or memorial lobby mod ID receiving official animations.
            log: Repair-job logger accepted for API consistency during input preparation.
            progress: Receives the initial check-step dictionary; None disables reporting.
            pin: Game install and server the operation is fixed to; None uses the current install.

        Returns:
            Token and rigs for setup posing; memorial lobby animation transfers return prepared plain rigs for
            add_animations_check directly.
        """
        pin = self._current(pin)
        record = self._record(mod_id)
        if record.get("repair_source_id") or self._repaired_id(record):
            raise ServiceError("entry_changed")
        now = time.monotonic()
        self._added_animations = {
            token: state
            for token, state in self._added_animations.items()
            if state["expires"] > now and state["source"] != mod_id
        }
        paths = self._paths(record)
        resource_type = resource_type_of_file(paths[0].name)
        if resource_type not in ("portrait", "memorialLobby"):
            raise ServiceError("add_animations_unavailable")
        identity = self._identity({path.name: path for path in paths})
        targets = self._spine_parts(core_of(paths[0].name), pin)
        if not targets:
            raise ServiceError("game_file_not_found", alt=stored_target(record))
        if progress:
            progress({"current": 1, "total": 1, "step": "import.step.check"})
        with temporary_directory(prefix=devmode.LIBRARY_STAGING_PREFIX + "remap-") as temporary:
            originals = self._stage_official(targets, Path(temporary) / "official", pin)
            try:
                inputs = self._remap_view(record, paths, targets, originals, pin)
                sources = [self._official_for(target, pin) for target in targets]
                rigs = self._add_animations_rigs(record, paths, inputs, sources, resource_type)
            except atlas_remap.RemapError as error:
                raise ServiceError(error.code, **error.params) from error
            except (ValueError, KeyError, EOFError, OSError) as error:
                raise ServiceError(
                    "atlas_remap_invalid", file=", ".join(path.name for path in paths), reason=str(error)
                ) from error
        if not rigs:
            raise ServiceError("add_animations_unavailable")
        official = inputs["official"]
        token = uuid.uuid4().hex

        def b64(data: bytes) -> str:
            return base64.b64encode(data).decode("ascii")

        if resource_type == "memorialLobby":
            # built at once (``add_animations.plain``); the page poses each carried animation and add_animations_check
            # takes the result
            built = self._add_animations_plain(rigs, inputs)
            if not built:
                raise ServiceError("add_animations_unavailable")
            self._added_animations[token] = {
                "source": mod_id,
                "identity": identity,
                "pin": pin.key,
                "expires": now + 3600,
                "rigs": rigs,
                "inputs": inputs,
                "prepared": built,
                "plain": True,
            }
            return {
                "token": token,
                "plain": True,
                "rigs": {
                    name: {"skeleton": b64(one.skeleton), "atlas": one.atlas, "animations": list(one.results)}
                    for name, one in built.items()
                },
            }
        self._added_animations[token] = {
            "source": mod_id,
            "identity": identity,
            "pin": pin.key,
            "expires": now + 3600,
            "rigs": rigs,
            "inputs": inputs,
            "prepared": None,
        }

        posed = {}
        for name, rig in rigs.items():
            base = rig["base"]
            posed[name] = {
                "resource_type": rig["resource_type"],
                "animations": rig["animations"],
                "mod": {"atlas": rig["atlas"], "skeleton": b64(rig["data"])},
                "base": {"atlas": base[0], "skeleton": b64(base[1])} if base else None,
                "official": {
                    "atlas": official.atlases[name].decode("utf-8-sig"),
                    "skeleton": b64(official.skeletons[rig["skeleton"]]),
                },
            }
        return {"token": token, "rigs": posed}

    @_serialized
    def add_animations_build(self, token: str, geometry: Any) -> dict:
        """Splice and pack portrait animation transfer rigs from posted setup poses for animation testing.

        Return test rigs by atlas name with base64 skeletons, atlas text, and check
        slots for each animation. official supplies the corresponding official-rig
        slot checks for the next posing step.

        Args:
            token: Active nonplain job token returned by animation transfer.
            geometry: Setup poses indexed by official atlas name, containing mod, official,
                and any base rig from rig-geometry.js setupOf.
        """
        state = self._add_animations_state(token)
        if not isinstance(geometry, dict) or state.get("plain"):
            raise ServiceError("bad_params", key="geometry")
        official = state["inputs"]["official"]
        prepared, out, refused = {}, {}, {}
        try:
            for name, rig in state["rigs"].items():
                posted = geometry.get(name)
                if not isinstance(posted, dict):
                    raise ServiceError("bad_params", key="geometry")
                mod_atlas = atlas_remap.parse_atlas(rig["atlas"], name)
                official_text = official.atlases[name].decode("utf-8-sig")
                mod_setup = animation_transfer.setup(posted.get("mod"), mod_atlas)
                official_setup = animation_transfer.setup(posted.get("official"))
                base_setup = animation_transfer.setup(posted["base"]) if rig["base"] and posted.get("base") else None
                mod_pages = {key: state["inputs"]["mod"].textures[key] for key in rig["pages"]}
                one = animation_transfer.prepare(
                    rig["data"],
                    rig["atlas"],
                    mod_pages,
                    official.skeletons[rig["skeleton"]],
                    official_text,
                    official.textures,
                    mod_setup,
                    official_setup,
                    base_setup,
                )
                prepared[name] = one
                refused.update(one.add_animations.refused)
                check = {animation: slots for animation, slots in one.slots.items()}
                check.update({test: one.slots[animation] for test, (animation, _c) in one.candidates.items()})
                out[name] = {
                    "skeleton": base64.b64encode(one.test_skeleton).decode("ascii"),
                    "atlas": one.test_atlas,
                    "check": check,
                    "official": dict(one.slots),
                }
        except animation_transfer.AddAnimationsError as error:
            raise ServiceError(error.code, **error.params) from error
        except atlas_remap.RemapError as error:
            raise ServiceError(error.code, **error.params) from error
        state["prepared"] = prepared
        return {"rigs": out, "refused": refused}

    @_serialized
    def add_animations_check(
        self,
        token: str,
        geometry: Any,
        log: LogFunc = no_log,
        progress: Callable[[dict], None] | None = None,
        pin: Pin | None = None,
    ) -> dict:
        """Validate added expressions against official poses and prepare a repair preview.

        Compare posted poses of the test
        and the official rigs (``add_animations.choose``), and prepare the review of the repaired copy as a remap's
        (``_remap_review``; accepted with ``accept_remap``).

        Args:
            token: Active token from add_animations_build or the plain memorial lobby route.
            geometry: Per-atlas test and official poses; plain animation transfers supply their carried-animation poses.
            log: Receives user-facing operation messages.
            progress: Receives per-target phase dictionaries; None disables reporting.
            pin: Game install and server the operation is fixed to; None uses the current install.
                Its key must match the animation transfer's pinned install.
        """
        state = self._add_animations_state(token)
        pin = self._current(pin)
        if state["prepared"] is None or not isinstance(geometry, dict):
            raise ServiceError("bad_params", key="geometry")
        if pin.key != state["pin"]:
            raise ServiceError("entry_changed")
        mod_id = state["source"]
        record = self._record(mod_id)
        paths = self._paths(record)
        if state["identity"] != self._identity({path.name: path for path in paths}):
            raise ServiceError("entry_changed")
        inputs = state["inputs"]
        mod, official = inputs["mod"], inputs["official"]
        if state.get("plain"):
            return self._add_animations_check_plain(token, state, geometry, paths, pin, log, progress)
        checked: dict[str, animation_transfer.Checked] = {}
        try:
            for name, one in state["prepared"].items():
                posted = geometry.get(name)
                if not isinstance(posted, dict):
                    raise ServiceError("bad_params", key="geometry")
                test = rebake.rig(
                    posted.get("test"),
                    atlas_remap.parse_atlas(one.test_atlas, name),
                    max_poses=len(one.candidates) + len(one.slots) + 1,
                )
                reference = rebake.rig(posted.get("official"), atlas_remap.parse_atlas(one.official_text, name))
                pages = {**{key: mod.textures[key] for key in state["rigs"][name]["pages"]}, **one.pages.images}
                checked[name] = animation_transfer.choose(one, reference, test, official.textures, pages)
        except animation_transfer.AddAnimationsError as error:
            raise ServiceError(error.code, **error.params) from error
        except atlas_remap.RemapError as error:
            raise ServiceError(error.code, **error.params) from error
        self._added_animations.pop(token, None)
        targets = self._spine_parts(core_of(paths[0].name), pin)
        if not targets:
            raise ServiceError("game_file_not_found", alt=stored_target(record))

        def step(key: str, current: int = 1) -> None:
            if progress:
                progress({"current": current, "total": len(targets), "step": f"import.step.{key}"})

        report = {
            "moved": [],
            "added": [],
            "official": [],
            "dropped": [],
            "upscaled": [],
            "warnings": [],
            "added_animations": [],
            "refused": {},
            "corrected": [],
            "unfixed": [],
            "grown": 0,
            "unanchored": False,
            "unchanged": False,
        }
        marks: list[str] = []
        for name, one in state["prepared"].items():
            result = checked[name]
            report["refused"].update(one.add_animations.refused)
            report["unanchored"] = report["unanchored"] or bool(one.unanchored)
            report["grown"] = max([report["grown"], *one.pages.grown.values()])
            for animation, outcome in result.results.items():
                report["added_animations"].append(animation)
                shown = [
                    c
                    for key in one.add_animations.shown.get(animation, [])
                    if (c := one.add_animations.carried.get(key))
                ]
                if any(c.slot in one.unanchored for c in shown):  # placed without an anchor: rung for a look
                    marks += [c.region for c in shown if c.region]
                if outcome["candidate"]:
                    report["corrected"].append(animation)
                if not outcome["fixed"]:
                    report["unfixed"].append(animation)
                    marks += [
                        c.region
                        for key in one.add_animations.shown.get(animation, [])
                        if (c := one.add_animations.carried.get(key)) and c.region
                    ]
        report["marks"] = sorted(set(marks))
        report["partial"] = bool(report["unfixed"] or report["refused"] or report["unanchored"])
        with temporary_directory(prefix=devmode.LIBRARY_STAGING_PREFIX + "remap-") as temporary:
            output = Path(temporary)
            step("check")
            originals = self._stage_official(targets, output / "official", pin)
            try:
                patch = self._add_animations_patch(paths, originals, official, mod, state["rigs"], checked)
            except atlas_remap.RemapError as error:
                raise ServiceError(error.code, **error.params) from error
            return self._remap_review(
                mod_id,
                state["identity"],
                paths,
                originals,
                official,
                inputs["view"],
                output,
                patch,
                report,
                pin,
                log,
                step,
            )

    def converter_root(self) -> Path:
        """Return the settings directory where downloaded helper programs are kept."""
        artifacts = getattr(self.settings, "artifacts", None)
        return artifacts if artifacts is not None else Path(self.settings.path).parent

    def converter_status(self) -> dict:
        """Report availability and installation status of the optional converter beside the settings."""
        status = spine_converter.status(self.converter_root())
        if self._converter_executable() is not None:
            status["installed"] = True
        return status

    def _converter_executable(self) -> Path | None:
        found = spine_converter.located(self.converter_root())
        if found is not None:
            return found
        if getattr(self.settings, "artifacts", None) is not None:
            return spine_converter.located(self.converter_root().parent.parent)
        return None

    def converter_download(self, consented: bool, progress: Callable[[dict], None] | None = None) -> dict:
        """Fetch the pinned Windows converter release with the user's licence consent.

        Args:
            consented: Whether the user accepted the converter's licence before requesting the download.
            progress: Receives current/total byte counts and the check-step key; None disables reporting.

        Returns:
            Converter status after installation.

        Raises:
            ServiceError: Consent, platform, download, or installation checks fail.
        """

        def step(done: int, total: int) -> None:
            if progress:
                progress({"current": done, "total": total, "step": "import.step.check"})

        try:
            spine_converter.download(self.converter_root(), consented=consented, progress=step)
        except spine_converter.ConverterError as error:
            raise ServiceError(error.code, **error.params) from error
        return self.converter_status()

    @_serialized
    def add_animations_convert(self, token: str, geometry: Any) -> dict:
        """Use the converter to animation transfer official animations into each mod rig and return pose-test inputs.

        Convert each mod skeleton and the official one through
        the converter to JSON, the official animations the mod lacks added there with what they need
        (``spine_converter.add_animations_json``), the regions they draw packed onto the mod's pages
        (``add_animations.pack``, from the
        posted setup poses as ``add_animations_build`` takes them) and the skeleton back to binary. Hands back, per
        rig, the
        mod's rig ``before`` and ``after`` to pose, with ``check``: the mod's own animations and the slots that draw
        them, for the pose comparison of ``add_animations_convert_check``; and ``refused``.

        Args:
            token: Active job token from animation transfer whose input rigs are retained.
            geometry: Per-atlas mod setup poses in the same shape accepted by add_animations_build.

        Raises:
            ServiceError: The converter is missing, job or geometry is invalid, or conversion/animation_transfer fails.
        """
        state = self._add_animations_state(token)
        if not isinstance(geometry, dict):
            raise ServiceError("bad_params", key="geometry")
        exe = self._converter_executable()
        if exe is None:
            raise ServiceError("converter_missing")
        official = state["inputs"]["official"]
        converted, out, refused = {}, {}, {}
        try:
            for name, rig in state["rigs"].items():
                posted = geometry.get(name)
                if not isinstance(posted, dict):
                    raise ServiceError("bad_params", key="geometry")
                official_text = official.atlases[name].decode("utf-8-sig")
                mod_json = spine_converter.to_json(exe, rig["data"])
                own = {
                    animation: [slot.get("name") for slot in mod_json.get("slots") or []]
                    for animation in mod_json.get("animations") or {}
                }
                result = spine_converter.add_animations_json(
                    mod_json, spine_converter.to_json(exe, official.skeletons[rig["skeleton"]])
                )
                refused.update(result.refused)
                mod_atlas = atlas_remap.parse_atlas(rig["atlas"], name)
                mod_setup = animation_transfer.setup(posted.get("mod"), mod_atlas)
                mod_pages = {key: state["inputs"]["mod"].textures[key] for key in rig["pages"]}
                pages = animation_transfer.pack(
                    rig["atlas"], official_text, result.regions, mod_pages, official.textures, mod_setup
                )
                atlas = animation_transfer.atlas_text(rig["atlas"], pages)
                skeleton = spine_converter.to_binary(exe, mod_json)
                converted[name] = {"result": result, "pages": pages, "atlas": atlas, "skeleton": skeleton, "own": own}
                out[name] = {
                    "before": {"atlas": rig["atlas"], "skeleton": base64.b64encode(rig["data"]).decode("ascii")},
                    "after": {"atlas": atlas, "skeleton": base64.b64encode(skeleton).decode("ascii")},
                    "check": own,
                }
        except spine_converter.ConverterError as error:
            raise ServiceError(error.code, **error.params) from error
        except animation_transfer.AddAnimationsError as error:
            raise ServiceError(error.code, **error.params) from error
        except atlas_remap.RemapError as error:
            raise ServiceError(error.code, **error.params) from error
        state["converted"] = converted
        return {"rigs": out, "refused": refused}

    @_serialized
    def add_animations_convert_check(
        self,
        token: str,
        geometry: Any,
        log: LogFunc = no_log,
        progress: Callable[[dict], None] | None = None,
        pin: Pin | None = None,
    ) -> dict:
        """Check converter-induced pose drift and prepare the converted repair preview.

        The mod's own animations posed before and after the rewrite (the
        converter rounds to two decimals) may move at most ``spine_converter.DRIFT``, else ``converter_drift``; then
        the review of the repaired copy as an animation transfer's (``_remap_review``). The carried attachments keep
        the official
        rig's placement (no anchor fit), so the review rings what they draw.

        Args:
            token: Active animation transfer token containing the converted rigs from add_animations_convert.
            geometry: Per-atlas before/after poses for the mod's own animations and setup pose.
            log: Receives user-facing operation messages.
            progress: Receives per-target phase dictionaries; None disables reporting.
            pin: Game install and server the operation is fixed to; None uses the current install.
                Its key must match the animation transfer's pinned install.

        Raises:
            ServiceError: Source or install changed, poses are invalid, or drift exceeds
                spine_converter.DRIFT (converter_drift).
        """
        state = self._add_animations_state(token)
        pin = self._current(pin)
        if not state.get("converted") or not isinstance(geometry, dict):
            raise ServiceError("bad_params", key="geometry")
        if pin.key != state["pin"]:
            raise ServiceError("entry_changed")
        mod_id = state["source"]
        record = self._record(mod_id)
        paths = self._paths(record)
        if state["identity"] != self._identity({path.name: path for path in paths}):
            raise ServiceError("entry_changed")
        inputs = state["inputs"]
        report = {
            "moved": [],
            "added": [],
            "official": [],
            "dropped": [],
            "upscaled": [],
            "warnings": [],
            "added_animations": [],
            "refused": {},
            "corrected": [],
            "unfixed": [],
            "grown": 0,
            "unanchored": True,
            "unchanged": False,
            "converter": True,
        }
        marks: list[str] = []
        checked = {}
        try:
            for name, one in state["converted"].items():
                posted = geometry.get(name)
                if not isinstance(posted, dict):
                    raise ServiceError("bad_params", key="geometry")
                limit = len(one["own"]) + 1
                before = rebake.rig(
                    posted.get("before"), atlas_remap.parse_atlas(state["rigs"][name]["atlas"], name), max_poses=limit
                )
                after = rebake.rig(posted.get("after"), atlas_remap.parse_atlas(one["atlas"], name), max_poses=limit)
                moved = spine_converter.drift(
                    *(
                        {
                            f"{pose}|{d.slot}|{d.region}|{k}": d.vertices.ravel().tolist()
                            for pose, drawn in rig.poses.items()
                            for k, d in enumerate(drawn)
                        }
                        for rig in (before, after)
                    )
                )
                if moved > spine_converter.DRIFT:
                    raise ServiceError("converter_drift", moved=f"{moved:.2f}", limit=spine_converter.DRIFT)
                result = one["result"]
                report["added_animations"] += result.added
                report["refused"].update(result.refused)
                report["grown"] = max([report["grown"], *one["pages"].grown.values()])
                marks += result.regions
                checked[name] = SimpleNamespace(atlas=one["atlas"], skeleton=one["skeleton"], pages=one["pages"].images)
        except atlas_remap.RemapError as error:
            raise ServiceError(error.code, **error.params) from error
        self._added_animations.pop(token, None)
        report["marks"] = sorted(set(marks))
        report["partial"] = True  # placed as the official rig places them: always for a look
        targets = self._spine_parts(core_of(paths[0].name), pin)
        if not targets:
            raise ServiceError("game_file_not_found", alt=stored_target(record))

        def step(key: str, current: int = 1) -> None:
            if progress:
                progress({"current": current, "total": len(targets), "step": f"import.step.{key}"})

        with temporary_directory(prefix=devmode.LIBRARY_STAGING_PREFIX + "remap-") as temporary:
            output = Path(temporary)
            step("check")
            originals = self._stage_official(targets, output / "official", pin)
            try:
                patch = self._add_animations_patch(
                    paths, originals, inputs["official"], inputs["mod"], state["rigs"], checked
                )
            except atlas_remap.RemapError as error:
                raise ServiceError(error.code, **error.params) from error
            return self._remap_review(
                mod_id,
                state["identity"],
                paths,
                originals,
                inputs["official"],
                inputs["view"],
                output,
                patch,
                report,
                pin,
                log,
                step,
            )

    @staticmethod
    def _add_animations_plain(
        rigs: dict[str, dict], inputs: dict, only: Mapping[str, set[str]] | None = None
    ) -> dict[str, animation_transfer.Plain]:
        """Build the ``add_animations.plain`` of every rig, leaving out the rigs with nothing to carry.

        ``only`` is per atlas the animations to carry.
        """
        official, mod = inputs["official"], inputs["mod"]
        built = {}
        for name, rig in rigs.items():
            wanted = None if only is None else only.get(name, set())
            if wanted is not None and not wanted:
                continue
            try:
                built[name] = animation_transfer.plain(
                    rig["data"],
                    rig["atlas"],
                    {key: mod.textures[key] for key in rig["pages"]},
                    official.skeletons[rig["skeleton"]],
                    official.atlases[name].decode("utf-8-sig"),
                    official.textures,
                    wanted,
                )
            except animation_transfer.AddAnimationsError as error:
                if error.code != "add_animations_refused":
                    raise ServiceError(error.code, **error.params) from error
            except atlas_remap.RemapError as error:
                raise ServiceError(error.code, **error.params) from error
        return built

    def _add_animations_check_plain(
        self,
        token: str,
        state: dict,
        geometry: Any,
        paths: list[Path],
        pin: Pin,
        log: LogFunc,
        progress: Callable[[dict], None] | None,
    ) -> dict:
        """Make the add_animations_check of a ``add_animations.plain``.

        ``geometry`` is per atlas ``failed``, the carried animations the page could not pose (``rig-geometry.js``
        ``failedPoses``). Those are built out again and reported as refused (``pose:``); the review is prepared as a
        remap's.
        """
        prepared: dict[str, animation_transfer.Plain] = state["prepared"]
        failed: dict[str, set[str]] = {}
        for name, one in prepared.items():
            posted = geometry.get(name)
            names = posted.get("failed") if isinstance(posted, dict) else None
            if not isinstance(names, list) or not all(isinstance(n, str) and n in one.results for n in names):
                raise ServiceError("bad_params", key="geometry")
            failed[name] = set(names)
        refused: dict[str, list[str]] = {}
        for one in prepared.values():
            refused.update(one.add_animations.refused)
        if any(failed.values()):
            for name in failed:
                refused.update({animation: ["pose:"] for animation in sorted(failed[name])})
            prepared = self._add_animations_plain(
                state["rigs"],
                state["inputs"],
                {name: set(one.results) - failed[name] for name, one in prepared.items()},
            )
            if not prepared:
                self._added_animations.pop(token, None)
                raise ServiceError("add_animations_refused", names=", ".join(sorted(refused)) or "—")
        self._added_animations.pop(token, None)
        record = self._record(state["source"])
        targets = self._spine_parts(core_of(paths[0].name), pin)
        if not targets:
            raise ServiceError("game_file_not_found", alt=stored_target(record))

        def step(key: str, current: int = 1) -> None:
            if progress:
                progress({"current": current, "total": len(targets), "step": f"import.step.{key}"})

        grown = [rows for one in prepared.values() for rows in one.grown.values()]
        report = {
            "moved": [],
            "added": [],
            "official": [],
            "dropped": [],
            "upscaled": [],
            "warnings": [],
            "added_animations": [animation for one in prepared.values() for animation in one.results],
            "refused": refused,
            "corrected": [],
            "unfixed": [],
            "grown": max(grown, default=0),
            "unanchored": False,
            "unchanged": False,
            "marks": [],
            "animations": True,
        }
        report["partial"] = bool(refused)
        inputs = state["inputs"]
        with temporary_directory(prefix=devmode.LIBRARY_STAGING_PREFIX + "remap-") as temporary:
            output = Path(temporary)
            step("check")
            originals = self._stage_official(targets, output / "official", pin)
            try:
                patch = self._add_animations_patch(
                    paths,
                    originals,
                    inputs["official"],
                    inputs["mod"],
                    {name: state["rigs"][name] for name in prepared},
                    prepared,
                )
            except atlas_remap.RemapError as error:
                raise ServiceError(error.code, **error.params) from error
            return self._remap_review(
                state["source"],
                state["identity"],
                paths,
                originals,
                inputs["official"],
                inputs["view"],
                output,
                patch,
                report,
                pin,
                log,
                step,
            )

    @_serialized
    def discard_add_animations(self, token: str) -> None:
        """Discard a temporary animation transfer token between steps without changing mod or game files."""
        if isinstance(token, str):
            self._added_animations.pop(token, None)

    @staticmethod
    def _add_animations_patch(
        paths: list[Path],
        originals: dict[Path, Path],
        official: atlas_remap.Inputs,
        mod: atlas_remap.Inputs,
        rigs: dict[str, dict],
        checked: dict,
    ) -> dict:
        """Build the patch of a added copy.

        It holds the mod's own text assets, every added skeleton and atlas over the official ones of their names, the
        mod's own textures and the added pages, all by ``cont_name_type``.
        """
        patch: dict = {}
        texts: dict[str, Any] = {}
        for original in originals.values():
            bundle = Bundle.load(original)
            if bundle is None:
                raise ServiceError("bundle_invalid", file=original.name)
            for key in bundle.get_asset_keys("cont_name_type"):
                if key.type == "TextAsset":
                    texts.setdefault(key.name, key)
        for path in paths:
            bundle = Bundle.load(path)
            if bundle is None:
                raise ServiceError("bundle_invalid", file=path.name)
            extracted, _ = bundle.extract_patch({"TextAsset"}, "cont_name_type")
            patch.update(extracted)
        if patch.keys() - set(texts.values()):
            raise atlas_remap.RemapError(
                "atlas_remap_target_mismatch",
                file=paths[0].name,
                matched=len(patch.keys() & set(texts.values())),
                expected=len(patch),
            )
        patch.update({official.keys[key]: image for key, image in mod.textures.items() if key in official.keys})
        for name, rig in rigs.items():
            result = checked[name]
            if name not in texts or rig["skeleton"] not in texts:
                raise atlas_remap.RemapError("atlas_remap_target_mismatch", file=paths[0].name, matched=0, expected=2)
            patch[texts[name]] = result.atlas.encode("utf-8")
            patch[texts[rig["skeleton"]]] = result.skeleton
            for key, image in result.pages.items():
                if key not in official.keys:
                    raise ServiceError("add_animations_page_missing", page=key)
                patch[official.keys[key]] = image
        return patch
