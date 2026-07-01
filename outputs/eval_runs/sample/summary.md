# Offline Evaluation Summary

Query count: 1

| Group | R@5 | R@10 | R@20 | P@5 | P@10 | P@20 | MRR | nDCG@5 | nDCG@10 | nDCG@20 | Raw | Dedup | Warnings | Source Error Rate |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| baseline | 0.333 | 0.333 | 0.333 | 0.200 | 0.100 | 0.050 | 1.000 | 0.542 | 0.542 | 0.542 | 3 | 1 | 1 | 0.333 |
| query_evolution | 0.667 | 0.667 | 0.667 | 0.400 | 0.200 | 0.100 | 1.000 | 0.884 | 0.884 | 0.884 | 6 | 2 | 1 | 0.167 |
| refchain | 1.000 | 1.000 | 1.000 | 0.600 | 0.300 | 0.150 | 1.000 | 1.000 | 1.000 | 1.000 | 8 | 3 | 1 | 0.143 |

## Per Query

### sample_llm_rerank

latest LLM reranking methods for scientific literature retrieval

| Group | Ranked IDs | Warnings |
| --- | --- | --- |
| baseline | doi:10.123/baseline | fixture simulated source warning |
| query_evolution | doi:10.123/baseline, doi:10.123/evolved | fixture simulated source warning |
| refchain | doi:10.123/baseline, doi:10.123/evolved, doi:10.123/refchain | fixture simulated source warning |
