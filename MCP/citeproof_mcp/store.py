# SPDX-License-Identifier: MIT
"""Per-review SQLite storage.

One file per review under ``<home>/reviews/<review_id>.db``. This module owns the
``meta`` table only. Every tool module owns its own tables: it declares their
``CREATE TABLE IF NOT EXISTS`` statements once with :func:`register_schema`, and
:func:`open_review` applies them the first time a review file is opened in this
process. Modules may also declare dynamic schema hooks with :func:`register_schema_hook`
which run inside the schema transaction after all statements have run. Modules
never touch each other's tables; when one module needs another
module's data it calls that module's public read function.

Concurrency: files use WAL journaling and a 30 s busy timeout, and write
transactions start with ``BEGIN IMMEDIATE`` (see :func:`review_db`), so two tool
calls on the same review wait for each other instead of failing with
"database is locked".
"""

from __future__ import annotations

import os
import re
import secrets
import sqlite3
import threading
import unicodedata
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REVIEW_ID_RE = re.compile(r"^[a-z0-9_-]{6,40}$")
HOME_ENV = "CITEPROOF_MCP_HOME"
BUSY_TIMEOUT_SECONDS = 30.0
MAX_PAPER_REF_CHARS = 300

PAPER_REF_HELP = (
    "paper_ref must be a DOI ('10.1234/abc', also accepted as 'doi:...' or a doi.org URL), "
    "'pmid:<digits>', or 'title:<title>|<year>'"
)


class StoreError(ValueError):
    """Bad review id, unknown review, or invalid input. The message is safe to show the agent."""


def now_iso() -> str:
    """Current UTC time as ISO-8601 with seconds, the timestamp format of every table."""
    return datetime.now(UTC).isoformat(timespec="seconds")


def data_dir() -> Path:
    """Root folder for all review files. Created on demand."""
    raw = os.environ.get(HOME_ENV, "").strip()
    root = Path(raw) if raw else Path.home() / ".citeproof-mcp"
    (root / "reviews").mkdir(parents=True, exist_ok=True)
    return root


def _db_path(review_id: str) -> Path:
    if not isinstance(review_id, str) or not REVIEW_ID_RE.match(review_id):
        raise StoreError("review_id must match ^[a-z0-9_-]{6,40}$ (use the id returned by review_create)")
    return data_dir() / "reviews" / f"{review_id}.db"


# ---------------------------------------------------------------------------
# Schema registry
# ---------------------------------------------------------------------------

_SCHEMAS: list[str] = []
_SCHEMA_HOOKS: list[Callable[[sqlite3.Connection], None]] = []
_APPLIED: dict[str, int] = {}
_SCHEMA_LOCK = threading.Lock()


def register_schema(statements: Sequence[str]) -> None:
    """Declare a module's ``CREATE ... IF NOT EXISTS`` statements (and ``ALTER TABLE ... ADD COLUMN``
    upgrades for older files). Call once at import time."""
    with _SCHEMA_LOCK:
        for stmt in statements:
            if stmt not in _SCHEMAS:
                _SCHEMAS.append(stmt)


def register_schema_hook(fn: Callable[[sqlite3.Connection], None]) -> None:
    """Declare a hook to run inside the schema transaction on a review connection after all schema
    statements have run. Call once at import time."""
    with _SCHEMA_LOCK:
        if fn not in _SCHEMA_HOOKS:
            _SCHEMA_HOOKS.append(fn)


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=BUSY_TIMEOUT_SECONDS)
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA busy_timeout = {int(BUSY_TIMEOUT_SECONDS * 1000)}")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _ensure_schema(conn: sqlite3.Connection, path: Path) -> None:
    key = str(path)
    with _SCHEMA_LOCK:
        wanted_stmts = list(_SCHEMAS)
        wanted_hooks = list(_SCHEMA_HOOKS)
    wanted_count = len(wanted_stmts) + len(wanted_hooks)
    if _APPLIED.get(key) == wanted_count:
        return
    try:
        # Files created before WAL was used are switched once; the setting is stored in the file.
        conn.execute("PRAGMA journal_mode = WAL")
    except sqlite3.OperationalError:
        pass  # another connection holds the file; WAL is an optimisation, not a requirement
    conn.execute("BEGIN IMMEDIATE")
    try:
        for stmt in wanted_stmts:
            try:
                conn.execute(stmt)
            except sqlite3.OperationalError as exc:
                # "ALTER TABLE ... ADD COLUMN" statements upgrade older files; on a
                # file that already has the column they fail harmlessly.
                if not (stmt.lstrip().upper().startswith("ALTER TABLE") and "duplicate column" in str(exc)):
                    raise
        for hook in wanted_hooks:
            hook(conn)
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    _APPLIED[key] = wanted_count


