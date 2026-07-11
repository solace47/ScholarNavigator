# 评测说明

## 统一口径

离线评测与批量评测共同使用 `scholar_agent.evaluation` 中的结果选择、论文匹配和指标实现。

默认结果策略为 `highly_and_partial`：先取 `highly_relevant`，再取 `partially_relevant`，各类别内按原始 rank 稳定排序。`highly_only` 只取高度相关论文；弱相关、不相关和证据不足论文均不进入正式列表。

论文匹配提取双方全部稳定标识符，任意交集即匹配。支持 DOI、arXiv（忽略版本号，并识别 arXiv DOI）、OpenAlex、Semantic Scholar 和 PubMed 的常见 URL 或前缀。只有双方均无稳定标识符时才按规范化标题和年份匹配。重复预测只计一次，一条预测最多匹配一个 gold。

核心排名指标为 F1@K，同时输出 Precision@K、Recall@K、MRR 和 nDCG@K；默认 K 为 5、10、20。Precision@K 的分母固定为 K。

聚合报告同时包含：

- `success_only_metrics`：仅统计成功且有有效 gold 的案例。
- `end_to_end_metrics`：失败、超时、取消、结果缺失或非法但有 gold 的案例按零分计入。
- `case_statistics`：记录总数、成功、失败、结果缺失、gold 缺失及对应比率。
- `efficiency`：汇总延迟、LLM 调用与 Token、搜索轮次、候选数、返回数、缓存命中和来源错误。无法准确取得的来源调用数记为 0，并附 unavailable warning。

没有有效 gold 的 batch case 记录在 `missing_gold_cases`，不进入两套指标分母；gold 存在但 batch 缺失的案例记录在 `missing_result_cases`，并以零分进入端到端指标。

## 离线消融组

| 分组 | 查询演化 | RefChain |
| --- | --- | --- |
| `baseline` | 关闭 | 关闭 |
| `query_evolution_only` | 开启 | 关闭 |
| `refchain_only` | 关闭 | 开启 |
| `query_evolution_plus_refchain` | 开启 | 开启 |

## 数据格式

离线 fixture 的 `search_cases.jsonl` 每行包含 `query_id`、`query`、`gold_papers` 和 `top_k_values`。批量 qrels 每行包含 `case_id` 与 `relevant_papers`。gold 论文可提供上述任一稳定标识符；没有稳定标识符时必须同时提供标题和年份。`relevance_grade` 大于 0 表示相关，缺省为 1。

## 运行命令

运行确定性 sample fixture 并生成 Markdown 汇总：

```bash
PYTHONPATH=src python scripts/eval_search_service.py \
  --fixtures-dir datasets/eval_fixtures/sample \
  --output-root outputs/eval_runs \
  --run-id sample

PYTHONPATH=src python scripts/summarize_eval_results.py \
  outputs/eval_runs/sample/result.json
```

评测已有 batch 结果：

```bash
PYTHONPATH=src python scripts/evaluate_search_batch.py \
  --batch-results outputs/manual_smoke/results.jsonl \
  --gold datasets/eval_fixtures/manual_smoke/qrels.filled.jsonl \
  --output outputs/manual_smoke/eval.json \
  --k 5 --k 10 --k 20 \
  --result-policy highly_and_partial
```

`--include-partial` 作为旧参数继续等价于 `--result-policy highly_and_partial`。与 `--result-policy highly_only` 同时使用会报错。

## 限制

sample fixture 使用本地假检索器，只验证评测流程、分组开关和输出可复现性，不代表真实 benchmark 性能。当前尚无官方完整 benchmark 的固定版本基线、显著性检验、跨运行方差和分领域切片；manual smoke qrels 也不是官方标注。
