# Evaluation — SciFact

Assay's science mode is scientific claim verification, so it should be measured on
the standard benchmark for that task: **SciFact** ([Wadden et al., 2020](https://github.com/allenai/scifact)).
This turns "I think it works" into a number.

## Get the data (not vendored — 3 MB, AllenAI's)

```bash
wget https://scifact.s3-us-west-2.amazonaws.com/release/latest/data.tar.gz
tar xzf data.tar.gz -C eval/        # -> eval/data/claims_dev.jsonl, eval/data/corpus.jsonl
```

## Run

```bash
# end-to-end: Assay retrieves from OpenAlex and grades (no API key)
python eval/scifact_eval.py --data-dir eval/data --limit 80

# grader: feed the gold abstract straight to the adjudicator (needs ANTHROPIC_API_KEY)
python eval/scifact_eval.py --data-dir eval/data --limit 80 --mode grader
```

## Result (end2end, 78-claim stratified sample, 2026-06-12)

```
gold           n  grade distribution (Assay)
SUPPORT       26  PEER_REVIEWED:15, UNSUPPORTED:11
CONTRADICT    26  UNSUPPORTED:18, PEER_REVIEWED:8
NEI           26  UNSUPPORTED:14, PEER_REVIEWED:10, PREPRINT_ONLY:2

recall on verifiable claims : 23/52 = 44%
NEI evidence-found rate     : 12/26 = 46%   (upper bound — see caveat)
claims flagged TOO_GENERIC  : 0/78          (guard sanity: correct, no false fires)
```

## What this says — honestly

- **Recall is 44%.** Assay finds peer-reviewed evidence for a genuinely verifiable
  claim less than half the time. That is **low**, and it should be read as a real
  result, not hidden.
- **Retrieval is the bottleneck, not judgment.** The verifiable-recall (44%) and the
  NEI-found rate (46%) are nearly identical — Assay's single keyword query over a
  terse claim sentence barely discriminates "real science exists" from "not enough
  info." This is exactly the finding SciFact's own paper reports (open-domain
  verification is retrieval-bound) and the limitation flagged throughout the README.
- **The guard is well-behaved** — zero false `TOO_GENERIC` fires on specific
  biomedical claims.
- **The fix is now measurable.** Better retrieval — query expansion, multiple
  sub-queries, adding PubMed/Semantic Scholar alongside OpenAlex, or a real retriever
  — should move this number. That's the point of having it.

## Caveat (don't misquote the number)

SciFact's **NEI is corpus-relative** ("the curated 5K-doc corpus has no evidence").
Assay searches the open web (all of OpenAlex), so it can *legitimately* find
literature for an NEI claim. So:

- the **recall on verifiable claims is a clean metric** (open web is a superset);
- the **NEI evidence-found rate is an upper bound on error**, not a true error rate —
  read it as "Assay finds *something* for ~half of all biomedical claims," which is
  the over-retrieval tendency the generic-claim guard and `--synthesize` exist to
  contain.

The `grader` mode (gold abstract → adjudicator) isolates the judgment layer from
retrieval and matches SciFact's actual SUPPORT/CONTRADICT/NEI task; it needs an
`ANTHROPIC_API_KEY` to run.
