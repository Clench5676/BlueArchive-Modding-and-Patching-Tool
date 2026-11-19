"""Synthetic catalog fields shared by format and library tests."""

from bmpt.unity.ccdb import Catalog

LOAD = "{PlatformUtils.AddressableLoadPath}"
PRELOAD = "{PlatformUtils.AddressablePreLoadPath}"


def bundle_id(prefix, name):
    """Build one catalog internal ID."""
    return f"{prefix}\\{name}".encode()


def synthetic_catalog():
    """Build a small catalog with both official bundle path prefixes."""
    return Catalog(
        version=1,
        locator=b"locator",
        instance=b"instance",
        scene=b"scene",
        providers=[b"provider" * 30],
        provider_ids=[b"provider-id", b"other-provider"],
        ids=[
            bundle_id(LOAD, "a.bundle"),
            b"ui/asset",
            bundle_id(PRELOAD, "preload.bundle"),
            bundle_id(LOAD, "b.bundle"),
            bundle_id(LOAD, "folder\\nested.bundle"),
            b"https://example.invalid/remote.bundle",
        ],
        keys=b"keys\0",
        buckets=b"\xffbuckets",
        entries=b"entries",
        extra=b"extra",
        types=[b"type", b""],
        tail=[b"tail"],
    )
