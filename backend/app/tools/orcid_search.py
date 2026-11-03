"""ORCID v3 public API integration for author identity and disambiguation.

This module deliberately does NOT implement a standalone paper-search source:
ORCID is an author registry, and its role in the retrieval pipeline is to feed
PaperRecord.authors with canonical, disambiguated name forms. It uses the
PUBLIC API only (https://pub.orcid.org/v3.0, Accept: application/json, no API
key, no membership required); the member API is never touched.

Endpoints used (verified against live API 2026-08):
    GET /v3.0/expanded-search/?q={query}&rows={limit}
        -> {"expanded-result": [{orcid-id, given-names, family-names,
           credit-name, other-name[], institution-name[]}], num-found}
        Used by resolve_orcid_authors() for name -> ORCID iD resolution.
    GET /v3.0/{orcid_id}  (expanded record summary)
        -> person.name.{given-names, family-names, credit-name}
        Used by fetch_orcid_profile() to canonicalize a known iD.

ORCID raw field -> adapter output mapping (AuthorIdentity, adapter-local type;
only canonical author name strings and confidently matched iDs reach PaperRecord):
    expanded-result[].orcid-id         -> AuthorIdentity.orcid_id
    expanded-result[].given-names      -> AuthorIdentity.given_names
    expanded-result[].family-names     -> AuthorIdentity.family_names
    expanded-result[].credit-name      -> AuthorIdentity.credit_name (preferred display form when set)
    expanded-result[].institution-name[] -> AuthorIdentity.institutions
    (derived)                          -> AuthorIdentity.display_name
                                          = credit-name, else "Given Family"
    (derived)                          -> PaperRecord.authors[i] via
                                          enrich_record_authors(): the author
                                          string is replaced by the canonical
                                          display_name when the ORCID record
                                          confidently matches the input name
                                          (ORCID name tokens are a subset of the
                                          input name's). Ambiguous or failed
                                          lookups leave the original untouched.
    (derived)                          -> PaperRecord.author_orcids[name] =
                                          AuthorIdentity.orcid_id, on the same
                                          confident match only.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from collections import Counter
from typing import Any

import httpx
from pydantic import BaseModel, Field

from backend.app.models.evidence import PaperRecord

logger = logging.getLogger(__name__)

ORCID_API_BASE = "https://pub.orcid.org/v3.0"
ORCID_TIMEOUT = 8.0
# Each enriched author costs one ORCID request, so the count is capped.
ORCID_MAX_AUTHORS_DEFAULT = 2
ORCID_MAX_AUTHORS_CAP = 10

_JSON_HEADERS = {"Accept": "application/json"}


def orcid_max_authors_from_env() -> int:
    """Leading authors to enrich, from ORCID_MAX_AUTHORS (default 2, clamped to 1..10)."""
    raw = os.getenv("ORCID_MAX_AUTHORS", "").strip()
    try:
        value = int(raw) if raw else ORCID_MAX_AUTHORS_DEFAULT
    except ValueError:
        logger.warning(f"Ignoring invalid ORCID_MAX_AUTHORS={raw!r}; using {ORCID_MAX_AUTHORS_DEFAULT}")
        value = ORCID_MAX_AUTHORS_DEFAULT
    return max(1, min(value, ORCID_MAX_AUTHORS_CAP))


class AuthorIdentity(BaseModel):
    """Adapter-local ORCID author identity; only orcid_id and display_name reach PaperRecord."""

    orcid_id: str
    given_names: str | None = None
    family_names: str | None = None
    credit_name: str | None = None
    institutions: list[str] = Field(default_factory=list)

    @property
    def display_name(self) -> str:
        """Canonical display form: credit-name when set, else 'Given Family'."""
        if self.credit_name and self.credit_name.strip():
            return self.credit_name.strip()
        return f"{self.given_names or ''} {self.family_names or ''}".strip()


def _name_tokens(name: str) -> set:
    """Lowercased alphanumeric tokens of a personal name."""
    return {t for t in re.findall(r"[a-z0-9]+", (name or "").lower()) if t}


def _confident_match(query_name: str, identity: AuthorIdentity) -> bool:
    """True when the ORCID record's name tokens are a subset of the query's.

    ORCID records may omit middle names/initials, so the check is directional:
    every token of the ORCID name must appear in the queried name. Single-token
    (surname-only) matches are never confident.
    """
    orcid_tokens = _name_tokens(identity.display_name)
    query_tokens = _name_tokens(query_name)
    return len(orcid_tokens) >= 2 and orcid_tokens.issubset(query_tokens)


def _parse_expanded_result(entry: dict) -> AuthorIdentity | None:
    orcid_id = (entry.get("orcid-id") or "").strip()
    if not orcid_id:
        return None
    institutions = [
        str(i).strip() for i in (entry.get("institution-name") or []) if str(i).strip()
    ]
    return AuthorIdentity(
        orcid_id=orcid_id,
        given_names=(entry.get("given-names") or "").strip() or None,
        family_names=(entry.get("family-names") or "").strip() or None,
        credit_name=(entry.get("credit-name") or "").strip() or None,
        institutions=institutions,
    )


async def resolve_orcid_authors(
    query: str,
    limit: int = 5,
    client: httpx.AsyncClient | None = None,
) -> list[AuthorIdentity]:
    """Resolve a free-text author name to ORCID iDs via the public expanded-search.

    Query syntax supports ORCID search fields (e.g. "family-names:Turing").
    Returns [] on any failure; never raises.
    """
    url = f"{ORCID_API_BASE}/expanded-search/"
    params = {"q": query, "rows": min(limit, 20)}

    try:
        if client is not None:
            resp = await client.get(url, params=params, headers=_JSON_HEADERS)
        else:
            async with httpx.AsyncClient(timeout=ORCID_TIMEOUT, follow_redirects=True) as own_client:
                resp = await own_client.get(url, params=params, headers=_JSON_HEADERS)

        if resp.status_code != 200:
            logger.warning(f"ORCID expanded-search returned status {resp.status_code} for: {query[:40]!r}")
            return []

        entries = (resp.json() or {}).get("expanded-result") or []
        identities = [i for i in (_parse_expanded_result(e) for e in entries if isinstance(e, dict)) if i]
        logger.info(f"ORCID resolved {len(identities)} identities for: {query[:40]!r}")
        return identities

    except Exception as e:  # noqa: BLE001
        logger.warning(f"ORCID expanded-search failed for {query[:40]!r}: {e}")
        return []


def _orcid_value(field: Any) -> str:
    """Unwrap an ORCID person field: the record endpoint returns
    {'value': 'Josiah'} objects while expanded-search returns plain strings."""
    if isinstance(field, dict):
        return str(field.get("value") or "").strip()
    return str(field or "").strip()


async def fetch_orcid_profile(
    orcid_id: str,
    client: httpx.AsyncClient | None = None,
) -> AuthorIdentity | None:
    """Fetch the canonical person name for a known ORCID iD. None on failure."""
    orcid_id = orcid_id.strip()
    if not re.fullmatch(r"\d{4}-\d{4}-\d{4}-[\dX]{4}", orcid_id):
        return None

    url = f"{ORCID_API_BASE}/{orcid_id}"
    try:
        if client is not None:
            resp = await client.get(url, headers=_JSON_HEADERS)
        else:
            async with httpx.AsyncClient(timeout=ORCID_TIMEOUT, follow_redirects=True) as own_client:
                resp = await own_client.get(url, headers=_JSON_HEADERS)

        if resp.status_code != 200:
            return None

        person = (resp.json() or {}).get("person") or {}
        name = person.get("name") or {}
        given = _orcid_value(name.get("given-names"))
        family = _orcid_value(name.get("family-names"))
        credit = _orcid_value(name.get("credit-name"))
        return AuthorIdentity(
            orcid_id=orcid_id,
            given_names=given or None,
            family_names=family or None,
            credit_name=credit or None,
        )

    except Exception as e:  # noqa: BLE001
        logger.warning(f"ORCID profile fetch failed for {orcid_id}: {e}")
        return None


async def enrich_record_authors(
    record: PaperRecord,
    max_authors: int = ORCID_MAX_AUTHORS_DEFAULT,
    client: httpx.AsyncClient | None = None,
) -> PaperRecord:
    """Canonicalize leading author names on a PaperRecord via ORCID.

    For each of the first `max_authors` authors, resolves the name against the
    ORCID public API; when exactly one candidate confidently matches (token
    subset, see _confident_match), the author string is replaced by the ORCID
    display name and its iD is stored in author_orcids under that name.
    Ambiguous or failed lookups leave the original untouched and store no iD,
    and no iD is kept for a name that appears more than once on the paper.

    Returns a new PaperRecord (model_copy); the input record is never mutated.
    Intended for the metadata-validation stage; opt-in via
    ORCID_AUTHOR_ENRICHMENT=1 to keep default pipeline latency unchanged.
    """
    authors = list(record.authors or [])
    if not authors:
        return record

    updated = list(authors)
    orcids = dict(record.author_orcids or {})
    changed = False

    if client is not None:
        own_client = None
        usable_client = client
    else:
        own_client = httpx.AsyncClient(timeout=ORCID_TIMEOUT, follow_redirects=True)
        usable_client = own_client

    try:
        for idx, raw_name in enumerate(authors[:max_authors]):
            if not raw_name or len(_name_tokens(raw_name)) < 2:
                continue
            # quoted phrase query keeps multi-word names together
            identities = await resolve_orcid_authors(f'"{raw_name}"', limit=5, client=usable_client)
            confident = [i for i in identities if _confident_match(raw_name, i)]
            if len(confident) == 1:
                canonical = confident[0].display_name or raw_name
                updated[idx] = canonical
                # Stored even when the name is already canonical.
                orcids[canonical] = confident[0].orcid_id
                changed = True
    except Exception as e:  # noqa: BLE001
        logger.debug(f"ORCID author enrichment skipped: {e}")
    finally:
        if own_client is not None:
            await own_client.aclose()

    # author_orcids is keyed by name, so an iD is only safe when that name is
    # unique on the paper; otherwise it would also be shown for a namesake.
    # Keys for names no longer on the paper (e.g. a pre-canonical spelling) are
    # dropped too.
    name_counts = Counter(updated)
    orcids = {name: oid for name, oid in orcids.items() if name_counts[name] == 1}

    if not changed:
        return record
    return record.model_copy(update={"authors": updated, "author_orcids": orcids})


async def enrich_records_authors(
    records: list[PaperRecord],
    max_authors: int = ORCID_MAX_AUTHORS_DEFAULT,
    concurrency: int = 4,
    client: httpx.AsyncClient | None = None,
) -> list[PaperRecord]:
    """Batch wrapper over enrich_record_authors with bounded concurrency."""
    semaphore = asyncio.Semaphore(concurrency)

    async def _one(rec: PaperRecord) -> PaperRecord:
        async with semaphore:
            return await enrich_record_authors(rec, max_authors=max_authors, client=client)

    return list(await asyncio.gather(*[_one(r) for r in records]))
