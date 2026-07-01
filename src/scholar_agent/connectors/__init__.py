"""Academic search connectors."""

from .arxiv import search_arxiv
from .openalex import fetch_openalex_references, search_openalex

__all__ = ["fetch_openalex_references", "search_arxiv", "search_openalex"]
