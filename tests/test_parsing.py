"""Parser tests against fixtures shaped like the real API responses observed on
2026-06-11 (OpenAlex, NIH RePORTER) plus the documented PatentsView shape.

These pin the field-paths the clients depend on, so future API drift fails loudly
in CI rather than silently producing empty records.
"""
from assay.sources import OpenAlexClient, NIHReporterClient, PatentClient, SBIRClient


# Trimmed from a real OpenAlex /authors result for Jennifer Doudna (2026-06-11).
OPENALEX_AUTHOR = {
    "id": "https://openalex.org/A5067184382",
    "display_name": "Jennifer A. Doudna",
    "works_count": 667,
    "cited_by_count": 118710,
    "summary_stats": {"h_index": 150},
    "last_known_institutions": [
        {"display_name": "University of California, Berkeley", "country_code": "US"}
    ],
    "topics": [{"display_name": "CRISPR and Genetic Engineering"}],
}


def test_openalex_parse_author_fields():
    a = OpenAlexClient()._parse_author(OPENALEX_AUTHOR, institution="University of California, Berkeley")
    assert a.display_name == "Jennifer A. Doudna"
    assert a.works_count == 667
    assert a.h_index == 150
    assert a.last_institution == "University of California, Berkeley"
    assert a.last_country == "US"
    assert a.top_topics == ["CRISPR and Genetic Engineering"]
    # institution matches the last-known institution -> strong confidence
    assert a.match_confidence == "strong"


def test_openalex_low_works_is_unverified():
    a = OpenAlexClient()._parse_author(
        {"id": "x", "display_name": "Common Name", "works_count": 1}, institution=None
    )
    assert a.match_confidence == "unverified"


def test_openalex_disambiguate_prefers_institution_match():
    results = [
        {"display_name": "J. Doudna", "last_known_institutions": [{"display_name": "Harvard"}]},
        {"display_name": "Jennifer Doudna", "last_known_institutions": [{"display_name": "UC Berkeley"}]},
    ]
    chosen = OpenAlexClient()._disambiguate(results, institution="Berkeley")
    assert chosen["display_name"] == "Jennifer Doudna"


def test_openalex_disambiguate_falls_back_to_first():
    results = [{"display_name": "First"}, {"display_name": "Second"}]
    assert OpenAlexClient()._disambiguate(results, institution=None)["display_name"] == "First"


# Documented PatentsView PatentSearch shape (nested inventors/assignees).
PATENT = {
    "patent_id": "10000000",
    "patent_title": "A method",
    "patent_date": "2020-01-01",
    "inventors": [{"inventor_name_first": "Jennifer", "inventor_name_last": "Doudna"}],
    "assignees": [{"assignee_organization": "The Regents of the University of California"}],
}


def test_patent_parse_nested():
    p = PatentClient()._parse(PATENT)
    assert p.patent_id == "10000000"
    assert p.assignee_organizations == ["The Regents of the University of California"]
    assert p.inventor_names == ["Jennifer Doudna"]


def test_patent_base_override_via_env(monkeypatch):
    monkeypatch.setenv("ASSAY_PATENT_BASE", "https://example.test/patent/")
    assert PatentClient().base == "https://example.test/patent/"


def test_sbir_and_nih_construct():
    # smoke: clients instantiate without network
    assert SBIRClient().BASE.startswith("https://")
    assert NIHReporterClient().BASE.startswith("https://")