# ---------------------------------------------------------------------------
# Reviews
# ---------------------------------------------------------------------------


def create_review(
    title: str,
    question: str,
    year_min: int | None = None,
    year_max: int | None = None,
) -> str:
    """Create a review file and return its id."""
    title = (title or "").strip() if isinstance(title, str) else ""
    question = (question or "").strip() if isinstance(question, str) else ""
    if not title:
        raise StoreError("title is required")
    if not question:
        raise StoreError("question is required: state the review question in one sentence")
    if len(title) > 500 or len(question) > 2000:
        raise StoreError("title is limited to 500 characters and question to 2000")
    check_year_range(year_min, year_max)

    for _ in range(5):
        review_id = f"rev-{secrets.token_hex(4)}"
        path = _db_path(review_id)
        if not path.exists():
            break
    else:  # pragma: no cover - 5 collisions of 32 random bits
        raise StoreError("could not allocate a review id, retry")

    conn = _connect(path)
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute(
            "CREATE TABLE meta (review_id TEXT PRIMARY KEY, title TEXT NOT NULL, question TEXT NOT NULL, "
            "created_at TEXT NOT NULL, year_min INTEGER, year_max INTEGER)"
        )
        conn.execute(
            "INSERT INTO meta VALUES (?, ?, ?, ?, ?, ?)",
            (review_id, title, question, now_iso(), year_min, year_max),
        )
        conn.commit()
    finally:
        conn.close()
    return review_id


def check_year_range(year_min: int | None, year_max: int | None) -> None:
    """Raise StoreError unless both years are None or within 1800..3000 and ordered."""
    for year in (year_min, year_max):
        if year is None:
            continue
        if isinstance(year, bool) or not isinstance(year, int) or not (1800 <= year <= 3000):
            raise StoreError("year_min and year_max must be whole years between 1800 and 3000")
    if year_min is not None and year_max is not None and year_min > year_max:
        raise StoreError("year_min must not be greater than year_max")


def set_year_range(conn: sqlite3.Connection, year_min: int | None, year_max: int | None) -> None:
    """Update the review's publication year range inside the caller's transaction."""
    check_year_range(year_min, year_max)
    conn.execute("UPDATE meta SET year_min = ?, year_max = ?", (year_min, year_max))


def open_review(review_id: str) -> sqlite3.Connection:
    """Open an existing review. Rows come back as ``sqlite3.Row``. The caller closes the connection."""
    path = _db_path(review_id)
    if not path.exists():
        raise StoreError(f"unknown review_id: {review_id} (call review_list to see existing reviews)")
    conn = _connect(path)
    try:
        _ensure_schema(conn, path)
    except BaseException:
        conn.close()
        raise
    return conn


@contextmanager
def review_db(review_id: str, write: bool = False) -> Iterator[sqlite3.Connection]:
    """Open a review for one tool call.

    With ``write=True`` the whole call runs in one ``BEGIN IMMEDIATE`` transaction:
    it commits when the block exits normally and rolls back on any exception.
    """
    conn = open_review(review_id)
    try:
        if write:
            conn.execute("BEGIN IMMEDIATE")
        yield conn
        if conn.in_transaction:
            conn.commit()
    except BaseException:
        if conn.in_transaction:
            conn.rollback()
        raise
    finally:
        conn.close()


def read_meta(conn: sqlite3.Connection) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM meta").fetchone()
    return dict(row) if row else {}


def get_meta(review_id: str) -> dict[str, Any]:
    with review_db(review_id) as conn:
        return read_meta(conn)


