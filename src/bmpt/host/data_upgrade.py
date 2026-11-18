"""Stage and publish a verified App data generation before normal services open.

Pinned payloads use native clones or hard links. Journal recovery proceeds when
the recorded digests prove the live and rollback data; any ambiguous outcome is
left pending with its evidence intact.
"""

from __future__ import annotations

import copy
import hashlib
import os
import shutil
import tempfile
import threading
import uuid
from collections.abc import Callable, Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import toml

from ..core import devmode
from ..core.errors import ServiceError
from ..core.filelock import file_lock
from . import upgrade_files as files
from .data_paths import APP_DIR_NAME, OLD_APP_DIR_NAME
from .format_manifest import DATA_MANIFEST, LIBRARY_MANIFEST, NewerManifest, encode_manifest
from .store_formats import (
    GENERATION,
    NAMESPACE,
    STORES,
    Document,
    InvalidStore,
    count_records,
    documents_equal,
    inverse_translate,
    load_document,
    translate,
)
from .upgrade_inputs import (
    ID,
    InputFile,
    Snapshot,
    UpgradeBlocked,
    discover_macos_data,
    epoch_path,
    input_document,
    inventory,
    legacy_fences,
    legacy_library,
    maybe_read_record,
    moved_prefix,
    namespace,
    path_record,
    read_head,
    read_record,
    read_route,
    rebase,
    recovery_data_id,
    resolve_record,
    write_route,
)
from .upgrade_journals import RecoveryPending, game_recovery_inputs
from .upgrade_verification import verify_library

if TYPE_CHECKING:
    from ..webui.instance import InstanceLock

_JOURNALS = ("T", "I", "D")
_NOTICE_PREF = "data_upgrade_notice"
_STEPS = (("check", 5), ("settings", 5), ("library", 80), ("verify", 5), ("open", 5))


def _upgrade_notice(path: Path) -> str | None:
    try:
        preferences = load_document("U", path).get("prefs", {})
    except FileNotFoundError:
        return None
    return "upgraded" if preferences.get(_NOTICE_PREF) == "upgraded" else None


@dataclass(frozen=True)
class CommittedPaths:
    """Explicit paths owned by one verified route and active head."""

    data_root: Path
    library_root: Path
    legacy_library: Path
    data_epoch: Path
    library_epoch: Path
    data_id: str
    library_id: str

    @property
    def config(self) -> Path:
        """Current settings file."""
        return self.data_epoch / "config.toml"

    @property
    def ui_state(self) -> Path:
        """Current UI state file."""
        return self.data_epoch / "webui.json"

    @property
    def runtime(self) -> Path:
        """Private process state."""
        return self.data_root / NAMESPACE / "runtime"

    @property
    def artifacts(self) -> Path:
        """Private mutable App artifacts."""
        return self.data_root / NAMESPACE / "artifacts"

    @property
    def library_cache(self) -> Path:
        """Disposable cache for the selected library generation."""
        return self.library_root / NAMESPACE / "cache" / f"g{GENERATION}"

    def cache(self, legacy_cache: Path) -> Path:
        """Return the generation-specific sibling of a legacy asset cache."""
        return legacy_cache.with_name(f"{legacy_cache.name}-bmpt-g{GENERATION}")


def _write_inverses(stage: Path, conversions: dict[str, Document]) -> None:
    if conversions:
        files.write_document(stage / "source-recovery" / "inverse.json", conversions)


def _to_stage_path(item: InputFile) -> str:
    if item.store == "BF":
        return "thumbnails/images/" + item.relative.removeprefix("photographs/images/")
    return item.relative


