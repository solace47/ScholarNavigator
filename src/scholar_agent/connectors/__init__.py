"""Academic search connectors."""

from .arxiv import search_arxiv, search_arxiv_detailed
from .openalex import (
    fetch_openalex_references,
    search_openalex,
    search_openalex_detailed,
)
from .schemas import ConnectorSearchResult

__all__ = [
    "ConnectorSearchResult",
    "fetch_openalex_references",
    "search_arxiv",
    "search_arxiv_detailed",
    "search_openalex",
    "search_openalex_detailed",
]
