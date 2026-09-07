"""
whop_integration/drive_fetcher.py
=================================
Lists and downloads files from PUBLIC Google Drive folders without
authentication.

Whop Content Rewards campaigns include `referenceMaterials` (typically
Google Drive / Dropbox / Loom / YouTube links) where the brand posts
the clips clippers should use. When those are public Drive folders
(`?usp=sharing` share mode), they can be scraped without auth.

Public Drive folder pages are server-rendered by Google with the file
list embedded in `AF_initDataCallback` JSON chunks in the HTML. The
simplest extraction path is:

  1. GET https://drive.google.com/drive/folders/<id>?usp=sharing
  2. Find each entry's data-id (file id) and the corresponding
     display name from the HTML
  3. To download: GET https://drive.google.com/uc?export=download&id=<id>
     (Drive redirects through a confirm page for large files; we
     follow the cookie `download_warning_` and re-issue the request)

Limitations:
  - Works only with folders shared as "Anyone with the link can view".
  - Private folders (login required) need OAuth (see TODO at bottom).
  - Drive rate-limits anonymous download volume; expect ~1 GB/day per IP.

Author: jarvismolinabot / clipper integration
Date: 2026-09-07
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Iterator
from urllib.parse import parse_qs, urlparse

import requests

logger = logging.getLogger(__name__)

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# Public Drive folder URL patterns we recognize
DRIVE_FOLDER_URL_PATTERNS = [
    re.compile(r"https?://drive\.google\.com/drive/(?:u/\d+/)?folders/([a-zA-Z0-9_-]{20,})"),
    re.compile(r"https?://drive\.google\.com/drive/(?:u/\d+/)?filefolders/([a-zA-Z0-9_-]{20,})"),
]

# Public Drive file URL patterns (for download_url reconstruction)
DRIVE_FILE_URL_PATTERNS = [
    re.compile(r"https?://drive\.google\.com/file/d/([a-zA-Z0-9_-]{20,})"),
    re.compile(r"https?://drive\.google\.com/open\?id=([a-zA-Z0-9_-]{20,})"),
    re.compile(r"https?://drive\.google\.com/uc\?(?:[^&#]*&)*id=([a-zA-Z0-9_-]{20,})"),
]


@dataclass
class DriveFile:
    """A single file or subfolder inside a Google Drive folder."""

    id: str
    name: str
    is_folder: bool = False
    mime_type: str = ""
    size_bytes: int = 0
    modified_at: str = ""
    folder_url: str = ""        # canonical folder URL (when is_folder=True)
    download_url: str = ""      # /uc?export=download&id=<id> (when not folder)
    view_url: str = ""          # /file/d/<id>/view

    @classmethod
    def from_raw(cls, file_id: str, name: str, is_folder: bool = False) -> "DriveFile":
        return cls(
            id=file_id,
            name=name,
            is_folder=is_folder,
            folder_url=f"https://drive.google.com/drive/folders/{file_id}",
            download_url=f"https://drive.google.com/uc?export=download&id={file_id}",
            view_url=f"https://drive.google.com/file/d/{file_id}/view",
        )

    def to_dict(self) -> dict:
        return asdict(self)


class DriveFetchError(Exception):
    """Raised when a Drive operation cannot be completed."""


class DriveFetcher:
    """Lists and downloads files from public Google Drive folders."""

    def __init__(
        self,
        user_agent: str = DEFAULT_USER_AGENT,
        timeout: int = 30,
        max_retries: int = 3,
        retry_backoff: float = 2.0,
    ) -> None:
        self.user_agent = user_agent
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_backoff = retry_backoff

    # ---------- public API ----------

    def list_folder(self, folder_url_or_id: str) -> list[DriveFile]:
        """Return all files and subfolders inside a public Drive folder.

        Accepts either a full URL (https://drive.google.com/drive/folders/<id>)
        or a bare folder id.
        """
        url = self._normalize_folder_url(folder_url_or_id)
        html = self._fetch_html(url)
        return self._extract_files_from_html(html)

    def download_file(
        self,
        file_url_or_id: str,
        dest_dir: str | Path,
        session: requests.Session | None = None,
    ) -> Path:
        """Download a single file from public Drive to `dest_dir`.

        Returns the path of the saved file. Filename is inferred from
        the response Content-Disposition header, falling back to <id>.
        """
        file_id = self._extract_file_id(file_url_or_id)
        sess = session or requests.Session()
        try:
            url = f"https://drive.google.com/uc?export=download&id={file_id}"
            resp = self._download_with_confirm(url, sess)
            filename = self._parse_filename(resp, fallback=file_id)
            dest = Path(dest_dir) / filename
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(resp.content)
            return dest
        finally:
            if session is None:
                sess.close()

    def iter_files(self, folder_url_or_id: str) -> Iterator[DriveFile]:
        """Yield DriveFile objects from a folder (one at a time)."""
        for f in self.list_folder(folder_url_or_id):
            yield f

    # ---------- internals ----------

    def _normalize_folder_url(self, url_or_id: str) -> str:
        url_or_id = url_or_id.strip()
        if url_or_id.startswith("http"):
            # Validate it's a folder URL
            for pat in DRIVE_FOLDER_URL_PATTERNS:
                m = pat.search(url_or_id)
                if m:
                    # Strip extra query params (keep ?usp=sharing as a hint)
                    return f"https://drive.google.com/drive/folders/{m.group(1)}?usp=sharing"
            raise DriveFetchError(
                f"URL does not look like a Drive folder URL: {url_or_id}"
            )
        # Treat as bare folder id
        if re.match(r"^[a-zA-Z0-9_-]{20,}$", url_or_id):
            return f"https://drive.google.com/drive/folders/{url_or_id}?usp=sharing"
        raise DriveFetchError(
            f"Input is neither a Drive folder URL nor a bare id: {url_or_id!r}"
        )

    @staticmethod
    def _extract_file_id(url_or_id: str) -> str:
        url_or_id = url_or_id.strip()
        if not url_or_id.startswith("http"):
            if re.match(r"^[a-zA-Z0-9_-]{20,}$", url_or_id):
                return url_or_id
            raise DriveFetchError(
                f"Input is neither a Drive file URL nor a bare id: {url_or_id!r}"
            )
        for pat in DRIVE_FILE_URL_PATTERNS:
            m = pat.search(url_or_id)
            if m:
                return m.group(1)
        # Fallback: maybe it's a folder URL — use the folder id as file id
        for pat in DRIVE_FOLDER_URL_PATTERNS:
            m = pat.search(url_or_id)
            if m:
                return m.group(1)
        raise DriveFetchError(f"Could not extract file id from: {url_or_id}")

    def _fetch_html(self, url: str) -> str:
        last_exc: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                resp = requests.get(
                    url,
                    headers={
                        "User-Agent": self.user_agent,
                        "Accept": "text/html,application/xhtml+xml",
                        "Accept-Language": "en-US,en;q=0.9",
                    },
                    timeout=self.timeout,
                )
                resp.raise_for_status()
                return resp.text
            except requests.RequestException as exc:
                last_exc = exc
                logger.warning(
                    "DriveFetcher attempt %d/%d failed for %s: %s",
                    attempt, self.max_retries, url, exc,
                )
                if attempt < self.max_retries:
                    time.sleep(self.retry_backoff * attempt)
        raise DriveFetchError(
            f"Failed to fetch {url} after {self.max_retries} attempts: {last_exc}"
        )

    @staticmethod
    def _extract_files_from_html(html: str) -> list[DriveFile]:
        """Extract file/folder entries from the SSR HTML.

        Drive's public folder page embeds file metadata as a parallel
        array of `data-id` attributes (file ids) and human-readable
        display names in `data-tooltip` attributes or in the visible
        HTML. For subfolders, the entry also has the "Shared folder"
        suffix in its tooltip.
        """
        # Approach 1: pair data-id with data-tooltip (name) by position
        # Drive renders them in the same DOM subtree per entry.
        ids = re.findall(r'data-id="([a-zA-Z0-9_-]{20,})"', html)
        tooltips = re.findall(r'data-tooltip="([^"]+)"', html)
        names: list[str] = []
        for tip in tooltips:
            # Strip the "Shortcut to " prefix that Drive adds for shortcuts
            tip = re.sub(r"^Shortcut to\s+", "", tip)
            # Strip the "Shared folder" / "Shortcut to Shared folder" suffix
            tip = re.sub(r"\s*(Shortcut to )?Shared folder\s*$", "", tip).strip()
            # Drop sort-order tooltips
            if any(
                skip in tip
                for skip in ("New to old", "Z to A", "A to Z", "Last modified")
            ):
                continue
            # Drop very short / empty
            if len(tip) < 2:
                continue
            names.append(tip)

        # Heuristic: first N ids are typically the folder entries;
        # we pair one tooltip per id, taking the longest names first
        # (folder names tend to be longer than emoji-prefixed labels).
        if len(ids) != len(names):
            # Fallback: pair what we can, in order
            n = min(len(ids), len(names))
            files: list[DriveFile] = []
            for fid, name in zip(ids[:n], names[:n]):
                is_folder = "folder" in html.lower() and "Shared folder" in html
                files.append(DriveFile.from_raw(fid, name, is_folder=is_folder))
            return files

        # When counts match, assume each id belongs to one tooltip in order
        # (Drive's SSR emits them in parallel arrays)
        files = []
        for fid, name in zip(ids, names):
            files.append(DriveFile.from_raw(fid, name, is_folder=False))
        return files

    def _download_with_confirm(
        self, url: str, session: requests.Session
    ) -> requests.Response:
        """Download a Drive file, handling the large-file confirm page.

        Files > ~100MB trigger a confirmation page with form fields.
        Drive returns `download_warning_` cookies; we re-issue the
        request after capturing those cookies.
        """
        for attempt in range(1, self.max_retries + 1):
            try:
                resp = session.get(
                    url,
                    headers={
                        "User-Agent": self.user_agent,
                        "Accept": "*/*",
                    },
                    timeout=self.timeout,
                    stream=False,
                )
                resp.raise_for_status()
                # If the response is HTML, it might be the confirm page
                ct = resp.headers.get("Content-Type", "")
                if "text/html" in ct and b"download_warning_" in resp.content[:50000]:
                    # Extract the confirm URL from the form action
                    m = re.search(
                        rb'action="([^"]+)"\s+method="post"', resp.content
                    ) or re.search(
                        rb"href=\"(/uc\?export=download[^\"]+)\"", resp.content
                    )
                    if m:
                        confirm = m.group(1).decode("utf-8", errors="ignore")
                        if confirm.startswith("/"):
                            confirm = "https://drive.google.com" + confirm
                        resp = session.get(
                            confirm,
                            headers={"User-Agent": self.user_agent},
                            timeout=self.timeout,
                        )
                        resp.raise_for_status()
                return resp
            except requests.RequestException as exc:
                last_exc = exc
                logger.warning(
                    "DriveFetcher download attempt %d/%d failed for %s: %s",
                    attempt, self.max_retries, url, exc,
                )
                if attempt < self.max_retries:
                    time.sleep(self.retry_backoff * attempt)
        raise DriveFetchError(
            f"Failed to download {url} after {self.max_retries} attempts: {last_exc}"
        )

    @staticmethod
    def _parse_filename(resp: requests.Response, fallback: str = "") -> str:
        """Best-effort filename extraction from response headers."""
        cd = resp.headers.get("Content-Disposition", "")
        if cd:
            # Try filename* first (RFC 5987 extended), then filename=
            for token in ("filename*=", "filename="):
                idx = cd.lower().find(token)
                if idx < 0:
                    continue
                rest = cd[idx + len(token):].lstrip()
                # Strip UTF-8'' language tag from filename*
                if rest.startswith("UTF-8''"):
                    rest = rest[7:]
                # Stop at next semicolon / newline
                for stop in (";", "\r", "\n"):
                    stop_idx = rest.find(stop)
                    if stop_idx >= 0:
                        rest = rest[:stop_idx]
                rest = rest.strip()
                # Strip surrounding double quotes if present
                if len(rest) >= 2 and rest[0] == '"' and rest[-1] == '"':
                    rest = rest[1:-1]
                if rest:
                    return rest.strip()
        # Some responses set no Content-Disposition; fall back to id
        return fallback or "drive_file"




# ---------- helpers ----------

def is_drive_url(url: str) -> bool:
    """True if `url` looks like any Google Drive public URL (file or folder)."""
    return ("drive.google.com" in url) or ("docs.google.com" in url)


def extract_drive_id(url: str) -> str | None:
    """Return the Drive id from a folder or file URL, or None."""
    for pat in DRIVE_FOLDER_URL_PATTERNS + DRIVE_FILE_URL_PATTERNS:
        m = pat.search(url)
        if m:
            return m.group(1)
    return None
