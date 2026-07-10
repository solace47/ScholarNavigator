# 评测说明

## fake fixture 的用途和限制

`datasets/eval_fixtures/sample/` 是手写的确定性 fixture，用于验证 SearchService、指标、脚本和三组开关对比：

| 组 | 查询演化 | RefChain |
| --- | --- | --- |
| `baseline` | 关闭 | 关闭 |
| `query_evolution` | 开启 | 关闭 |
| `refchain` | 开启 | 开启 |

fixture retriever 和 reference fetcher 不访问真实 connector。其分数只说明离线链路可复现，不能作为真实检索性能、正式 benchmark 或比赛成绩。

运行 fake fixture：

```bash
PYTHONPATH=src python scripts/eval_search_service.py \
  --fixtures-dir datasets/eval_fixtures/sample \
  --output-root outputs/eval_runs \
  --run-id sample

PYTHONPATH=src python scripts/summarize_eval_results.py \
  outputs/eval_runs/sample/result.json
```

输出位于 `outputs/eval_runs/<run-id>/result.json` 和同目录的 `summary.md`。

## 真实 batch 评测

`run_search_batch.py` 会调用真实 SearchService 和所选学术来源；`evaluate_search_batch.py` 只读取已生成结果与本地 qrels，不访问网络。

```bash
PYTHONPATH=src python scripts/run_search_batch.py \
  --input datasets/eval_fixtures/manual_smoke/queries.jsonl \
  --output outputs/manual_smoke/results.jsonl \
  --sources arxiv,semantic_scholar \
  --top-k 5 \
  --run-profile fast \
  --current-year 2026 \
  --dump-ranked-candidates

PYTHONPATH=src python scripts/evaluate_search_batch.py \
  --batch-results outputs/manual_smoke/results.jsonl \
  --gold datasets/eval_fixtures/manual_smoke/qrels.filled.jsonl \
  --output outputs/manual_smoke/eval.json \
  --k 1 \
  --k 5 \
  --include-partial
```

batch 结果写入 `--output` 指定的 JSONL；可选候选诊断写入同目录 `ranked_candidates.jsonl`；评分写入评测命令的 `--output`。

## gold 与 qrels 格式

离线 fixture 使用 `search_cases.jsonl`，每行包含：

```json
{"query_id":"q1","query":"...","gold_papers":[{"doi":"10.x/example","relevance_grade":2}],"top_k_values":[5,10,20]}
```

真实 batch qrels 每行包含：

```json
{"case_id":"q1","relevant_papers":[{"title":"...","year":2025,"doi":"10.x/example","arxiv_id":null,"semantic_scholar_id":null}]}
```

`relevance_grade` 大于 0 表示相关；缺省为 1。`--include-partial` 会把部分相关结果接在高度相关结果之后，否则只评高度相关结果。

## 当前指标

- 排名质量：Recall@K、Precision@K、MRR、nDCG@K；nDCG 支持分级相关性。
- 离线 fixture 额外统计原始、去重和排序候选数、重复率、各来源返回量、warning 数、来源错误率和失败率。
- 当前脚本尚未实现比赛核心 F1，也没有统一汇总 API 调用、Token 和端到端延迟。

## 当前匹配规则

离线 evaluator 的 `canonical_paper_id` 采用单一优先级：DOI（arXiv DOI 会转为 arXiv ID）→ arXiv ID（去版本号）→ OpenAlex ID → Semantic Scholar ID → PubMed ID → 规范化 title+year。

batch evaluator 当前使用标识符集合匹配：

1. 识别 DOI、arXiv ID 和 Semantic Scholar ID，并把 `10.48550/arXiv.*` DOI 同时映射为 arXiv ID。
2. 预测与 gold 只要任一方含受支持标识符，就必须有标识符交集，不再回退标题。
3. 双方都没有受支持标识符时，才使用规范化 title+year。
4. 同一 gold 每个查询最多匹配一次。

OpenAlex ID 和 PubMed ID 虽可写入 batch qrels Schema，但当前 batch 匹配器尚未使用，这是已知差异。

## 已知评测缺口

- 尚未接入官方或完整 LitSearch、AstaBench、PaSa 等 benchmark 并形成可复现基线。
- `manual_smoke` qrels 是本地人工集合，不是官方标注；未验证覆盖度和一致性。
- 两套 evaluator 的匹配规则和失败样本聚合口径尚未统一。
- failed batch case 会从指标平均中排除，需另看 `failed_cases`，避免只读均值。
- 没有显著性检验、跨运行方差、分领域切片和正式效率报告。
- fake fixture 与 mock connector 测试不会验证上游 API 的实时质量或稳定性。
