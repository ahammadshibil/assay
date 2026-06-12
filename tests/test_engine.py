"""Engine flag-rule tests — fully offline, no network.

The most important property under test is *integrity*: a source that FAILED must
never produce the same flag as a source that succeeded and found nothing. In a
diligence tool, "couldn't check" and "checked, clean" must be distinguishable.
"""
from assay.engine import (
    _SourceErrors,
    _evaluate,
    _grant_flags,
    _patent_flags,
    _research_flags,
)
from assay.models import Author, Grant, Patent, ProvenanceReport, Severity


def codes(flags):
    return {f.code for f in flags}


def by_code(flags, code):
    return next(f for f in flags if f.code == code)


# ---------------------------------------------------------------- integrity ----
class TestSourceFailureIsNotAbsence:
    """The headline correctness guarantee."""

    def test_patent_source_error_is_not_no_patents(self):
        flags = _patent_flags("Jane", "Acme", [], error="patents: getaddrinfo failed")
        assert codes(flags) == {"patent_source_unavailable"}
        # must not assert clean IP in any form
        assert "no_patents_found" not in codes(flags)
        assert "ip_owned_by_company" not in codes(flags)
        assert by_code(flags, "patent_source_unavailable").severity is Severity.WATCH

    def test_patent_empty_without_error_is_a_real_finding(self):
        flags = _patent_flags("Jane", "Acme", [])
        assert codes(flags) == {"no_patents_found"}

    def test_research_source_error_is_not_no_record(self):
        flags = _research_flags(None, error="openalex: timeout")
        assert codes(flags) == {"research_source_unavailable"}
        assert "no_research_record" not in codes(flags)

    def test_research_none_without_error_is_a_real_finding(self):
        flags = _research_flags(None)
        assert codes(flags) == {"no_research_record"}

    def test_both_grant_sources_down_is_unverified(self):
        flags = _grant_flags("Acme", [], _SourceErrors(sbir="429", nih="500"))
        assert codes(flags) == {"grant_sources_unavailable"}

    def test_partial_grant_coverage_is_noted_but_not_unverified(self):
        flags = _grant_flags("Acme", [], _SourceErrors(sbir="429", nih=None))
        assert codes(flags) == {"no_grants_found"}
        f = by_code(flags, "no_grants_found")
        assert "partial" in f.message.lower()
        assert f.evidence.get("partial_source_down") == "SBIR"


# ------------------------------------------------------------------ IP rules ----
class TestPatentOwnership:
    def test_institutional_assignee_only_is_a_RISK(self):
        p = Patent(patent_id="1", assignee_organizations=["CSIR-IGIB"])
        flags = _patent_flags("Jane", "Acme Bio", [p])
        risk = by_code(flags, "ip_owned_by_institution")
        assert risk.severity is Severity.RISK
        assert "CSIR-IGIB" in risk.evidence["institutional_assignees"]

    def test_company_owned_is_info(self):
        p = Patent(patent_id="1", assignee_organizations=["Acme Bio Inc"])
        flags = _patent_flags("Jane", "Acme Bio", [p])
        assert "ip_owned_by_company" in codes(flags)
        assert "ip_owned_by_institution" not in codes(flags)

    def test_mixed_ownership_is_a_watch_not_a_risk(self):
        p1 = Patent(patent_id="1", assignee_organizations=["Acme Bio Inc"])
        p2 = Patent(patent_id="2", assignee_organizations=["Stanford University"])
        flags = _patent_flags("Jane", "Acme Bio", [p1, p2])
        assert "mixed_ip_ownership" in codes(flags)
        assert "ip_owned_by_institution" not in codes(flags)

    def test_company_named_inside_institution_is_not_double_counted(self):
        # An assignee that contains the company name should count as company-owned,
        # not institutional, even if it also contains an institutional token.
        p = Patent(patent_id="1", assignee_organizations=["Acme Bio Research Institute"])
        flags = _patent_flags("Jane", "Acme Bio", [p])
        assert "ip_owned_by_company" in codes(flags)


# ----------------------------------------------------------------- research ----
class TestResearchFlags:
    def test_stale_research_flagged(self):
        a = Author(display_name="X", works_count=10, last_publication_year=2010, h_index=5)
        flags = _research_flags(a)
        assert "stale_research" in codes(flags)

    def test_recent_prolific_author_not_stale(self):
        a = Author(display_name="X", works_count=100, last_publication_year=2026, h_index=40)
        flags = _research_flags(a)
        assert "stale_research" not in codes(flags)
        assert "research_profile" in codes(flags)


# ------------------------------------------------------------------- grants ----
class TestGrantFlags:
    def test_grant_to_company_is_validation(self):
        g = Grant(source="SBIR", recipient="Acme Bio Inc", amount=1_000_000)
        flags = _grant_flags("Acme Bio", [g])
        assert "non_dilutive_validation" in codes(flags)

    def test_grant_to_other_entity_is_a_watch(self):
        g = Grant(source="NIH", recipient="Stanford University", amount=500_000)
        flags = _grant_flags("Acme Bio", [g])
        assert "grants_to_other_entity" in codes(flags)


# ----------------------------------------------------------------- ordering ----
def test_flags_sorted_risk_first():
    r = ProvenanceReport(founder="Jane", company="Acme Bio")
    r.author = Author(display_name="Jane", works_count=10, h_index=5, last_publication_year=2026)
    r.patents = [Patent(patent_id="1", assignee_organizations=["CSIR-IGIB"])]
    r.grants = []
    from assay.engine import _sort_flags
    flags = _sort_flags(_evaluate(r, _SourceErrors()))
    severities = [f.severity for f in flags]
    assert severities == sorted(severities, key=lambda s: {Severity.RISK: 0, Severity.WATCH: 1, Severity.INFO: 2}[s])
    assert flags[0].code == "ip_owned_by_institution"
