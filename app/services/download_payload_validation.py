"""Valida el resultado de download antes de crear un job de transcribe.

El Windows Worker guarda descargas HTTP como ``<job_id>.bin`` a proposito
(ffprobe/WhisperX no necesitan la extension). Un ``.bin`` grande de un
asset cuyo ``kind``/``name`` es video NO es un fallo terminal.
"""
from __future__ import annotations
import os
from dataclasses import dataclass

VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".flv", ".wmv",
              ".ts", ".m2ts", ".3gp", ".mpg", ".mpeg", ".vob", ".ogv"}
AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".ogg", ".flac", ".aac", ".opus", ".wma"}
ALLOWED_EXTS = VIDEO_EXTS | AUDIO_EXTS
PLACEHOLDER_EXTS = {".bin", ".tmp", ".part", ".crdownload", ".download"}

VIDEO_MIMES = {"video/mp4", "video/quicktime", "video/x-matroska", "video/webm",
               "video/x-msvideo", "video/x-flv", "video/x-ms-wmv", "video/mpeg",
               "video/3gpp", "video/ogg", "video/mov"}
AUDIO_MIMES = {"audio/mpeg", "audio/mp3", "audio/wav", "audio/x-wav",
               "audio/aac", "audio/ogg", "audio/flac", "audio/mp4",
               "audio/x-m4a", "audio/opus", "audio/x-ms-wma"}
ALLOWED_MIMES = VIDEO_MIMES | AUDIO_MIMES

# Drive/resolver kinds that mean "this is footage", not a folder/doc.
MEDIA_KINDS = {
    "mp4", "mov", "mkv", "webm", "avi", "m4v", "video", "footage",
    "mp3", "wav", "m4a", "audio",
}

# Below this, a .bin is almost certainly HTML/error page, not a clip.
MIN_PLACEHOLDER_BYTES = 1_000_000


class MediaFormatInvalid(Exception):
    """El Worker reporto un file_path/mime que NO es media reproducible."""


@dataclass
class ValidationResult:
    file_path: str
    mime_type: str | None
    extension: str
    accepted_placeholder: bool = False


def _ext(path):
    if not path:
        return ""
    base = os.path.basename(str(path).split("?")[0].split("#")[0])
    return os.path.splitext(base)[1].lower()


def _hint_is_media(*, mime_type, kind, source_name, result_data):
    mime = (mime_type or "").strip().lower()
    if mime in ALLOWED_MIMES:
        return True
    k = (kind or "").strip().lower().lstrip(".")
    if k in MEDIA_KINDS:
        return True
    name = source_name or ""
    if _ext(name) in ALLOWED_EXTS:
        return True
    if isinstance(result_data, dict):
        size = result_data.get("file_size") or result_data.get("size") or 0
        try:
            size = int(size)
        except (TypeError, ValueError):
            size = 0
        if size >= MIN_PLACEHOLDER_BYTES:
            return True
    return False


def validate_download_result(
    file_path,
    mime_type,
    result_data=None,
    *,
    kind=None,
    source_name=None,
):
    ext = _ext(file_path)
    if not file_path:
        raise MediaFormatInvalid("download reported no file_path")

    if ext in PLACEHOLDER_EXTS:
        if _hint_is_media(
            mime_type=mime_type,
            kind=kind,
            source_name=source_name,
            result_data=result_data,
        ):
            return ValidationResult(
                file_path=file_path,
                mime_type=mime_type,
                extension=ext,
                accepted_placeholder=True,
            )
        raise MediaFormatInvalid(
            f"download file_path extension {ext!r} is a temp/binary placeholder "
            f"without media hints. file_path={file_path!r}"
        )
    if not ext:
        if _hint_is_media(
            mime_type=mime_type,
            kind=kind,
            source_name=source_name,
            result_data=result_data,
        ):
            return ValidationResult(
                file_path=file_path,
                mime_type=mime_type,
                extension=ext,
                accepted_placeholder=True,
            )
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
        if m == "application/octet-stream" and not _hint_is_media(
            mime_type=None,
            kind=kind,
            source_name=source_name,
            result_data=result_data,
        ):
            raise MediaFormatInvalid(
                f"download mime_type is application/octet-stream; Worker did "
                f"not recognize the media. file_path={file_path!r}"
            )
    return ValidationResult(file_path=file_path, mime_type=mime_type, extension=ext)
