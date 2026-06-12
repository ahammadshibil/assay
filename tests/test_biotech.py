"""Biotech vertical tests — offline, by overriding the network calls.

Same integrity property as the core engine: a failed ClinicalTrials.gov call must
flag UNVERIFIED, not "no registered trials" (which would read as a clinical-stage
discrepancy that isn't real).
"""
import asyncio

from assay.verticals import VerticalContext
from assay.verticals.biotech import BiotechModule


def _run(coro):
    return asyncio.run(coro)


def codes(result):
    return {f.code for f in result.flags}


class _Stub(BiotechModule):
    def __init__(self, trials=None, terr=None, approvals=None, aerr=None):
        self._t, self._te, self._a, self._ae = trials or [], terr, approvals or [], aerr

    async def _clinical_trials(self, company):
        return self._t, self._te

    async def _fda_approvals(self, company):
        return self._a, self._ae


def test_trial_source_error_is_unverified_not_absent():
    res = _run(_Stub(terr="clinicaltrials: 403 Forbidden").evaluate(VerticalContext("f", "Acme")))
    assert "trial_source_unavailable" in codes(res)
    assert "no_registered_trials" not in codes(res)


def test_empty_trials_without_error_is_a_real_discrepancy():
    res = _run(_Stub().evaluate(VerticalContext("f", "Acme")))
    assert "no_registered_trials" in codes(res)
    assert "trial_source_unavailable" not in codes(res)


def test_trials_present_confirms_clinical_stage():
    trials = [{"nct_id": "NCT1", "phases": ["PHASE2"], "status": "RECRUITING"}]
    res = _run(_Stub(trials=trials).evaluate(VerticalContext("f", "Acme")))
    assert "clinical_stage_confirmed" in codes(res)


def test_fda_source_error_is_flagged_separately():
    res = _run(_Stub(aerr="openfda: 500").evaluate(VerticalContext("f", "Acme")))
    assert "fda_source_unavailable" in codes(res)
    assert "fda_approval" not in codes(res)
