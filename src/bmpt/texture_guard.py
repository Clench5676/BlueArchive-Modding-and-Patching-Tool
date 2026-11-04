"""Bound texture sizes before decoding.

A Texture2D declares its own width, height and format, and the decoders trust them. Every decode of a texture read
from a bundle goes through ``texture_image`` (or ``check_texture`` before a decode of its own), which refuses sizes
outside a sane range and image data shorter than the declared size needs, before any decoder runs.
"""

from __future__ import annotations

from typing import Any

from PIL import Image
from UnityPy.enums import BuildTarget, TextureFormat
from UnityPy.export import Texture2DConverter
from UnityPy.helpers import TextureSwizzler

from .errors import ServiceError

# Largest width or height accepted for a texture.
MAX_TEXTURE_SIDE = 16384

TF = TextureFormat


class TextureRefused(ValueError):
    """A texture whose declared size or image data is outside the bounds checked before decoding."""


# Bytes per pixel of the formats that carry plain pixel values.
_PIXEL_BYTES: dict[TextureFormat, int] = {
    TF.Alpha8: 1, TF.ARGB4444: 2, TF.RGB24: 3, TF.RGBA32: 4, TF.ARGB32: 4, TF.ARGBFloat: 16, TF.RGB565: 2,
    TF.BGR24: 3, TF.R16: 2, TF.RGBA4444: 2, TF.BGRA32: 4, TF.RHalf: 2, TF.RGHalf: 4, TF.RGBAHalf: 8, TF.RFloat: 4,
    TF.RGFloat: 8, TF.RGBAFloat: 16, TF.RGB9e5Float: 4, TF.RG16: 2, TF.R8: 1, TF.RG32: 4, TF.RGB48: 6,
    TF.RGBA64: 8, TF.R8_SIGNED: 1, TF.RG16_SIGNED: 2, TF.RGB24_SIGNED: 3, TF.RGBA32_SIGNED: 4, TF.R16_SIGNED: 2,
    TF.RG32_SIGNED: 4, TF.RGB48_SIGNED: 6, TF.RGBA64_SIGNED: 8,
}

# Bytes per block of the block-compressed formats (the block's size in pixels is UnityPy's own table).
_BLOCK_BYTES: dict[TextureFormat, int] = {
    TF.DXT1: 8, TF.DXT3: 16, TF.DXT5: 16, TF.BC4: 8, TF.BC5: 16, TF.BC6H: 16, TF.BC7: 16,
    TF.PVRTC_RGB2: 8, TF.PVRTC_RGBA2: 8, TF.PVRTC_RGB4: 8, TF.PVRTC_RGBA4: 8,
    TF.ETC_RGB4: 8, TF.ETC_RGB4_3DS: 8, TF.ETC_RGBA8_3DS: 16, TF.ETC2_RGB: 8, TF.ETC2_RGBA1: 8, TF.ETC2_RGBA8: 16,
    TF.EAC_R: 8, TF.EAC_R_SIGNED: 8, TF.EAC_RG: 16, TF.EAC_RG_SIGNED: 16,
    TF.ATC_RGB4: 8, TF.ATC_RGBA8: 16,
}

# UnityPy's block table has no entry for ATC, which decodes in 4x4 blocks: padded here.
_ATC = {TF.ATC_RGB4, TF.ATC_RGBA8}

# Largest GOB shift (a power of two of rows of blocks) accepted from a platform blob; real files use 0 to 5.
_MAX_GOB_SHIFT = 8


def _refuse(reason: str) -> TextureRefused:
    return TextureRefused(f"Texture2D bounds check failed: {reason}")


def _is_astc(texture_format: TextureFormat) -> bool:
    return texture_format.name.startswith("ASTC")


def supported(texture_format: TextureFormat) -> bool:
    """Whether the size a format needs is known: every format UnityPy decodes except the crunched ones, whose data
    cannot be bounded before it is unpacked."""
    return texture_format in _PIXEL_BYTES or texture_format in _BLOCK_BYTES or _is_astc(texture_format)


