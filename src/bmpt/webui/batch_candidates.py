"""What a batch import works with: its settings, its candidates and results, and the evidence a row shows."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from bmpt.spine import atlas_remap
from bmpt.core import devmode
from bmpt.engine import (
    AssetType,
    Bundle,
    SaveOptions,
    get_skel_version,
    no_log,
    parse_hex_bytes,
)
from ..host.config import install_needs_crc
from ..core.errors import ServiceError
from ..library import integrity
from ..library.rebuild import UpdateScope


logger = logging.getLogger("bmpt.webui.batch_import")  # batch import logs under one name

PREF = "batch_import"
TYPES = ("Texture2D", "TextAsset", "Mesh", "AnimationClip", "ALL")
CHOICES = {
    "matchStrategy": ("path_id", "containerNameType", "name_type"),
    "crcCorrection": ("auto", "on", "off"),
    "compression": ("lzma", "lz4", "original", "none"),
}
BOOLS = ("skipUnchanged", "createBackup", "enableAfterImport", "previewAfterImport")
IMPORTABLE = frozenset(("ready", "convert", "update"))
# Staging folders are named by the first hex digits of an id or digest. The bundle names inside keep their full form,
# so the folder names stay short enough for Windows paths (259 characters) with the longest official bundle names.
STAGING_DIGITS = 16


def staging_name(digest: str) -> str:
    """The short, stable name of the staging folder of an entry id or an original's digest."""
    return digest[:STAGING_DIGITS]


def options_of(value: Any, defaults: dict) -> dict:
    """Validate complete import options, with the defaults filling in what is missing.

    Args:
        value: The options as the page sends them or as they are saved; a dict without keys the defaults lack.
        defaults: The options the settings give (``BatchImport.defaults``).

    Returns:
        The options, with ``assetTypes`` deduplicated and ``outputFolder`` resolved to an absolute path.

    Raises:
        ServiceError: ``bad_params`` naming the invalid option.
    """
    if not isinstance(value, dict) or set(value) - set(defaults):
        raise ServiceError("bad_params", key="options")
    out = {**defaults, **value}
    for key, choices in CHOICES.items():
        if not isinstance(out[key], str) or out[key] not in choices:
            raise ServiceError("bad_params", key=key)
    if any(not isinstance(out[key], bool) for key in BOOLS):
        raise ServiceError("bad_params", key="options")
    if (
        not isinstance(out["assetTypes"], list)
        or not out["assetTypes"]
        or any(not isinstance(item, str) or item not in TYPES for item in out["assetTypes"])
    ):
        raise ServiceError("bad_params", key="assetTypes")
    out["assetTypes"] = list(dict.fromkeys(out["assetTypes"]))
    if not isinstance(out["threads"], int) or isinstance(out["threads"], bool) or not 1 <= out["threads"] <= 64:
        raise ServiceError("bad_params", key="threads")
    if not isinstance(out["extraBytes"], str) or (out["extraBytes"] and parse_hex_bytes(out["extraBytes"]) is None):
        raise ServiceError("bad_params", key="extraBytes")
    if out["outputFolder"] is not None and (
        not isinstance(out["outputFolder"], str) or not out["outputFolder"].strip()
    ):
        raise ServiceError("bad_params", key="outputFolder")
    if out["outputFolder"] is not None:
        out["outputFolder"] = str(Path(out["outputFolder"]).expanduser().resolve())
        devmode.check_read(Path(out["outputFolder"]))
    return out


def engine_match_strategy(value: str) -> str:
    """Translate the container matching option for the engine."""
    return "cont_name_type" if value == "containerNameType" else value


