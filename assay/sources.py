"""Source clients. Each one is independent, fails soft (returns empty +
records an error string), and uses only public endpoints so the whole engine
runs with zero paid keys.

IMPORTANT (verify before trusting in production):
  - OpenAlex: free, no key. Add a `mailto` to join the polite pool.
  - Patents: the USPTO is mid-migration. The legacy PatentsView site is moving
    to the Open Data Portal (data.uspto.gov); from 2026-06-18 access requires a
    USPTO.gov account / API key. This client targets the PatentsView Search API
    and accepts an optional key via X-Api-Key. Field names and the base URL are
    the most likely thing to drift — confirm against current docs.
  - SBIR.gov and NIH RePORTER: free, no key, but endpoint shapes change; the
    parsers here are deliberately defensive.

Live-test status (2026-06-11, from a datacenter IP):
  - OpenAlex, NIH RePORTER: verified working.
  - ClinicalTrials.gov: API verified working; some datacenter IPs are WAF-blocked
    (403) — a network/egress condition, not a client bug.
  - SBIR.gov: public API was returning 429 "not available at this time"
    (service-side maintenance); endpoint and params confirmed correct against docs.
  - PatentsView PatentSearch (search.patentsview.org): host no longer resolves —
    the API is mid-migration to data.uspto.gov with no public ETA. The keyless
    path is effectively down; ODP access needs a USPTO.gov API key (--patents-api-key).
    Critically, the engine now flags an unreachable patent source as UNVERIFIED
    rather than "no patents" — see engine._patent_flags.
"""
from __future__ import annotations

import asyncio

import httpx

from .models import Author, Patent, Grant

_TIMEOUT = httpx.Timeout(30.0)
_UA = {
    "User-Agent": "assay-provenance/0.1 (+https://github.com/ahammadshibil/assay)",
    "Accept": "application/json",
}


# ---------------------------------------------------------------- OpenAlex ----
class OpenAlexClient:
    BASE = "https://api.openalex.org"

    def __init__(self, mailto: str | None = None) -> None:
        self.mailto = mailto

    async def find_author(self, name: str, institution: str | None = None) -> tuple[Author | None, str | None]:
        params = {"search": name, "per-page": 5}
        if self.mailto:
            params["mailto"] = self.mailto
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT, headers=_UA) as c:
                r = await c.get(f"{self.BASE}/authors", params=params)
                r.raise_for_status()
                results = r.json().get("results", [])
        except Exception as e:  # noqa: BLE001 - fail soft by design
            return None, f"openalex: {e}"
        if not results:
            return None, None

        chosen = self._disambiguate(results, institution)
        author = self._parse_author(chosen, institution)
        # Fetch most recent work to measure research recency.
        recency_err = await self._attach_recency(author)
        return author, recency_err

    def _disambiguate(self, results: list[dict], institution: str | None) -> dict:
        if institution:
            inst_l = institution.lower()
            for a in results:
                insts = " ".join(
                    (i.get("display_name") or "")
                    for i in (a.get("last_known_institutions") or [])
                ).lower()
                if inst_l in insts:
                    return a
        return results[0]  # OpenAlex ranks by relevance

    def _parse_author(self, a: dict, institution: str | None) -> Author:
        stats = a.get("summary_stats") or {}
        insts = a.get("last_known_institutions") or []
        inst0 = insts[0] if insts else {}
        topics = [t.get("display_name") for t in (a.get("topics") or [])[:5] if t.get("display_name")]
        confidence = "likely" if (a.get("works_count") or 0) > 3 else "unverified"
        if institution and inst0.get("display_name") and institution.lower() in inst0["display_name"].lower():
            confidence = "strong"
        return Author(
            openalex_id=a.get("id"),
            display_name=a.get("display_name"),
            works_count=a.get("works_count") or 0,
            cited_by_count=a.get("cited_by_count") or 0,
            h_index=stats.get("h_index"),
            last_institution=inst0.get("display_name"),
            last_country=inst0.get("country_code"),
            top_topics=topics,
            match_confidence=confidence,
        )

    async def _attach_recency(self, author: Author) -> str | None:
        if not author.openalex_id:
            return None
        aid = author.openalex_id.rsplit("/", 1)[-1]
        params = {"filter": f"author.id:{aid}", "sort": "publication_date:desc", "per-page": 1}
        if self.mailto:
            params["mailto"] = self.mailto
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT, headers=_UA) as c:
                r = await c.get(f"{self.BASE}/works", params=params)
                r.raise_for_status()
                works = r.json().get("results", [])
            if works:
                author.last_publication_year = works[0].get("publication_year")
        except Exception as e:  # noqa: BLE001
            return f"openalex/works: {e}"
        return None


