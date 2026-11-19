"""Read, write, and redirect bundle IDs in a CCDB content catalog."""

from __future__ import annotations

import re
import struct
import zlib
from collections.abc import Mapping
from dataclasses import dataclass, replace

_MAGIC = b"CCDB"
_VERSION = 1
_HEADER = _MAGIC + bytes([_VERSION])
_INT32 = struct.Struct("<i")
_LOAD_PREFIX = "{PlatformUtils.AddressableLoadPath}"
_PRELOAD_PREFIX = "{PlatformUtils.AddressablePreLoadPath}"
_PREFIXES = {prefix.encode(): prefix for prefix in (_LOAD_PREFIX, _PRELOAD_PREFIX)}
_PLACED_NAME = re.compile(r"[0-9a-f]{40}\.bundle")
_CRC_MASK = 0xFFFFFFFF
_CRC_POLYNOMIAL = 0xEDB88320


class CatalogFormatError(ValueError):
    """A catalog or redirect that failed validation, with a machine-readable reason."""

    def __init__(self, reason: str, message: str) -> None:
        """Record the reason code and a description of the failure."""
        super().__init__(message)
        self.reason = reason


@dataclass
class Catalog:
    """The ordered CCDB fields, with strings and binary tables stored as bytes."""

    version: int
    locator: bytes
    instance: bytes
    scene: bytes
    providers: list[bytes]
    provider_ids: list[bytes]
    ids: list[bytes]
    keys: bytes
    buckets: bytes
    entries: bytes
    extra: bytes
    types: list[bytes]
    tail: list[bytes]


class _Reader:
    def __init__(self, raw: bytes | memoryview) -> None:
        self.raw = raw
        self.offset = len(_HEADER)

    def _advance(self, size: int) -> int:
        start = self.offset
        if not 0 <= size <= len(self.raw) - start:
            raise CatalogFormatError("roundtrip", f"Catalog length is out of bounds at byte {start}.")
        self.offset += size
        return start

    def _int32(self) -> int:
        return _INT32.unpack_from(self.raw, self._advance(_INT32.size))[0]

    def _bytes(self, size: int) -> bytes:
        start = self._advance(size)
        return bytes(self.raw[start : self.offset])

    def string(self) -> bytes:
        size = 0
        for shift in range(0, 28, 7):
            byte = self.raw[self._advance(1)]
            size |= (byte & 0x7F) << shift
            if byte < 0x80:
                return self._bytes(size)
        last = self.raw[self._advance(1)]
        if last > 0x07:
            raise CatalogFormatError("roundtrip", "Catalog string length exceeds a signed 32-bit integer.")
        return self._bytes(size | (last << 28))

    def strings(self) -> list[bytes]:
        count = self._int32()
        # An empty string still needs one byte for its length.
        if not 0 <= count <= len(self.raw) - self.offset:
            raise CatalogFormatError("roundtrip", f"Catalog string count is out of bounds at byte {self.offset}.")
        return [self.string() for _ in range(count)]

    def blob(self) -> bytes:
        return self._bytes(self._int32())


def parse(raw: bytes | memoryview) -> Catalog:
    """Parse a complete CCDB catalog, checking all lengths against the input.

    Args:
        raw: The catalog bytes, including the header and all fields.

    Returns:
        The parsed fields in their original order.

    Raises:
        CatalogFormatError: The header or body is invalid, or extra bytes follow the fields.
    """
    if isinstance(raw, memoryview):
        try:
            raw = raw.cast("B") if raw.c_contiguous else raw.tobytes()
        except ValueError as error:
            raise CatalogFormatError("roundtrip", "Catalog bytes are unavailable.") from error
    if raw[: len(_HEADER)] != _HEADER:
        raise CatalogFormatError("version", "Unsupported catalog magic or version.")

    reader = _Reader(raw)
    catalog = Catalog(
        version=_VERSION,
        locator=reader.string(),
        instance=reader.string(),
        scene=reader.string(),
        providers=reader.strings(),
        provider_ids=reader.strings(),
        ids=reader.strings(),
        keys=reader.blob(),
        buckets=reader.blob(),
        entries=reader.blob(),
        extra=reader.blob(),
        types=reader.strings(),
        tail=reader.strings(),
    )
    if reader.offset != len(raw):
        raise CatalogFormatError("trailing", "Unexpected bytes follow the catalog fields.")
    return catalog


def write(catalog: Catalog) -> bytes:
    """Serialize the catalog fields with canonical length encodings.

    Args:
        catalog: The fields to serialize.

    Returns:
        The catalog bytes, ending with the last field.
    """
    out = bytearray(_MAGIC)
    out.append(catalog.version)

    def string(value: bytes) -> None:
        size = len(value)
        while size >= 0x80:
            out.append((size & 0x7F) | 0x80)
            size >>= 7
        out.append(size)
        out.extend(value)

    def strings(values: list[bytes]) -> None:
        out.extend(_INT32.pack(len(values)))
        for value in values:
            string(value)

    string(catalog.locator)
    string(catalog.instance)
    string(catalog.scene)
    strings(catalog.providers)
    strings(catalog.provider_ids)
    strings(catalog.ids)
    for blob in (catalog.keys, catalog.buckets, catalog.entries, catalog.extra):
        out.extend(_INT32.pack(len(blob)))
        out.extend(blob)
    strings(catalog.types)
    strings(catalog.tail)
    return bytes(out)


