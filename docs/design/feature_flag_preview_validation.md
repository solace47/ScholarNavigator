# Feature Flag Preview Validation

## Scope

This document records a manual validation of:

```text
POST /api/v1/internal/search/preview
```

Validation date: 2026-07-01

No frontend files, `third_party` files, or Mock API behavior were changed. No LLM
calls were made.

## Backend Command

```bash
PYTHONPATH=src uvicorn scholar_agent.app.main:app \
  --host 127.0.0.1 \
  --port 8000
```

## Shared Request

```json
{
  "query": "latest LLM reranking methods for scientific literature retrieval",
  "top_k": 10,
  "run_profile": "balanced",
  "current_year": 2026
}
```

Cases:

- `baseline`: `enable_query_evolution=false`, `enable_refchain=false`
- `evolution`: `enable_query_evolution=true`, `enable_refchain=false`
- `evolution_refchain`: `enable_query_evolution=true`, `enable_refchain=true`

## Summary

| Case | HTTP | Raw | Deduplicated | Service latency |
| --- | ---: | ---: | ---: | ---: |
| baseline | 200 | 30 | 11 | 2.078s |
| evolution | 200 | 60 | 23 | 3.466s |
| evolution_refchain | 200 | 60 | 23 | 2.921s |

Notes:

- OpenAlex search calls were attempted, but backend logs reported
  `HTTP Error 503: Service Unavailable`. The connector failed closed and
  returned `0` papers, so `source_stats.error_message` stayed `null`.
- arXiv search calls returned papers.
- Query Evolution generated 3 evolved queries.
- RefChain did not fetch references because selected top seeds lacked OpenAlex ID
  or DOI, so the agent returned controlled warnings.

## Baseline

Flags:

```json
{
  "enable_query_evolution": false,
  "enable_refchain": false
}
```

Result:

- HTTP status: `200`
- `raw_count`: `30`
- `deduplicated_count`: `11`
- `latency_seconds`: `2.0776694999076426`
- `query_evolution_records`: `0`
- `refchain_output`: `null`
- warnings: `[]`

Top 5 ranked papers:

| Rank | Title | Year | Sources | Final score |
| ---: | --- | ---: | --- | ---: |
| 1 | CoRank: LLM-Based Compact Reranking with Document Features for Scientific Retrieval | 2025 | arxiv | 0.9043 |
| 2 | Passage Query Methods for Retrieval and Reranking in Conversational Agents | 2025 | arxiv | 0.8653 |
| 3 | LegalMALR:Multi-Agent Query Understanding and LLM-Based Reranking for Chinese Statute Retrieval | 2026 | arxiv | 0.8646 |
| 4 | DS@GT at TREC TOT 2025: Bridging Vague Recollection with Fusion Retrieval and Learned Reranking | 2026 | arxiv | 0.8386 |
| 5 | Tensor Manifold-Based Graph-Vector Fusion for AI-Native Academic Literature Retrieval | 2026 | arxiv | 0.7703 |

Source stats returned by the endpoint. Backend logs showed OpenAlex `503`
failures even though the connector returned empty lists without propagating
errors into `source_stats.error_message`.

| Source | Returned | Latency | Error |
| --- | ---: | ---: | --- |
| openalex | 0 | 0.758578s | null |
| arxiv | 10 | 1.160827s | null |
| openalex | 0 | 0.757861s | null |
| arxiv | 10 | 0.641674s | null |
| openalex | 0 | 0.757507s | null |
| arxiv | 10 | 1.280700s | null |

## Evolution

Flags:

```json
{
  "enable_query_evolution": true,
  "enable_refchain": false
}
```

Result:

- HTTP status: `200`
- `raw_count`: `60`
- `deduplicated_count`: `23`
- `latency_seconds`: `3.465796208009124`
- `query_evolution_records`: `1`
- `refchain_output`: `null`
- warnings: `[]`

Generated evolved queries:

1. `LLM reranking methods scientific literature retrieval recent advances`
2. `llm reranking retrieval scientific methods rag`
3. `LLM reranking retrieval scientific CoRank LLM-Based Compact`

Top 5 ranked papers:

| Rank | Title | Year | Sources | Final score |
| ---: | --- | ---: | --- | ---: |
| 1 | CoRank: LLM-Based Compact Reranking with Document Features for Scientific Retrieval | 2025 | arxiv | 0.9043 |
| 2 | RankArena: A Unified Platform for Evaluating Retrieval, Reranking and RAG with Human and LLM Feedback | 2025 | arxiv | 0.8913 |
| 3 | Rethinking LLM Parametric Knowledge as Post-retrieval Confidence for Dynamic Retrieval and Reranking | 2025 | arxiv | 0.8913 |
| 4 | Scientific Paper Retrieval with LLM-Guided Semantic-Based Ranking | 2025 | arxiv | 0.8783 |
| 5 | Passage Query Methods for Retrieval and Reranking in Conversational Agents | 2025 | arxiv | 0.8653 |

