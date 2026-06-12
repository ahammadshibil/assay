# Assay

**Provenance + scientific-claim diligence for deeptech founders and companies.**

[![CI](https://github.com/ahammadshibil/assay/actions/workflows/ci.yml/badge.svg)](https://github.com/ahammadshibil/assay/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](pyproject.toml)

Generic startup-intelligence APIs (Crunchbase, Harmonic, PitchBook) answer *who
raised and who invested*. They don't answer the question that actually decides a
deeptech deal: **is the science real, does the founder own the IP, and has anyone
non-dilutive already underwritten the work?**

Assay has two modes, both built on free public APIs:

- **Provenance** — is the founder a real researcher, do they *own* the IP (or does their old lab?), and has anyone non-dilutive funded the work?
- **Science** ([below](#science-mode--confirm-the-claim-not-the-founder)) — take a technical *claim* and grade it against the primary literature, with **independent replication** as the headline signal.

Provenance triangulates three public data layers nobody bothers to wire together:

| Layer | Source | Question it answers |
|---|---|---|
| Research lineage | **OpenAlex** | Is the founder a real, recent, cited researcher? Where? |
| Patent ownership | **USPTO / PatentsView** | Are they the inventor — and is the *company* the assignee, or their old lab? |
| Non-dilutive funding | **SBIR.gov + NIH RePORTER** | Has the technical claim been independently funded? |

It returns a structured report with severity-ranked flags. The highest-value
check is `ip_owned_by_institution`: the founder is named as inventor, but the
patent is assigned to a university or national lab — meaning the startup needs an
explicit field-of-use license before the IP is investable. That single flag has
killed real deals.

All three layers are **free, keyless APIs**, so the engine runs with zero paid
data subscriptions. The only optional key is Anthropic, for an LLM verdict on top
of the deterministic flags.

## Install

```bash
git clone https://github.com/ahammadshibil/assay.git
cd assay
pip install -e ".[llm]"      # drop [llm] if you don't want the synthesis layer
```

## Use

```bash
# CLI
assay --founder "Jane Researcher" --company "Acme Bio" \
      --institution "CSIR-IGIB" --email you@fund.com

# add an LLM verdict (needs ANTHROPIC_API_KEY)
assay --founder "Jane Researcher" --company "Acme Bio" --synthesize

# attach a vertical for extra sources (biotech = ClinicalTrials.gov + openFDA)
assay --founder "Jane Researcher" --company "Acme Bio" --vertical biotech

# machine-readable, e.g. to pipe into Airtable
assay --founder "Jane Researcher" --company "Acme Bio" --json
```

```python
# Library
import asyncio
from assay import run_check, synthesize

report = asyncio.run(run_check(
    founder="Jane Researcher", company="Acme Bio",
    institution="CSIR-IGIB", mailto="you@fund.com",
))
for f in report.flags:
    print(f.severity.value, f.message)
report.verdict = synthesize(report)   # optional
```

## Science mode — confirm the *claim*, not the founder

The founder being real is table stakes. The question that decides a deeptech deal
is whether the **underlying science is real, published, and corroborated by
someone other than the founders.** Science mode takes a technical claim and grades
it against the primary literature (OpenAlex), with **independent replication** as
the headline signal.

```bash
# CLI
assay-science \
  --claim "CSP-TTK21 activates p300/CBP acetyltransferase and promotes axon regeneration" \
  --founders "Tapas Kumar Kundu, Snehajyoti Chatterjee"   # enables the independence check

assay-science --claim "..." --founders "..." --synthesize   # LLM reads actual support
assay-science --claim "..." --json
```

```python
import asyncio
from assay import verify_claim
r = asyncio.run(verify_claim(
    "enFnCas9 high-fidelity CRISPR variant with improved specificity",
    founders=["Sundaram Acharya", "Debojyoti Chakraborty"],
))
print(r.grade.value)          # REPLICATED / SINGLE_GROUP / PREPRINT_ONLY / ...
```

**Grades** (the unit is the claim, the evidence is the literature):

| Grade | Meaning |
|---|---|
| `REPLICATED` | peer-reviewed primary article from a group **other than the founders** |
| `SINGLE_GROUP` | peer-reviewed, but only the founders' own lab — no independent replication |
| `PEER_REVIEWED` | peer-reviewed primary exists; independence not assessed (pass `--founders`) |
| `PREPRINT_ONLY` | only preprints — not yet peer-reviewed |
| `UNSUPPORTED` | no literature found for the claim's terms |
| `CONTRADICTED` | a supporting work is **retracted** |
| `TOO_GENERIC` | the claim is all buzzwords ("an AI platform designs molecules") — it matches the field, not the company |

**Generic-claim guard.** A claim built only from buzzwords has nothing specific to
verify and otherwise greenwashes to `REPLICATED` off unrelated papers — so a claim
carrying no distinctive term (a named compound / target / mechanism) grades
`TOO_GENERIC` instead. Caught a real portfolio case where *"a generative-AI platform
designs optimized molecules"* was matching the entire AI-drug-discovery literature.
Subtler topical mismatches (a real term, but off-topic papers) need `--synthesize`
to catch — the deterministic grade can't read.

The `independent_replication` flag is graded conservatively: reviews mistyped as
articles are excluded, a single lightly-cited corroboration is downgraded to
`replication_thin`, and — per the same integrity rule as provenance mode — a
literature search that *errored* is flagged `literature_source_unavailable`, never
silently graded `UNSUPPORTED`.

### `--synthesize`: adversarial adjudication (structural → verified)

The deterministic grade answers *"does independent peer-reviewed literature
**exist**?"* — but it can't read, so a review or a keyword-only match can inflate
it. `--synthesize` closes that gap with a loop adapted from DeepMind's Co-Scientist
(Nature, 2026): for each surfaced paper it reconstructs the abstract and runs a
**self-adversarial pass** — steelman support, steelman refutation, then rule —
yielding `SUPPORTS / PARTIAL / IRRELEVANT / CONTRADICTS` per paper. A meta-review
aggregates these into a *verified stance*:

| Verified stance | Meaning |
|---|---|
| `INDEPENDENTLY SUPPORTED` | a non-founder paper actually supports the claim on reading |
| `SUPPORTED (founders' group only)` | only the founders' papers genuinely support it |
| `NOT SUPPORTED` | literature matched keywords but none establishes the claim |
| `REFUTED` | a paper contradicts it |

So `REPLICATED` (structural) + `NOT SUPPORTED` (verified) is a real and common
outcome — keyword hits that evaporate on reading. Needs `ANTHROPIC_API_KEY`; the
aggregation logic is pure and offline-tested. A title/abstract read is still not a
substitute for reading the papers — it's a sharper triage.

## Emit into a diligence memo (`--format l3`)

Both modes can emit their findings as ready-to-paste memo sections instead of a
console report — so a run *drafts* the deal memo rather than feeding a retype:

```bash
# Science → "§10 Claim Verification" (✅ checks out / ⚠️ inflated / ❌ couldn't verify)
#           + "Appendix A — Key Publications"
assay-science --claims-file deck_claims.txt --founders "..." --format l3

# Provenance → "§3 Founder — Research Lineage" + "§8 IP Portfolio"
assay --founder "..." --company "..." --institution "..." --format l3
```

`--claims-file` takes one claim per line and builds the full three-bucket ledger.
Each claim's bucket comes from its evidence grade — or, if `--synthesize` ran, from
the adversarial *verified stance* (so a keyword-only `REPLICATED` lands in
❌ "couldn't verify", not ✅). The integrity rule holds in the output too: an
unreachable patent or literature source renders as **UNVERIFIED, not clean**.
Markdown only — Assay prints the sections; it never writes into your vault.

## Tests

```bash
pip install -e ".[dev]"
pytest -q          # 65 offline tests: provenance, integrity, parsers, science grader, adjudication, renderers
```

The suite is network-free — it pins the parser field-paths against fixtures and
asserts the integrity property (failed source ⇒ `*_source_unavailable`, never
"none found"), so API drift fails loudly in CI.

## Evaluation

Science mode is benchmarked against [**SciFact**](https://github.com/allenai/scifact),
the standard scientific-claim-verification dataset — see [`eval/`](eval/). The eval
loop drove two improvements and then told us when to stop: a single full-sentence
query scored **44%** recall → adding a content-term query (`_content_query`) lifted it
to **78%** → adding PubMed reached **84%**, at which point the NEI rate caught up and
the eval showed **retrieval is no longer the bottleneck — judgment is** (the
`--synthesize` layer). Every change has a number attached, including the decision to
quit optimizing retrieval.

## Verticals

The core engine is one domain-agnostic agent. Verticals are **plug-in modules**
that attach extra sources behind it and contribute additional flags — they never
replace the core. This keeps the surface area small: you build the provenance
triangle once, and a vertical adds only what's genuinely sector-specific.

Shipped:

- **`biotech`** — ClinicalTrials.gov v2 (does the company sponsor registered
  trials, at what phase/status?) and openFDA drugsfda (any approved products?).
  The `no_registered_trials` WATCH is the useful one: a company positioning as
  clinical-stage with nothing registered is a discrepancy to reconcile.

To add one (e.g. space, semis): subclass `VerticalModule` in
`assay/verticals/`, implement `evaluate(ctx) -> VerticalResult`, decorate with
`@register`, and import it in `assay/verticals/__init__.py`. It then appears as a
`--vertical` choice automatically. ~40 lines per vertical.

## How it scores

Flags carry one of three severities:

- **RISK** — a hard gate (e.g. IP assigned to an institution, not the company).
- **WATCH** — verify manually (stale research, weak name match, grants to the wrong entity).
- **INFO** — recorded signal (h-index, patent count, non-dilutive validation).

Rules live in `assay/engine.py` and are deliberately readable — the judgment
about *what a flag means* is the moat, not the data access, so the rules are
meant to be edited per your thesis.

## Honest caveats

- **Live-test status (2026-06-11).** OpenAlex and NIH RePORTER verified working
  end-to-end. ClinicalTrials.gov works but WAF-blocks some datacenter IPs (403) —
  a network condition, not a bug. SBIR.gov was returning 429 (service-side
  maintenance). The PatentsView PatentSearch host (`search.patentsview.org`) no
  longer resolves — see below.
- **Unverified ≠ clean.** When a source can't be reached, the engine emits a
  `*_source_unavailable` WATCH ("IP ownership is UNVERIFIED"), never the
  substantive "none found" flag. A failed lookup must never read as a clean bill
  of health — that distinction is enforced and tested (`tests/test_engine.py`).
- **USPTO is mid-migration.** PatentSearch is moving to the Open Data Portal
  (`data.uspto.gov`) with no public ETA, and the old keyless host is down. Point
  the client at the ODP endpoint once available via `export ASSAY_PATENT_BASE=...`
  and pass `--patents-api-key`. Until then the patent layer flags as unverified.
- **US-centric grants.** SBIR and NIH are US-only. Indian (e.g. BIRAC, DST) and
  EU (CORDIS) non-dilutive sources are not wired in yet — see roadmap.
- **Name disambiguation is shallow.** OpenAlex matching uses institution as a
  tiebreak; common names need a manual check (surfaced as a WATCH flag).
- This is a sourcing/triage aid, not legal or investment advice.

## Roadmap

- **More verticals** behind the plug-in interface: `space` (Space-Track orbital
  data, FCC/ITU spectrum filings), `semis` (deeper patent/process analysis),
  `robotics` (citation-graph depth). Biotech is the reference implementation.
- **Lens.org** patent↔paper joins (links a patent to the scholarly work behind it).
- **EPO OPS** + **Google Patents (BigQuery)** for non-US IP.
- **Indian grant registries** (BIRAC, DST/SERB) + **CORDIS** (EU) in `grants`.
- **Crossref / Semantic Scholar** for citation-graph depth and influence weighting.
- Caching layer + proper author-ID resolution to cut repeat API calls.
- Confidence scoring on the disambiguation step.
- A thin web UI / Airtable sync for non-CLI users.

## License

MIT.
