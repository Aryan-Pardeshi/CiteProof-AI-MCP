import logging
import os
import secrets
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from pydantic import BaseModel

root_dir = Path(__file__).resolve().parent.parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from fastapi.middleware.cors import CORSMiddleware

from backend.app.api.agent import router as agent_router
from backend.app.api.research_mode import router as research_mode_router
from backend.app.graph.builder import set_checkpointer as set_ds_checkpointer
from backend.app.graph.research_mode_builder import (
    set_checkpointer as set_rm_checkpointer,
)
from backend.app.llm import lazy_llm, llm_fast, llm_pro
from backend.app.stats import init_stats_db

load_dotenv()

logger = logging.getLogger(__name__)
ENV_PATH = root_dir / ".env"


def _config_api_token() -> str:
    return os.getenv("CONFIG_API_TOKEN", "").strip()


def _config_api_is_open() -> bool:
    return os.getenv("ALLOW_OPEN_CONFIG_API", "").strip().lower() in {"1", "true", "yes"}


def _authorize_config_write(token: str | None) -> None:
    """Guards the endpoints that rewrite .env.

    This API can overwrite LLM_API_KEY and repoint LLM_BASE_URL, so it fails closed:
    without CONFIG_API_TOKEN set, or ALLOW_OPEN_CONFIG_API opted in for local work,
    it stays shut. A public deployment that forgets to configure anything is locked,
    not wide open.
    """
    expected = _config_api_token()
    if expected:
        if not token or not secrets.compare_digest(token, expected):
            raise HTTPException(status_code=401, detail="Invalid or missing X-Config-Token header")
        return
    if _config_api_is_open():
        return
    raise HTTPException(
        status_code=403,
        detail="Configuration API is disabled. Set CONFIG_API_TOKEN, or ALLOW_OPEN_CONFIG_API=1 for local use."
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    db_path_str = os.getenv("RESEARCH_DB_PATH", "./data/research_state.db")
    db_path = Path(db_path_str).resolve()
    db_path.parent.mkdir(parents=True, exist_ok=True)

    async with AsyncSqliteSaver.from_conn_string(str(db_path)) as checkpointer:
        await checkpointer.setup()
        set_rm_checkpointer(checkpointer)
        set_ds_checkpointer(checkpointer)
        # Warm the cumulative paper-counter DB so /research-mode/total-papers is
        # ready before the first request (and survives restarts on a mounted disk).
        await init_stats_db()
        yield


# The interactive API docs live at /api-docs so that /docs can serve the documentation portal.
app = FastAPI(title="AI Research Assistant Bot", lifespan=lifespan, docs_url="/api-docs")


@app.middleware("http")
async def add_security_headers(request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "geolocation=(), camera=(), microphone=()"
    return response


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(agent_router)
app.include_router(research_mode_router)

# Mount static directory for serving charts
static_path = Path(__file__).resolve().parent / "static"
static_path.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_path)), name="static")

frontend_path = root_dir / "frontend"


@app.get("/healthz")
def read_root():
    return {"status": "running", "message": "Research Bot API is up and running"}

def _get_config_status():
    llm_key = os.getenv("LLM_API_KEY") or os.getenv("DEEPSEEK_API_KEY")
    llm_url = os.getenv("LLM_BASE_URL") or os.getenv("DEEPSEEK_BASE_URL") or "https://api.deepseek.com"
    tavily_key = os.getenv("TAVILY_API_KEY")
    openalex_email = os.getenv("OPENALEX_EMAIL")
    
    placeholders = {"your_api_key_here", "your_key_here", "your_email@example.com", ""}
    
    missing_required = []
    if not llm_key or llm_key.strip() in placeholders:
        missing_required.append("LLM_API_KEY")
    if not tavily_key or tavily_key.strip() in placeholders:
        missing_required.append("TAVILY_API_KEY")
        
    return {
        "ok": len(missing_required) == 0,
        "llm_base_url": (llm_url or "") if _config_api_is_open() else "",
        "llm_api_key_configured": bool(llm_key) and llm_key.strip() not in placeholders,
        "tavily_configured": bool(tavily_key) and tavily_key.strip() not in placeholders,
        "llm_model_planner": os.getenv("LLM_MODEL_PLANNER", "deepseek-chat") if _config_api_is_open() else "",
        "llm_model_researcher": os.getenv("LLM_MODEL_RESEARCHER", "deepseek-chat") if _config_api_is_open() else "",
        "llm_model_aggregator": os.getenv("LLM_MODEL_AGGREGATOR", "deepseek-chat") if _config_api_is_open() else "",
        # Only echoed back when the config API is locally open; on a public
        # deployment this is somebody's personal address.
        "openalex_email": (openalex_email or "") if _config_api_is_open() else "",
        "config_writable": _config_api_is_open() or bool(_config_api_token()),
        "config_requires_token": bool(_config_api_token()),
        "semantic_scholar_api_key_configured": bool(os.getenv("SEMANTIC_SCHOLAR_API_KEY")),
        "core_api_key_configured": bool(os.getenv("CORE_API_KEY")),
        "missing_required": missing_required,
        "issues": [f"{k} is not set or using placeholder" for k in missing_required]
    }

@app.get("/config/status")
@app.get("/health/config")
def check_config():
    return _get_config_status()