Source stats returned by the endpoint. Backend logs showed OpenAlex `503`
failures even though the connector returned empty lists without propagating
errors into `source_stats.error_message`.

| Source | Returned | Latency | Error |
| --- | ---: | ---: | --- |
| openalex | 0 | 0.770415s | null |
| arxiv | 10 | 0.631021s | null |
| openalex | 0 | 0.770996s | null |
| arxiv | 10 | 0.637595s | null |
| openalex | 0 | 0.767956s | null |
| arxiv | 10 | 0.625139s | null |
| openalex | 0 | 0.680805s | null |
| arxiv | 10 | 1.269259s | null |
| openalex | 0 | 0.677513s | null |
| arxiv | 10 | 1.096495s | null |
| openalex | 0 | 0.680058s | null |
| arxiv | 10 | 1.047198s | null |

## Evolution + RefChain

Flags:

```json
{
  "enable_query_evolution": true,
  "enable_refchain": true
}
```

Result:

- HTTP status: `200`
- `raw_count`: `60`
- `deduplicated_count`: `23`
- `latency_seconds`: `2.921042292029597`
- `query_evolution_records`: `1`
- `refchain_output`: present
- `refchain_output.references`: `0`
- RefChain warnings:
  - `refchain_seed_missing_supported_identifier:1`
  - `refchain_seed_missing_supported_identifier:2`
  - `refchain_seed_missing_supported_identifier:3`

Generated evolved queries:

1. `LLM reranking methods scientific literature retrieval recent advances`
2. `llm reranking retrieval scientific methods rag`
3. `LLM reranking retrieval scientific CoRank LLM-Based Compact`

Top 5 ranked papers:

| Rank | Title | Year | Sources | Final score |
| ---: | --- | ---: | --- | ---: |
| 1 | CoRank: LLM-Based Compact Reranking with Document Features for Scientific Retrieval | 2025 | arxiv | 0.9043 |
| 2 | RankArena: A Unified Platform for Evaluating Retrieval, Reranking and RAG with Human and LLM Feedback | 2025 | arxiv | 0.8913 |
| 3 | Rethinking LLM Parametric Knowledge as Post-retrieval Confidence for Dynamic Retrieval and Reranking | 2025 | arxiv | 0.8913 |
| 4 | Scientific Paper Retrieval with LLM-Guided Semantic-Based Ranking | 2025 | arxiv | 0.8783 |
| 5 | Passage Query Methods for Retrieval and Reranking in Conversational Agents | 2025 | arxiv | 0.8653 |

Source stats returned by the endpoint. Backend logs showed OpenAlex `503`
failures even though the connector returned empty lists without propagating
errors into `source_stats.error_message`.

| Source | Returned | Latency | Error |
| --- | ---: | ---: | --- |
| openalex | 0 | 0.769962s | null |
| arxiv | 10 | 0.726009s | null |
| openalex | 0 | 0.770017s | null |
| arxiv | 10 | 0.740558s | null |
| openalex | 0 | 0.769470s | null |
| arxiv | 10 | 0.731522s | null |
| openalex | 0 | 0.620773s | null |
| arxiv | 10 | 0.677392s | null |
| openalex | 0 | 0.622737s | null |
| arxiv | 10 | 0.680557s | null |
| openalex | 0 | 0.622298s | null |
| arxiv | 10 | 0.670589s | null |
| refchain | 0 | 0.000017s | refchain seed missing supported identifiers |

## External Access Observed

- OpenAlex Works search: yes. Requests were made, but server logs showed
  `HTTP Error 503: Service Unavailable`. The endpoint still returned HTTP 200
  because the connector fails closed and returned empty OpenAlex results.
- arXiv API search: yes. Requests completed successfully and returned papers.
- OpenAlex reference metadata: no request was made during this run because
  RefChain skipped the top seeds before calling the reference fetcher. The
  selected seed papers came from arXiv and lacked OpenAlex ID or DOI.

## Interpretation

Query Evolution increased candidate coverage from 30 raw / 11 deduplicated papers
to 60 raw / 23 deduplicated papers and introduced additional high-scoring arXiv
results.

RefChain was correctly gated by seed metadata requirements. It did not crash, did
not recurse, and surfaced warnings explaining why no references were fetched.

Known issue from this validation: OpenAlex connector-level failures are visible
in backend logs but not reflected in `source_stats.error_message`, because
`search_openalex` returns an empty list instead of raising. This is acceptable
for fail-closed behavior, but it makes manual diagnostics less explicit.
