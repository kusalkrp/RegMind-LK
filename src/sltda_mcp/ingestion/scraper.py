"""
SLTDA downloads page scraper.
Crawls sltda.gov.lk/en/download-2 and returns a list of candidate documents.
Applies English-only filtering per section_map.yaml config.
"""

import hashlib
import logging
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

import httpx
import yaml
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

SLTDA_DOWNLOADS_URL = "https://sltda.gov.lk/en/download-2"
USER_AGENT = "sltda-mcp-research/1.0 (portfolio research project)"

_CONFIG_PATH = Path(__file__).parents[4] / "ingestion" / "config" / "section_map.yaml"


@dataclass
class CandidateDocument:
    section_id: int
    section_name: str
    document_name: str
    source_url: str
    filename: str
    language: str = "english"


def _load_section_config() -> dict:
    with open(_CONFIG_PATH) as f:
        return yaml.safe_load(f)


def _is_language_excluded(filename: str, config: dict) -> bool:
    """Return True if filename indicates Sinhala or Tamil version."""
    lower = filename.lower()
    for pattern in config.get("language_exclusion_patterns", []):
        if pattern in lower:
            return True
    return False


def _has_excessive_non_latin(text: str, config: dict) -> bool:
    """Return True if first N chars contain > threshold non-Latin Unicode."""
    check_chars = config.get("non_latin_check_chars", 500)
    threshold = config.get("non_latin_threshold", 0.30)
    sample = text[:check_chars]
    if not sample:
        return False
    non_latin = sum(
        1 for ch in sample
        if unicodedata.category(ch) not in ("Ll", "Lu", "Lt", "Lm", "Lo", "Nd", "Zs", "Po", "Pd")
        and not ch.isascii()
    )
    ratio = non_latin / len(sample)
    return ratio > threshold


async def scrape_document_list(
    timeout_seconds: int = 30,
) -> list[CandidateDocument]:
    """
    Fetch the SLTDA downloads page and return all English-only PDF candidates.

    Raises:
        httpx.HTTPError: if the page cannot be fetched
    """
    config = _load_section_config()
    sections_config = config.get("sections", {})

    async with httpx.AsyncClient(
        headers={"User-Agent": USER_AGENT},
        timeout=timeout_seconds,
        follow_redirects=True,
    ) as client:
        logger.info("Fetching SLTDA downloads page: %s", SLTDA_DOWNLOADS_URL)
        response = await client.get(SLTDA_DOWNLOADS_URL)
        response.raise_for_status()

    soup = BeautifulSoup(response.text, "lxml")
    candidates: list[CandidateDocument] = []

    # SLTDA page uses accordion sections; each section has a heading + table of links
    # The exact selector may need updating if SLTDA restructures their page
    for section_idx, (section_id, sec_cfg) in enumerate(sections_config.items(), 1):
        section_name = sec_cfg["name"]
        language_filter = sec_cfg.get("language_filter", True)

        # Find anchor tags with .pdf in href within this section's context
        # This is a best-effort selector; update section_map.yaml if page structure changes
        pdf_links = soup.find_all("a", href=re.compile(r"\.pdf", re.IGNORECASE))

        for link in pdf_links:
            href = link.get("href", "")
            text = link.get_text(strip=True)

            if not href or not text:
                continue

            # Make absolute URL
            if href.startswith("/"):
                href = f"https://sltda.gov.lk{href}"
            elif not href.startswith("http"):
                continue

            filename = href.split("/")[-1]

            # Apply language filter
            if language_filter and _is_language_excluded(filename, config):
                logger.debug("Language-excluded: %s", filename)
                continue

            candidates.append(
                CandidateDocument(
                    section_id=section_id,
                    section_name=section_name,
                    document_name=text,
                    source_url=href,
                    filename=filename,
                    language="english",
                )
            )

    # Deduplicate by URL
    seen_urls: set[str] = set()
    unique: list[CandidateDocument] = []
    for doc in candidates:
        if doc.source_url not in seen_urls:
            seen_urls.add(doc.source_url)
            unique.append(doc)

    logger.info(
        "Scraper found %d unique English PDF candidates (from %d total links)",
        len(unique),
        len(candidates),
    )
    return unique


def compute_content_hash(file_path: Path) -> str:
    """SHA-256 hash of a file for change detection."""
    sha256 = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            sha256.update(chunk)
    return sha256.hexdigest()
