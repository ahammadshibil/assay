"""Science mode: confirm a *claim*, not a founder.

Given a technical assertion (ideally a specific compound/mechanism, e.g.
"CSP-TTK21 activates p300/CBP and crosses the blood-brain barrier"), pull the
primary literature and grade the evidence. The headline signal is **independent
corroboration** — has a group *other than the founders* reproduced it in a
peer-reviewed primary article? That is what separates real science from one
lab's hopeful story.

Grades (see models.EvidenceGrade):
  REPLICATED     peer-reviewed primary article from an independent group
  SINGLE_GROUP   peer-reviewed, but only the founders' own group
  PEER_REVIEWED  peer-reviewed primary exists; independence not assessed (no founders given)
  PREPRINT_ONLY  only preprints support it
  UNSUPPORTED    no literature found for the claim's terms
  CONTRADICTED   the supporting work is retracted

Integrity rule (inherited from the provenance engine): a literature search that
*failed* must never grade as UNSUPPORTED. "Couldn't check" ≠ "no evidence".

Honest scope: the deterministic grader describes the *evidence landscape around
the claim's terms* (keyword + relevance match). Whether a given paper truly
*supports vs refutes* the claim is a reading task — left to the analyst or to the
optional LLM adjudication in synthesize.synthesize_science.
"""
from __future__ import annotations

import re

import httpx

from .models import ClaimReport, EvidenceGrade, Flag, Severity, Work

# Words too common to make a claim *verifiable* — buzzwords + method words that
# appear across the whole AI/drug-discovery literature. A claim built only from
# these ("a generative-AI platform designs optimized molecules") has nothing
# specific to confirm; it can only match the field, not the company.
_GENERIC_TERMS = frozenset("""
ai ml artificial intelligence machine learning deep generative model models algorithm algorithms
design designs designed optimize optimized optimizing optimization platform platforms technology
technologies system systems approach approaches method methods framework pipeline workflow novel
data dataset datasets candidate candidates drug drugs molecule molecules molecular compound compounds
small large biologic biologics therapeutic therapeutics therapy therapies treatment de novo validated
validation experimentally experimental silico vitro vivo precision multi omics using based via predict
prediction predictive discovery development scalable proprietary leverages enables improves improving
reduces reduce reducing accelerate accelerating need wet lab screening high throughput
""".split())

_STOP = frozenset("""
a an the is are was were be of for and or that with to in on at by as from against into their its our
this these those it they we you can will more most than then so such across over under between within
""".split())


def _distinctive_terms(text: str) -> set[str]:
    """Content terms specific enough to anchor verification — drops buzzwords and
    stopwords, splits hyphens, keeps real nouns (a compound, gene, mechanism)."""
    terms: set[str] = set()
    for tok in re.findall(r"[A-Za-z0-9\-]{3,}", text.lower()):
        for part in tok.split("-"):
            if len(part) >= 4 and part not in _GENERIC_TERMS and part not in _STOP:
                terms.add(part)
    return terms

_TIMEOUT = httpx.Timeout(30.0)
_UA = {
    "User-Agent": "assay-provenance/0.1 (+https://github.com/ahammadshibil/assay)",
    "Accept": "application/json",
}

# Venues / source types that mark a work as a not-yet-peer-reviewed preprint.
_PREPRINT_VENUES = (
    "biorxiv", "medrxiv", "arxiv", "chemrxiv", "research square",
    "preprints.org", "ssrn", "osf", "authorea",
)
# OpenAlex `type` values that are original research (vs review / editorial / etc.).
_PRIMARY_TYPES = ("article",)
# OpenAlex mistypes many reviews as "article". A review that *cites* a mechanism
# is not *replication* of it, so these must not count toward independent primary
# evidence. Heuristic title/venue markers catch the common cases the type field
# misses. Imperfect by nature — the independent-replication flag says so.
_REVIEW_TITLE_MARKERS = (
    "review", "perspective", "overview", "a survey", "advances in", "insights into",
    "current understanding", "state of the art", "landscape", "role of",
    "what we know", "meta-analysis", "an update", "emerging roles", "recent advances",
    "and beyond", "strategies in", "approaches to", "opportunities and challenges",
)
_REVIEW_VENUE_MARKERS = ("annual review", "trends in", "nature reviews", "wiley interdisciplinary")


def _looks_like_review(title: str | None, venue: str | None) -> bool:
    t = (title or "").lower()
    v = (venue or "").lower()
    return (any(m in t for m in _REVIEW_TITLE_MARKERS)
            or any(m in v for m in _REVIEW_VENUE_MARKERS))


def reconstruct_abstract(inverted_index: dict | None, max_words: int = 320) -> str | None:
    """OpenAlex stores abstracts as {word: [positions]}; rebuild the prose so the
    adjudicator can reason over real content, not just the title."""
    if not inverted_index:
        return None
    positioned: list[tuple[int, str]] = []
    for word, positions in inverted_index.items():
        for p in positions:
            positioned.append((p, word))
    if not positioned:
        return None
    positioned.sort(key=lambda x: x[0])
    words = [w for _, w in positioned[:max_words]]
    return " ".join(words)


