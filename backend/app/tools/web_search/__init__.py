"""Provider-neutral web search for DeepSearch.

``search_web`` is the only public entry point for the researcher agent; it
keeps its original name and signature and always returns one readable text
block. Which backend serves it is decided by ``WEB_SEARCH_PROVIDERS`` (ordered,
default ``tavily``) plus each provider's credentials. See the "Web search
providers" section of ``README.md``.
"""

from dotenv import load_dotenv

from backend.app.tools.web_search.base import (
    SearchResult,
    WebSearchProvider,
    format_results,
    normalize_search_topic,
    normalize_time_range,
)
from backend.app.tools.web_search.registry import (
    DEFAULT_PROVIDER_ORDER,
    PROVIDERS,
    PROVIDERS_ENV_VAR,
    configured_providers,
    listed_providers,
    provider_order,
    run_search,
    search_web,
)
from backend.app.tools.web_search.serpbase import SerpBaseError, SerpBaseProvider
from backend.app.tools.web_search.tavily import TavilyProvider, tavily_client

# Same behaviour as the old tavily_search module: pick up .env if present.
# Keys themselves are only read when a provider is actually used.
load_dotenv()

__all__ = [
    "DEFAULT_PROVIDER_ORDER",
    "PROVIDERS",
    "PROVIDERS_ENV_VAR",
    "SearchResult",
    "SerpBaseError",
    "SerpBaseProvider",
    "TavilyProvider",
    "WebSearchProvider",
    "configured_providers",
    "format_results",
    "listed_providers",
    "normalize_search_topic",
    "normalize_time_range",
    "provider_order",
    "run_search",
    "search_web",
    "tavily_client",
]
