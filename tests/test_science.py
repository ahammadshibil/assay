"""Science-mode grader tests — offline, no network.

The property under test is the same integrity guarantee as the provenance engine,
applied to claims: independent peer-reviewed corroboration is graded REPLICATED;
founders-only is SINGLE_GROUP; preprints-only is PREPRINT_ONLY; a *failed* search
is never UNSUPPORTED; retracted support is a RISK.
"""
import asyncio

import pytest

from assay.models import EvidenceGrade, Work
from assay.science import (
    LiteratureClient, _distinctive_terms, _is_founder_authored, _surnames, verify_claim,
)


def codes(report):
    return {f.code for f in report.flags}


# ------------------------------------------------------------ author match ----
def test_surname_extraction_ignores_initials():
    assert _surnames(["Tapas Kumar Kundu", "S. Chatterjee"]) == {"kundu", "chatterjee"}


def test_founder_authorship_by_surname():
    w = Work(authors=["Akash Kumar Singh", "Tapas K. Kundu"])
    assert _is_founder_authored(w, {"kundu"}) is True
    assert _is_founder_authored(w, {"doudna"}) is False


# ------------------------------------------------------------------ parsing ----
def test_parse_detects_preprint_by_venue():
    w = LiteratureClient()._parse({
        "id": "x", "title": "Oral p300 activator", "publication_year": 2023, "type": "article",
        "primary_location": {"source": {"display_name": "bioRxiv (Cold Spring Harbor Laboratory)"}},
        "cited_by_count": 2, "authorships": [{"author": {"display_name": "A. Singh"}}],
    })
    assert w.is_preprint is True
    assert w.is_primary_article is False  # preprint is not a peer-reviewed primary article


def test_parse_marks_peer_reviewed_article_primary():
    w = LiteratureClient()._parse({
        "id": "y", "title": "A novel activator", "publication_year": 2013, "type": "article",
        "primary_location": {"source": {"display_name": "Journal of Neuroscience"}},
        "cited_by_count": 166, "authorships": [{"author": {"display_name": "S. Chatterjee"}}],
    })
    assert w.is_preprint is False
    assert w.is_primary_article is True


# --------------------------------------------------------------- grading ----
def _verify(works, founders, total=None, error=None):
    """Drive _grade through verify_claim by monkeypatching the literature search."""
    import assay.science as sci

    async def fake_search(self, claim, per_page=25):
        return list(works), (total if total is not None else len(works)), error

    orig = LiteratureClient.search
    LiteratureClient.search = fake_search
    try:
        return asyncio.run(verify_claim("the claim", founders=founders, use_pubmed=False))
    finally:
        LiteratureClient.search = orig


def _article(title, founder_author, cites=10, retracted=False):
    a = "Kundu" if founder_author else "Independent Person"
    return Work(title=title, type="article", is_primary_article=True, cited_by_count=cites,
                is_retracted=retracted, authors=[f"Some {a}"])


def _preprint(title):
    return Work(title=title, type="article", is_preprint=True, venue="bioRxiv", authors=["A B"])


def test_independent_primary_is_replicated():
    r = _verify([_article("indep", founder_author=False), _article("own", founder_author=True)],
                founders=["Tapas Kumar Kundu"])
    assert r.grade is EvidenceGrade.REPLICATED
    assert "independent_replication" in codes(r)


def test_single_independent_paper_flags_thin_replication():
    r = _verify([_article("indep", founder_author=False, cites=2), _article("own", founder_author=True)],
                founders=["Tapas Kumar Kundu"])
    assert r.grade is EvidenceGrade.REPLICATED
    assert "replication_thin" in codes(r)  # one lightly-cited independent paper != robust


def test_multiple_well_cited_independent_is_not_thin():
    works = [_article(f"indep{i}", founder_author=False, cites=50) for i in range(3)]
    r = _verify(works, founders=["Tapas Kumar Kundu"])
    assert r.grade is EvidenceGrade.REPLICATED
    assert "replication_thin" not in codes(r)