class ImportSettings:
    """A read-only overlay; the classic config and the library's shared settings stay intact."""

    def __init__(self, base: Any, options: dict):
        """Overlay import options on the settings.

        Args:
            base: The settings.
            options: Validated import options (``options_of``).
        """
        self.base, self.options = base, options

    def __getattr__(self, name: str) -> Any:
        """Read any other setting from the base settings."""
        return getattr(self.base, name)

    def asset_types(self) -> set[str]:
        """Return the asset types the options select."""
        return set(self.options["assetTypes"])

    def crc_needed(self, target: Path | None = None) -> bool:
        """Decide whether bundles get their CRC corrected.

        Args:
            target: Accepted for the settings' interface; it does not change the answer.

        Returns:
            The option's choice (``on`` or ``off``); ``auto`` follows the selected install, as
            ``Settings.crc_needed`` does.
        """
        setting = self.options["crcCorrection"]
        if setting != "auto":
            return setting == "on"
        return install_needs_crc(self.base.get("file_source"))

    def match_strategy(self) -> str:
        """Return the engine's matching discriminator."""
        return engine_match_strategy(self.options["matchStrategy"])

    def save_options(self, perform_crc: bool) -> SaveOptions:
        """Build the options for saving a bundle, with the compression and extra bytes of the import options.

        Args:
            perform_crc: Whether the saved bundle gets its CRC corrected.
        """
        return SaveOptions(
            perform_crc=perform_crc,
            compression=self.options["compression"],
            extra_bytes=parse_hex_bytes(self.options["extraBytes"]),
        )


@dataclass
class Candidate:
    """One import row: the files of one mod and what the analysis found about them.

    ``files`` maps file names to paths; ``entry`` is the row the page shows. The other fields hold the analysis for
    the import itself: its error, its identity (what the files are), the official targets and originals, how the
    files are converted or copied, and the missing parts.
    """

    files: dict[str, Path]
    entry: dict
    error: str | None = None
    identity: frozenset = frozenset()
    targets: list[Path] = field(default_factory=list)
    originals: list[Path] = field(default_factory=list)
    spine_version: str | None = None
    source_override: dict | None = None
    conversion: bool = False
    rebuild: bool = False
    not_transferred: list[str] = field(default_factory=list)
    failure: dict | None = None
    direct: dict[Path, Path] = field(default_factory=dict)
    provenance: dict | None = None
    missing_cabs: set[str] = field(default_factory=set)
    # the import preview composed this row (``BatchImport.preview``): a refusal of such a row is marked in reports
    previewed: bool = False
    # the trusted package manifest beside this row's files that lists them all (``ba_mod.read``)
    package: dict | None = None


@dataclass
class ImportResult:
    """What an import run did: the result of each row, and what it added to the library."""

    results: list[dict]
    cores: set[str] = field(default_factory=set)
    resource_types: set[str] = field(default_factory=set)
    added: list[Any] = field(default_factory=list)
    mods_changed: bool = True

    def to_json(self) -> dict:
        """Return the per-row results, as the page gets them."""
        return {"results": self.results}


def claimed_first(choices: list[dict], package: dict | None) -> list[dict]:
    """Order the guessed sources of a row, the one its trusted package manifest names first.

    A claim only orders the guesses the files allow; it never adds one or rules one out.

    Args:
        choices: The guessed sources, each with a ``server`` and a ``platform``.
        package: The row's package manifest (``ba_mod.read``); None leaves the order alone.
    """
    if not package:
        return choices
    claim = (package["target"]["server"], package["target"]["platform"])
    return sorted(choices, key=lambda choice: (choice["server"], choice["platform"]) != claim)


def source_file(candidate: Candidate) -> str:
    """The file an error about a whole entry names: its first source, which the list shows as the entry's file."""
    return str(min(candidate.files.values()))


def blocked_error(candidate: Candidate) -> ServiceError:
    """The error of a blocked candidate: its code with the details recorded when it was analyzed, and a file."""
    params = dict(candidate.failure["params"]) if candidate.failure else {}
    params.setdefault("file", source_file(candidate))
    return ServiceError(candidate.error, **params)


