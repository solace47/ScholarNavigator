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
- `efficiency`：直接读取 SearchService/API 的真实 CostReport，输出平均总 API、检索 API、引用 API、重试、错误、缓存命中、限流等待、LLM 调用和 LLM Token；不再通过 `source_stats` 条数推算请求数。

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

## 公开 Benchmark

当前接入 `benchmark/AutoScholarQuery_test.jsonl`：1000 条 test 查询、2403 条 gold 论文，所有 gold 均带 arXiv ID；原数据没有分级相关性，适配器按其二元 gold 关系使用 `relevance_grade=1`。查询、标题、标识符和源文件顺序均保持不变。仓库中的 LitSearch 目录目前只有代码，没有本地 query/corpus 数据，因此未建立 LitSearch Adapter。

数据检查不访问外网：

```bash
PYTHONPATH=src python scripts/inspect_benchmark.py \
  --dataset auto_scholar_query
```

可恢复的真实运行示例：

```bash
PYTHONPATH=src python scripts/run_benchmark.py \
  --dataset auto_scholar_query \
  --limit 10 \
  --output-root outputs/benchmark_runs \
  --run-id autoscholar_smoke \
  --run-profile balanced \
  --sources openalex,arxiv,semantic_scholar \
  --result-policy highly_and_partial \
  --top-k 20
```

每次运行独立写入 `config.json`、`dataset_report.json`、`results.jsonl`、`metrics.json`、`failures.jsonl` 和 `summary.md`。`--resume` 跳过成功案例、重试失败案例，并在配置签名一致时重新汇总全部结果。

增加 `--diagnostics` 后，Runner 还会写入 `stage_metrics.json`、`error_analysis.json` 和 `gold_diagnostics.jsonl`。阶段快照只包含论文标识符、标题、年份、来源、子查询 provenance、rank、Judgement 分类与分数，不保存摘要或 Prompt；gold 只在 SearchService 返回后参与统一 identifier matching。

固定开发诊断使用 AutoScholarQuery 原始顺序前 10 条、`top_k=20`、关闭 LLM，并统一使用两轮、150 候选和 90 秒预算。当前完成了 arXiv-only 与三源 baseline；后者受到 Semantic Scholar 429、OpenAlex 400/超时影响，来源错误率为 0.402。Query Evolution 配置启动后仍持续失败，已按公共服务保护要求中止；RefChain 和 full 配置未运行，不生成替代结果。

两组完整结果都显示主要问题是初始检索召回：候选 Recall 分别为 0.150 和 0.170；三源 baseline 的端到端 F1@20 从 0.0179 增至 0.0259，但平均 API 请求从 2.7 增至 8.2、平均延迟从 4.01 秒增至 29.78 秒。10 条数据只用于开发诊断，不代表完整 Benchmark 或比赛成绩。

加入来源查询适配、run 内去重和限流降级后，使用同一 10 条、同一预算完成 A2/B2。A2 相对 A：候选 Recall 0.150→0.025、F1@20 0.0179→0.0083、平均 API 2.7→2.4、来源错误率均为 0、平均延迟 4.01→10.57 秒；延迟增加主要来自 arXiv 的 3 秒请求间隔。B2 相对 B：候选 Recall 0.170→0.045、F1@20 0.0259→0.0163、平均 API 8.2→5.1、来源错误率 0.402→0.059、平均延迟 29.78→19.93 秒，OpenAlex 400 从 10 次降为 0，Semantic Scholar 请求从 51 次降为 8 次。可靠性和成本改善，但该固定小样本上的召回与 F1 下降，不能声明质量提升。

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

sample fixture 使用本地假检索器，只验证评测流程、分组开关和输出可复现性，不代表真实 benchmark 性能。

当前真实运行只覆盖 AutoScholarQuery 原始顺序前 5 条链路 smoke，以及固定前 10 条的开发诊断对比。这些样本都不能代表完整 Benchmark、比赛成绩或多源长期性能；完整 1000 条基线、重复运行、显著性检验和分领域切片尚未完成。
