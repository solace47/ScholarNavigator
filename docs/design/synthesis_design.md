# Citation-Backed Synthesis Design

## Scope

This document designs a future citation-backed synthesis layer for
ScholarNavigator.

Current phase boundaries:

- Design only.
- Do not connect the layer to `SearchService`.
- Do not change FastAPI routes or API schemas.
- Do not change frontend code.
- Do not change `third_party`.
- Do not call an LLM.
- Do not access external networks.

The goal is to prepare a structured synthesis layer that can turn ranked,
evidence-bearing search results into a concise answer with explicit citation
coverage and limitations.

## Reference Takeaways

### PaperQA2

PaperQA2 decomposes scientific QA into search, evidence gathering, optional
citation traversal, and answer generation. The useful ideas for this project
are:

- Evidence gathering should be an explicit stage, not hidden inside final
  answer generation.
- Evidence should be ranked and compressed before synthesis. PaperQA2 uses
  contextual summarization and relevance scores; our MVP can use existing
  `JudgementResult.evidence` and `RankedPaper.evidence` instead.
- Answer generation should be grounded only in retrieved context. If context is
  insufficient, the system should say so rather than fill gaps.
- Citation traversal can improve recall by expanding around already relevant
  papers. Our current RefChain stage is the lightweight analogue.
- Context budget matters. Too many weak contexts can reduce precision, so the
  synthesis layer should cap cited papers and evidence rows.
- Citation format must be constrained. PaperQA2 requires citations to use valid
  source keys from the supplied context only.

Borrowed design principle:

```text
No synthesized claim may exist without an evidence row and a valid citation key.
```

### ai2-scholarqa-lib

`ai2-scholarqa-lib` organizes scholar QA as retrieval, reranking, quote
extraction, planning/clustering, section generation, and citation validation.
The useful ideas for this project are:

- Separate evidence selection from prose generation.
- Prefer exact quotes or short evidence snippets over unconstrained summaries.
- Organize evidence into dimensions or sections before writing a final report.
- Preserve paper metadata alongside every citation, including title, year,
  authors, venue, source identifiers, and snippet metadata.
- Validate citations after generation. The library has tests that filter
  suspicious citations when metadata lacks reliable body/section/sentence
  location.
- The UI pattern of inline paper chips plus evidence popovers is useful for a
  future frontend display.

Borrowed design principle:

```text
Each citation should resolve to a concrete evidence object, not only to a paper.
```

## Current Project Context

The current internal pipeline already has most upstream inputs:

- `SearchServiceOutput.ranked_papers`
- `RankedPaper.evidence`
- `RankedPaper.paper` metadata and identifiers
- `SearchServiceOutput.warnings`
- `SearchServiceOutput.source_stats`
- `SearchServiceOutput.refchain_output`

The API mapper currently derives frontend-facing structures:

- `missing_evidence`
- `citation_graph`
- `method_clusters`
- `timeline`
- `cost_report`

Important current gap:

`SearchServiceOutput` does not yet expose first-class `missing_evidence` or
`citation_graph` fields. They are currently mapper-derived from warnings,
source errors, filtered papers, Query Evolution records, and RefChain output.
The synthesis design should therefore either:

1. build from `SearchServiceOutput` plus the same deterministic mapper helpers;
   or
2. later add explicit internal synthesis/citation fields before expanding the
   public API.

## MVP Citation-Backed Synthesis

The MVP should be rule-based and deterministic. It should not call an LLM, read
PDF full text, or introduce facts outside the search result metadata and
existing evidence.

### Inputs

Logical inputs should come from `SearchServiceOutput` and mapper-derived
diagnostics:

- `search_plan.query_analysis`
- `ranked_papers`
- `ranked_papers[*].evidence`
- `ranked_papers[*].paper`
- `warnings`
- `source_stats[*].error_message`
- `query_evolution_records`
- `refchain_output.reference_edges`
- mapper-derived `missing_evidence`
- mapper-derived `citation_graph`

Only the following evidence sources are allowed in MVP:

- `title`
- `abstract`
- `venue`
- `metadata`
- RefChain edge metadata already present in `refchain_output`

### Output Fields

Proposed MVP output:

```text
SynthesisOutput
  answer_summary: str
  key_findings: list[SynthesisFinding]
  evidence_table: list[SynthesisEvidenceRow]
  citation_coverage: CitationCoverage
  limitations: list[str]
  warnings: list[str]
```

Suggested field meanings:

- `answer_summary`: short answer grounded in cited evidence rows.
- `key_findings`: bullet-style findings, each with one or more citation keys.
- `evidence_table`: machine-readable evidence rows used by the summary.
- `citation_coverage`: coverage and quality counters.
- `limitations`: explicit gaps, missing sources, source errors, and evidence
  insufficiency notes.
- `warnings`: synthesis-stage warnings, such as no evidence rows or invalid
  citation attempts.

### Evidence Table

Each evidence row should be small and traceable:

