"""
tests/test_drive_url_resolver.py
================================
Tests for the Drive URL canonicalization in
`app.services.discovery.asset_resolver.rewrite_drive_url`.

These cover the four families of Drive URLs that Whop campaigns hand us in
`referenceMaterials`:

  1. File preview URL:     drive.google.com/file/d/<ID>/view?usp=sharing
  2. Already-canonical:    drive.google.com/uc?export=download&id=<ID>
  3. Folder URLs:          drive.google.com/drive/folders/<ID>?usp=sharing
                           (incl. localized /drive/u/<N>/folders/<ID>)
                           -> rejected (None): must be enumerated, not downloaded
  4. Google Docs/Sheets:   docs.google.com/document/d/<ID>/edit?usp=sharing
                           -> rejected (None): not a video, can't be downloaded

Non-Drive URLs are passed through unchanged.
"""

from __future__ import annotations

import pytest

from app.services.discovery.asset_resolver import (
    classify_link,
    rewrite_drive_url,
)


# ----------------- 1. File preview -> canonical download -----------------


class TestFilePreviewRewrite:
    def test_real_world_yomi_denzel_url(self):
        # The exact URL Molina reported as the canonical "this fails" case.
        url = (
            "https://drive.google.com/file/d/"
            "1mQQ5FIc_2Ru0eDj6XNNgPFIld2QDE_u7/view?usp=sharing"
        )
        assert rewrite_drive_url(url) == (
            "https://drive.google.com/uc?export=download&id="
            "1mQQ5FIc_2Ru0eDj6XNNgPFIld2QDE_u7"
        )

    def test_preview_without_usp_param(self):
        url = "https://drive.google.com/file/d/ABC_DEF-1234567890abcdefghij/view"
        assert rewrite_drive_url(url) == (
            "https://drive.google.com/uc?export=download&id="
            "ABC_DEF-1234567890abcdefghij"
        )

    def test_bare_file_d_url_without_view(self):
        # Some campaigns omit /view at the end. Still a file.
        url = "https://drive.google.com/file/d/ABC_DEF-1234567890abcdefghij"
        assert rewrite_drive_url(url) == (
            "https://drive.google.com/uc?export=download&id="
            "ABC_DEF-1234567890abcdefghij"
        )

    def test_idempotent_on_already_canonical(self):
        url = (
            "https://drive.google.com/uc?export=download&id="
            "1mQQ5FIc_2Ru0eDj6XNNgPFIld2QDE_u7"
        )
        assert rewrite_drive_url(url) == url

    def test_idempotent_on_canonical_with_confirm(self):
        # Drive adds &confirm=t for the virus-scan warning bypass.
        url = (
            "https://drive.google.com/uc?export=download&id="
            "1mQQ5FIc_2Ru0eDj6XNNgPFIld2QDE_u7&confirm=t"
        )
        assert rewrite_drive_url(url) == url


# ------------------------ 2. Folders -> rejected -------------------------


class TestFolderRejection:
    def test_folder_with_usp_sharing(self):
        url = (
            "https://drive.google.com/drive/folders/"
            "15CRuz33sWIFz5moouuxu2vRS_UO5VpfZ?usp=sharing"
        )
        assert rewrite_drive_url(url) is None

    def test_folder_bare_no_query(self):
        url = (
            "https://drive.google.com/drive/folders/"
            "1h_DaEx4rVi3OaQAkhrENtWasvLOmPxHb"
        )
        assert rewrite_drive_url(url) is None

    def test_localized_folder_u1(self):
        # /drive/u/<N>/folders/<ID> for localized Google accounts.
        url = (
            "https://drive.google.com/drive/u/1/folders/"
            "1TU0zp1NO53vP47Ge2HRdh5c6yVTbyqoT?usp=sharing"
        )
        assert rewrite_drive_url(url) is None

    def test_localized_folder_u0(self):
        url = (
            "https://drive.google.com/drive/u/0/folders/"
            "1abc_DEF-234567890abcdefghijkl?usp=sharing"
        )
        assert rewrite_drive_url(url) is None


# ------------------ 3. docs.google.com -> rejected -----------------------


class TestDocsGoogleRejection:
    def test_document_editor(self):
        url = (
            "https://docs.google.com/document/d/"
            "1FtFgAlk_JZqAM3jJzPJZMooXgLuKF-q3wPdOU7OcP5E/edit?usp=sharing"
        )
        assert rewrite_drive_url(url) is None

    def test_spreadsheet_editor(self):
        url = (
            "https://docs.google.com/spreadsheets/d/"
            "1abc_DEF-234567890abcdefghijkl/edit"
        )
        assert rewrite_drive_url(url) is None

    def test_presentation_editor(self):
        url = (
            "https://docs.google.com/presentation/d/"
            "1abc_DEF-2344567890abcdefghijkl/edit?usp=sharing"
        )
        assert rewrite_drive_url(url) is None

    def test_forms_editor(self):
        url = (
            "https://docs.google.com/forms/d/"
            "1abc_DEF-2344567890abcdefghijkl/edit"
        )
        assert rewrite_drive_url(url) is None


# ------------------- 4. Non-Drive URLs -> pass-through --------------------


class TestNonDrivePassthrough:
    def test_youtube(self):
        url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        assert rewrite_drive_url(url) == url

    def test_youtu_be_short(self):
        url = "https://youtu.be/dQw4w9WgXcQ"
        assert rewrite_drive_url(url) == url

    def test_dropbox(self):
        url = "https://www.dropbox.com/s/abc123/clip.mp4?dl=0"
        assert rewrite_drive_url(url) == url

    def test_arbitrary_external(self):
        url = "https://example.com/foo.mp4"
        assert rewrite_drive_url(url) == url

    def test_mega(self):
        url = "https://mega.nz/file/AbCdEfGh#IjKlMnOp"
        assert rewrite_drive_url(url) == url


# ------------------------- 5. Edge cases ---------------------------------


class TestEdgeCases:
    def test_empty_string(self):
        assert rewrite_drive_url("") is None

    def test_whitespace_only(self):
        assert rewrite_drive_url("   ") is None

    def test_drive_https_lowercase(self):
        url = "https://drive.google.com/file/d/ABC_DEF-1234567890abcdefghij/view"
        result = rewrite_drive_url(url)
        assert result is not None
        assert "export=download&id=ABC_DEF-1234567890abcdefghij" in result

    def test_drive_with_uppercase_host(self):
        url = "HTTPS://DRIVE.GOOGLE.COM/file/d/ABC_DEF-1234567890abcdefghij/view"
        result = rewrite_drive_url(url)
        # Drive is case-insensitive on hostname but we should still rewrite.
        assert result is not None
        assert "id=ABC_DEF-1234567890abcdefghij" in result


# --------------- 6. Integration: classify_link stays correct --------------


class TestClassifyLinkUnchanged:
    """Adding rewrite_drive_url must NOT change classify_link behavior -
    classify_link is called *after* the rewrite, on the canonical URL."""

    def test_canonical_drive_is_drive(self):
        url = (
            "https://drive.google.com/uc?export=download&id="
            "1mQQ5FIc_2Ru0eDj6XNNgPFIld2QDE_u7"
        )
        assert classify_link(url) == "drive"

    def test_youtube_still_youtube(self):
        assert classify_link("https://www.youtube.com/watch?v=x") == "youtube"

    def test_unknown_is_external(self):
        assert classify_link("https://example.com/x") == "external"
