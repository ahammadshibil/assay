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

## Results — the eval loop in action

| Retrieval | Verifiable recall | NEI-found (upper bound) | TOO_GENERIC |
|---|---|---|---|
| **v1** — single full-sentence query | 23/52 = **44%** | 12/26 = 46% | 0/78 |
| **v2** — + content-term query, unioned | 31/40 = **78%** | 13/20 = 65% | 0/60 |

(Stratified samples of the dev set; v1 n=78, v2 n=60. The lift is far larger than
sample noise.)

**v2 grade distribution** (2026-06-12):

```
gold           n  grade distribution (Assay)
SUPPORT       20  PEER_REVIEWED:15, UNSUPPORTED:5
CONTRADICT    20  PEER_REVIEWED:15, UNSUPPORTED:4, PREPRINT_ONLY:1
NEI           20  PEER_REVIEWED:13, UNSUPPORTED:7
```

## What this says — honestly

- **Retrieval was the bottleneck, and it's measurable.** v1's verifiable-recall (44%)
  and NEI-found rate (46%) were nearly identical — a single keyword query over a terse
  claim sentence barely discriminated "real science exists" from "not enough info,"
  exactly the retrieval-bound failure SciFact's own paper reports.
- **One targeted fix nearly doubled recall.** v2 adds a second, stopword-stripped
  *content query* and unions the results (`science._content_query`). Recall went
  **44% → 78%**, and the gap to the NEI rate widened from ~2 points to 13 — so it
  retrieves the right paper more often, not just *more* papers.
- **The guard stayed clean** — zero false `TOO_GENERIC` across both runs.
- **Still room up.** NEI-found at 65% reflects open-web over-retrieval (see caveat);
  adding PubMed/Semantic Scholar, or the `--synthesize` judgment layer on top, is the
  next lever. The point is each change now has a number attached.

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
