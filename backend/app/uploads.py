"""
Upload validation. Owner: Sampurna.

Every file (user upload or demo preset) passes validate_upload() BEFORE anything is stored:
  - at most config.MAX_UPLOAD_BYTES, not empty
  - its first bytes must match a known image / video / audio format; the file extension and
    the browser's Content-Type are NOT trusted. The detected type is what gets stored.
Errors carry an HTTP status and a friendly message for the frontend.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import BinaryIO, Optional

from app import config

SUPPORTED_TEXT = "JPEG, PNG, WebP, GIF, BMP, TIFF, HEIC/AVIF, MP4/MOV/3GP, WebM/MKV, AVI, MP3, WAV, M4A/AAC, OGG/Opus, FLAC, AMR"

# ISO base media ("ftyp") brands -> (kind, mime). Unknown brands are treated as generic MP4 video.
_FTYP_BRANDS = {
    "heic": ("image", "image/heic"), "heix": ("image", "image/heic"), "hevc": ("image", "image/heic"),
    "heim": ("image", "image/heic"), "heis": ("image", "image/heic"), "mif1": ("image", "image/heif"),
    "msf1": ("image", "image/heif"), "avif": ("image", "image/avif"), "avis": ("image", "image/avif"),
    "qt  ": ("video", "video/quicktime"),
    "3gp4": ("video", "video/3gpp"), "3gp5": ("video", "video/3gpp"), "3gp6": ("video", "video/3gpp"),
    "3g2a": ("video", "video/3gpp2"),
    "M4A ": ("audio", "audio/mp4"), "M4B ": ("audio", "audio/mp4"), "F4A ": ("audio", "audio/mp4"),
    "M4V ": ("video", "video/x-m4v"),
}


@dataclass
class UploadError(Exception):
    status: int
    message: str

    def __str__(self) -> str:
        return self.message


def sniff(head: bytes) -> Optional[tuple[str, str]]:
    """(kind, mime) from a file's first bytes, or None if it is not a supported media format."""
    h = head
    if h.startswith(b"\xff\xd8\xff"):
        return "image", "image/jpeg"
    if h.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image", "image/png"
    if h[:6] in (b"GIF87a", b"GIF89a"):
        return "image", "image/gif"
    if h[:4] == b"RIFF" and h[8:12] == b"WEBP":
        return "image", "image/webp"
    if h[:4] == b"RIFF" and h[8:12] == b"WAVE":
        return "audio", "audio/wav"
    if h[:4] == b"RIFF" and h[8:12] == b"AVI ":
        return "video", "video/x-msvideo"
    if h[:2] == b"BM" and len(h) >= 14:
        return "image", "image/bmp"
    if h[:4] in (b"II*\x00", b"MM\x00*"):
        return "image", "image/tiff"
    if h[4:8] == b"ftyp":
        return _FTYP_BRANDS.get(h[8:12].decode("latin-1"), ("video", "video/mp4"))
    if h.startswith(b"\x1a\x45\xdf\xa3"):
        return "video", "video/webm"
    if h.startswith(b"ID3") or (len(h) > 1 and h[0] == 0xFF and (h[1] & 0xE0) == 0xE0):
        return "audio", "audio/mpeg"          # MP3 (ID3 tag or frame sync) / AAC ADTS
    if h.startswith(b"OggS"):
        return "audio", "audio/ogg"
    if h.startswith(b"fLaC"):
        return "audio", "audio/flac"
    if h.startswith(b"#!AMR"):
        return "audio", "audio/amr"
    return None


@dataclass
class CheckedFile:
    filename: str
    stream: BinaryIO
    mime: str                       # detected from content
    declared_mime: Optional[str]    # what the client said (kept for the custody log)
    size: int


def human_size(n: int) -> str:
    """1536 -> '1.5 KB', 250_000_000 -> '238 MB'."""
    for unit, scale in (("GB", 1024 ** 3), ("MB", 1024 ** 2), ("KB", 1024)):
        if n >= scale:
            v = n / scale
            return f"{v:.1f} {unit}" if v < 10 else f"{v:.0f} {unit}"
    return f"{n} bytes"


def _size_of(stream: BinaryIO) -> int:
    pos = stream.tell()
    stream.seek(0, 2)
    size = stream.tell()
    stream.seek(pos)
    return size


def validate_upload(filename: str, stream: BinaryIO, declared_mime: Optional[str] = None,
                    size: Optional[int] = None) -> CheckedFile:
    """Raise UploadError (413 / 415 / 422) or return the file with its detected type."""
    size = _size_of(stream) if size is None else size
    if size == 0:
        raise UploadError(422, f"'{filename}' is empty.")
    if size > config.MAX_UPLOAD_BYTES:
        raise UploadError(413, f"'{filename}' is {human_size(size)}. The limit is "
                               f"{human_size(config.MAX_UPLOAD_BYTES)} per file.")
    stream.seek(0)
    head = stream.read(64)
    stream.seek(0)
    detected = sniff(head)
    if detected is None:
        raise UploadError(
            415,
            f"'{filename}' is not a supported image, video or audio file (its content does not match "
            f"any supported format, whatever its extension). Supported: {SUPPORTED_TEXT}.",
        )
    return CheckedFile(filename=filename, stream=stream, mime=detected[1], declared_mime=declared_mime, size=size)


def check_count(n: int) -> None:
    if n > config.MAX_UPLOAD_FILES:
        raise UploadError(422, f"Too many files ({n}). Upload at most {config.MAX_UPLOAD_FILES} files per investigation.")
