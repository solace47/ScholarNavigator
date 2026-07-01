# Synthesis Agent Runbook

## Scope

This runbook covers the internal rule-based citation-backed synthesis module:

```text
src/scholar_agent/core/synthesis_schemas.py
src/scholar_agent/agents/synthesis.py
```

Current boundaries:

- Integrated into `SearchService` as an optional final internal output.
- No FastAPI/API schema changes.
- No frontend changes.
- No `third_party` changes.
- No LLM calls.
- No external network access.
- No Mock API behavior change.

## Public Entry Point

```python
from scholar_agent.agents.synthesis import synthesize_answer

synthesis = synthesize_answer(search_service_output)
```

Input:

```text
SearchServiceOutput
```

Output:

```text
SynthesisOutput
```

## Schemas

New internal schemas are defined in:

```text
src/scholar_agent/core/synthesis_schemas.py
```

Models:

- `SynthesisOptions`
- `SynthesisEvidenceRow`
- `SynthesisFinding`
- `CitationCoverage`
- `SynthesisOutput`

These schemas are internal only. They are not yet exposed through
`SearchRunResultResponse`.

## MVP Rule Logic

The current implementation is deterministic and metadata-only.

Inputs used:

- `search_output.ranked_papers`
- `ranked_papers[*].evidence`
- `search_output.warnings`
- `search_output.source_stats`
- `search_output.refchain_output`

Allowed evidence sources:

- `title`
- `abstract`
- `venue`
- `metadata`

Unsupported evidence sources are filtered and surfaced in
`SynthesisOutput.warnings`.

Citation keys:

- Generated from final rank.
- Rank 1 becomes `R1`, rank 2 becomes `R2`.
- Evidence rows use stable IDs such as `R1-E1`.

Default limits:

- At most 8 cited papers.
- At most 3 evidence rows per cited paper.
- At most 5 findings.

The agent skips papers classified as `irrelevant` or
`insufficient_evidence`.

## SearchService Integration

`SearchServiceOutput` now includes:

```text
synthesis_output: SynthesisOutput | None = None
```

`SearchService.run_search` accepts:

```text
enable_synthesis: bool = True
```

When enabled, synthesis runs after the final `rerank_papers` call. This means
the synthesis sees final ranked papers after the optional Query Evolution and
RefChain stages have already participated in deduplication, judgement, and
reranking.

When `enable_synthesis=False`, `synthesis_output` remains `None`.

The implementation constructs `SearchServiceOutput` first, then calls
`synthesize_answer(output)` and assigns the result. This avoids import-time
cycles between `search_service.py` and `agents/synthesis.py`.

API boundaries:

- The raw internal preview endpoint can expose `synthesis_output`.
- The API-result preview endpoint still maps to the existing
  `SearchRunResultResponse` schema and does not expose synthesis yet.
- Existing Mock API endpoints are unchanged.

## Insufficient Evidence Behavior

If no valid evidence rows are available, the agent returns:

- `status="insufficient_evidence"`
- an insufficient-evidence `answer_summary`
- no findings
- an empty evidence table
- limitations explaining the evidence gap

It does not fabricate conclusions from paper titles or external facts.

## Limitations

The agent adds limitations for:

- `SearchServiceOutput.warnings`
- source errors from `source_stats[*].error_message`
- unavailable full-text evidence
- metadata-only evidence
- unavailable RefChain output
- RefChain warnings when present

This keeps the synthesis suitable for demo diagnostics without implying that
the system read PDFs or full paper text.

## Citation Coverage

`CitationCoverage` reports:

- `ranked_paper_count`
- `cited_paper_count`
- `evidence_row_count`
- `cited_evidence_row_count`
- `missing_evidence_count`
- `source_error_count`
- `coverage_ratio`

The current `coverage_ratio` is:

```text
cited_paper_count / ranked_paper_count
```

When no ranked papers exist, the ratio is `0.0`.

## Future Integration

Recommended next steps:

1. Extend `api_mapper` to expose an optional synthesis field.
2. Add a frontend synthesis panel above paper cards.
3. Keep `missing_evidence` as the fallback diagnostic surface for clients that
   do not render synthesis yet.

## Future LLM Enhancement

`prompts/synthesis.md` is reserved for a future LLM-enhanced version. Current
code does not call it.

Future LLM output must be validated:

- every citation key must exist in the evidence table;
- every finding must cite at least one evidence row;
- no external facts may be introduced;
- unknown citation keys must fail validation;
- the rule-based synthesis should remain the fallback.

## Tests

Coverage is in:

```text
tests/test_synthesis.py
```

The tests verify:

- no ranked papers returns insufficient evidence;
- valid evidence generates citation keys and findings;
- every finding has legal citation keys;
- source errors and warnings enter limitations;
- citation coverage is computed;
- unsupported evidence sources are filtered;
- metadata-only evidence is marked as a limitation;
- output is stable and local.
