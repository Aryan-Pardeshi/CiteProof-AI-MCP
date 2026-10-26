import asyncio
import logging
import os
import time
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv
from langchain_community.cache import SQLiteCache
from langchain_core.globals import set_llm_cache
from langchain_openai import ChatOpenAI

load_dotenv()
logger = logging.getLogger(__name__)


class LoggingSQLiteCache(SQLiteCache):
    def lookup(self, prompt: str, llm_string: str):
        result = super().lookup(prompt, llm_string)
        prefix = (prompt[:50] + "...") if len(prompt) > 50 else prompt
        if result is not None:
            logger.info(f"[LLM CACHE HIT] Prompt: {prefix!r}")
        else:
            logger.info(f"[LLM CACHE MISS] Prompt: {prefix!r}")
        return result


def init_llm_cache():
    try:
        cache_path_str = os.getenv("LLM_CACHE_PATH", "./data/llm_cache.db")
        cache_path = Path(cache_path_str).resolve()
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        set_llm_cache(LoggingSQLiteCache(database_path=str(cache_path)))
        logger.info(f"LLM cache initialized at {cache_path}")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Failed to initialize SQLite LLM cache: {e}")


init_llm_cache()


def get_llm_config():
    api_key = os.getenv("LLM_API_KEY") or os.getenv("DEEPSEEK_API_KEY")
    base_url = os.getenv("LLM_BASE_URL") or os.getenv("DEEPSEEK_BASE_URL") or "https://api.deepseek.com"
    return api_key, base_url


# Model ids that must never receive a research prompt: `openrouter/free` once
# routed one to a safety classifier, and any classifier answers "User Safety:
# safe" instead of a report.
_FORBIDDEN_MODEL_IDS = frozenset({"openrouter/free"})
_FORBIDDEN_MODEL_MARKERS = ("safety", "guard", "moderation")

# Transient failures worth retrying: rate limits, provider errors and 5xx.
_LLM_MAX_ATTEMPTS = 3
_LLM_RETRY_BACKOFF_SECONDS = (0.5, 1.5)
_TRANSIENT_STATUS_CODES = frozenset({404, 429, 500, 502, 503, 504})
_TRANSIENT_MARKERS = (
    "rate limit",
    "rate-limit",
    "ratelimited",
    "provider returned error",
    "temporarily unavailable",
    "overloaded",
    "timeout",
    "timed out",
    "connection reset",
    "connection error",
    "server error",
)


def require_safe_model(model: str) -> str:
    """Refuse a model id that is a router or a safety classifier."""
    lowered = str(model or "").strip().lower()
    if lowered in _FORBIDDEN_MODEL_IDS or any(
        marker in lowered for marker in _FORBIDDEN_MODEL_MARKERS
    ):
        raise RuntimeError(
            f"Model {model!r} is not a report writer. Configure a completion "
            "model that is not a router or a safety, guard or moderation classifier."
        )
    return model


def _status_code(exc: BaseException) -> int | None:
    value = getattr(exc, "status_code", None)
    if isinstance(value, int):
        return value
    response = getattr(exc, "response", None)
    value = getattr(response, "status_code", None)
    return value if isinstance(value, int) else None


def is_transient_llm_error(exc: BaseException) -> bool:
    """True for a free-model failure worth retrying.

    Free OpenRouter models regularly answer 429 (rate limited), 404 ("provider
    returned error") and 5xx. Auth, validation and schema errors are not
    transient and must fail fast.
    """
    status = _status_code(exc)
    if status is not None:
        return status in _TRANSIENT_STATUS_CODES
    text = str(exc).lower()
    return any(marker in text for marker in _TRANSIENT_MARKERS)


def _retry_delay(attempt: int) -> float:
    """Short bounded backoff; the whole retry budget stays under 60 seconds."""
    index = min(max(attempt - 1, 0), len(_LLM_RETRY_BACKOFF_SECONDS) - 1)
    return _LLM_RETRY_BACKOFF_SECONDS[index]


def invoke_llm(llm, *args, **kwargs):
    """Call ``llm.invoke``, retrying transient failures up to three attempts."""
    for attempt in range(1, _LLM_MAX_ATTEMPTS + 1):
        try:
            return llm.invoke(*args, **kwargs)
        except Exception as exc:
            if attempt >= _LLM_MAX_ATTEMPTS or not is_transient_llm_error(exc):
                raise
            time.sleep(_retry_delay(attempt))


async def ainvoke_llm(llm, *args, **kwargs):
    """Await ``llm.ainvoke`` with the same bounded retry budget."""
    for attempt in range(1, _LLM_MAX_ATTEMPTS + 1):
        try:
            return await llm.ainvoke(*args, **kwargs)
        except Exception as exc:
            if attempt >= _LLM_MAX_ATTEMPTS or not is_transient_llm_error(exc):
                raise
            await asyncio.sleep(_retry_delay(attempt))


OPENROUTER_APP_HEADERS = {
    "HTTP-Referer": "https://citeproof.top",
    "X-Title": "CiteProof",
}


