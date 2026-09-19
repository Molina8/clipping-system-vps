"""2026-09-18 (Molina): valida el resultado de download antes de crear
un job de transcribe. Si el Worker reporta un file_path con extension no
soportada (.bin, .tmp, .part) o un mime octet-stream, lanza
MediaFormatInvalid para que el caller marque el asset como terminal failed
idempotente (no se reintenta, no bloquea la campana).
"""
from __future__ import annotations
import os
from dataclasses import dataclass

VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".flv", ".wmv",
              ".ts", ".m2ts", ".3gp", ".mpg", ".mpeg", ".vob", ".ogv"}
AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".ogg", ".flac", ".aac", ".opus", ".wma"}
ALLOWED_EXTS = VIDEO_EXTS | AUDIO_EXTS
BLOCKED_EXTS = {".bin", ".tmp", ".part", ".crdownload", ".download"}

VIDEO_MIMES = {"video/mp4", "video/quicktime", "video/x-matroska", "video/webm",
               "video/x-msvideo", "video/x-flv", "video/x-ms-wmv", "video/mpeg",
               "video/3gpp", "video/ogg"}
AUDIO_MIMES = {"audio/mpeg", "audio/mp3", "audio/wav", "audio/x-wav",
               "audio/aac", "audio/ogg", "audio/flac", "audio/mp4",
               "audio/x-m4a", "audio/opus", "audio/x-ms-wma"}
ALLOWED_MIMES = VIDEO_MIMES | AUDIO_MIMES


class MediaFormatInvalid(Exception):
    """El Worker reporto un file_path/mime que NO es media reproducible."""


@dataclass
class ValidationResult:
    file_path: str
    mime_type: str | None
    extension: str


def _ext(path):
    if not path:
        return ""
    base = os.path.basename(path.split("?")[0].split("#")[0])
    return os.path.splitext(base)[1].lower()


def validate_download_result(file_path, mime_type, result_data=None):
    ext = _ext(file_path)
    if not file_path:
        raise MediaFormatInvalid("download reported no file_path")
    if ext in BLOCKED_EXTS:
        raise MediaFormatInvalid(
            f"download file_path extension {ext!r} is a temp/binary placeholder "
            f"(not a media file). file_path={file_path!r}"
        )
    if not ext:
        raise MediaFormatInvalid(
            f"download file_path has no extension; cannot transcribe. "
            f"file_path={file_path!r}"
        )
    if ext not in ALLOWED_EXTS:
        raise MediaFormatInvalid(
            f"download file_path extension {ext!r} not in allowed media "
            f"set {sorted(ALLOWED_EXTS)}. file_path={file_path!r}"
        )
    if mime_type and mime_type.strip():
        m = mime_type.strip().lower()
        if m == "application/octet-stream":
            raise MediaFormatInvalid(
                f"download mime_type is application/octet-stream; Worker did "
                f"not recognize the media. file_path={file_path!r}"
            )
    return ValidationResult(file_path=file_path, mime_type=mime_type, extension=ext)
