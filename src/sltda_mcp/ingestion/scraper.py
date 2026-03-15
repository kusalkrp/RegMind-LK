"""
SLTDA downloads page scraper.
Crawls sltda.gov.lk/en/download-2, associates each PDF link with its
page section, and returns English-only candidates.
"""

import hashlib
import logging
import re
from dataclasses import dataclass
from pathlib import Path

import httpx
import yaml
from bs4 import BeautifulSoup, Tag

logger = logging.getLogger(__name__)

SLTDA_DOWNLOADS_URL = "https://sltda.gov.lk/en/download-2"
USER_AGENT = "sltda-mcp-research/1.0 (portfolio research project)"

_CONFIG_PATH = Path(__file__).parents[3] / "ingestion" / "config" / "section_map.yaml"


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
    """Return True if filename signals a Sinhala or Tamil version."""
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
        if ord(ch) > 0x024F  # Beyond Latin Extended Additional block
    )
    return (non_latin / len(sample)) > threshold


def _match_section(heading_text: str, sections_config: dict) -> tuple[int, str] | None:
    """
    Match a heading string against section names in config.
    Returns (section_id, section_name) or None if no match.
    """
    heading_lower = heading_text.lower().strip()
    for section_id, sec_cfg in sections_config.items():
        name_lower = sec_cfg["name"].lower()
        # Fuzzy match: check if key words from section name appear in heading
        key_words = [w for w in name_lower.split() if len(w) > 3]
        if any(kw in heading_lower for kw in key_words):
            return int(section_id), sec_cfg["name"]
    return None


def _find_nearest_section_heading(
    tag: Tag,
    soup: BeautifulSoup,
    sections_config: dict,
) -> tuple[int, str]:
    """
    Walk backward through the DOM from `tag` to find the nearest
    section heading. Falls back to section 1 if nothing matches.
    """
    heading_tags = {"h1", "h2", "h3", "h4", "h5", "h6"}
    # Traverse all preceding siblings and parents
    for ancestor in tag.parents:
        # Check preceding siblings of each ancestor for headings
        for sibling in ancestor.previous_siblings:
            if not isinstance(sibling, Tag):
                continue
            # Direct heading
            if sibling.name in heading_tags:
                result = _match_section(sibling.get_text(), sections_config)
                if result:
                    return result
            # Heading nested inside (e.g., inside a div/accordion header)
            found = sibling.find(heading_tags)
            if found:
                result = _match_section(found.get_text(), sections_config)
                if result:
                    return result
    # No match found — assign to section 1 (Registration) as safe default
    default = sections_config.get(1, {})
    return 1, default.get("name", "Registration & Renewal")


async def scrape_document_list(
    timeout_seconds: int = 30,
    url: str = SLTDA_DOWNLOADS_URL,
) -> list[CandidateDocument]:
    """
    Fetch the SLTDA downloads page and return all English-only PDF candidates.
    Each candidate is associated with its page section via DOM proximity analysis.

    Raises:
        httpx.HTTPStatusError: if the page returns a non-2xx status
        httpx.TimeoutException: if the request times out
    """
    config = _load_section_config()
    sections_config = config.get("sections", {})

    async with httpx.AsyncClient(
        headers={"User-Agent": USER_AGENT},
        timeout=timeout_seconds,
        follow_redirects=True,
    ) as client:
        logger.info("Fetching SLTDA downloads page: %s", url)
        response = await client.get(url)
        response.raise_for_status()

    soup = BeautifulSoup(response.text, "lxml")

    # Find all PDF links on the page (single pass)
    pdf_links = soup.find_all("a", href=re.compile(r"\.pdf", re.IGNORECASE))
    logger.info("Found %d raw PDF links on page", len(pdf_links))

    candidates: list[CandidateDocument] = []
    excluded_count = 0

    for link in pdf_links:
        href = link.get("href", "").strip()
        text = link.get_text(strip=True)

        if not href or not text:
            continue

        # Normalise to absolute URL
        if href.startswith("/"):
            href = "https://sltda.gov.lk" + href
        elif not href.startswith("http"):
            continue

        filename = href.rstrip("/").split("/")[-1]

        # Language filter at filename level (Section 5.2 of design doc)
        if _is_language_excluded(filename, config):
            logger.debug("Language-excluded (filename): %s", filename)
            excluded_count += 1
            continue

        # Determine section via DOM proximity
        section_id, section_name = _find_nearest_section_heading(link, soup, sections_config)
        sec_cfg = sections_config.get(section_id, {})

        # Skip non-English sections if language_filter is set
        if sec_cfg.get("language_filter", True) and _is_language_excluded(filename, config):
            excluded_count += 1
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

    # Deduplicate by source URL (preserves first occurrence)
    seen_urls: set[str] = set()
    unique: list[CandidateDocument] = []
    for doc in candidates:
        if doc.source_url not in seen_urls:
            seen_urls.add(doc.source_url)
            unique.append(doc)

    logger.info(
        "Scraper result: %d unique English PDF candidates "
        "(%d excluded by language filter, %d duplicates removed)",
        len(unique),
        excluded_count,
        len(candidates) - len(unique),
    )
    return unique


def compute_content_hash(file_path: Path) -> str:
    """SHA-256 hash of file content — used for change detection."""
    sha256 = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            sha256.update(chunk)
    return sha256.hexdigest()
