"""Backwards-compatible import path for the Tavily web-search adapter.

The implementation moved to :mod:`backend.app.tools.web_search`; this module
keeps the old import path (and the ``tavily_client`` test seam) working.
"""

from backend.app.tools.web_search import search_web
from backend.app.tools.web_search.tavily import tavily_client

__all__ = ["search_web", "tavily_client"]
