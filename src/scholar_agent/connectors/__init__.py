"""Academic search connectors."""

from .arxiv import search_arxiv
from .openalex import search_openalex

__all__ = ["search_arxiv", "search_openalex"]