def get_llm(model: str | None = None, role: str | None = None, temperature: float = 0.0) -> ChatOpenAI:
    api_key, base_url = get_llm_config()
    if not api_key or api_key == "your_api_key_here" or api_key == "your_key_here":
        raise RuntimeError(
            "LLM_API_KEY is not set. "
            "Set it in the .env file or via the settings modal."
        )

    if not model:
        if role == "planner":
            model = os.getenv("LLM_MODEL_PLANNER", "deepseek-chat")
        elif role == "researcher":
            model = os.getenv("LLM_MODEL_RESEARCHER", "deepseek-chat")
        elif role == "aggregator":
            model = os.getenv("LLM_MODEL_AGGREGATOR", "deepseek-chat")
        else:
            model = os.getenv("LLM_MODEL_PLANNER", "deepseek-chat")
    model = require_safe_model(model)

    timeout_val = float(os.getenv("LLM_REQUEST_TIMEOUT", "60.0"))
    kwargs = {}
    host = (urlparse(base_url or "").hostname or "").lower()
    if host == "openrouter.ai" or host.endswith(".openrouter.ai"):
        # App attribution: OpenRouter lists usage under this name and site
        # instead of "Unknown".
        kwargs["default_headers"] = dict(OPENROUTER_APP_HEADERS)
    return ChatOpenAI(
        model=model,
        openai_api_key=api_key,
        openai_api_base=base_url,
        temperature=temperature,
        request_timeout=timeout_val,
        max_retries=0,
        **kwargs,
    )


class _LazyLLM:
    def __init__(self, role: str | None = None, model_override: str | None = None):
        self._role = role
        self._model_override = model_override
        self._instance = None

    def _get(self):
        if self._instance is None:
            self._instance = get_llm(model=self._model_override, role=self._role)
        return self._instance

    def reset(self):
        self._instance = None

    def __getattr__(self, name):
        return getattr(self._get(), name)


llm = _LazyLLM()
lazy_llm = llm
llm_fast = _LazyLLM(role="researcher")
llm_pro = _LazyLLM(role="aggregator")


STRUCTURED_METHODS = ("json_schema", "function_calling", "json_mode")
_structured_method_cache: dict[tuple[str, str], str] = {}


def invoke_structured(base_llm, schema, prompt):
    """Invokes an LLM with structured output, negotiating the method per provider."""
    model = getattr(base_llm, "model_name", "?")
    base = str(getattr(base_llm, "openai_api_base", "") or "")
    key = (base, model)

    methods = STRUCTURED_METHODS
    cached = _structured_method_cache.get(key)
    if cached:
        methods = (cached,) + tuple(m for m in STRUCTURED_METHODS if m != cached)

    last_error = None
    for attempt in range(1, _LLM_MAX_ATTEMPTS + 1):
        transient = False
        for method in methods:
            try:
                result = base_llm.with_structured_output(schema, method=method).invoke(prompt)
                if result is None:
                    last_error = ValueError(f"{method} returned no parsed output")
                    continue
                if _structured_method_cache.get(key) != method:
                    logger.info(f"Structured output via '{method}' for {model} at {base}")
                    _structured_method_cache[key] = method
                return result
            except Exception as e:  # noqa: BLE001
                last_error = e
                transient = transient or is_transient_llm_error(e)
                logger.debug(f"Structured output method '{method}' failed for {model}: {e}")

        if not transient or attempt >= _LLM_MAX_ATTEMPTS:
            break
        time.sleep(_retry_delay(attempt))

    raise last_error if last_error else RuntimeError("Structured output failed")


async def ainvoke_structured_with_retry(
    base_llm,
    schema,
    prompt: str,
    max_retries: int = 2,
    strict: bool = False
):
    """Async structured output invocation with exponential backoff retry.
    
    strict=True enforces json_schema method only (for critical extraction agents).
    strict=False negotiates the full fallback chain (json_schema -> function_calling -> json_mode).
    """
    model = getattr(base_llm, "model_name", "?")
    base = str(getattr(base_llm, "openai_api_base", "") or "")
    key = (base, model)

    methods = ("json_schema",) if strict else STRUCTURED_METHODS
    cached = _structured_method_cache.get(key)
    if cached and not strict:
        methods = (cached,) + tuple(m for m in STRUCTURED_METHODS if m != cached)

    last_error = None
    start_time = asyncio.get_event_loop().time()
    total_deadline = 60.0  # seconds

    for attempt in range(max_retries + 1):
        if asyncio.get_event_loop().time() - start_time > total_deadline:
            break
        transient = False
        for method in methods:
            try:
                structured_chain = base_llm.with_structured_output(schema, method=method)
                result = await asyncio.to_thread(structured_chain.invoke, prompt)
                if result is not None:
                    if not strict and _structured_method_cache.get(key) != method:
                        _structured_method_cache[key] = method
                    return result
                last_error = ValueError(f"{method} returned empty structured output")
            except Exception as e:  # noqa: BLE001
                last_error = e
                transient = transient or is_transient_llm_error(e)
                logger.debug(f"Structured attempt {attempt+1}/{max_retries+1} ({method}) failed: {e}")
                # If non-transient error like invalid parameter/schema, don't repeat full method matrix
                if not is_transient_llm_error(e) and strict:
                    break

        if attempt < max_retries and transient:
            await asyncio.sleep(min(1.5 ** attempt, 5.0))
        elif attempt < max_retries:
            break

    raise last_error or RuntimeError("Structured output failed after retries")
