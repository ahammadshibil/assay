"""Assay — provenance diligence + scientific-claim verification for deeptech."""
from .engine import run_check
from .models import (
    Author, ClaimReport, EvidenceGrade, Flag, Grant, Patent,
    ProvenanceReport, Severity, Work,
)
from .render import (
    render_claim_verification, render_key_publications,
    render_provenance_l3, bucket_for_claim,
)
from .science import verify_claim
from .synthesize import synthesize, synthesize_science

__version__ = "0.1.0"
__all__ = [
    "run_check", "synthesize",
    "verify_claim", "synthesize_science",
    "render_claim_verification", "render_key_publications",
    "render_provenance_l3", "bucket_for_claim",
    "ProvenanceReport", "Flag", "Severity", "Author", "Patent", "Grant",
    "ClaimReport", "EvidenceGrade", "Work",
]
