"""Tests for the L3/L5 template renderers — offline, no network/LLM.

Verifies a claim lands in the right ✅/⚠️/❌ bucket from its grade/flags/stance,
that the rendered §10 table matches Shibil's headings, and that the provenance
IP renderer surfaces the right concern (incl. the integrity case).
"""
from assay.models import (
    Author, ClaimReport, EvidenceGrade, Flag, Patent, ProvenanceReport, Severity, Work,
)
from assay.render import (
    bucket_for_claim, render_claim_verification, render_ip_portfolio,
    render_key_publications, render_research_lineage,
)


def _claim(grade, claim="C", flags=(), works=(), independent=(), adjudications=()):
    r = ClaimReport(claim=claim, grade=grade)
    r.flags = [Flag(c, Severity.WATCH, c) for c in flags]
    r.works = list(works)
    r.independent_works = list(independent)
    r.adjudications = list(adjudications)
    return r


# ----------------------------------------------------------------- buckets ----
def test_replicated_checks_out():
    assert bucket_for_claim(_claim(EvidenceGrade.REPLICATED)) == "checks_out"


def test_thin_replication_is_inflated():
    assert bucket_for_claim(_claim(EvidenceGrade.REPLICATED, flags=["replication_thin"])) == "inflated"


def test_single_group_is_inflated():
    assert bucket_for_claim(_claim(EvidenceGrade.SINGLE_GROUP)) == "inflated"


def test_unsupported_is_unverified():
    assert bucket_for_claim(_claim(EvidenceGrade.UNSUPPORTED)) == "unverified"


def test_source_error_is_unverified_not_checks_out():
    # Integrity carries into rendering: a failed search can't read as a clean claim.
    r = _claim(EvidenceGrade.UNSUPPORTED, flags=["literature_source_unavailable"])
    assert bucket_for_claim(r) == "unverified"


def test_adjudicated_stance_overrides_structural_grade():
    # Structural grade says REPLICATED, but the adversarial read says keyword-only.
    adj = [{"verdict": "IRRELEVANT", "founder_authored": False}]
    r = _claim(EvidenceGrade.REPLICATED, adjudications=adj)
    assert bucket_for_claim(r) == "unverified"


def test_adjudicated_founders_only_is_inflated():
    adj = [{"verdict": "SUPPORTS", "founder_authored": True}]
    r = _claim(EvidenceGrade.REPLICATED, adjudications=adj)
    assert bucket_for_claim(r) == "inflated"


# ------------------------------------------------------------- §10 render ----
def test_claim_verification_sorts_into_three_buckets():
    reports = [
        _claim(EvidenceGrade.REPLICATED, claim="A works"),
        _claim(EvidenceGrade.SINGLE_GROUP, claim="B is founders-only"),
        _claim(EvidenceGrade.UNSUPPORTED, claim="C unfounded"),
    ]
    md = render_claim_verification(reports)
    assert "## 10. CLAIM VERIFICATION" in md
    assert "Claims That Check Out ✅" in md
    assert "Claims That Are Inflated or Misleading ⚠️" in md
    assert "Claims That Could Not Be Verified ❌" in md
    # each claim shows up under its bucket
    assert "A works" in md and "B is founders-only" in md and "C unfounded" in md


def test_key_publications_dedupes_and_ranks():
    w1 = Work(id="1", title="Big", venue="Nature", year=2024, cited_by_count=100)
    w2 = Work(id="2", title="Small", venue="J", year=2023, cited_by_count=5, is_preprint=True)
    r1 = _claim(EvidenceGrade.REPLICATED, works=[w1, w2])
    r2 = _claim(EvidenceGrade.PEER_REVIEWED, works=[w1])  # dup w1
    md = render_key_publications([r1, r2])
    assert md.count("Big") == 1                      # de-duped
    assert md.index("Big") < md.index("Small")       # ranked by citations
    assert "preprint" in md                          # tag rendered


# --------------------------------------------------- provenance IP render ----
def test_ip_portfolio_flags_institutional_ownership():
    r = ProvenanceReport(founder="J", company="Acme")
    r.patents = [Patent(patent_id="1", assignee_organizations=["CSIR-IGIB"])]
    r.flags = [Flag("ip_owned_by_institution", Severity.RISK, "x")]
    md = render_ip_portfolio(r)
    assert "## 8. IP PORTFOLIO" in md
    assert "field-of-use" in md.lower()
    assert "Gate" in md  # assessment line


def test_ip_portfolio_unavailable_is_not_clean():
    r = ProvenanceReport(founder="J", company="Acme")
    r.flags = [Flag("patent_source_unavailable", Severity.WATCH, "x")]
    md = render_ip_portfolio(r)
    assert "UNVERIFIED" in md
    assert "not clean" in md.lower()


def test_research_lineage_flags_unmatched_institution():
    r = ProvenanceReport(founder="Sundaram Acharya", company="Acme")
    r.author = Author(display_name="Sundaram Acharya", works_count=23, cited_by_count=510,
                      h_index=8, last_institution=None, match_confidence="likely")
    md = render_research_lineage(r)
    assert "h-index 8" in md
    assert "verify this is the right person" in md.lower()