def blocked_reason(error: ServiceError) -> str:
    """Map the error that blocks a row to the reason the page shows.

    Args:
        error: The error of the analysis.

    Returns:
        The error's code when it is a known reason, a grouped reason for a few codes (``unreadable``,
        ``no_target``, ``unsupported``), else ``failed``.
    """
    aliases = {
        "bundle_invalid": "unreadable",
        "game_file_not_found": "no_target",
        "mod_unsupported": "unsupported",
        "source_unsupported": "unsupported",
    }
    reasons = {
        "mod_platform_unknown",
        "library_io_error",
        "mod_target_ambiguous",
        "mod_target_unknown",
        "mod_parts_missing",
        "mod_release_mismatch",
        "game_file_conflict",
        "mod_platform_mismatch",
        "mod_unity_version_mismatch",
        "required_asset_extract_failed",
        "required_asset_apply_failed",
        "required_assets_unmatched",
        "spine_version",
        "source_unknown",
        "source_conflict",
        "platform_unsupported",
        "target_unknown",
        "conversion_names",
        "no_matching_assets",
        "conversion_failed",
        "entry_changed",
        "official_unavailable",
        "official_invalid",
        "conflict",
        "unreadable",
        "unsupported",
        "failed",
    }
    return aliases.get(error.code, error.code if error.code in reasons else "failed")


#: The unresolved CABs an incomplete entry names, and the bundles whose summary a refused entry carries
EVIDENCE_CABS = 40
EVIDENCE_BUNDLES = 20


def missing_detail(cabs: set[str], found: list[dict]) -> dict:
    """Build an incomplete entry's ``missing``.

    Args:
        cabs: The CABs the entry references that neither its files, the install nor the other dropped files hold.
        found: The unselected files that complete it.

    Returns:
        ``unresolved`` (how many CABs), the first ``EVIDENCE_CABS`` of them as ``cabs``, and ``found``.
    """
    return {"unresolved": len(cabs), "cabs": sorted(cabs)[:EVIDENCE_CABS], "found": found}


def bundle_evidence(candidate: Candidate, inspection: integrity.Inspection) -> list[dict]:
    """Summarize what the check read of an entry's files, for the error report and the log.

    A refusal from the files' format can then be told from one guessed from their names or targets.

    Args:
        candidate: The entry.
        inspection: The check's inspection, whose ``summary`` gives what it read of a file.

    Returns:
        A row per file, at most ``EVIDENCE_BUNDLES`` of them, with the file's name and its summary.
    """
    rows = []
    for path in sorted(candidate.files.values())[:EVIDENCE_BUNDLES]:
        summary = inspection.summary(path)
        if summary is not None:
            rows.append({"file": path.name, **summary})
    return rows


def target_evidence(entry: dict, resolution: Any, attempts: list[dict]) -> None:
    """Record what a refused entry's check found of its targets, for the error report and the log.

    Per file that is how the resolver matched it and the official parts and dates it found
    (``Resolution.evidence``); for a guessed source it is every server and platform tried, with its failure.

    Args:
        entry: The row, which gets ``resolution`` and ``attempts``.
        resolution: The resolver's result; None when there was none.
        attempts: The sources tried, each with its failure.
    """
    if resolution is not None:
        try:
            entry["resolution"] = resolution.evidence()[:EVIDENCE_BUNDLES]
        except (ServiceError, OSError, ValueError):
            logger.debug("Batch import: no target evidence", exc_info=True)
    if attempts:
        entry["attempts"] = attempts


def failure_detail(error: ServiceError, paths: Any, log: Any = no_log, *, secrets: set[str] | None = None) -> dict:
    """Log a refused entry's error, redacted, and return it as JSON.

    Args:
        error: The error.
        paths: The entry's files, which the log record lists.
        log: Receives the logged message too.
        secrets: Secret values to hide from the record; None hides none.

    Returns:
        The error as ``ServiceError.to_json`` gives it.
    """
    from .diagnostics import clean

    detail = error.to_json()
    safe = clean({"files": list(map(str, paths)), **detail}, secrets or set())
    message = f"Batch import: {json.dumps(safe, ensure_ascii=False, sort_keys=True)}"
    logger.warning(message)
    log(message)
    return detail


