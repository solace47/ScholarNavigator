"""Academic search connectors."""

from .arxiv import search_arxiv, search_arxiv_detailed
from .openalex import (
    fetch_openalex_references,
    search_openalex,
    search_openalex_detailed,
)
from .pubmed import search_pubmed, search_pubmed_detailed
from .schemas import ConnectorSearchResult
from .semantic_scholar import (
    search_semantic_scholar,
    search_semantic_scholar_detailed,
)

__all__ = [
    "ConnectorSearchResult",
    "fetch_openalex_references",
    "search_arxiv",
    "search_arxiv_detailed",
    "search_openalex",
    "search_openalex_detailed",
    "search_pubmed",
    "search_pubmed_detailed",
    "search_semantic_scholar",
    "search_semantic_scholar_detailed",
]