def _is_file_name(name: bytes) -> bool:
    return bool(name.rstrip(b". ")) and not any(char in name for char in (b"\\", b"/", b":", b"\0"))


def _bundle_index(catalog: Catalog, redirects: Mapping[str, str] | None = None) -> dict[str, tuple[int, str]]:
    index = {}
    for position, identifier in enumerate(catalog.ids):
        if not identifier.endswith(b".bundle"):
            continue
        prefix, _, raw_name = identifier.partition(b"\\")
        if prefix not in _PREFIXES or not _is_file_name(raw_name):
            continue
        name = raw_name.decode("utf-8", errors="surrogateescape")
        if redirects is not None and name not in redirects:
            continue
        if name in index:
            raise CatalogFormatError("ambiguous", f"Multiple catalog IDs use {name!r}.")
        index[name] = position, _PREFIXES[prefix]
    return index


def bundle_ids(catalog: Catalog) -> dict[str, tuple[int, str]]:
    """Index the bare bundle filenames under the load and preload prefixes.

    Args:
        catalog: The parsed catalog whose internal IDs are indexed.

    Returns:
        Each filename's internal ID index and prefix, with the separator omitted.

    Raises:
        CatalogFormatError: A filename occurs more than once across the two prefixes.
    """
    return _bundle_index(catalog)


def _crc_table() -> tuple[int, ...]:
    table = []
    for byte in range(256):
        register = byte
        for _ in range(8):
            register = (register >> 1) ^ (_CRC_POLYNOMIAL if register & 1 else 0)
        table.append(register)
    return tuple(table)


_CRC_TABLE = _crc_table()
_CRC_REVERSE = {value >> 24: index for index, value in enumerate(_CRC_TABLE)}


def crc_suffix(body_crc: int, target_crc: int) -> bytes:
    """Find the four bytes that change a running CRC32 to the target.

    Args:
        body_crc: The running CRC32 as returned by zlib.
        target_crc: The desired unsigned CRC32.

    Returns:
        Four bytes to append to the body.
    """
    # The high byte of each table entry selects a unique index when walking backwards.
    indices = []
    register = target_crc ^ _CRC_MASK
    for _ in range(4):
        index = _CRC_REVERSE[register >> 24]
        indices.append(index)
        register = ((register ^ _CRC_TABLE[index]) << 8) & _CRC_MASK

    suffix = bytearray()
    register = body_crc ^ _CRC_MASK
    for index in reversed(indices):
        suffix.append((register ^ index) & 0xFF)
        register = (register >> 8) ^ _CRC_TABLE[index]
    return bytes(suffix)


def redirect(official: bytes, redirects: Mapping[str, str], subdir: str) -> bytes:
    """Redirect load-path bundle IDs and preserve the official catalog's CRC32.

    Args:
        official: The complete official catalog bytes.
        redirects: Official bundle basenames mapped to content-addressed filenames.
        subdir: The single subfolder containing the placed bundles.

    Returns:
        The edited catalog followed by its four CRC adjustment bytes.

    Raises:
        CatalogFormatError: The official catalog, a redirect, or the generated result fails validation.
    """
    catalog = parse(official)
    if write(catalog) != official:
        raise CatalogFormatError("roundtrip", "Serializing the official catalog changed its bytes.")

    index = _bundle_index(catalog, redirects)
    try:
        folder = subdir.encode()
    except UnicodeEncodeError as error:
        raise CatalogFormatError("name", "Invalid bundle subfolder name.") from error
    if not _is_file_name(folder):
        raise CatalogFormatError("name", "Invalid bundle subfolder name.")

    ids = catalog.ids.copy()
    placed_names = set()
    for name, placed in redirects.items():
        if name not in index:
            raise CatalogFormatError("not_found", f"No catalog bundle ID uses {name!r}.")
        position, prefix = index[name]
        if prefix == _PRELOAD_PREFIX:
            raise CatalogFormatError("preload", f"Cannot redirect preload bundle {name!r}.")
        if not isinstance(placed, str) or not _PLACED_NAME.fullmatch(placed):
            raise CatalogFormatError("name", f"Invalid placed bundle name {placed!r}.")
        if placed in placed_names:
            raise CatalogFormatError("ambiguous", f"Multiple redirects use placed bundle name {placed!r}.")
        placed_names.add(placed)
        ids[position] = b"\\".join((prefix.encode(), folder, placed.encode()))

    edited = replace(catalog, ids=ids)
    result = write(edited)
    target_crc = zlib.crc32(official)
    result += crc_suffix(zlib.crc32(result), target_crc)
    if zlib.crc32(result) != target_crc:
        raise CatalogFormatError("crc", "The redirected catalog does not match the official CRC32.")

    try:
        reparsed = parse(memoryview(result)[:-4])
    except CatalogFormatError as error:
        raise CatalogFormatError("reparse", "The redirected catalog could not be parsed.") from error
    if reparsed != edited:
        raise CatalogFormatError("reparse", "The redirected catalog fields do not match the requested edits.")
    return result
