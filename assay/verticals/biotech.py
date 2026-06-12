"""Biotech vertical module.

Adds two biotech-specific signal sources on top of the core provenance check:

  - ClinicalTrials.gov v2 (free, keyless): does the company actually sponsor
    registered trials, and at what phase/status? A company positioning itself as
    clinical-stage with zero registered trials is a real red flag.
  - openFDA drugsfda (free, keyless): any FDA-approved products under the
    company name? Rare at early stage, strong signal when present.

As with the core sources, these were not exercised live from the build sandbox
(restricted egress). Endpoint shapes drift — the parsers fail soft.
"""
from __future__ import annotations

import httpx

from . import VerticalContext, VerticalModule, VerticalResult, register
from ..models import Flag, Severity

_TIMEOUT = httpx.Timeout(30.0)
_UA = {"User-Agent": "assay-provenance/0.1 (+https://github.com/ahammadshibil/assay)"}


@register
class BiotechModule(VerticalModule):
    name = "biotech"

    async def evaluate(self, ctx: VerticalContext) -> VerticalResult:
        result = VerticalResult()
        trials, terr = await self._clinical_trials(ctx.company)
        approvals, aerr = await self._fda_approvals(ctx.company)

        result.findings["clinical_trials"] = trials
        result.findings["fda_approvals"] = approvals
        result.errors = [e for e in (terr, aerr) if e]

        # ---- clinical-trial flags ----
        # A failed ClinicalTrials.gov call must not masquerade as "no trials".
        if terr is not None:
            result.flags.append(Flag(
                "trial_source_unavailable", Severity.WATCH,
                "Could not query ClinicalTrials.gov — trial status is UNVERIFIED, not "
                "absent. Do not treat as a clinical-stage discrepancy until re-checked.",
                {"error": terr},
            ))
        elif trials:
            phases = sorted({p for t in trials for p in t.get("phases", []) if p})
            statuses = sorted({t.get("status") for t in trials if t.get("status")})
            result.flags.append(Flag(
                "clinical_stage_confirmed", Severity.INFO,
                f"{len(trials)} registered trial(s) sponsored by the company"
                f"{' — phases: ' + ', '.join(phases) if phases else ''}"
                f"{' — status: ' + ', '.join(statuses) if statuses else ''}.",
                {"nct_ids": [t.get("nct_id") for t in trials]},
            ))
        else:
            result.flags.append(Flag(
                "no_registered_trials", Severity.WATCH,
                "No trials on ClinicalTrials.gov sponsored by this company. If the "
                "deck claims clinical-stage, reconcile the discrepancy; trials may be "
                "run under a CRO, partner, or different legal entity.",
            ))

        # ---- FDA approval flags ----
        if aerr is not None:
            result.flags.append(Flag(
                "fda_source_unavailable", Severity.INFO,
                "Could not query openFDA — approval status unverified.",
                {"error": aerr},
            ))
        elif approvals:
            result.flags.append(Flag(
                "fda_approval", Severity.INFO,
                f"{len(approvals)} FDA drugsfda record(s) under the company name.",
                {"application_numbers": [a.get("application_number") for a in approvals]},
            ))
        return result

    async def _clinical_trials(self, company: str) -> tuple[list[dict], str | None]:
        url = "https://clinicaltrials.gov/api/v2/studies"
        params = {"query.spons": company, "pageSize": 25, "format": "json"}
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT, headers=_UA) as c:
                r = await c.get(url, params=params)
                r.raise_for_status()
                studies = r.json().get("studies", [])
        except Exception as e:  # noqa: BLE001
            return [], f"clinicaltrials: {e}"

        company_l = company.lower()
        out = []
        for s in studies:
            ps = s.get("protocolSection", {})
            ident = ps.get("identificationModule", {})
            status = ps.get("statusModule", {})
            design = ps.get("designModule", {})
            spons = ps.get("sponsorCollaboratorsModule", {}).get("leadSponsor", {})
            lead = (spons.get("name") or "")
            # Keep only studies where the company is the lead sponsor.
            if company_l and company_l not in lead.lower():
                continue
            out.append({
                "nct_id": ident.get("nctId"),
                "title": ident.get("briefTitle"),
                "status": status.get("overallStatus"),
                "phases": design.get("phases", []),
                "lead_sponsor": lead,
            })
        return out, None

    async def _fda_approvals(self, company: str) -> tuple[list[dict], str | None]:
        url = "https://api.fda.gov/drug/drugsfda.json"
        params = {"search": f'sponsor_name:"{company}"', "limit": 10}
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT, headers=_UA) as c:
                r = await c.get(url, params=params)
                if r.status_code == 404:  # openFDA returns 404 for zero matches
                    return [], None
                r.raise_for_status()
                results = r.json().get("results", [])
        except Exception as e:  # noqa: BLE001
            return [], f"openfda: {e}"
        return [{
            "application_number": a.get("application_number"),
            "sponsor_name": a.get("sponsor_name"),
            "products": [p.get("brand_name") for p in a.get("products", []) if p.get("brand_name")],
        } for a in results], None
