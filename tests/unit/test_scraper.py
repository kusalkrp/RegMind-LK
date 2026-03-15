"""
Unit tests for ingestion/scraper.py.
All HTTP calls are mocked — no network access.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sltda_mcp.ingestion.scraper import (
    CandidateDocument,
    _has_excessive_non_latin,
    _is_language_excluded,
    _load_section_config,
    compute_content_hash,
    scrape_document_list,
)


# ─── Fixtures ─────────────────────────────────────────────────────────────────

SAMPLE_HTML = """
<html><body>
<h2>Registration &amp; Renewal</h2>
<table>
  <tr><td><a href="/downloads/registration_form_en.pdf">Registration Form English</a></td></tr>
  <tr><td><a href="/downloads/registration_form_si.pdf">Registration Form Sinhala</a></td></tr>
  <tr><td><a href="/downloads/registration_form_sinhala.pdf">Registration Form Sinhala 2</a></td></tr>
</table>
<h2>Financial Concessions &amp; Banking Circulars</h2>
<table>
  <tr><td><a href="/downloads/banking_circular_07_2019.pdf">Banking Circular No 07 2019</a></td></tr>
</table>
<h2>Niche Tourism Toolkits</h2>
<table>
  <tr><td><a href="/downloads/wellness_toolkit.pdf">Wellness Tourism Toolkit</a></td></tr>
  <tr><td><a href="https://external.com/document.pdf">External PDF</a></td></tr>
</table>
</body></html>
"""

DUPLICATE_HTML = """
<html><body>
<h2>Registration &amp; Renewal</h2>
<a href="/downloads/form.pdf">Form A</a>
<a href="/downloads/form.pdf">Form A (duplicate link)</a>
</body></html>
"""


def _make_mock_response(html: str, status_code: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.text = html
    resp.status_code = status_code
    resp.raise_for_status = MagicMock()
    return resp


# ─── Language exclusion ───────────────────────────────────────────────────────

class TestLanguageExclusion:
    def test_sinhala_si_suffix_excluded(self):
        config = _load_section_config()
        assert _is_language_excluded("registration_form_si.pdf", config) is True

    def test_sinhala_full_word_excluded(self):
        config = _load_section_config()
        assert _is_language_excluded("registration_sinhala.pdf", config) is True

    def test_sinhala_sin_suffix_excluded(self):
        config = _load_section_config()
        assert _is_language_excluded("form_sin_2024.pdf", config) is True

    def test_tamil_ta_suffix_excluded(self):
        config = _load_section_config()
        assert _is_language_excluded("guidelines_ta.pdf", config) is True

    def test_tamil_full_word_excluded(self):
        config = _load_section_config()
        assert _is_language_excluded("form_tamil_v2.pdf", config) is True

    def test_english_document_not_excluded(self):
        config = _load_section_config()
        assert _is_language_excluded("registration_form_en.pdf", config) is False

    def test_english_only_filename_not_excluded(self):
        config = _load_section_config()
        assert _is_language_excluded("annual_report_2023.pdf", config) is False

    def test_case_insensitive_exclusion(self):
        config = _load_section_config()
        assert _is_language_excluded("FORM_SINHALA.PDF", config) is True


class TestNonLatinFilter:
    def test_pure_latin_text_passes(self):
        config = _load_section_config()
        text = "This is a standard English PDF document about tourism registration."
        assert _has_excessive_non_latin(text, config) is False

    def test_high_sinhala_content_rejected(self):
        config = _load_section_config()
        # Sinhala Unicode block: U+0D80–U+0DFF
        sinhala_chars = "ක" * 400  # 400 Sinhala chars = 80% non-Latin
        text = sinhala_chars + "English"
        assert _has_excessive_non_latin(text, config) is True

    def test_empty_text_passes(self):
        config = _load_section_config()
        assert _has_excessive_non_latin("", config) is False

    def test_mixed_content_below_threshold_passes(self):
        config = _load_section_config()
        # 10% non-Latin — below 30% threshold
        text = "English text " * 90 + "ක" * 10
        assert _has_excessive_non_latin(text, config) is False


# ─── Scraper ─────────────────────────────────────────────────────────────────

class TestScrapeDocumentList:
    @pytest.mark.asyncio
    async def test_returns_candidate_documents(self):
        mock_response = _make_mock_response(SAMPLE_HTML)

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            results = await scrape_document_list()

        assert len(results) > 0
        assert all(isinstance(r, CandidateDocument) for r in results)

    @pytest.mark.asyncio
    async def test_language_excluded_filenames_absent(self):
        mock_response = _make_mock_response(SAMPLE_HTML)

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            results = await scrape_document_list()

        filenames = [r.filename for r in results]
        assert "registration_form_si.pdf" not in filenames
        assert "registration_form_sinhala.pdf" not in filenames

    @pytest.mark.asyncio
    async def test_deduplicates_same_url(self):
        mock_response = _make_mock_response(DUPLICATE_HTML)

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            results = await scrape_document_list()

        urls = [r.source_url for r in results]
        assert len(urls) == len(set(urls)), "Duplicate URLs found in results"

    @pytest.mark.asyncio
    async def test_relative_urls_made_absolute(self):
        mock_response = _make_mock_response(SAMPLE_HTML)

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            results = await scrape_document_list()

        for r in results:
            assert r.source_url.startswith("http"), (
                f"Non-absolute URL found: {r.source_url}"
            )

    @pytest.mark.asyncio
    async def test_all_candidates_have_english_language(self):
        mock_response = _make_mock_response(SAMPLE_HTML)

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            results = await scrape_document_list()

        for r in results:
            assert r.language == "english"

    @pytest.mark.asyncio
    async def test_empty_page_returns_empty_list(self):
        mock_response = _make_mock_response("<html><body><p>No downloads</p></body></html>")

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            results = await scrape_document_list()

        assert results == []


# ─── Content hash ─────────────────────────────────────────────────────────────

class TestComputeContentHash:
    def test_hash_is_64_char_hex(self, tmp_path):
        f = tmp_path / "test.pdf"
        f.write_bytes(b"%PDF-1.4 test content")
        h = compute_content_hash(f)
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)

    def test_same_content_same_hash(self, tmp_path):
        content = b"%PDF-1.4 identical content"
        f1 = tmp_path / "a.pdf"
        f2 = tmp_path / "b.pdf"
        f1.write_bytes(content)
        f2.write_bytes(content)
        assert compute_content_hash(f1) == compute_content_hash(f2)

    def test_different_content_different_hash(self, tmp_path):
        f1 = tmp_path / "a.pdf"
        f2 = tmp_path / "b.pdf"
        f1.write_bytes(b"%PDF-1.4 content A")
        f2.write_bytes(b"%PDF-1.4 content B")
        assert compute_content_hash(f1) != compute_content_hash(f2)
