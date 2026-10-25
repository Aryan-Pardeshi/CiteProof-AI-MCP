"""The backend serves the documentation pages at their clean URLs."""

import pytest
from fastapi.testclient import TestClient

from backend.app.main import app

client = TestClient(app)


@pytest.mark.parametrize(
    ("path", "marker"),
    [
        ("/docs", "CiteProof documentation"),
        ("/docs/mcp", "MCP tool reference"),
        ("/docs/research-workflow", "<h1>"),
        ("/docs/self-hosting", "Self-hosting CiteProof"),
        ("/docs/troubleshooting", "<h1>"),
        ("/mcp", "Give Claude Science a verifiable research workflow"),
    ],
)
def test_documentation_pages_are_served(path: str, marker: str) -> None:
    response = client.get(path)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "no-cache" in response.headers["cache-control"]
    assert marker in response.text


def test_interactive_api_docs_moved_to_api_docs() -> None:
    assert client.get("/api-docs").status_code == 200
    assert "swagger" in client.get("/api-docs").text.lower()
    assert client.get("/docs").text != client.get("/api-docs").text
