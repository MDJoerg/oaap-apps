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


class _Bad(Exception):
    pass


def _strip_jpeg(d):
    """APP1 (EXIF/XMP), APP12, APP13 (IPTC/Photoshop) und Kommentare entfernen;
    alles ab Start-of-Scan unverändert. APP0 (JFIF), APP2 (ICC) und APP14 (Adobe,
    Farbraum) bleiben -- sie tragen Darstellung, keine Personen."""
    if d[:2] != b"\xff\xd8":
        raise _Bad()
    out, i = [b"\xff\xd8"], 2
    while i < len(d):
        if d[i] != 0xFF:
            raise _Bad()
        marker = d[i + 1]
        if marker == 0xD9:                       # EOI
            out.append(d[i:i + 2])
            return b"".join(out)
        if marker == 0xDA:                       # SOS: Rest unverändert
            out.append(d[i:])
            return b"".join(out)
        if 0xD0 <= marker <= 0xD7 or marker == 0x01:
            out.append(d[i:i + 2])
            i += 2
            continue
        n = int.from_bytes(d[i + 2:i + 4], "big")
        seg = d[i:i + 2 + n]
        if len(seg) < 4:
            raise _Bad()
        if marker not in (0xE1, 0xEC, 0xED, 0xFE):
            out.append(seg)
        i += 2 + n
    raise _Bad()


PNG_DROP = {b"tEXt", b"iTXt", b"zTXt", b"eXIf", b"tIME"}


def _strip_png(d):
    if d[:8] != b"\x89PNG\r\n\x1a\n":
        raise _Bad()
    out, i = [d[:8]], 8
    while i + 8 <= len(d):
        n = int.from_bytes(d[i:i + 4], "big")
        typ = d[i + 4:i + 8]
        chunk = d[i:i + 12 + n]
        if len(chunk) < 12 + n:
            raise _Bad()
        if typ not in PNG_DROP:
            out.append(chunk)
        i += 12 + n
        if typ == b"IEND":
            return b"".join(out)
    raise _Bad()


def _strip_webp(d):
    """RIFF-Container: EXIF- und XMP-Chunks entfernen, Größe und VP8X-Flags anpassen."""
    if d[:4] != b"RIFF" or d[8:12] != b"WEBP":
        raise _Bad()
    out, i = [], 12
    while i + 8 <= len(d):
        typ = d[i:i + 4]
        n = int.from_bytes(d[i + 4:i + 8], "little")
        chunk = d[i:i + 8 + n + (n & 1)]
        if typ not in (b"EXIF", b"XMP "):
            if typ == b"VP8X" and len(chunk) >= 9:
                flags = chunk[8] & ~0x0C                 # EXIF- und XMP-Bit aus
                chunk = chunk[:8] + bytes([flags]) + chunk[9:]
            out.append(chunk)
        i += 8 + n + (n & 1)
    body = b"WEBP" + b"".join(out)
    return b"RIFF" + len(body).to_bytes(4, "little") + body


def strip_metadata(path, media):
    """Metadaten aus der Datei entfernen (RFC-0077 §15.8.4) -- an Ort und Stelle.
    Ein Bild, das sich nicht sauber zerlegen lässt, wird abgelehnt statt
    ungeprüft abgelegt."""
    with open(path, "rb") as f:
        d = f.read()
    try:
        new = {"image/jpeg": _strip_jpeg, "image/png": _strip_png,
               "image/webp": _strip_webp}[media](d)
    except (_Bad, IndexError, KeyError):
        raise ImageRefused("Das Bild lässt sich nicht lesen (beschädigt oder ungewöhnlich "
                           "aufgebaut). Bitte neu speichern, z. B. als PNG.")
    if new != d:
        with open(path, "wb") as f:
            f.write(new)
    return len(new)


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