# ------------------------------------------------------------- literature ----
class LiteratureClient:
    """OpenAlex /works — keyless, returns citations, author sets, venue, type,
    and retraction status. The backbone source for claim verification."""

    BASE = "https://api.openalex.org/works"

    def __init__(self, mailto: str | None = None) -> None:
        self.mailto = mailto

    async def search(self, claim: str, per_page: int = 25) -> tuple[list[Work], int, str | None]:
        params = {"search": claim, "per-page": per_page, "sort": "relevance_score:desc"}
        if self.mailto:
            params["mailto"] = self.mailto
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT, headers=_UA,
                                         follow_redirects=True) as c:
                r = await c.get(self.BASE, params=params)
                r.raise_for_status()
                j = r.json()
        except Exception as e:  # noqa: BLE001 — fail soft by design
            return [], 0, f"openalex/works: {e}"
        total = (j.get("meta") or {}).get("count", 0)
        return [self._parse(w) for w in j.get("results", [])], total, None

    def _parse(self, w: dict) -> Work:
        loc = (w.get("primary_location") or {})
        src = (loc.get("source") or {})
        venue = src.get("display_name")
        wtype = w.get("type")
        venue_l = (venue or "").lower()
        is_preprint = (
            wtype == "preprint"
            or (src.get("type") == "repository")
            or any(v in venue_l for v in _PREPRINT_VENUES)
        )
        authors = [
            (a.get("author") or {}).get("display_name")
            for a in (w.get("authorships") or [])
        ]
        title = w.get("title")
        is_review = (wtype == "review") or _looks_like_review(title, venue)
        return Work(
            id=w.get("id"),
            title=title,
            year=w.get("publication_year"),
            venue=venue,
            type=wtype,
            cited_by_count=w.get("cited_by_count") or 0,
            authors=[a for a in authors if a],
            is_preprint=is_preprint,
            is_retracted=bool(w.get("is_retracted")),
            # Primary = original experimental research: a peer-reviewed article that
            # is neither a preprint nor a review.
            is_primary_article=(wtype in _PRIMARY_TYPES) and not is_preprint and not is_review,
            abstract=reconstruct_abstract(w.get("abstract_inverted_index")),
            doi=w.get("doi"),
            url=(loc.get("landing_page_url") or w.get("doi")),
        )


# --------------------------------------------------------------- matching ----
def _surnames(names: list[str]) -> set[str]:
    out = set()
    for n in names or []:
        toks = [t.strip(".,").lower() for t in n.split() if len(t.strip(".,")) >= 4]
        if toks:
            out.add(toks[-1])  # last substantive token = surname
    return out


def _is_founder_authored(work: Work, founder_surnames: set[str]) -> bool:
    auth_surnames = _surnames(work.authors)
    return bool(founder_surnames & auth_surnames)




# ----------------------------------------------------------------- engine ----
async def verify_claim(
    claim: str,
    founders: list[str] | None = None,
    mailto: str | None = None,
    max_works: int = 25,
) -> ClaimReport:
    """Verify a scientific claim against the primary literature and grade it."""
    founders = founders or []
    report = ClaimReport(claim=claim, founders=founders)

    works, total, err = await LiteratureClient(mailto=mailto).search(claim, per_page=max_works)
    report.total_hits = total
    if err:
        report.source_errors.append(err)

    # Annotate founder authorship if we know whose claim it is.
    fsur = _surnames(founders)
    if fsur:
        for w in works:
            w.founder_authored = _is_founder_authored(w, fsur)

    report.works = works
    report.preprint_works = [w for w in works if w.is_preprint]
    report.retracted_works = [w for w in works if w.is_retracted]
    primary = [w for w in works if w.is_primary_article and not w.is_retracted]
    if fsur:
        report.founder_works = [w for w in primary if w.founder_authored]
        report.independent_works = [w for w in primary if not w.founder_authored]

    report.grade, report.flags = _grade(report, primary, bool(fsur), err)
    return report


