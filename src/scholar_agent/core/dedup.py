"""Paper deduplication and merge helpers."""

from __future__ import annotations

import re
import string
from difflib import SequenceMatcher
from typing import Iterable

from scholar_agent.core.paper_schemas import Paper, PaperIdentifiers, PaperUrls


TITLE_SIMILARITY_THRESHOLD = 0.92
_LATEX_COMMAND_RE = re.compile(r"\\[a-zA-Z]+\*?(?:\[[^\]]*\])?(?:\{([^{}]*)\})?")
_ARXIV_VERSION_RE = re.compile(r"v\d+$", re.IGNORECASE)


def deduplicate_papers(papers: list[Paper]) -> list[Paper]:
    """Deduplicate papers while preserving first-seen order."""

    deduplicated: list[Paper] = []
    for paper in papers:
        match_index = _find_duplicate_index(deduplicated, paper)
        if match_index is None:
            deduplicated.append(paper.model_copy(deep=True))
            continue
        deduplicated[match_index] = _merge_papers(deduplicated[match_index], paper)

    return deduplicated


def normalize_title(title: str | None) -> str:
    """Normalize title text for fuzzy comparison."""

    if not title:
        return ""

    text = title.lower()
    text = _LATEX_COMMAND_RE.sub(lambda match: match.group(1) or " ", text)
    text = text.replace("$", " ")
    text = text.replace("\\", " ")
    text = text.replace("{", " ").replace("}", " ")
    text = text.replace("^", " ").replace("_", " ").replace("~", " ")
    text = text.translate(str.maketrans("", "", string.punctuation))
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def normalize_doi(value: str | None) -> str | None:
    text = _clean(value)
    if not text:
        return None
    lower = text.lower()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
        if lower.startswith(prefix):
            lower = lower[len(prefix) :]
            break
    return lower.strip()


def normalize_arxiv_id(value: str | None) -> str | None:
    text = _clean(value)
    if not text:
        return None
    text = text.rsplit("/", 1)[-1]
    text = text.removeprefix("arXiv:").removeprefix("arxiv:")
    text = _ARXIV_VERSION_RE.sub("", text)
    return text.lower().strip() or None


def normalize_simple_id(value: str | None) -> str | None:
    text = _clean(value)
    if not text:
        return None
    return text.rstrip("/").rsplit("/", 1)[-1].lower().strip() or None


def _find_duplicate_index(existing: list[Paper], candidate: Paper) -> int | None:
    for index, paper in enumerate(existing):
        if _is_duplicate(paper, candidate):
            return index
    return None


def _is_duplicate(left: Paper, right: Paper) -> bool:
    left_ids = left.identifiers
    right_ids = right.identifiers

    if _same_non_empty(normalize_doi(left_ids.doi), normalize_doi(right_ids.doi)):
        return True
    if _same_non_empty(normalize_arxiv_id(left_ids.arxiv_id), normalize_arxiv_id(right_ids.arxiv_id)):
        return True
    if _same_non_empty(normalize_simple_id(left_ids.openalex_id), normalize_simple_id(right_ids.openalex_id)):
        return True
    if _same_non_empty(
        normalize_simple_id(left_ids.semantic_scholar_id),
        normalize_simple_id(right_ids.semantic_scholar_id),
    ):
        return True
    if _same_non_empty(normalize_simple_id(left_ids.pubmed_id), normalize_simple_id(right_ids.pubmed_id)):
        return True
    return _titles_match(left, right)


def _titles_match(left: Paper, right: Paper) -> bool:
    if left.year is None or right.year is None:
        return False
    if abs(left.year - right.year) > 1:
        return False

    left_title = normalize_title(left.title)
    right_title = normalize_title(right.title)
    if not left_title or not right_title:
        return False
    if left_title == right_title:
        return True
    return SequenceMatcher(None, left_title, right_title).ratio() >= TITLE_SIMILARITY_THRESHOLD


def _merge_papers(existing: Paper, incoming: Paper) -> Paper:
    return Paper(
        title=_choose_title(existing.title, incoming.title),
        authors=_choose_authors(existing.authors, incoming.authors),
        year=existing.year if existing.year is not None else incoming.year,
        venue=existing.venue or incoming.venue,
        abstract=_choose_longer_text(existing.abstract, incoming.abstract),
        identifiers=_merge_identifiers(existing.identifiers, incoming.identifiers),
        urls=_merge_urls(existing.urls, incoming.urls),
        sources=_merge_unique(existing.sources, incoming.sources),
        citation_count=max(existing.citation_count or 0, incoming.citation_count or 0),
    )


def _merge_identifiers(left: PaperIdentifiers, right: PaperIdentifiers) -> PaperIdentifiers:
    return PaperIdentifiers(
        doi=left.doi or right.doi,
        arxiv_id=left.arxiv_id or right.arxiv_id,
        semantic_scholar_id=left.semantic_scholar_id or right.semantic_scholar_id,
        openalex_id=left.openalex_id or right.openalex_id,
        pubmed_id=left.pubmed_id or right.pubmed_id,
    )


def _merge_urls(left: PaperUrls, right: PaperUrls) -> PaperUrls:
    return PaperUrls(
        landing_page=left.landing_page or right.landing_page,
        pdf=left.pdf or right.pdf,
    )


def _choose_title(left: str, right: str) -> str:
    if _is_placeholder_title(left) and not _is_placeholder_title(right):
        return right
    if _is_placeholder_title(right) and not _is_placeholder_title(left):
        return left
    return right if len(right.strip()) > len(left.strip()) else left


def _choose_authors(left: list[str], right: list[str]) -> list[str]:
    return right if len(right) > len(left) else left


def _choose_longer_text(left: str, right: str) -> str:
    return right if len(right.strip()) > len(left.strip()) else left


def _merge_unique(*values: Iterable[str]) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for group in values:
        for value in group:
            key = value.strip().lower()
            if not key or key in seen:
                continue
            merged.append(value)
            seen.add(key)
    return merged


def _same_non_empty(left: str | None, right: str | None) -> bool:
    return bool(left and right and left == right)


def _is_placeholder_title(value: str) -> bool:
    return normalize_title(value).startswith("untitled")


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