def copy_compatibility(report: dict, uploaded: integrity.Contents) -> tuple[dict, dict, bool]:
    """Collect the Spine versions that decide whether a direct copy fits the retained official parts.

    A direct copy installs every uploaded asset alongside them.

    Args:
        report: The library's compatibility report for the copy: its official skeletons and ``misfit``.
        uploaded: The uploaded files' contents.

    Returns:
        The official Spine versions per uploaded skeleton name (a set), the uploaded skeletons' own versions, and
        whether a page size mismatch refuses the copy (``misfit``).
    """
    skeletons = report.get("official", {}).get("skeletons", {})
    incoming = {name: get_skel_version(data) for name, data in uploaded.skeletons.items()}
    versions = {data["version"] for data in skeletons.values()} or {None}
    official = {name: {skeletons[name]["version"]} if name in skeletons else versions for name in incoming}
    # A page size the texture does not fit refuses the copy; what the official rig changed does not (the library's
    # compatibility check tells it and offers the remap).
    return official, incoming, report.get("misfit", False)


def transfer_compatibility(
    inspection: integrity.Inspection, sources: list[Path], targets: list[Path], originals: list[Path], strategy: str
) -> tuple[dict, dict, bool]:
    """Check transferred Spine inputs against their counterparts and retained official parts.

    Args:
        inspection: The inspection that reads the bundles; its asset types say what is transferred.
        sources: The uploaded bundles.
        targets: The bundles the transfer writes into.
        originals: The official bundles whose parts are retained.
        strategy: How assets are matched, one of ``CHOICES["matchStrategy"]``.

    Returns:
        The official Spine versions per matched skeleton (a set), the uploaded skeletons' versions, and whether
        atlas pages that are not transferred mismatch the transferred textures in size, which a remap could solve.

    Raises:
        ServiceError: A bundle is unreadable (raised by ``inspection.validate``).
    """
    scope = UpdateScope([inspection.bundle(path) for path in targets], strategy, inspection.asset_types)
    key_of = Bundle._get_key_func(strategy)
    incoming, official = {}, {}
    atlases, textures, transferred_atlases, transferred_textures = {}, {}, set(), {}
    for path in originals:
        contents = inspection.inspect(path)
        atlases.update(contents.atlases)
        textures.update(contents.textures)
    for path in sources:
        contents = inspection.inspect(path, allow_unknown=True)
        for obj in inspection.bundle(path).env.objects:
            if obj.type not in (AssetType.TextAsset, AssetType.Texture2D):
                continue
            if "ALL" not in inspection.asset_types and obj.type.name not in inspection.asset_types:
                continue
            key = key_of(obj)
            if key not in scope.counterparts:
                continue
            name = obj.peek_name()
            for target, counterpart in scope.counterparts[key]:
                target_name = counterpart.peek_name()
                if obj.type == AssetType.TextAsset and name in contents.skeletons:
                    incoming[key] = get_skel_version(contents.skeletons[name])
                    data = inspection.inspect(target.path).skeletons.get(target_name)
                    official.setdefault(key, set()).add(get_skel_version(data) if data is not None else None)
                elif obj.type == AssetType.TextAsset and name in contents.atlases:
                    transferred_atlases.add(target_name)
                elif obj.type == AssetType.Texture2D:
                    size = contents.textures.get(integrity.texture_key(name))
                    if size is not None:
                        transferred_textures[integrity.texture_key(target_name)] = size
    try:
        mixed = {
            name: data
            for name, data in atlases.items()
            if name not in transferred_atlases
            and any(
                integrity.texture_key(page) in transferred_textures
                for page in atlas_remap.parse_atlas(data, name).pages
            )
        }
        remappable = bool(mixed) and atlas_remap.page_size_mismatch(mixed, {**textures, **transferred_textures})
    except (ValueError, UnicodeError):
        remappable = False
    inspection.validate()
    return official, incoming, remappable