```text
SynthesisEvidenceRow
  citation_key: str
  rank: int
  paper_title: str
  year: int | None
  venue: str
  sources: list[str]
  identifiers: PaperIdentifiers
  category: str
  final_score: float
  evidence_source: title | abstract | venue | metadata
  evidence_text: str
  supported_terms: list[str]
  supported_claim: str
```

Rules:

- `citation_key` should be deterministic, for example `R1`, `R2`, based on
  final rank.
- `evidence_text` must be copied from an existing `EvidenceItem.text` or from
  paper metadata already present in the ranked paper.
- `supported_claim` must be a conservative paraphrase of the evidence row. It
  must not introduce new methods, datasets, metrics, or comparisons.
- Long abstracts should be clipped to a short snippet.

### Answer Summary

Rule-based summary template:

```text
For the query "<query>", the current ranked evidence mainly points to
<topic/method terms> in <time range>. The strongest candidates are <R1>, <R2>,
and <R3>. Evidence is strongest for <matched themes>. Evidence is limited for
<missing evidence / failed sources / unavailable full text>.
```

Summary constraints:

- Cite every concrete paper-specific statement.
- Do not claim "best", "first", "state of the art", or "outperforms" unless
  those words appear in the available evidence.
- If there are no valid evidence rows, return an insufficient-evidence summary.
- Keep the summary concise enough for the existing result page.

### Key Findings

MVP key findings can be generated by grouping evidence rows:

- by high-frequency matched terms;
- by method constraints from `query_analysis`;
- by year bucket for recent-progress queries;
- by source or venue when a venue constraint exists;
- by RefChain edges when references are enabled.

Each finding must include:

```text
SynthesisFinding
  text: str
  citation_keys: list[str]
  confidence: float
  evidence_row_ids: list[str]
```

If a finding has no citation key, it must be rejected.

### Citation Coverage

The coverage object should make answer trustworthiness visible:

```text
CitationCoverage
  ranked_paper_count: int
  cited_paper_count: int
  evidence_row_count: int
  cited_evidence_row_count: int
  missing_evidence_count: int
  citation_graph_node_count: int
  citation_graph_edge_count: int
  source_error_count: int
  coverage_ratio: float
```

Coverage should be computed deterministically:

- `cited_paper_count / ranked_paper_count`
- `cited_evidence_row_count / evidence_row_count`
- source errors from `source_stats[*].error_message`
- graph counts from mapper-derived `citation_graph` or `refchain_output`

## Internal Schema Needs

Yes, a future implementation should add internal schema, likely in
`src/scholar_agent/core/search_schemas.py` or a dedicated synthesis schema
module:

```text
SynthesisOptions
SynthesisCitation
SynthesisEvidenceRow
SynthesisFinding
CitationCoverage
SynthesisOutput
```

`SearchServiceOutput` can later add:

```text
synthesis_output: SynthesisOutput | None = None
```

This keeps synthesis optional and avoids disrupting the current pipeline.

## API Schema Needs

No API change should be made in this design-only phase.

For a later frontend-ready release, extend `SearchRunResultResponse` with an
optional field:

```text
synthesis: SynthesisOutput | None
```

The field should be optional so existing Mock Demo and current frontend result
rendering remain compatible. Until then, selected synthesis limitations can be
mirrored into `missing_evidence` by the API mapper.

## Frontend Display Needs

No frontend change should be made now.

Later, the frontend should add a synthesis panel above the paper lists:

- compact answer summary;
- key findings with citation chips;
- citation coverage counters;
- evidence table with expandable rows;
- limitations and source-error diagnostics.

The existing paper cards can remain the detailed per-paper view. Citation chips
should jump to the paper card or open an evidence popover.

## MVP Rule-Based Algorithm

Recommended deterministic flow:

1. Select ranked papers with category `highly_relevant` or
   `partially_relevant`.
2. Keep at most `max_cited_papers`, default `8`.
3. For each selected paper, collect valid `EvidenceItem` rows.
4. Assign deterministic citation keys by rank.
5. Drop evidence rows with empty text or unsupported source labels.
6. Generate key findings from matched terms, query constraints, venue/year
   constraints, and evidence source labels.
7. Reject any finding without at least one citation key.
8. Generate a short `answer_summary` from accepted findings.
9. Compute `citation_coverage`.
10. Add limitations from source errors, warnings, missing years, missing
    abstracts, disabled RefChain, and absent full-text evidence.

Failure behavior:

- If no ranked papers are present, return an insufficient-evidence synthesis.
- If ranked papers exist but no evidence rows exist, return limitations and do
  not create unsupported findings.
- If citation graph is empty, do not infer citation relationships.

## Future LLM Enhancement

A later LLM version can improve readability and grouping, but it must remain
evidence-bound.

Recommended integration:

- Add `prompts/synthesis.md`.
- Pass only `evidence_table`, `query_analysis`, and allowed citation keys to the
  LLM.