def list_reviews(limit: int = 50) -> list[dict[str, Any]]:
    """Most recently created first."""
    out: list[dict[str, Any]] = []
    for path in (data_dir() / "reviews").glob("*.db"):
        try:
            out.append(get_meta(path.stem))
        except (StoreError, sqlite3.Error):
            continue
    out.sort(key=lambda m: m.get("created_at") or "", reverse=True)
    return out[:limit]


# ---------------------------------------------------------------------------
# Paper references
# ---------------------------------------------------------------------------

_DOI_RE = re.compile(r"10\.\d{4,9}/\S+", re.IGNORECASE)
_DOI_PREFIX_RE = re.compile(r"^(?:https?://)?(?:dx\.)?doi\.org/|^doi:\s*", re.IGNORECASE)


def normalize_doi(raw: Any) -> str | None:
    """Lower-cased bare DOI from a DOI, ``doi:`` string or doi.org URL; None when there is none."""
    if not isinstance(raw, str):
        return None
    text = _DOI_PREFIX_RE.sub("", raw.strip()).strip()
    m = _DOI_RE.match(text)
    if not m:
        return None
    doi = m.group(0).lower().rstrip(".,;")
    while doi.endswith(")") and doi.count("(") < doi.count(")"):
        doi = doi[:-1]
    return doi if re.match(r"^10\.\d{4,9}/.+", doi) else None


def normalize_title(title: str) -> str:
    """Lower-case title with accents folded ('Müller' -> 'muller') and punctuation turned into
    single spaces. Letters of any script are kept, so non-Latin titles do not collapse to ''."""
    if not isinstance(title, str):
        return ""
    folded = "".join(
        ch for ch in unicodedata.normalize("NFKD", title) if not unicodedata.combining(ch)
    )
    return re.sub(r"[\W_]+", " ", folded.lower()).strip()


def normalize_paper_ref(
    doi: str | None = None,
    pmid: str | None = None,
    title: str | None = None,
    year: int | None = None,
) -> str:
    """Stable paper key: normalised DOI, else ``pmid:<n>``, else ``title:<norm title>|<year>``."""
    norm_doi = normalize_doi(doi) if doi else None
    if norm_doi:
        return norm_doi
    if pmid is not None and str(pmid).strip().isdigit():
        return f"pmid:{int(str(pmid).strip())}"
    if title and title.strip():
        norm = normalize_title(title)
        if norm:
            return f"title:{norm}|{year if year else 'nd'}"
    raise StoreError("need a doi, pmid or title to identify a paper")


def canonical_paper_ref(ref: Any) -> str:
    """Validate a paper_ref supplied by the agent and return its canonical form.

    DOIs are lower-cased (DOIs are case-insensitive) with any ``doi:`` or doi.org
    prefix removed; ``pmid:`` keeps digits only; ``title:`` refs get the same
    title normalisation as :func:`normalize_paper_ref`. The same paper therefore
    always maps to the same key, whichever form the agent sends.
    """
    if not isinstance(ref, str) or not ref.strip():
        raise StoreError(f"paper_ref is required; {PAPER_REF_HELP}")
    text = ref.strip()
    if len(text) > MAX_PAPER_REF_CHARS:
        raise StoreError(f"paper_ref is longer than {MAX_PAPER_REF_CHARS} characters; {PAPER_REF_HELP}")
    low = text.lower()
    if low.startswith("pmid:"):
        digits = low[5:].strip()
        if digits.isdigit():
            return f"pmid:{int(digits)}"
        raise StoreError(f"'{text}' is not a valid pmid ref; {PAPER_REF_HELP}")
    if low.startswith("title:"):
        body = text[6:]
        title_part, sep, year_part = body.rpartition("|")
        if not sep:
            title_part, year_part = body, ""
        year_part = year_part.strip().lower()
        if year_part and year_part != "nd" and not re.fullmatch(r"\d{4}", year_part):
            raise StoreError(f"the year after '|' in '{text}' must be 4 digits or 'nd'; {PAPER_REF_HELP}")
        norm = normalize_title(title_part)
        if not norm:
            raise StoreError(f"'{text}' has an empty title; {PAPER_REF_HELP}")
        return f"title:{norm}|{year_part or 'nd'}"
    doi = normalize_doi(text)
    if doi:
        return doi
    raise StoreError(f"'{text[:80]}' is not a recognised paper_ref; {PAPER_REF_HELP}")
