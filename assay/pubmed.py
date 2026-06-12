"""PubMed retrieval via NCBI E-utilities (keyless).

A second literature source behind science mode. OpenAlex has broad coverage and
citation counts; PubMed adds biomedical recall and — crucially — *authoritative*
publication-type metadata, so reviews and retractions are read from the source
instead of guessed from the title. Fails soft like every other client: a throttle
or outage contributes nothing, it never breaks the run.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET

import httpx

from .models import Work

_TIMEOUT = httpx.Timeout(30.0)
_UA = {"User-Agent": "assay-provenance/0.1 (+https://github.com/ahammadshibil/assay)"}
_EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"


class PubMedClient:
    def __init__(self, email: str | None = None) -> None:
        self.email = email

    def _common(self) -> dict:
        p = {"db": "pubmed"}
        if self.email:
            p["email"] = self.email
            p["tool"] = "assay"
        return p

    async def search(self, query: str, retmax: int = 20) -> tuple[list[Work], str | None]:
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT, headers=_UA) as c:
                es = await c.get(f"{_EUTILS}/esearch.fcgi", params={
                    **self._common(), "term": query, "retmax": retmax,
                    "retmode": "json", "sort": "relevance"})
                es.raise_for_status()
                ids = ((es.json().get("esearchresult") or {}).get("idlist")) or []
                if not ids:
                    return [], None
                ef = await c.get(f"{_EUTILS}/efetch.fcgi", params={
                    **self._common(), "id": ",".join(ids),
                    "retmode": "xml", "rettype": "abstract"})
                ef.raise_for_status()
                xml = ef.text
        except Exception as e:  # noqa: BLE001 — fail soft by design
            return [], f"pubmed: {e}"
        return parse_pubmed_xml(xml), None


def parse_pubmed_xml(xml: str) -> list[Work]:
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return []
    out: list[Work] = []
    for art in root.findall(".//PubmedArticle"):
        a = art.find(".//Article")
        if a is None:
            continue
        title = _text(a.find("ArticleTitle"))
        journal = _text(a.find(".//Journal/Title"))
        year = _year(a)
        authors = []
        for au in a.findall(".//AuthorList/Author"):
            last, fore = _text(au.find("LastName")), _text(au.find("ForeName"))
            name = " ".join(x for x in (fore, last) if x)
            if name:
                authors.append(name)
        abstract = " ".join(_text(t) or "" for t in a.findall(".//Abstract/AbstractText")).strip() or None
        ptypes = {(_text(p) or "").lower() for p in a.findall(".//PublicationTypeList/PublicationType")}
        is_review = any("review" in p for p in ptypes)
        is_retracted = "retracted publication" in ptypes
        doi = None
        for aid in art.findall(".//ArticleIdList/ArticleId"):
            if aid.get("IdType") == "doi":
                doi = (aid.text or "").strip() or None
        out.append(Work(
            id=(doi or title),
            title=title,
            year=year,
            venue=journal,
            type="review" if is_review else "article",
            cited_by_count=0,  # PubMed doesn't expose citation counts
            authors=authors,
            is_preprint=False,
            is_retracted=is_retracted,
            # Primary = an original journal article that is neither a review nor retracted.
            is_primary_article=("journal article" in ptypes) and not is_review and not is_retracted,
            abstract=abstract,
            doi=doi,
            url=(f"https://doi.org/{doi}" if doi else None),
        ))
    return out


def _text(el) -> str | None:
    if el is None:
        return None
    return "".join(el.itertext()).strip() or None


def _year(article) -> int | None:
    y = article.find(".//Journal/JournalIssue/PubDate/Year")
    if y is not None and (y.text or "").isdigit():
        return int(y.text)
    md = _text(article.find(".//Journal/JournalIssue/PubDate/MedlineDate")) or ""
    for tok in md.split():
        if tok[:4].isdigit():
            return int(tok[:4])
    return None