# ------------------------------------------------------------------ Patents ----
class PatentClient:
    # PatentsView PatentSearch API. As of 2026-06 this host no longer resolves —
    # the API is migrating to the USPTO Open Data Portal (data.uspto.gov) with no
    # public ETA. Override the base once ODP exposes an equivalent search endpoint:
    #   export ASSAY_PATENT_BASE="https://data.uspto.gov/api/v1/patent/..."
    # and pass --patents-api-key. The engine treats an unreachable patent source as
    # UNVERIFIED, never as "no patents" — so a dead endpoint can't read as clean IP.
    BASE = "https://search.patentsview.org/api/v1/patent/"

    def __init__(self, api_key: str | None = None, base: str | None = None) -> None:
        import os
        self.api_key = api_key
        self.base = base or os.getenv("ASSAY_PATENT_BASE") or self.BASE

    async def search_by_inventor(self, name: str) -> tuple[list[Patent], str | None]:
        last = name.strip().split()[-1]
        q = {"_contains": {"inventors.inventor_name_last": last}}
        f = ["patent_id", "patent_title", "patent_date",
             "inventors.inventor_name_first", "inventors.inventor_name_last",
             "assignees.assignee_organization"]
        headers = dict(_UA)
        if self.api_key:
            headers["X-Api-Key"] = self.api_key
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT, headers=headers,
                                         follow_redirects=True) as c:
                r = await c.get(self.base, params={"q": _json(q), "f": _json(f),
                                                   "o": _json({"size": 25})})
                r.raise_for_status()
                ctype = r.headers.get("content-type", "")
                if "json" not in ctype.lower():
                    # Migration redirect to an HTML portal page, etc. Don't pretend
                    # an HTML body is an empty patent set.
                    return [], (f"patents: non-JSON response from {r.url} "
                                f"(content-type {ctype!r}); endpoint likely moved")
                patents = r.json().get("patents") or []
        except Exception as e:  # noqa: BLE001
            return [], f"patents: {e}"
        return [self._parse(p) for p in patents], None

    def _parse(self, p: dict) -> Patent:
        assignees = [a.get("assignee_organization") for a in (p.get("assignees") or [])
                     if a.get("assignee_organization")]
        inventors = [
            " ".join(x for x in [i.get("inventor_name_first"), i.get("inventor_name_last")] if x)
            for i in (p.get("inventors") or [])
        ]
        return Patent(
            patent_id=p.get("patent_id"),
            title=p.get("patent_title"),
            date=p.get("patent_date"),
            assignee_organizations=assignees,
            inventor_names=[n for n in inventors if n],
        )


# ------------------------------------------------------------------- Grants ----
class SBIRClient:
    BASE = "https://api.www.sbir.gov/public/api/awards"

    async def search_by_firm(self, firm: str) -> tuple[list[Grant], str | None]:
        # SBIR.gov throttles aggressively (429) and has maintenance windows; one
        # gentle retry smooths over transient 429s without hammering the service.
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT, headers=_UA) as c:
                for attempt in range(2):
                    r = await c.get(self.BASE, params={"firm": firm, "format": "json"})
                    if r.status_code == 429 and attempt == 0:
                        await asyncio.sleep(2.0)
                        continue
                    break
                r.raise_for_status()
                rows = r.json()
        except Exception as e:  # noqa: BLE001
            return [], f"sbir: {e}"
        rows = rows if isinstance(rows, list) else rows.get("results", [])
        out = []
        for g in rows[:25]:
            out.append(Grant(
                source="SBIR", title=g.get("award_title") or g.get("title"),
                recipient=g.get("firm"), agency=g.get("agency"),
                year=_int(g.get("award_year") or g.get("year")),
                amount=_float(g.get("award_amount")),
            ))
        return out, None


class NIHReporterClient:
    BASE = "https://api.reporter.nih.gov/v2/projects/search"

    async def search_by_pi(self, name: str) -> tuple[list[Grant], str | None]:
        body = {"criteria": {"pi_names": [{"any_name": name}]},
                "limit": 25, "offset": 0}
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT, headers=_UA) as c:
                r = await c.post(self.BASE, json=body)
                r.raise_for_status()
                results = r.json().get("results", [])
        except Exception as e:  # noqa: BLE001
            return [], f"nih: {e}"
        out = []
        for g in results:
            org = (g.get("organization") or {}).get("org_name")
            out.append(Grant(
                source="NIH", title=g.get("project_title"),
                recipient=org, agency=g.get("agency_ic_admin", {}).get("name") if isinstance(g.get("agency_ic_admin"), dict) else "NIH",
                year=_int(g.get("fiscal_year")), amount=_float(g.get("award_amount")),
            ))
        return out, None


# ------------------------------------------------------------------ helpers ----
def _json(obj) -> str:
    import json
    return json.dumps(obj, separators=(",", ":"))


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