class ConfigUpdate(BaseModel):
    LLM_BASE_URL: str | None = None
    LLM_API_KEY: str | None = None
    LLM_MODEL_PLANNER: str | None = None
    LLM_MODEL_RESEARCHER: str | None = None
    LLM_MODEL_AGGREGATOR: str | None = None
    OPENALEX_EMAIL: str | None = None
    SEMANTIC_SCHOLAR_API_KEY: str | None = None
    CORE_API_KEY: str | None = None
    TAVILY_API_KEY: str | None = None
    DEEPSEEK_API_KEY: str | None = None

def _apply_config_update(body: ConfigUpdate):
    updated = []
    try:
        content = ENV_PATH.read_text() if ENV_PATH.exists() else ""
        lines = content.splitlines(keepends=True)

        def upsert(key, value):
            if value is None:
                return
            for i, line in enumerate(lines):
                if line.strip().startswith(key + "=") or line.strip().startswith(key + " ="):
                    lines[i] = f'{key}="{value}"\n'
                    return
            lines.append(f'{key}="{value}"\n')

        data = body.model_dump(exclude_unset=True)
        for k, v in data.items():
            if v is not None:
                upsert(k, v)
                os.environ[k] = str(v)
                updated.append(k)

        ENV_PATH.write_text("".join(lines))
        load_dotenv(dotenv_path=ENV_PATH, override=True)

        lazy_llm.reset()
        llm_fast.reset()
        llm_pro.reset()

        return {"ok": True, "updated": updated, "message": "Applied configuration changes successfully"}
    except Exception:
        logger.exception("Failed to apply configuration changes")
        raise HTTPException(
            status_code=500, detail="Failed to apply configuration changes. Check the server logs for details."
        ) from None

@app.post("/config/setup")
@app.post("/health/config")
def update_config(body: ConfigUpdate, x_config_token: str | None = Header(None, alias="X-Config-Token")):
    _authorize_config_write(x_config_token)
    return _apply_config_update(body)


# Dedicated SEO and Crawler File Handlers
@app.get("/robots.txt", include_in_schema=False)
def serve_robots():
    robots_file = frontend_path / "robots.txt"
    if robots_file.exists():
        return FileResponse(robots_file, media_type="text/plain")
    raise HTTPException(status_code=404, detail="robots.txt not found")

@app.get("/sitemap.xml", include_in_schema=False)
def serve_sitemap():
    sitemap_file = frontend_path / "sitemap.xml"
    if sitemap_file.exists():
        return FileResponse(sitemap_file, media_type="application/xml")
    raise HTTPException(status_code=404, detail="sitemap.xml not found")

@app.get("/site.webmanifest", include_in_schema=False)
def serve_manifest():
    manifest_file = frontend_path / "site.webmanifest"
    if manifest_file.exists():
        return FileResponse(manifest_file, media_type="application/manifest+json")
    raise HTTPException(status_code=404, detail="site.webmanifest not found")

# Dedicated Research Workspace Application Handlers
@app.get("/app", include_in_schema=False)
@app.get("/app/", include_in_schema=False)
@app.get("/workspace", include_in_schema=False)
@app.get("/research", include_in_schema=False)
def serve_workspace_app():
    app_file = frontend_path / "app.html"
    if app_file.exists():
        return FileResponse(app_file, media_type="text/html", headers={"Cache-Control": "no-cache, must-revalidate"})
    raise HTTPException(status_code=404, detail="Research workspace app.html not found")

# Documentation portal and MCP setup pages. The static deployment serves these through clean
# URLs. Here each route maps to one known file, so nothing is built from the request path.
_DOC_PAGES = {
    "/mcp": "mcp.html",
    "/docs": "docs.html",
    "/docs/mcp": "docs/mcp.html",
    "/docs/research-workflow": "docs/research-workflow.html",
    "/docs/self-hosting": "docs/self-hosting.html",
    "/docs/troubleshooting": "docs/troubleshooting.html",
}


def _page_route(relative_path: str):
    def serve_page():
        page = frontend_path / relative_path
        if page.exists():
            return FileResponse(page, media_type="text/html", headers={"Cache-Control": "no-cache, must-revalidate"})
        raise HTTPException(status_code=404, detail=f"{relative_path} not found")

    return serve_page


for _route, _file in _DOC_PAGES.items():
    app.add_api_route(_route, _page_route(_file), methods=["GET"], include_in_schema=False)


class UIStaticFiles(StaticFiles):
    """StaticFiles that forces revalidation of HTML.

    StaticFiles sends only ETag/Last-Modified and no Cache-Control. With no
    caching directive at all, browsers fall back to heuristic caching (RFC 9111
    4.2.2) and may reuse index.html for a long time WITHOUT revalidating. Because
    index.html is what names the ?v= query on app.js and the stylesheets, a stale copy
    pins the browser to an old script forever: shipping a fix changes nothing for
    anyone already holding a cached page, and the only escape is a manual hard
    refresh. Serving HTML as no-cache keeps the ETag revalidation (cheap 304s)
    while guaranteeing a deploy is actually picked up. The versioned assets it
    references stay cacheable, since their URL changes whenever they do.
    """

    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        content_type = response.headers.get("content-type", "")
        if content_type.startswith("text/html"):
            response.headers["Cache-Control"] = "no-cache, must-revalidate"
        return response


# Serve the UI from the application root. Registered last so every API route above
# keeps precedence: a mount at "/" would otherwise swallow them.
if frontend_path.exists():
    app.mount("/", UIStaticFiles(directory=str(frontend_path), html=True), name="frontend_ui")

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.getenv("PORT", "8000")), reload=True)
