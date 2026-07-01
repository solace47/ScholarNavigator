# RefChain Runbook

## Scope

`RefChainAgent` is a deterministic, no-LLM, single-layer reference expansion
component. It prepares the future RefChain stage but is not connected to
`SearchService` yet.

Current boundaries:

- No LLM calls.
- No network access inside the agent.
- No recursive citation traversal.
- No SearchService integration yet.
- No FastAPI Mock API changes.
- No frontend changes.
- No `third_party` changes.

## Module Responsibility

Files:

```text
src/scholar_agent/core/search_schemas.py
src/scholar_agent/agents/refchain.py
```

Public function:

```python
from scholar_agent.agents.refchain import expand_refchain
from scholar_agent.connectors.openalex import fetch_openalex_references

output = expand_refchain(
    query_analysis=query_analysis,
    ranked_papers=ranked_papers,
    fetch_references=fetch_openalex_references,
)
```

Tests inject a fake `fetch_references` function. They do not access OpenAlex or
any other external service.

## Schemas

The internal pipeline schema includes:

- `RefChainOptions`
- `RefChainSeed`
- `ReferenceEdge`
- `RefChainRecord`
- `RefChainOutput`

`RefChainOutput` contains:

- `references`
- `reference_edges`
- `record`
- `warnings`
- `latency_seconds`

## Seed Selection

The agent reads already ranked papers and selects seeds in rank order.

Eligible seeds:

- `highly_relevant`
- `partially_relevant` with `final_score >= min_seed_score`

Ineligible seeds:

- `weakly_relevant`
- `irrelevant`
- `insufficient_evidence`

Default options:

- `max_seed_papers=3`
- `max_references_per_seed=15`
- `max_total_references=50`
- `min_seed_score=0.45`

## Identifier Requirement

The current RefChain MVP is OpenAlex-oriented. A seed must have one of:

- `paper.identifiers.openalex_id`
- `paper.identifiers.doi`

If neither identifier exists, the seed is skipped and the output includes:

```text
refchain_seed_missing_supported_identifier:<rank>
```

References also need an OpenAlex ID or DOI so that `ReferenceEdge` can be
grounded. References without a supported identifier are skipped with:

```text
refchain_reference_missing_identifier:<seed_rank>
```

## Fetching Contract

`RefChainAgent` does not perform HTTP requests. It receives:

```python
Callable[[Paper, int], list[Paper]]
```

The injected fetcher is called once per eligible seed with the per-seed limit.
For production OpenAlex RefChain, pass `fetch_openalex_references`. For tests,
use a fake fetcher.

Single seed failures are isolated. If a fetcher raises, the agent records:

```text
refchain_seed_failed:<rank>:<error>
```

and continues with later seeds.

## Single-Layer Boundary

The agent only expands references for the original seed papers. It never calls
the fetcher on returned references, so recursive/multi-layer citation expansion
is out of scope.

## Future SearchService Integration

Future integration should run after initial retrieval, deduplication, judgement,
reranking, and optional Query Evolution:

```text
ranked_papers
  -> expand_refchain
  -> merge references with existing candidates
  -> deduplicate_papers
  -> judge_papers
  -> rerank_papers
```

The existing Mock API should remain unchanged until the real SearchService path
is explicitly promoted behind a feature flag.