- Require strict JSON output matching `SynthesisOutput`.
- Require every finding and sentence-level claim to cite known keys.
- Reject output with unknown citation keys.
- Reject output containing citation-free paper-specific claims.
- Reject output that introduces external papers, metrics, datasets, or facts not
  present in input evidence.
- Fall back to the rule-based synthesis if validation fails.

The LLM should never receive API keys, raw connector credentials, or permission
to browse.

## Fabrication Controls

Hard constraints:

- Use only evidence rows derived from ranked papers.
- Use only citation keys assigned by the synthesis layer.
- Do not synthesize citation graph edges not present in `refchain_output`.
- Do not cite a paper if its evidence row was filtered out.
- Do not use external knowledge to fill missing abstracts, venues, years, or
  identifiers.
- Mark source outages and unsupported sources in limitations.
- Prefer "the retrieved metadata suggests" over strong claims when evidence is
  metadata-only.

Validation checks:

- every citation key resolves to one evidence row;
- every finding has at least one valid citation key;
- every citation key belongs to a visible ranked paper;
- no duplicate citation keys for different papers;
- no unsupported evidence source labels;
- no answer summary when `evidence_table` is empty except insufficient-evidence
  text;
- no citation graph counts from inferred edges.

## Testing Plan

Future tests should be local-only and deterministic:

- `tests/test_synthesis.py`
  - no evidence returns insufficient-evidence synthesis;
  - evidence rows generate deterministic citation keys;
  - every finding has valid citations;
  - unknown citation keys are rejected;
  - source errors and warnings appear in limitations;
  - citation coverage counters are correct;
  - metadata-only evidence is labeled as limited;
  - empty abstracts do not crash synthesis;
  - no LLM or network call is made.

- `tests/test_api_mapper_synthesis.py`
  - optional synthesis field maps without breaking existing result structure;
  - absence of synthesis maps as `null`;
  - limitations can still surface through `missing_evidence`.

- Future frontend tests
  - synthesis panel renders summary, citations, evidence table, and limitations;
  - citation chips link to the correct paper/evidence row;
  - empty synthesis state is clear and non-blocking.

## Implementation Placement

Recommended future files:

```text
src/scholar_agent/agents/synthesis.py
src/scholar_agent/core/synthesis_schemas.py
prompts/synthesis.md
tests/test_synthesis.py
docs/design/synthesis_runbook.md
```

Recommended service integration point:

```text
after final rerank_papers
before api_mapper conversion
```

This allows synthesis to use final ranks, evidence, warnings, and RefChain
records without changing retrieval, judgement, or reranking behavior.

## Actual Referenced Files

Project files:

- `docs/reference_papers/paperqa2.pdf`
- `docs/design/api_mapper_runbook.md`
- `docs/design/frontend_real_preview_validation.md`
- `docs/design/real_preview_stability_validation.md`
- `src/scholar_agent/services/search_service.py`
- `src/scholar_agent/services/api_mapper.py`
- `src/scholar_agent/core/search_schemas.py`
- `src/scholar_agent/core/api_schemas.py`

PaperQA2 reference implementation:

- `third_party/paper-qa/README.md`
- `third_party/paper-qa/src/paperqa/prompts.py`
- `third_party/paper-qa/src/paperqa/types.py`
- `third_party/paper-qa/src/paperqa/agents/tools.py`
- `third_party/paper-qa/src/paperqa/core.py`
- `third_party/paper-qa/src/paperqa/docs.py`

AI2 Scholar QA reference implementation:

- `third_party/ai2-scholarqa-lib/README.md`
- `third_party/ai2-scholarqa-lib/api/scholarqa/scholar_qa.py`
- `third_party/ai2-scholarqa-lib/api/scholarqa/models.py`
- `third_party/ai2-scholarqa-lib/api/scholarqa/rag/multi_step_qa_pipeline.py`
- `third_party/ai2-scholarqa-lib/api/scholarqa/llms/prompts.py`
- `third_party/ai2-scholarqa-lib/api/tests/test_citation_validation.py`
- `third_party/ai2-scholarqa-lib/ui/src/components/widgets/EvidenceCard.tsx`
- `third_party/ai2-scholarqa-lib/ui/src/components/widgets/EvidenceCardContent.tsx`
- `third_party/ai2-scholarqa-lib/ui/src/components/widgets/InlinePaperChipWidgetWithEvidence.tsx`

## Unreadable Or Limited References

- No required project reference file was unreadable.
- `docs/reference_papers/paperqa2.pdf` was readable through local Python
  `pypdf`. The system `pdftotext` command was unavailable, so it was not used.
- The initially advertised PDF skill path
  `/Users/xs/.codex/plugins/cache/openai-primary-runtime/pdf/26.630.12035/skills/pdf/SKILL.md`
  did not exist in this environment. The installed PDF skill was available at
  version `26.630.12135` and was read instead.
- Some broad third-party file reads produced output too large for the terminal
  response. Relevant files were then read with targeted commands.