def test_review_title_not_counted_as_primary():
    from assay.science import LiteratureClient
    w = LiteratureClient()._parse({
        "id": "z", "title": "The Role of Histone Modifications in Neurogenesis: a Landscape",
        "publication_year": 2026, "type": "article",
        "primary_location": {"source": {"display_name": "Brain and Behavior"}},
        "cited_by_count": 0, "authorships": [{"author": {"display_name": "X Y"}}],
    })
    assert w.is_primary_article is False  # mistyped review must not count as primary evidence


def test_founders_only_is_single_group():
    r = _verify([_article("own1", founder_author=True), _article("own2", founder_author=True)],
                founders=["Tapas Kumar Kundu"])
    assert r.grade is EvidenceGrade.SINGLE_GROUP
    assert "single_group_only" in codes(r)


def test_no_founders_given_is_peer_reviewed_unassessed():
    r = _verify([_article("a", founder_author=False)], founders=[])
    assert r.grade is EvidenceGrade.PEER_REVIEWED
    assert "independence_unassessed" in codes(r)


def test_preprint_only_grade():
    r = _verify([_preprint("oral activator preprint")], founders=["Kundu"])
    assert r.grade is EvidenceGrade.PREPRINT_ONLY
    assert "preprint_only" in codes(r)


def test_no_works_is_unsupported_not_error():
    r = _verify([], founders=["Kundu"])
    assert r.grade is EvidenceGrade.UNSUPPORTED
    assert "no_literature_support" in codes(r)


def test_search_error_is_unverified_not_unsupported():
    r = _verify([], founders=["Kundu"], error="openalex/works: timeout")
    # Integrity: a failed search must flag UNVERIFIED, never silently grade "no evidence".
    assert "literature_source_unavailable" in codes(r)
    assert "no_literature_support" not in codes(r)


def _verify_claim_with(claim, works, total, founders=()):
    """Drive verify_claim for an arbitrary claim string + total hit count."""
    import asyncio

    async def fake_search(self, c, per_page=25):
        return list(works), total, None

    orig = LiteratureClient.search
    LiteratureClient.search = fake_search
    try:
        return asyncio.run(verify_claim(claim, founders=list(founders), use_pubmed=False))
    finally:
        LiteratureClient.search = orig


# --------------------------------------------------- generic-claim guard ----
def test_distinctive_terms_drops_buzzwords():
    # A pure platform claim has no distinctive term left after buzzword removal.
    assert _distinctive_terms("a generative-AI platform designs optimized small molecules") == set()
    assert _distinctive_terms("machine learning reduces wet-lab screening") == set()
    # Specific claims keep their anchors.
    assert "enmetazobactam" in _distinctive_terms("enmetazobactam restores cefepime activity")
    assert "ttk21" in _distinctive_terms("CSP-TTK21 activates p300 acetyltransferase")
    assert "enfncas9" in _distinctive_terms("enFnCas9 high-fidelity CRISPR variant")


def test_pure_platform_claim_is_too_generic():
    # The portfolio failure: a vague platform claim that greenwashed to REPLICATED.
    r = _verify_claim_with("a generative-AI platform designs optimized drug molecules",
                           [_article("unrelated AI review", founder_author=False)], total=589)
    assert r.grade is EvidenceGrade.TOO_GENERIC
    assert "claim_too_generic" in codes(r)


def test_specific_claim_exempt_from_generic_guard():
    r = _verify_claim_with("enmetazobactam restores cefepime activity against ESBL Enterobacterales",
                           [_article("on enmetazobactam", founder_author=False)], total=76)
    assert r.grade is not EvidenceGrade.TOO_GENERIC  # has distinctive terms


def test_retracted_support_is_a_risk():
    r = _verify([_article("retracted finding", founder_author=False, retracted=True)],
                founders=["Kundu"])
    assert "retracted_support" in codes(r)
    sev = next(f.severity.value for f in r.flags if f.code == "retracted_support")
    assert sev == "RISK"
