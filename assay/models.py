"""Core data structures for a deeptech founder/company provenance check.

Everything the engine produces is a plain dataclass so it serialises cleanly
to JSON and is trivial to feed into an LLM, a CRM (Airtable), or a report.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Optional


class Severity(str, Enum):
    INFO = "INFO"      # neutral signal worth recording
    WATCH = "WATCH"    # soft concern, verify manually
    RISK = "RISK"      # hard red flag that should block or gate the deal


@dataclass
class Flag:
    code: str
    severity: Severity
    message: str
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass
class Author:
    """An OpenAlex author record."""
    openalex_id: Optional[str] = None
    display_name: Optional[str] = None
    works_count: int = 0
    cited_by_count: int = 0
    h_index: Optional[int] = None
    last_institution: Optional[str] = None
    last_country: Optional[str] = None
    last_publication_year: Optional[int] = None
    top_topics: list[str] = field(default_factory=list)
    match_confidence: str = "unverified"  # unverified | likely | strong


@dataclass
class Patent:
    patent_id: Optional[str] = None
    title: Optional[str] = None
    date: Optional[str] = None
    assignee_organizations: list[str] = field(default_factory=list)
    inventor_names: list[str] = field(default_factory=list)


@dataclass
class Grant:
    source: str = ""           # SBIR | NIH
    title: Optional[str] = None
    recipient: Optional[str] = None
    agency: Optional[str] = None
    year: Optional[int] = None
    amount: Optional[float] = None


@dataclass
class ProvenanceReport:
    founder: str
    company: str
    institution: Optional[str] = None
    author: Optional[Author] = None
    patents: list[Patent] = field(default_factory=list)
    grants: list[Grant] = field(default_factory=list)
    vertical: Optional[str] = None
    vertical_findings: dict[str, Any] = field(default_factory=dict)
    flags: list[Flag] = field(default_factory=list)
    source_errors: list[str] = field(default_factory=list)
    verdict: Optional[str] = None  # filled by the optional LLM synthesiser

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# =============================================================================
# Science mode — confirm the *claim*, not the founder.
# The unit of analysis is a scientific assertion; the evidence is the primary
# literature; the headline signal is whether an *independent* group has
# corroborated it (not just the founders' own lab).
# =============================================================================


class EvidenceGrade(str, Enum):
    REPLICATED = "REPLICATED"        # peer-reviewed primary article from an independent group
    SINGLE_GROUP = "SINGLE_GROUP"    # peer-reviewed, but only the founders' own group
    PEER_REVIEWED = "PEER_REVIEWED"  # peer-reviewed primary exists; independence not assessed
    PREPRINT_ONLY = "PREPRINT_ONLY"  # only preprints support it (not yet peer-reviewed)
    UNSUPPORTED = "UNSUPPORTED"      # no literature found for the claim's terms
    CONTRADICTED = "CONTRADICTED"    # the supporting work is retracted (or refuted)
    TOO_GENERIC = "TOO_GENERIC"      # claim is so broad it matches the field, not the company


@dataclass
class Work:
    """A single piece of literature (paper / preprint / review)."""
    id: Optional[str] = None
    title: Optional[str] = None
    year: Optional[int] = None
    venue: Optional[str] = None
    type: Optional[str] = None            # article | preprint | review | ...
    cited_by_count: int = 0
    authors: list[str] = field(default_factory=list)
    is_preprint: bool = False
    is_retracted: bool = False
    is_primary_article: bool = False      # an original research article (not a review)
    founder_authored: Optional[bool] = None  # None = independence not assessed
    abstract: Optional[str] = None        # reconstructed; used by the LLM adjudicator
    doi: Optional[str] = None
    url: Optional[str] = None


@dataclass
class ClaimReport:
    claim: str
    founders: list[str] = field(default_factory=list)   # whose claim it is (for independence)
    grade: EvidenceGrade = EvidenceGrade.UNSUPPORTED
    total_hits: int = 0                                 # OpenAlex's total count for the query
    works: list[Work] = field(default_factory=list)     # top supporting candidates
    independent_works: list[Work] = field(default_factory=list)
    founder_works: list[Work] = field(default_factory=list)
    preprint_works: list[Work] = field(default_factory=list)
    retracted_works: list[Work] = field(default_factory=list)
    flags: list[Flag] = field(default_factory=list)
    source_errors: list[str] = field(default_factory=list)
    verdict: Optional[str] = None  # optional LLM adjudication of actual support/contradiction
    adjudications: list[dict[str, Any]] = field(default_factory=list)  # per-paper support verdicts

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
