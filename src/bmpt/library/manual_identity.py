"""Validate self-contained uploads without making claims about their install targets."""

from pathlib import Path

from ..unity import mod_origin
from ..core.errors import ServiceError
from bmpt.engine import parse_filename
from . import integrity
from .identity import content_cores
from .judge import resource_type_of


def validate(files: dict[str, Path], resource_type: str | None = None) -> str:
    """Require readable bundles and complete parts; unknown platforms remain storage-only.

    Known platform evidence must be supported and consistent. No choice made here authorizes installation.

    Args:
        files: Uploaded paths indexed by safe bundle basename, forming one complete mod.
        resource_type: Required library kind, or None to infer it from filenames and character content.

    Returns:
        The single inferred portrait, memorial lobby, or chibi kind.

    Raises:
        ServiceError: Files are unsupported, unreadable, ambiguous, incomplete, or inconsistent in platform.
    """
    refused = next((name for name in files if Path(name).name != name or not name.endswith(".bundle")), None)
    if not files:
        raise ServiceError("source_unsupported")  # no file to name
    if refused is not None:
        raise ServiceError("mod_unsupported", file=refused)
    inspection = integrity.Inspection()
    combined = integrity.Contents()
    resource_types = set()
    for name, path in files.items():
        contents = inspection.inspect(path, allow_unknown=True)
        known = contents.platforms - mod_origin.UNKNOWN_PLATFORMS
        if any(platform not in mod_origin.UNITY_PLATFORMS for platform in known):
            raise ServiceError("platform_unsupported", file=name)
        previous = combined.platforms - mod_origin.UNKNOWN_PLATFORMS
        if previous and known and previous != known:
            raise ServiceError(
                "mod_platform_mismatch",
                file=name,
                platform=", ".join(sorted(known)),
                expected=", ".join(sorted(previous)),
            )
        combined.merge(contents)
        parsed = parse_filename(name)
        declared = resource_type_of(parsed.core, parsed.category)
        if declared:
            resource_types.add(declared)
        for core in content_cores(path):
            detected = "chibi" if core.startswith("chibi:") else resource_type_of(core)
            if detected:
                resource_types.add(detected)
    if len(resource_types) != 1 or (resource_type is not None and resource_type not in resource_types):
        first = next(iter(files))
        raise ServiceError("mod_target_ambiguous" if len(resource_types) > 1 else "mod_unsupported", file=first)
    inferred = next(iter(resource_types))
    if inferred in ("portrait", "memorialLobby") and not combined.skeletons:
        raise ServiceError("mod_parts_missing", files="skeleton.skel")
    missing = integrity.missing_parts(combined)
    if missing:
        raise ServiceError("mod_parts_missing", files=", ".join(sorted(missing)))
    inspection.validate()
    return inferred
