"""Stand-ins for Texture2D objects in tests that never build a real bundle."""

from types import SimpleNamespace


def fake_texture(image, **fields):
    """An object that decodes to ``image`` and declares the size and data the texture checks accept (RGBA32)."""
    width, height = image.size
    return SimpleNamespace(image=image, m_Width=width, m_Height=height, m_TextureFormat=4,
                           get_image_data=lambda: bytes(width * height * 4), **fields)