def required_size(width: int, height: int, texture_format: TextureFormat) -> int:
    """Bytes of image data the first mip level needs, from UnityPy's own padding."""
    if texture_format in _PIXEL_BYTES:
        return width * height * _PIXEL_BYTES[texture_format]
    if _is_astc(texture_format):
        block = Texture2DConverter.TEXTURE_FORMAT_BLOCK_SIZE_TABLE[texture_format]
        padded = Texture2DConverter.get_compressed_image_size(width, height, texture_format)
        return Texture2DConverter.calculate_astc_compressed_size(*padded, block)
    if texture_format in _BLOCK_BYTES:
        if texture_format in _ATC:
            block = (4, 4)
            padded = tuple(-(-side // 4) * 4 for side in (width, height))
        else:
            block = Texture2DConverter.TEXTURE_FORMAT_BLOCK_SIZE_TABLE[texture_format]
            padded = Texture2DConverter.get_compressed_image_size(width, height, texture_format)
        return (padded[0] // block[0]) * (padded[1] // block[1]) * _BLOCK_BYTES[texture_format]
    raise _refuse("unsupported format")


def _switch_padding(texture: Any, texture_format: TextureFormat, width: int, height: int):
    """The padded size and format of a Switch-swizzled texture (UnityPy's rules), None for any other platform."""
    reader = getattr(texture, "object_reader", None)
    blob = getattr(texture, "m_PlatformBlob", None)
    if getattr(reader, "platform", BuildTarget.UnknownPlatform) != BuildTarget.Switch or not blob or len(blob) < 12:
        return None
    try:
        shift = int.from_bytes(bytes(blob[8:12]), "little")
    except (TypeError, ValueError):
        raise _refuse("platform data") from None
    if shift > _MAX_GOB_SHIFT:
        raise _refuse("platform data")
    if not TextureSwizzler.is_switch_swizzled(BuildTarget.Switch, blob):
        return None
    texture_format = {TF.RGB24: TF.RGBA32, TF.BGR24: TF.BGRA32}.get(texture_format, texture_format)
    block = TextureSwizzler.TEXTURE_FORMAT_BLOCK_SIZE_MAP.get(texture_format)
    if block is None:
        raise _refuse("unsupported format")
    gobs = TextureSwizzler.get_switch_gobs_per_block(blob)
    return TextureSwizzler.get_padded_texture_size(width, height, *block, gobs), texture_format


def check_texture(texture: Any, file: str | None = None) -> None:
    """Refuse a Texture2D whose size or image data is out of bounds, before anything decodes it.

    Refused: a width or height that is not a positive number up to ``MAX_TEXTURE_SIDE``, a format the size check
    does not know (crunched formats included), and image data (stream data included) shorter than the declared size
    needs.
    Raises ``ServiceError("bundle_invalid", file=file)`` when the file is known, else ``TextureRefused`` (a ValueError).
    """
    try:
        _check(texture)
    except TextureRefused as refusal:
        if file is None:
            raise
        raise ServiceError("bundle_invalid", file=file) from refusal


def texture_image(texture: Any, file: str | None = None) -> Image.Image:
    """The decoded image of a Texture2D, after ``check_texture``."""
    check_texture(texture, file)
    return texture.image


def _check(texture: Any) -> None:
    width, height = texture.m_Width, texture.m_Height
    for side in (width, height):
        if type(side) is not int or not 0 < side <= MAX_TEXTURE_SIDE:
            raise _refuse("size")
    try:
        texture_format = TextureFormat(texture.m_TextureFormat)
    except ValueError:
        raise _refuse("unsupported format") from None
    if not supported(texture_format):
        raise _refuse("unsupported format")
    switch = _switch_padding(texture, texture_format, width, height)
    try:
        data = texture.get_image_data()
    except ValueError:
        data = b""
    if not data:
        raise _refuse("no image data")
    if switch is not None:
        (width, height), texture_format = switch
    if len(data) < required_size(width, height, texture_format):
        raise _refuse("image data")
