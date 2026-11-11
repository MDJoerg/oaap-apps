"""Bilder für Icon und Screenshots (RFC-0077 §12).

Nur PNG, JPEG und WebP -- erkannt an den ersten Bytes, nicht am Namen.
Kein SVG: ein SVG ist ein Dokument, das Skript tragen kann. Ein Bild wird
nie umgewandelt oder ausgewertet, nur abgelegt und mit festem Typ,
`nosniff` und strenger CSP ausgeliefert.
"""
MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_SCREENSHOTS = 8


class ImageRefused(Exception):
    pass


def media_type(head):
    """Der Typ aus den ersten Bytes, oder None."""
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    return None


def check(path, size):
    if size > MAX_IMAGE_BYTES:
        raise ImageRefused(f"Ein Bild darf höchstens {MAX_IMAGE_BYTES // (1024 * 1024)} MB groß sein.")
    with open(path, "rb") as f:
        head = f.read(16)
    mt = media_type(head)
    if not mt:
        raise ImageRefused("Nur PNG, JPEG oder WebP (erkannt am Inhalt, nicht am Namen); "
                           "SVG und andere Formate werden nicht angenommen.")
    return mt