def _grade(report: ClaimReport, primary: list[Work], have_founders: bool,
           error: str | None) -> tuple[EvidenceGrade, list[Flag]]:
    flags: list[Flag] = []

    # Integrity: a failed search is UNKNOWN, never UNSUPPORTED.
    if error is not None and not report.works:
        flags.append(Flag("literature_source_unavailable", Severity.WATCH,
                          "Could not query the literature — evidence is UNVERIFIED, not "
                          "absent. Re-run before concluding the claim is unsupported.",
                          {"error": error}))
        return EvidenceGrade.UNSUPPORTED, flags  # grade is nominal; the flag is the truth

    # Retracted supporting work is a hard signal regardless of everything else.
    if report.retracted_works:
        flags.append(Flag("retracted_support", Severity.RISK,
                          f"{len(report.retracted_works)} supporting work(s) are RETRACTED. "
                          "Treat the underlying claim as compromised until reconciled.",
                          {"titles": [w.title for w in report.retracted_works]}))

    if not report.works:
        flags.append(Flag("no_literature_support", Severity.WATCH,
                          "No literature found for the claim's terms. Either the wording is "
                          "off, or the science is unpublished/novel — confirm which.",
                          {"query": report.claim}))
        return EvidenceGrade.UNSUPPORTED, flags

    # Generic-claim guard: a claim built only from buzzwords ("a generative-AI
    # platform designs optimized molecules") has nothing specific to verify, so it
    # greenwashes to REPLICATED off unrelated AI papers. With no distinctive term,
    # it confirms the field exists, not the company's claim. (Subtler topical
    # mismatches — a real term but off-topic papers — need --synthesize to catch.)
    if not _distinctive_terms(report.claim):
        flags.append(Flag("claim_too_generic", Severity.WATCH,
                          "Claim has no specific, verifiable term — built from buzzwords; it "
                          "matches the field, not the company. Re-run with a named compound / "
                          "target / mechanism.",
                          {"matched_works": report.total_hits}))
        flags.append(_evidence_summary(report, primary))
        return EvidenceGrade.TOO_GENERIC, flags

    # Determine grade from the evidence landscape.
    if report.retracted_works and not primary:
        grade = EvidenceGrade.CONTRADICTED
    elif primary and have_founders and report.independent_works:
        grade = EvidenceGrade.REPLICATED
    elif primary and have_founders:  # peer-reviewed primary, but all from the founders
        grade = EvidenceGrade.SINGLE_GROUP
    elif primary:  # peer-reviewed primary exists, independence not assessed
        grade = EvidenceGrade.PEER_REVIEWED
    elif report.preprint_works:
        grade = EvidenceGrade.PREPRINT_ONLY
    else:
        grade = EvidenceGrade.UNSUPPORTED

    flags += _grade_flags(report, grade)
    flags.append(_evidence_summary(report, primary))
    return grade, flags


def _grade_flags(report: ClaimReport, grade: EvidenceGrade) -> list[Flag]:
    out: list[Flag] = []
    if grade is EvidenceGrade.REPLICATED:
        top = sorted(report.independent_works, key=lambda w: w.cited_by_count, reverse=True)[:3]
        out.append(Flag("independent_replication", Severity.INFO,
                        f"Independently corroborated: {len(report.independent_works)} peer-reviewed "
                        "primary article(s) from group(s) other than the founders. Strongest "
                        "preclinical-evidence signal — but eyeball the titles: review/secondary "
                        "papers can't be perfectly excluded from the OpenAlex type field.",
                        {"papers": [f"{w.title} ({w.venue}, {w.year}, {w.cited_by_count} cites)" for w in top]}))
        # Don't let one recent, lightly-cited paper masquerade as robust replication.
        strongest = max((w.cited_by_count for w in report.independent_works), default=0)
        if len(report.independent_works) == 1 or strongest < 5:
            out.append(Flag("replication_thin", Severity.WATCH,
                            "Independent corroboration is thin — it rests on a single and/or "
                            "lightly-cited paper. Closer to early corroboration than robust, "
                            "multi-group replication. Confirm by reading it."))
    elif grade is EvidenceGrade.SINGLE_GROUP:
        out.append(Flag("single_group_only", Severity.WATCH,
                        "Peer-reviewed, but every supporting primary article is from the founders' "
                        "own group. No independent replication found — a real risk for a claim the "
                        "thesis depends on.",
                        {"founder_papers": [w.title for w in report.founder_works[:3]]}))
    elif grade is EvidenceGrade.PEER_REVIEWED:
        out.append(Flag("independence_unassessed", Severity.WATCH,
                        "Peer-reviewed primary literature exists, but independence was not assessed "
                        "— pass --founders to check whether anyone outside the founding group has "
                        "reproduced it."))
    elif grade is EvidenceGrade.PREPRINT_ONLY:
        out.append(Flag("preprint_only", Severity.WATCH,
                        f"Only preprints support this ({len(report.preprint_works)} found) — not yet "
                        "peer-reviewed. Promising but unvetted; track the journal version.",
                        {"preprints": [f"{w.title} ({w.venue}, {w.year})" for w in report.preprint_works[:3]]}))
    return out


def _evidence_summary(report: ClaimReport, primary: list[Work]) -> Flag:
    years = [w.year for w in report.works if w.year]
    top = max(report.works, key=lambda w: w.cited_by_count, default=None)
    return Flag("evidence_summary", Severity.INFO,
                f"{report.total_hits} works match the claim; "
                f"{len(primary)} peer-reviewed primary, {len(report.preprint_works)} preprint(s)"
                f"{f', spanning {min(years)}–{max(years)}' if years else ''}."
                + (f" Most-cited: '{top.title}' ({top.venue}, {top.year}, {top.cited_by_count} cites)."
                   if top else ""),
                {"top_works": [f"{w.title} ({w.venue}, {w.year}, {w.cited_by_count}c)"
                               for w in sorted(report.works, key=lambda w: w.cited_by_count, reverse=True)[:5]]})