class DataUpgrade:
    """Own data and library leases, upgrade decisions and the committed path binding."""

    def __init__(
        self,
        data_dir: Path,
        *,
        fault: Callable[[str], None] | None = None,
        qualify: Callable[[Path], str] = files.volume,
        updated_in_app: bool | None = None,
    ) -> None:
        """Create a coordinator without opening normal application services."""
        self.data_dir = data_dir.resolve()
        self.paths: CommittedPaths | None = None
        self._fault = fault or (lambda _label: None)
        self._qualify = qualify
        if updated_in_app is None:
            from .updater import consume_upgrade_handoff

            updated_in_app = consume_upgrade_handoff()
        self._updated_in_app = updated_in_app
        self._leases: ExitStack | None = None
        self._startup_instance: InstanceLock | None = None
        self._session: ExitStack | None = None
        self._state_lock = threading.Lock()
        self._step_index: int | None = None
        self._percent = 0
        self._operation = threading.Lock()
        self._prompt: tuple[str, str, str] | None = None
        self._confirm_legacy: tuple[str, str, str] | None = None
        self._prompt_sequence: int | None = None
        self._log_folder = self.data_dir / NAMESPACE / "runtime"
        self._state: Document = {
            "status": "running",
            "phase": "discovery",
            "reason": None,
            "progress": None,
            "notice": None,
            "affected": [],
            "counts": None,
            "logFolder": str(self._log_folder),
        }

    @property
    def log_folder(self) -> Path:
        """Return the private folder containing the upgrade log."""
        return self._log_folder

    def state(self) -> Document:
        """Read a snapshot of the bootstrap state."""
        with self._state_lock:
            state = copy.deepcopy(self._state)
            steps = None
            if self._step_index is not None and state["status"] in ("running", "ready"):
                steps = [
                    {
                        "id": name,
                        "status": "done"
                        if state["status"] == "ready" or index < self._step_index
                        else "running"
                        if index == self._step_index
                        else "pending",
                    }
                    for index, (name, _) in enumerate(_STEPS)
                ]
            return {**state, "steps": steps, "percent": self._percent}

    def acknowledge(self) -> None:
        """Durably clear the completed format upgrade notice for this data directory."""
        with self._operation:
            if self.paths is None or self.state()["status"] != "ready" or self.state()["notice"] != "upgraded":
                return
            path = self.paths.ui_state
            with file_lock(path.with_suffix(".json.lock"), timeout=2):
                document = load_document("U", path)
                document.setdefault("prefs", {})[_NOTICE_PREF] = None
                files.write_document(path, document)
            self._status(notice=None)

    def _status(self, **values: Any) -> None:
        with self._state_lock:
            self._state.update(values)
            if values.get("status") == "running":
                self._step_index = 0
                self._percent = 0
            elif values.get("status") == "ready":
                self._percent = 100
            elif values.get("status") in ("failed", "changed"):
                self._percent = min(99, self._percent)
            elif (
                self._state["status"] == "running"
                and self._step_index is not None
                and (progress := values.get("progress"))
                and progress["total"]
            ):
                start = sum(weight for _, weight in _STEPS[: self._step_index])
                weight = _STEPS[self._step_index][1]
                self._percent = start + weight * progress["done"] // progress["total"]

    def _advance_step(self, name: str) -> None:
        index = next(index for index, (step, _) in enumerate(_STEPS) if step == name)
        with self._state_lock:
            if self._state["status"] == "running" and self._step_index is not None and index > self._step_index:
                self._step_index = index
                self._percent = sum(weight for _, weight in _STEPS[:index])

    def _failure(self, bootstrap_reason: str, **details: Any) -> None:
        self._status(**{"status": "failed", "reason": bootstrap_reason, "progress": None, **details})

    def _phase(self, label: str) -> None:
        self._status(phase=label)
        self._fault(label)

    def _log(self, detail: str, *, temporary: bool = False) -> None:
        if temporary and self._log_folder.is_relative_to(self.data_dir):
            self._log_folder = Path(tempfile.mkdtemp(prefix="bmpt-upgrade-"))
            self._status(logFolder=str(self._log_folder))
        message = f"{self.state()['phase']}: {detail}\n"
        try:
            files.make_directory(self._log_folder)
            with (self._log_folder / "data-upgrade.log").open("a", encoding="utf-8") as output:
                output.write(message)
        except (OSError, ValueError):
            self._log_folder = Path(tempfile.mkdtemp(prefix="bmpt-upgrade-"))
            (self._log_folder / "data-upgrade.log").write_text(message, encoding="utf-8")
            self._status(logFolder=str(self._log_folder))

    def _lock(self, stack: ExitStack, path: Path, reason: str) -> None:
        if files.present(path) and not path.is_file():
            raise InvalidStore(f"Invalid lock path: {path}")
        try:
            stack.enter_context(file_lock(path, timeout=0.1))
        except TimeoutError as error:
            raise UpgradeBlocked(reason, f"Folder already in use: {path.parent}") from error

    def _legacy_writers(self, peer: Path) -> None:
        from ..webui.classic.processes import find_classic
        from .data_paths import _standalone_classic_in_use

        if find_classic(peer) or find_classic(peer / NAMESPACE / "runtime") or _standalone_classic_in_use():
            raise UpgradeBlocked("classic", f"Classic UI is still running for {peer}")

    def _volume(self, path: Path) -> str:
        try:
            volume = self._qualify(path)
        except (OSError, ValueError) as error:
            raise UpgradeBlocked("unsupportedStorage", f"Cannot establish volume guarantees: {path}") from error
        if volume not in ("apfs", "ntfs"):
            raise UpgradeBlocked("unsupportedStorage", f"Unsupported filesystem at {path}: {volume}")
        return volume

    def close(self) -> None:
        """Drop committed paths and release the process's library and data leases."""
        with self._operation:
            self._close()
            if self._startup_instance is not None:
                self._startup_instance.release()
                self._startup_instance = None

    def take_instance_lock(self, instance: InstanceLock) -> None:
        """Hold the launcher's acquired data-directory lock through the notice and the session."""
        if not instance.held or instance.data_dir.resolve() != self.data_dir:
            raise ValueError("The session requires an acquired lock on its data directory")
        self._startup_instance = instance

    def open_failed(self) -> None:
        """Return to the notice after services cannot finish opening the commit."""
        with self._operation:
            self._close()
        self._log("The committed generation could not finish recovery or initialize its services")
        self._failure("recoveryPending", notice=None)

    def _close(self) -> None:
        self.paths = None
        if self._session is not None:
            self._session.close()
            self._session = None
        if self._leases is not None:
            self._leases.close()
            self._leases = None

    def run(self, action: str = "retry", *, target: Path | None = None) -> Document:
        """Try to open the generation or commit a decision against the fingerprint shown on the notice."""
        if not self._operation.acquire(blocking=False):
            return self.state()
        try:
            if self.paths is not None:
                return self.state()
            prior = self.state()
            if action in ("keep", "import") and (prior["status"] != "changed" or self._prompt is None):
                return prior
            self._status(status="running", reason=None, notice=None, progress=None, affected=[], counts=None)
            self._attempt(action, target)
        except UpgradeBlocked as error:
            self._log(str(error))
            if self.state()["status"] != "changed":
                self._failure(error.reason)
        except files.UnpinnablePayload as error:
            self._log(str(error))
            self._failure("unsupportedStorage")
        except NewerManifest as error:
            self._failure("newerFormat", phase=error.owner)
            self._log(str(error), temporary=True)
        except RecoveryPending as error:
            self._log(str(error))
            self._failure("recoveryPending")
        except (OSError, ValueError, KeyError, TypeError, ServiceError) as error:
            self._log(str(error))
            self._failure("error")
        finally:
            self._operation.release()
        return self.state()

    def _attempt(self, action: str, target: Path | None) -> None:
        self._phase("discovery")
        data = self.data_dir
        devmode.check_read(data)
        if devmode.data_dir() is None and data.name in (APP_DIR_NAME, OLD_APP_DIR_NAME):
            discover_macos_data(data.parent)
        route = read_route(data)
        if route is not None:
            read_head(resolve_record(route["library"], data))
        if target is not None:
            read_head(target.expanduser().resolve())
        source_d = inventory(data, "data")
        ui, _ = input_document(source_d, "U")
        moved = moved_prefix(source_d)
        rebound = False
        try:
            source_l_path = legacy_library(source_d, ui, moved)
        except UpgradeBlocked:
            if not route or not route.get("established"):
                raise
            configured = (ui or {}).get("prefs", {}).get("library_dir")
            routed = resolve_record(route["library"], data)
            if target is None:
                active = read_head(routed)
                entry = (active or {}).get("members", {}).get(route["dataId"], {})
                if (
                    (active or {}).get("libraryId") != route["libraryId"]
                    or entry.get("token") != route["token"]
                    or entry.get("legacySetting") != configured
                ):
                    raise
                source_l_path = resolve_record(entry["source"], data)
                if not files.present(source_l_path):
                    raise
            else:
                source_l_path = target.expanduser().resolve()
                moved_head = read_head(source_l_path)
                member = (moved_head or {}).get("members", {}).get(route["dataId"], {})
                if (
                    (moved_head or {}).get("libraryId") != route["libraryId"]
                    or member.get("token") != route["token"]
                    or inventory(source_l_path, "library").fingerprint != member.get("baseline", {}).get("library")
                ):
                    raise RecoveryPending("The selected folder cannot be proven to be the moved library") from None
                rebound = True
        selected = resolve_record(route["library"], data) if route else source_l_path
        if target is not None:
            selected = target.expanduser().resolve()
            devmode.check_read(selected)
            if route is not None and route.get("established") and not rebound:
                moved_head = read_head(selected)
                member = (moved_head or {}).get("members", {}).get(route["dataId"], {})
                if (moved_head or {}).get("libraryId") != route["libraryId"] or member.get("token") != route["token"]:
                    raise UpgradeBlocked("recoveryPending", "The selected folder does not own the established route")
                rebound = True
        if route and route.get("established") and not files.present(selected):
            raise UpgradeBlocked("unavailable", f"Selected library is unavailable: {selected}")
        if route and selected != source_l_path and not files.present(source_l_path) and route.get("established"):
            raise UpgradeBlocked("unavailable", f"Legacy library is unavailable: {source_l_path}")
        self._volume(data)
        self._volume(selected)
        if selected != data and selected.is_relative_to(namespace(data)):
            raise InvalidStore("Library is inside the data upgrade namespace")
        for reserved in ("cache", "cache-bmpt-g2", "Addons", "output"):
            if selected.is_relative_to(data / reserved):
                raise InvalidStore(f"Library overlaps {data / reserved}")
        preview_head = read_head(selected)
        if source_l_path != selected:
            read_head(source_l_path)
        source_l = inventory(source_l_path, "library")
        observation = (str(source_l_path), source_d.fingerprint, source_l.fingerprint)
        had_writer_protocol = bool(
            (ui or {}).get("prefs", {}).get("data_id")
            or source_l.item("profiles.json")
            or any(item.store in _JOURNALS for item in source_l.items)
        )
        self._phase("locking")
        with ExitStack() as leases, ExitStack() as session, ExitStack() as fences:
            files.make_directory(data)
            if data.name in (APP_DIR_NAME, OLD_APP_DIR_NAME):
                self._lock(fences, data.parent / f".{APP_DIR_NAME}.migration.lock", "olderVersion")
            if self._startup_instance is None:
                self._lock(leases, data / "instance.lock", "olderVersion")
            self._legacy_writers(data)
            if preview_head:
                for peer in preview_head["members"].values():
                    peer_path = Path(peer["path"])
                    if peer_path != data and files.present(peer_path):
                        peer_route = read_route(peer_path)
                        if peer_route and peer_route["libraryId"] != preview_head["libraryId"]:
                            continue
                        self._legacy_writers(peer_path)
                        with ExitStack() as probe:
                            self._lock(probe, peer_path / "instance.lock", "busy")
            files.make_directory(selected)
            for library in sorted({selected, source_l_path}):
                if library == selected or files.present(library):
                    if library == selected:
                        self._lock(session, namespace(library) / "session.lock", "busy")
                    elif files.present(namespace(library) / "session.lock"):
                        self._lock(fences, namespace(library) / "session.lock", "busy")
            for lock in legacy_fences(data, source_l_path):
                self._lock(fences, lock, "olderVersion")
            self._phase("snapshot")
            if read_route(data) != route or read_head(selected) != preview_head:
                raise UpgradeBlocked("busy", "The selected route or library changed while locking")
            self._quarantine_staging(data, "data")
            self._quarantine_staging(selected, "library")
            current_d, current_l = inventory(data, "data"), inventory(source_l_path, "library")
            if (current_d.fingerprint, current_l.fingerprint) != observation[1:]:
                raise UpgradeBlocked("olderVersion", "Legacy input changed while locking")
            if (source_l.item("state.json") or source_d.item("webui.json")) and not preview_head:
                if not had_writer_protocol and self._confirm_legacy != observation:
                    self._confirm_legacy = observation
                    raise UpgradeBlocked("olderVersion", "Confirm that old builds using the library have closed")
            if rebound:
                entry = preview_head["members"][route["dataId"]]
                if entry["baseline"]["library"] != observation[2] or entry["baseline"]["data"] != observation[1]:
                    raise RecoveryPending("Legacy inputs changed while rebinding the moved library")
                route = {**route, "library": path_record(selected, data)}
                head = copy.deepcopy(preview_head)
                member = head["members"][route["dataId"]]
                member["source"] = path_record(source_l_path, data)
                member["legacySetting"] = (ui or {}).get("prefs", {}).get("library_dir")
                self._advance_step("verify")
                self._validate_epoch(epoch_path(selected, "library", head["epoch"]), head["libraryId"], "library")
                self._rescan(source_d, source_l)
                self._publish_head(selected, preview_head, head)
                write_route(data, route, member["epoch"])
                self._open(data, selected, source_l_path, route, head)
            else:
                self._open_or_publish(source_d, source_l, selected, route, preview_head, ui, moved, observation, action)
            self._leases, self._session = leases.pop_all(), session.pop_all()
        self._status(status="ready", phase="open", reason=None, progress=None)

    def _quarantine_staging(self, root: Path, owner: str) -> None:
        folder = namespace(root) / owner
        if not files.present(folder):
            return
        for candidate in folder.glob(".stage-*"):
            key = candidate.name.removeprefix(".stage-")
            if not ID.fullmatch(key) or not files.present(candidate) or not candidate.is_dir():
                continue
            receipt = maybe_read_record(candidate / "stage.json")
            if not receipt or receipt.get("epoch") != key or receipt.get("owner") != owner:
                continue
            destination = folder / "quarantine" / key
            if not files.present(destination):
                files.make_directory(destination.parent)
                files.replace_path(candidate, destination)
                self._fault("orphan:quarantined")

    def _member(self, data: Snapshot, route: Document | None, head: Document | None) -> tuple[Document | None, bool]:
        if not route:
            return None, False
        if head is None or head["libraryId"] != route["libraryId"]:
            if route.get("established"):
                raise RecoveryPending("The established library head is missing or has a different identity")
            return None, False
        entry = head["members"].get(route["dataId"])
        if not entry or entry["token"] != route["token"]:
            if route.get("established"):
                raise RecoveryPending("The established route has no matching library membership")
            return None, False
        registered = Path(entry["path"]).resolve()
        if registered != data.root and files.present(registered / DATA_MANIFEST):
            old = read_route(registered)
            if old and old["dataId"] == route["dataId"] and old["token"] == route["token"]:
                return None, True
        return entry, False

    def _open_or_publish(
        self,
        source_d: Snapshot,
        source_l: Snapshot,
        selected: Path,
        route: Document | None,
        head: Document | None,
        ui: Document | None,
        moved: tuple[Path, Path] | None,
        observation: tuple[str, str, str],
        action: str,
    ) -> None:
        member, copied = self._member(source_d, route, head)
        if head and (prerequisite := head.get("prerequisite")) and not head.get("activated"):
            issuer = Path(prerequisite["data"])
            existing = read_route(issuer)
            if existing is None or any(existing[key] != prerequisite[key] for key in ("libraryId", "token")):
                raise RecoveryPending("The destination head is waiting for its issuing route")
        if head:
            for peer in head["members"].values():
                peer_path = Path(peer["path"])
                if peer_path == source_d.root or not files.present(peer_path):
                    continue
                peer_route = read_route(peer_path)
                if peer_route is None or peer_route["libraryId"] == head["libraryId"]:
                    self._legacy_writers(peer_path)

        changed_d = changed_l = False
        if member:
            baseline = member.get("acknowledged", member["baseline"])
            expected_l = member.get("source", "")
            changed_d = baseline["data"] != observation[1]
            changed_l = baseline["library"] != observation[2] or expected_l != path_record(source_l.root, source_d.root)
            if changed_d or changed_l:
                if (
                    action not in ("keep", "import")
                    or self._prompt != observation
                    or self._prompt_sequence != head["sequence"]
                ):
                    self._prompt = observation
                    self._prompt_sequence = head["sequence"]
                    affected = [str(source_d.root)]
                    if changed_l:
                        affected.extend(
                            peer["path"] for peer in head["members"].values() if peer["path"] != str(source_d.root)
                        )
                    documents = (input_document(source_l, "S")[0] or {}).get("mods", [])
                    self._status(
                        status="changed",
                        phase="changes",
                        affected=sorted(set(affected)),
                        counts={"mods": len(documents)},
                    )
                    raise UpgradeBlocked("error", "Legacy data changed")
                if action == "keep":
                    proposed = copy.deepcopy(head)
                    kept = proposed["members"][route["dataId"]]
                    kept["acknowledged"] = {"data": observation[1], "library": observation[2]}
                    kept["source"] = path_record(source_l.root, source_d.root)
                    self._rescan(source_d, source_l)
                    self._publish_head(selected, head, proposed)
                    head = proposed
                elif changed_l and len(head["members"]) > 1 and expected_l != path_record(source_l.root, source_d.root):
                    raise UpgradeBlocked(
                        "recoveryPending", "Attached data folders have different legacy library sources"
                    )
            if action != "import" or not (changed_d or changed_l):
                if member["path"] != str(source_d.root):
                    proposed = copy.deepcopy(head)
                    proposed["members"][route["dataId"]]["path"] = str(source_d.root)
                    self._rescan(source_d, source_l)
                    self._publish_head(selected, head, proposed)
                    head = proposed
                self._open(source_d.root, selected, source_l.root, route, head)
                return
            current = epoch_path(selected, "library", head["epoch"])
            if changed_l and expected_l != path_record(source_l.root, source_d.root):
                missing = [
                    store
                    for store, name in (("R", "rigs/index.json"), ("O", "originals.json"))
                    if files.present(current / name) and source_l.item(name) is None
                ]
                if missing:
                    self._log("Legacy library move omitted " + ", ".join(missing) + "; retained in the backup")
            if any(any((current / STORES[key].current).glob("*/journal.json")) for key in _JOURNALS):
                if not changed_l or not self._old_recovery_proven(current, source_l):
                    raise RecoveryPending("Current data has a pending journal and its legacy outcome cannot be proven")

        self._phase("staging")
        library_id = head["libraryId"] if head else uuid.uuid4().hex
        epoch_d = uuid.uuid4().hex if not member or changed_d else member["epoch"]
        epoch_l = uuid.uuid4().hex if not head or changed_l else head["epoch"]
        if member is None:
            route = {
                "format": GENERATION,
                "dataId": uuid.uuid4().hex,
                "libraryId": library_id,
                "library": path_record(selected, source_d.root),
                "token": uuid.uuid4().hex,
            }
        proposed = (
            copy.deepcopy(head)
            if head
            else {"format": GENERATION, "libraryId": library_id, "members": {}, "backups": []}
        )
        proposed["epoch"] = epoch_l
        if member:
            old_data, old_library = member["epoch"], head["epoch"]
            if old_data == epoch_d:
                old_data = self._freeze(source_d.root, "data", old_data, route["dataId"])
            if old_library == epoch_l:
                old_library = self._freeze(selected, "library", old_library, library_id)
            proposed["backups"].append(
                {
                    "dataId": route["dataId"],
                    "dataEpoch": old_data,
                    "libraryEpoch": old_library,
                    "sequence": head["sequence"],
                }
            )
        baseline = {"data": observation[1], "library": observation[2]}
        new_member = {
            "token": route["token"],
            "epoch": epoch_d,
            "path": str(source_d.root),
            "source": path_record(source_l.root, source_d.root),
            "baseline": baseline,
        }
        proposed["members"][route["dataId"]] = new_member
        if changed_l:
            for peer in proposed["members"].values():
                their_source = resolve_record(peer["source"], Path(peer["path"]))
                if their_source == source_l.root:
                    peer["baseline"]["library"] = observation[2]
                    peer.get("acknowledged", {}).pop("library", None)
                    peer.pop("acknowledged", None)

        candidates = []
        try:
            if not member or changed_d:
                prepared_ui = recovery_data_id(ui or {}, source_d.root, copied=copied)
                if member:
                    previous_ui = epoch_path(source_d.root, "data", member["epoch"]) / "webui.json"
                    notice = _upgrade_notice(previous_ui)
                else:
                    notice = "upgraded" if (source_d.items or source_l.items) and not self._updated_in_app else None
                prepared_ui["prefs"][_NOTICE_PREF] = notice
                staged = self._stage(
                    source_d,
                    source_d.root,
                    "data",
                    epoch_d,
                    route["dataId"],
                    moved,
                    ui=prepared_ui,
                )
                candidates.append(staged)
            if not head or changed_l:
                staged = self._stage(source_l, selected, "library", epoch_l, library_id, moved)
                candidates.append(staged)
            else:
                self._advance_step("verify")
                self._validate_epoch(epoch_path(selected, "library", epoch_l), library_id, "library")
            self._rescan(source_d, source_l)
            for stage, destination in candidates:
                files.make_directory(destination.parent)
                files.replace_path(stage, destination)
                self._fault(f"epoch:{destination.parent.parent.name}:renamed")
                self._validate_epoch(
                    destination,
                    route["dataId"] if destination.parent.parent.name == "data" else library_id,
                    destination.parent.parent.name,
                )
            self._rescan(source_d, source_l)
            if member is None:
                write_route(source_d.root, route, epoch_d)
                self._fault("route:durable")
            self._publish_head(selected, head, proposed)
        finally:
            for stage, _ in candidates:
                if stage.is_dir():
                    shutil.rmtree(stage)
        self._open(source_d.root, selected, source_l.root, route, proposed)

    def _old_recovery_proven(self, current: Path, source: Snapshot) -> bool:
        inverse = maybe_read_record(current / "source-recovery" / "inverse.json") or {}
        seal = read_record(current / "seal.json")
        original = {entry["path"]: entry["sha256"] for entry in seal["files"]}
        legacy_state, _ = input_document(source, "S")
        legacy_applied = (legacy_state or {}).get("applied", {})
        for key in _JOURNALS:
            for journal in (current / STORES[key].current).glob("*/journal.json"):
                relative = journal.relative_to(current).as_posix()
                if relative not in inverse or source.item(relative) or original.get(relative) != files.digest(journal):
                    return False
                document = load_document(key, journal)
                if document["phase"] == "committed":
                    continue
                if key == "I":
                    if not self._old_import_resolved(source, document, legacy_state or {}):
                        return False
                    continue
                if key == "D":
                    if not self._old_deletion_resolved(document, legacy_state or {}):
                        return False
                    continue
                old = inverse_translate(key, document)["old_applied"]
                if not self._applied_agrees(legacy_applied, old):
                    return False
                for item in document["files"]:
                    target = Path(item["target"])
                    if not files.present(target) or files.digest(target, "sha1") == item["after"]:
                        return False

        return True

    @staticmethod
    def _applied_agrees(actual: dict, expected: dict) -> bool:
        def without_mtime(record):
            return None if record is None else {key: value for key, value in record.items() if key != "mtime"}

        return all(without_mtime(record) == without_mtime(actual.get(path)) for path, record in expected.items())

    @staticmethod
    def _old_import_resolved(source: Snapshot, document: Document, state: Document) -> bool:
        key = document["id"]
        record = next((row for row in state.get("mods", ()) if isinstance(row, dict) and row.get("id") == key), {})
        if document.get("operation") == "update":
            alternatives = [document["old"], document["new"]]
        elif not record:
            return not any(item.relative.startswith(f"mods/{key}/") for item in source.items)
        else:
            alternatives = [document["hashes"]]
        return any(
            record.get("files") == sorted(names)
            and all(
                (item := source.item(f"mods/{key}/{name}")) is not None and files.digest(item.path, "sha1") == sha1
                for name, sha1 in names.items()
            )
            for names in alternatives
        )

    def _old_deletion_resolved(self, document: Document, state: Document) -> bool:
        legacy = inverse_translate("D", document)
        records = {
            item["id"]: item
            for item in state.get("mods", ())
            if isinstance(item, dict) and isinstance(item.get("id"), str)
        }
        if any(records.get(key) != legacy["records"].get(key) for key in legacy["mods"]):
            return False
        if not self._applied_agrees(state.get("applied", {}), legacy["applied"]):
            return False
        return all(
            files.present(target := Path(item["target"])) and files.digest(target, "sha1") == item["before"]
            for item in legacy["games"]
        )

    def _rescan(self, source_d: Snapshot, source_l: Snapshot) -> None:
        self._advance_step("verify")
        self._phase("rescan")
        self._legacy_writers(source_d.root)
        for snapshot, owner in ((source_d, "data"), (source_l, "library")):
            if inventory(snapshot.root, owner).fingerprint != snapshot.fingerprint:
                raise UpgradeBlocked(
                    "olderVersion", f"Legacy {owner} data changed during verification: {snapshot.root}"
                )

    def _stage(
        self,
        source: Snapshot,
        root: Path,
        owner: str,
        epoch: str,
        identity: str,
        moved: tuple[Path, Path] | None,
        *,
        ui: Document | None = None,
    ) -> tuple[Path, Path]:
        self._advance_step("settings" if owner == "data" else "library")
        destination = epoch_path(root, owner, epoch)
        stage = destination.parent.parent / f".stage-{epoch}"
        files.make_directory(stage)
        conversions: dict[str, Document] = {}
        counts: dict[str, Any] = {}
        consumed: set[str] = set()
        try:
            files.write_document(stage / "stage.json", {"epoch": epoch, "identity": identity, "owner": owner})
            for store_id, store in STORES.items():
                if store.owner != owner or store.tree or store_id == "A":
                    continue
                self._fault(f"read:{store_id}")
                document, selected = input_document(source, store_id)
                if document is None and store_id != "U":
                    continue
                if store_id == "C":
                    item = source.item(selected)
                    consumed.add(selected)
                    content = item.path.read_bytes()
                    rebased = rebase(document, "C", moved)
                    if document != rebased:
                        files.write_bytes(stage / "source-recovery" / item.relative, content)
                        content = toml.dumps(rebased).encode()
                    files.write_bytes(stage / store.current, content)
                    load_document("C", stage / store.current)
                    continue
                source_doc = ui if store_id == "U" else document
                if store_id == "U" and document is not None and not documents_equal(document, source_doc):
                    files.write_document(stage / "source-recovery" / "identity" / store.legacy, document)
                translated, shape = self._translate(stage, store_id, store.current, source_doc, moved)
                conversions[store.current] = {"source": selected, "shape": shape}
                counts[store_id] = count_records(store_id, translated)
                if selected == store.legacy:
                    consumed.add(selected)
                previous_name = f"{store.legacy}.prev"
                if store.previous and (previous := source.item(previous_name)):
                    try:
                        previous_doc = load_document(store_id, previous.path, generation=1)
                    except ValueError:
                        pass
                    else:
                        _, previous_shape = self._translate(
                            stage, store_id, f"{store.current}.prev", previous_doc, moved
                        )
                        conversions[f"{store.current}.prev"] = {"source": previous_name, "shape": previous_shape}
                        consumed.add(previous_name)
            self._status(progress={"done": 0, "total": len(source.items)})
            for index, item in enumerate(source.items, 1):
                if item.relative not in consumed:
                    self._transfer(stage, item, conversions, moved)
                self._status(progress={"done": index, "total": len(source.items)})
            if owner == "library":
                self._capture_game_inputs(source, stage)
                self._record_original_digests(stage, source)
                counts.update(verify_library(stage, check_game=False))
            _write_inverses(stage, conversions)
            self._seal(stage, epoch, identity, owner, source, counts)
            self._log(f"Verified {owner} epoch {epoch} with counts {counts}")
            return stage, destination
        except BaseException:
            shutil.rmtree(stage, ignore_errors=True)
            raise

    def _translate(
        self,
        stage: Path,
        store_id: str,
        relative: str,
        document: Document,
        moved: tuple[Path, Path] | None,
    ) -> tuple[Document, list[Document]]:
        rebased = rebase(document, store_id, moved)
        translated, shape = translate(store_id, rebased)
        path = stage / relative
        files.write_document(path, translated)
        self._fault(f"write:{store_id}")
        checked = load_document(store_id, path)
        if not documents_equal(inverse_translate(store_id, checked, shape), rebased):
            raise InvalidStore(f"Inverse translation differs for {relative}")
        if count_records(store_id, checked) != count_records(store_id, rebased, generation=1):
            raise InvalidStore(f"Record counts differ for {relative}")
        if not documents_equal(rebased, document):
            files.write_document(stage / "source-recovery" / "rebased" / relative, document)
        return checked, shape

    def _transfer(
        self,
        stage: Path,
        item: InputFile,
        conversions: dict,
        moved: tuple[Path, Path] | None,
    ) -> None:
        store_id, relative = item.store, item.relative
        store = STORES.get(store_id)
        if store_id in _JOURNALS and item.path.name == "journal.json":
            document = load_document(store_id, item.path, generation=1)
            _, shape = self._translate(stage, store_id, relative, document, moved)
            conversions[relative] = {"source": relative, "shape": shape}
            return
        if store_id == "A" or store is not None and store.tree:
            self._fault(f"pin:{store_id}")
            files.pin(item.path, stage / _to_stage_path(item), item.sha256)
        else:
            raw = item.path.read_bytes()
            if hashlib.sha256(raw).hexdigest() != item.sha256:
                raise InvalidStore(f"Legacy recovery input changed: {item.path}")
            files.write_bytes(stage / "source-recovery" / relative, raw)

    @staticmethod
    def _record_original_digests(stage: Path, source: Snapshot) -> None:
        digests = {
            item.path.name: item.sha256
            for item in source.items
            if item.store == "BO" and Path(item.relative).parent == Path("originals")
        }
        if not digests:
            return
        index = stage / "originals.json"
        document = load_document("O", index) if files.present(index) else {"version": GENERATION, "officials": {}}
        for name, digest in document.get("digests", {}).items():
            if name in digests and digests[name] != digest:
                raise InvalidStore(f"Conflicting kept official digest: {name}")
        files.write_document(index, {**document, "digests": {**document.get("digests", {}), **digests}})

    def _capture_game_inputs(self, source: Snapshot, stage: Path) -> None:
        receipts = {}
        for journal in sorted((stage / "transactions").glob("*/journal.json")):
            document = load_document("T", journal)
            for game_file, sha1 in game_recovery_inputs(source.root, document):
                sha256 = files.digest(game_file)
                target = stage / "recovery-inputs" / sha256
                if not files.present(target):
                    try:
                        files.pin(game_file, target, sha256)
                    except files.UnpinnablePayload as error:
                        raise RecoveryPending(f"Cannot retain the verified game recovery input: {game_file}") from error
                if files.digest(target, "sha1") != sha1:
                    raise RecoveryPending(f"Game-side recovery bytes changed: {game_file}")
                receipts.setdefault(journal.parent.name, {})[str(game_file)] = sha256
        if receipts:
            files.write_document(stage / "recovery-inputs" / "index.json", receipts)

    def _seal(
        self,
        stage: Path,
        epoch: str,
        identity: str,
        owner: str,
        source: Snapshot | None,
        counts: dict,
    ) -> None:
        entries = [
            {
                "path": path.relative_to(stage).as_posix(),
                "length": files.owned_stat(path).st_size,
                "sha256": files.digest(path),
            }
            for path in files.tree_files(stage)
            if path.name != "seal.json"
        ]
        files.write_document(
            stage / "seal.json",
            {
                "format": GENERATION,
                "epoch": epoch,
                "identity": identity,
                "owner": owner,
                "files": entries,
                "counts": counts,
                "inputs": [] if source is None else [item.receipt() for item in source.items],
            },
        )
        self._fault(f"seal:{owner}")
        for directory, _, _ in os.walk(stage, topdown=False):
            files.sync_directory(Path(directory))

    def _validate_epoch(self, epoch: Path, identity: str, owner: str) -> None:
        for path in (epoch.parent.parent, epoch.parent, epoch):
            if not files.present(path) or not path.is_dir():
                raise RecoveryPending(f"Missing established epoch: {epoch}")
        seal = read_record(epoch / "seal.json")
        if (seal.get("format"), seal.get("epoch"), seal.get("identity"), seal.get("owner")) != (
            GENERATION,
            epoch.name,
            identity,
            owner,
        ):
            raise RecoveryPending(f"Epoch seal has the wrong identity: {epoch}")
        if not files.present(epoch / "opened.json"):
            inventory = {path.relative_to(epoch).as_posix() for path in files.tree_files(epoch)}
            declared = {entry["path"] for entry in seal["files"]}
            if inventory != declared | {"seal.json"}:
                raise RecoveryPending(f"Epoch seal has a different file set: {epoch}")
            for entry in seal["files"]:
                path = epoch / entry["path"]
                if files.owned_stat(path).st_size != entry["length"] or files.digest(path) != entry["sha256"]:
                    raise RecoveryPending(f"Epoch seal digest differs: {path}")
        elif read_record(epoch / "opened.json") != {"epoch": epoch.name, "identity": identity}:
            raise RecoveryPending(f"Invalid epoch opening receipt: {epoch}")
        for store_id, store in STORES.items():
            if store.owner != owner or store.tree or store_id == "A":
                continue
            path = epoch / store.current
            if files.present(path):
                load_document(store_id, path)
            if store.previous and files.present(path.with_name(f"{path.name}.prev")):
                load_document(store_id, path.with_name(f"{path.name}.prev"))

    def _publish_head(self, root: Path, previous: Document | None, proposed: Document) -> None:
        self._advance_step("verify")
        self._phase("publish")
        private = namespace(root)
        path = root / LIBRARY_MANIFEST
        raw = path.read_bytes() if previous is not None else None
        sequence = previous["sequence"] if previous else 0
        proposed["sequence"] = sequence + 1
        proposed["predecessor"] = hashlib.sha256(raw).hexdigest() if raw is not None else None
        if raw is not None:
            receipt = private / "heads" / f"{sequence}.json"
            if files.present(receipt) and receipt.read_bytes() != raw:
                raise RecoveryPending("The previous head receipt differs")
            files.write_bytes(receipt, raw)
            self._fault("head:previous")
        self._fault("head:beforeReplace")
        encoded = encode_manifest("library", proposed)
        try:
            files.write_document(path, encoded)
        except OSError as error:
            try:
                if maybe_read_record(path) == encoded:
                    if raw is None:
                        path.unlink()
                        files.sync_directory(path.parent)
                    else:
                        files.write_bytes(path, raw)
            except (OSError, ValueError) as rollback:
                raise RecoveryPending("Could not determine the durable head after a failed publication") from rollback
            raise error
        self._fault("head:durable")

    def _open(self, data: Path, library: Path, source: Path, route: Document, head: Document) -> None:
        self._advance_step("verify")
        self._phase("open")
        member = head["members"][route["dataId"]]
        d_epoch = epoch_path(data, "data", member["epoch"])
        l_epoch = epoch_path(library, "library", head["epoch"])
        for epoch, identity, owner in ((d_epoch, route["dataId"], "data"), (l_epoch, head["libraryId"], "library")):
            self._validate_epoch(epoch, identity, owner)
        self._advance_step("open")
        if not route.get("established") or route.get("epoch") != member["epoch"]:
            route = {**route, "established": True}
            write_route(data, route, member["epoch"])
        verify_library(l_epoch, check_game=True)
        for epoch, identity in ((d_epoch, route["dataId"]), (l_epoch, head["libraryId"])):
            if not files.present(epoch / "opened.json"):
                files.write_document(epoch / "opened.json", {"epoch": epoch.name, "identity": identity})
        if head.get("prerequisite") and not head.get("activated"):
            self._publish_head(library, head, {**head, "activated": True})
        self._fault("open:ready")
        paths = CommittedPaths(data, library, source, d_epoch, l_epoch, route["dataId"], head["libraryId"])
        self._status(notice=_upgrade_notice(paths.ui_state))
        self._carry_library_caches(paths)
        self.paths = paths

    def _carry_library_caches(self, paths: CommittedPaths) -> None:
        for store_id in ("V", "W", "Q"):
            store = STORES[store_id]
            source = paths.legacy_library / store.legacy
            target = paths.library_cache / store.current
            try:
                if not files.present(source) or files.present(target):
                    continue
                if store_id != "Q":
                    files.write_bytes(target, source.read_bytes())
                    continue
                files.make_directory(target.parent)
                with tempfile.TemporaryDirectory(prefix=".poses-", dir=target.parent) as directory:
                    staged = Path(directory) / target.name
                    staged.mkdir()
                    for pose in source.glob("*.npz"):
                        if files.present(pose) and pose.is_file():
                            shutil.copy2(pose, staged / pose.name)
                    files.replace_path(staged, target)
            except (OSError, InvalidStore) as error:
                self._log(f"Could not reuse {store.legacy}: {error}")

    def _freeze(self, root: Path, owner: str, old_key: str, identity: str) -> str:
        source = epoch_path(root, owner, old_key)
        key = uuid.uuid4().hex
        destination = epoch_path(root, owner, key)
        self._copy_epoch(source, destination, key, identity, owner)
        return key

    def _copy_epoch(
        self,
        source: Path,
        destination: Path,
        key: str,
        identity: str,
        owner: str,
        *,
        ui_document: Document | None = None,
    ) -> None:
        stage = destination.parent.parent / f".stage-{key}"
        files.make_directory(stage)
        try:
            for payload in files.tree_files(source):
                relative = payload.relative_to(source)
                if relative.as_posix() in ("seal.json", "opened.json", "stage.json"):
                    continue
                if ui_document is not None and relative.as_posix() == "webui.json":
                    files.write_document(stage / relative, ui_document)
                else:
                    files.pin(payload, stage / relative, files.digest(payload))
            self._seal(stage, key, identity, owner, None, {})
            files.make_directory(destination.parent)
            files.replace_path(stage, destination)
            self._validate_epoch(destination, identity, owner)
        finally:
            if stage.is_dir():
                shutil.rmtree(stage, ignore_errors=True)

    def relocate(
        self,
        target: Path,
        *,
        adopt: bool = False,
    ) -> CommittedPaths:
        """Publish a full library snapshot at a destination, then switch the durable route."""
        with self._operation:
            paths = self.paths
            if paths is None:
                raise RecoveryPending("An open generation is required for relocation")
            target = target.expanduser().resolve()
            devmode.check_read(target)
            if target == paths.library_root:
                return paths
            if target.is_relative_to(paths.library_root / NAMESPACE) or paths.library_root.is_relative_to(target):
                raise InvalidStore("The source and target library locations overlap")
            self._volume(target)
            previous_probe = read_head(target)
            snapshot = inventory(target, "library") if previous_probe is None else None

            with ExitStack() as target_lease:
                files.make_directory(target)
                self._lock(target_lease, namespace(target) / "session.lock", "busy")
                route = read_route(paths.data_root)
                source = read_head(paths.library_root)
                previous = read_head(target)
                if previous != previous_probe:
                    raise UpgradeBlocked("busy", "The destination library changed during discovery")
                legacy_target = inventory(target, "library") if previous is None else None
                if not adopt and (previous is not None or legacy_target and legacy_target.items):
                    adoptable = previous is not None or any(
                        item.store in {"S", "P", "F"} for item in legacy_target.items
                    )
                    raise ServiceError("library_target_not_empty", path=target, adoptable=adoptable)
                member = copy.deepcopy(source["members"][route["dataId"]])
                token = uuid.uuid4().hex
                if previous is None:
                    identity = uuid.uuid4().hex
                    key = uuid.uuid4().hex
                    if legacy_target and legacy_target.items:
                        with ExitStack() as fences:
                            for lock in legacy_fences(paths.data_root, target):
                                self._lock(fences, lock, "olderVersion")
                            before = inventory(target, "library")
                            if snapshot is not None and before.fingerprint != snapshot.fingerprint:
                                raise UpgradeBlocked("olderVersion", "Destination data changed during adoption")
                            stage, destination = self._stage(before, target, "library", key, identity, None)
                            try:
                                if before.fingerprint != inventory(target, "library").fingerprint:
                                    raise UpgradeBlocked("olderVersion", "Destination data changed during adoption")
                                files.make_directory(destination.parent)
                                files.replace_path(stage, destination)
                            finally:
                                if stage.is_dir():
                                    shutil.rmtree(stage, ignore_errors=True)
                    else:
                        self._copy_epoch(
                            paths.library_epoch,
                            epoch_path(target, "library", key),
                            key,
                            identity,
                            "library",
                        )
                    proposed = {
                        **(copy.deepcopy(source) if not legacy_target or not legacy_target.items else {}),
                        "format": GENERATION,
                        "libraryId": identity,
                        "epoch": key,
                        "members": {},
                        "backups": [],
                        "activated": False,
                        "prerequisite": {"data": str(paths.data_root), "token": token, "libraryId": identity},
                    }
                    for backup in source["backups"]:
                        new_key = uuid.uuid4().hex
                        self._copy_epoch(
                            epoch_path(paths.library_root, "library", backup["libraryEpoch"]),
                            epoch_path(target, "library", new_key),
                            new_key,
                            identity,
                            "library",
                        )
                        proposed["backups"].append({**backup, "libraryEpoch": new_key})
                else:
                    identity, key = previous["libraryId"], previous["epoch"]
                    proposed = copy.deepcopy(previous)
                target_epoch = epoch_path(target, "library", key)
                self._validate_epoch(target_epoch, identity, "library")
                verify_library(target_epoch, check_game=True)
                member["token"] = token
                proposed["members"][route["dataId"]] = member
                proposed.setdefault("locations", []).append(
                    {"library": str(paths.library_root), "route": route, "sequence": source["sequence"]}
                )
                self._publish_head(target, previous, proposed)
                prior_route = route
                route = {
                    **prior_route,
                    "library": path_record(target, paths.data_root),
                    "libraryId": identity,
                    "token": token,
                }
                route_path = paths.data_root / DATA_MANIFEST
                self._fault("relocation:beforeRoute")
                prior_bytes = route_path.read_bytes()
                encoded_route = encode_manifest("data", route)
                try:
                    files.write_document(route_path, encoded_route)
                    self._fault("relocation:route")
                    self._open(paths.data_root, target, paths.legacy_library, route, proposed)
                except (OSError, ValueError, UpgradeBlocked) as error:
                    try:
                        if maybe_read_record(route_path) == encoded_route:
                            files.write_bytes(route_path, prior_bytes)
                        self.paths = paths
                    except (OSError, ValueError) as rollback:
                        self.paths = None
                        raise RecoveryPending("Could not prove the active route after relocation failed") from rollback
                    raise error
                self._session.close()
                self._session = target_lease.pop_all()
                return self.paths

    def restore_backup(self, index: int) -> CommittedPaths:
        """Verify and publish a retained backup while keeping the current open pair."""
        with self._operation:
            paths = self.paths
            if paths is None:
                raise RecoveryPending("An open generation is required to restore a backup")
            head = read_head(paths.library_root)
            route = read_route(paths.data_root)
            backup = head["backups"][index]
            if backup["dataId"] != paths.data_id:
                raise InvalidStore("Backup belongs to another data directory")
            if any(any((paths.library_epoch / STORES[key].current).glob("*/journal.json")) for key in _JOURNALS):
                raise RecoveryPending("The current generation has a pending journal")
            old_data = epoch_path(paths.data_root, "data", backup["dataEpoch"])
            old_library = epoch_path(paths.library_root, "library", backup["libraryEpoch"])
            self._validate_epoch(old_data, paths.data_id, "data")
            self._validate_epoch(old_library, paths.library_id, "library")
            keys = {owner: uuid.uuid4().hex for owner in ("data", "library")}
            for owner, source, root, identity in (
                ("data", old_data, paths.data_root, paths.data_id),
                ("library", old_library, paths.library_root, paths.library_id),
            ):
                ui_document = None
                if owner == "data" and files.present(source / "webui.json"):
                    ui_document = load_document("U", source / "webui.json")
                    ui_document.setdefault("prefs", {})[_NOTICE_PREF] = _upgrade_notice(paths.ui_state)
                self._copy_epoch(
                    source,
                    epoch_path(root, owner, keys[owner]),
                    keys[owner],
                    identity,
                    owner,
                    ui_document=ui_document,
                )
            verify_library(epoch_path(paths.library_root, "library", keys["library"]), check_game=True)
            proposed = copy.deepcopy(head)
            member = proposed["members"][paths.data_id]
            proposed["backups"].append(
                {
                    "dataId": paths.data_id,
                    "dataEpoch": member["epoch"],
                    "libraryEpoch": head["epoch"],
                    "sequence": head["sequence"],
                }
            )
            member["epoch"] = keys["data"]
            proposed["epoch"] = keys["library"]
            self._publish_head(paths.library_root, head, proposed)
            self._open(paths.data_root, paths.library_root, paths.legacy_library, route, proposed)
            return self.paths

    def _legacy_game_roots(self) -> set[Path]:
        paths = self.paths
        if paths is None:
            return set()
        roots = set()
        head = read_head(paths.library_root)
        for member in head["members"].values():
            data = Path(member["path"])
            if not files.present(data):
                continue
            config_path = data / "config.toml"
            config = load_document("C", config_path, generation=1) if files.present(config_path) else {}
            for field in ("game_resource_dir", "game_resource_dir_japan"):
                value = (config or {}).get("Directories", {}).get(field)
                if isinstance(value, str) and value and Path(value).is_absolute():
                    roots.add(Path(value).resolve())
        legacy = paths.legacy_library
        if files.present(legacy / "profiles.json"):
            profiles = load_document("P", legacy / "profiles.json", generation=1)
            roots.update(Path(key).resolve() for key in profiles.get("folders", {}) if Path(key).is_absolute())
        if files.present(legacy / "state.json"):
            state = load_document("S", legacy / "state.json", generation=1)
            for key in state.get("applied", {}):
                path = Path(key)
                if path.is_absolute():
                    roots.add(path.parent.resolve())
        return roots

    @contextmanager
    def game_write(
        self,
        targets: list[Path],
        install: Path,
        is_official: Callable[[Path, str], bool],
    ) -> Iterator[None]:
        """Guard a game footprint using restore paths understood by older builds."""
        paths = self.paths
        if paths is None:
            raise ServiceError("library_recovery_pending")
        legacy = paths.legacy_library
        with ExitStack() as fences:
            try:
                if files.present(legacy):
                    self._lock(fences, legacy / "transactions.lock", "olderVersion")
                roots = self._legacy_game_roots()
            except (OSError, ValueError, UpgradeBlocked) as error:
                raise ServiceError("library_recovery_pending", file=legacy) from error
            shared = any(install.is_relative_to(root) or root.is_relative_to(install) for root in roots)
            if shared:
                for target in targets:
                    options = (legacy / "originals" / target.name, target.with_name(target.name + ".backup"))
                    if not any(
                        devmode.allows_read(path)
                        and files.present(path)
                        and path.resolve() == path
                        and is_official(path, target.name)
                        for path in options
                    ):
                        raise ServiceError("official_unavailable", file=target, folder=str(install))
            yield

    def classic_read_only(self) -> bool:
        """Return whether classic game-writing commands lack a proven private install."""
        try:
            return bool(self._legacy_game_roots())
        except (OSError, ValueError):
            return True

    def protected_destinations(self) -> set[Path]:
        """List authoritative destinations that general-purpose exports cannot modify."""
        paths = self.paths
        if paths is None:
            raise ServiceError("library_recovery_pending")
        protected = {paths.data_epoch, paths.library_epoch, paths.library_cache}
        for store in STORES.values():
            if store.owner == "library" and store.tree:
                protected.update({paths.legacy_library / store.legacy, paths.library_root / store.legacy})
        return protected

    def classic_protected_destinations(self) -> list[str]:
        """Return the managed store and shared game destinations Classic must preserve."""
        try:
            game_roots = self._legacy_game_roots()
        except (OSError, ValueError):
            game_roots = set()
        return sorted(str(path) for path in self.protected_destinations() | game_roots)

    def check_output(self, destination: Path) -> None:
        """Refuse an export path inside a legacy or committed authoritative store."""
        path = destination.resolve()
        if any(path.is_relative_to(root) for root in self.protected_destinations()):
            raise ServiceError("bad_params", key="outputFolder")
