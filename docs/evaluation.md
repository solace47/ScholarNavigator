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

真实四组消融固定使用 AutoScholarQuery 原始顺序开发集 10 条与独立验证集 5 条，来源为 arXiv/OpenAlex，使用 adaptive、balanced、Top-20、关闭 LLM，并限制为两轮、150 候选和 120 秒。阶段诊断事后统计 seed、查询或引用、去重候选、gold、Judgement/Top-K 丢失、API、重试、缓存和模块耗时；gold 不进入在线决策。

2026-07-19 的开发集 baseline 完成，候选 Recall、Recall@20、F1@5/10/20 分别为 0.150、0.125、0.0222/0.0143/0.0179，平均 API 3.1、平均延迟 32.81 秒。OpenAlex 0 次成功，TLS timeout 重试后出现 HTTP 429，随后 9 个 case 进入 cooldown；按实验协议停止其余开发组和全部验证组。当前没有可比较的模块增量，不得据此声明 Query Evolution、RefChain 或组合有效。

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
  --query-adapter-policy adaptive \
  --result-policy highly_and_partial \
  --top-k 20
```

每次运行独立写入 `config.json`、`dataset_report.json`、`results.jsonl`、`metrics.json`、`failures.jsonl` 和 `summary.md`。`--resume` 跳过成功案例、重试失败案例，并在配置签名一致时重新汇总全部结果。

真实响应快照支持 `live`、`record`、`record-missing`、`plan` 和 `replay`。`plan` 完全离线执行真实流水线并输出动态缺键及依赖，采集器串行补齐后再次规划到固定点；`--retry-failed-snapshots` 才会重试已冻结失败。结构合法的 success 或 failed 条目都算覆盖，但分别统计；Replay 缺键、Schema 不兼容或内容哈希不一致时失败，不访问外网：

```bash
PYTHONPATH=src python scripts/run_benchmark.py \
  --dataset auto_scholar_query --offset 0 --limit 10 \
  --sources arxiv,openalex --query-adapter-policy adaptive \
  --run-profile balanced --top-k 20 --max-workers 1 \
  --retrieval-mode replay \
  --snapshot-dir outputs/benchmark_snapshots/autoscholar_qe_refchain_dev10 \
  --run-id qe_refchain_replay_baseline --diagnostics

PYTHONPATH=src python scripts/inspect_benchmark_snapshot.py \
  --snapshot-dir outputs/benchmark_snapshots/autoscholar_qe_refchain_dev10
```

动态组使用 `prepare_benchmark_ablation_snapshots.py` 的保守集中上限执行“规划→采集→再规划”，计划与四组覆盖汇总位于快照目录的 `plans/`。`replay-ready` 不等于外部请求全部成功；只有纯离线 Replay 完成后才标记 `replay-verified` 并生成模块结论。

`cost_report` 在 Replay 中只表示本次执行成本，HTTP、重试和网络等待均为 0；`snapshot_cost_report` 与 `metrics.json.snapshot_costs` 另列快照所记录的 live 请求、重试、错误、限流等待和延迟。两套数字不得混加或把记录成本描述为 Replay 实际成本。

2026-07-20 固定开发集前 10 条的四组快照均达到 `replay-ready` 并完成纯离线 Replay，实际 HTTP、重试和网络等待均为 0。各组必需键的 success/failed 分别为：baseline 27/3、Query Evolution 52/2、RefChain 27/6、组合组 52/8；failed 条目属于可复现覆盖，不表示成功检索。四组 Recall@20 均为 0.1250，F1@20 均为 0.0179。Query Evolution 把候选 Recall 从 0.1500 提高到 0.1875，并新增 197 个唯一候选，但没有新增 gold，结论为 `new_candidates_but_no_gold`；RefChain 的引用响应均为冻结的 OpenAlex 失败，未产生新候选，结论为 `no_action_generated` 和 `source_failure_dominated`。所有结论同时标记 `insufficient_sample` 与 `small_sample_diagnostic_only`，不得外推为模块有效性或正式性能。

覆盖缺口策略使用 arXiv-only 冻结快照对比 `off`、`seed_expansion`、`coverage_gap`。开发集前 10 条中，三组 F1@20/Recall@20 均为 0.0179/0.1250；旧策略生成 24 条查询、53 次记录请求、197 个新增唯一候选，无效候选占比 0.7462，新策略为 10 条、41 次、232 个，占比 0.6584。独立 offset 10 的 5 条验证中，三组均为 0.0190/0.2000；旧策略为 12 条查询、27 次请求、135 个新增唯一候选，无效候选占比 0.8657，新策略为 4 条、18 次、78 个，占比 0.5938。两组策略在开发和验证子集都没有新增 gold，因此每新增 gold 的 API 与延迟为 `null`，产品开关保持默认关闭。

`run_benchmark.py --query-evolution-policy` 记录策略并选择独立快照组；`analyze_query_evolution_policies.py` 输出三组汇总、逐查询诊断和中文摘要。质量门使用事前约束信号，gold 只在全部运行完成后参与上述对比。

初始查询规划另以 arXiv-only、adaptive、balanced、Top-20、关闭 Query Evolution/RefChain/LLM 的冻结快照比较 `current_rules` 与 `facet_balanced` v1.2。开发前 10 条中，两者候选 Recall/F1@20/Recall@20 同为 0.1500/0.0179/0.1250；新策略平均记录请求为 2.6（旧策略 2.7），重复率为 0.2538（旧策略 0.4167）。独立 offset 10 的 5 条验证中，两者为 0.2000/0.0190/0.2000；新策略平均记录请求为 2.6（旧策略 2.4），唯一 gold 均为 1。由于未新增 gold 且成本增加，验收不通过，默认保持 `current_rules`。

`analyze_query_planning_policies.py` 将策略汇总、facet 贡献、逐查询原始/适配查询、候选、事后 gold、重复率、记录成本和无效原因写入 `outputs/benchmark_runs/initial_query_planning_analysis/`。四次 Replay 的实际 HTTP、重试和网络等待均为 0；gold 只在运行后参与诊断。

增加 `--diagnostics` 后，Runner 还会写入 `stage_metrics.json`、`error_analysis.json` 和 `gold_diagnostics.jsonl`。阶段快照只包含论文标识符、标题、年份、来源、子查询 provenance、rank、Judgement 分类与分数，不保存摘要或 Prompt；gold 只在 SearchService 返回后参与统一 identifier matching。

固定开发诊断使用 AutoScholarQuery 原始顺序前 10 条、`top_k=20`、关闭 LLM，并统一使用两轮、150 候选和 90 秒预算。当前完成了 arXiv-only 与三源 baseline；后者受到 Semantic Scholar 429、OpenAlex 400/超时影响，来源错误率为 0.402。Query Evolution 配置启动后仍持续失败，已按公共服务保护要求中止；RefChain 和 full 配置未运行，不生成替代结果。

两组完整结果都显示主要问题是初始检索召回：候选 Recall 分别为 0.150 和 0.170；三源 baseline 的端到端 F1@20 从 0.0179 增至 0.0259，但平均 API 请求从 2.7 增至 8.2、平均延迟从 4.01 秒增至 29.78 秒。10 条数据只用于开发诊断，不代表完整 Benchmark 或比赛成绩。

加入来源查询适配、run 内去重和限流降级后，使用同一 10 条、同一预算完成 A2/B2。A2 相对 A：候选 Recall 0.150→0.025、F1@20 0.0179→0.0083、平均 API 2.7→2.4、来源错误率均为 0、平均延迟 4.01→10.57 秒；延迟增加主要来自 arXiv 的 3 秒请求间隔。B2 相对 B：候选 Recall 0.170→0.045、F1@20 0.0259→0.0163、平均 API 8.2→5.1、来源错误率 0.402→0.059、平均延迟 29.78→19.93 秒，OpenAlex 400 从 10 次降为 0，Semantic Scholar 请求从 51 次降为 8 次。可靠性和成本改善，但该固定小样本上的召回与 F1 下降，不能声明质量提升。

查询适配回归修复后，A3 使用 `hybrid` 在相同前 10 条恢复候选 Recall 至 0.250，Recall@20 为 0.225、F1@20 为 0.0274，平均 API 3.6、平均延迟 32.73 秒、来源错误率为 0。三源 B3 因 Semantic Scholar 连续最终 429 在 9/10 安全停止；其部分指标不得与完整 B/B2 等同。独立原始顺序 10–14 的 5 条验证中，`safe_original` 与 `hybrid` 的候选 Recall 和 Recall@20 均为 0.200，F1@5/10/20 均为 0.0667/0.0364/0.0190；该小样本只说明 hybrid 未丢失 safe-original 候选，不代表总体提升。

A4 在相同开发集重新对比三种策略：safe、hybrid、adaptive 的候选 Recall 分别为 0.150/0.250/0.150，Recall@20 为 0.125/0.225/0.125，F1@20 为 0.0179/0.0274/0.0179，平均 API 为 2.6/3.5/2.7，平均延迟为 34.27/30.96/25.37 秒。adaptive 执行 compact 1/26 次（3.85%），平均新增 20 个唯一候选，事后 gold 增量为 0；来源错误率均为 0。延迟受公开服务波动影响，不应仅凭单次 safe/hybrid 顺序作因果解释。

独立 V2 在三组全部完成后才读取 gold：三种策略的候选 Recall、Recall@20 和 F1@5/10/20 均为 0.200、0.200 和 0.0667/0.0364/0.0190。adaptive 平均 API 2.2，与 safe 相同并低于 hybrid 的 3.0；平均延迟 50.50 秒，低于 hybrid 的 54.00 秒；compact 执行 0/12。该 5 条结果支持保留 adaptive 默认值，但样本太小，不能外推到完整数据集。

M4 使用 arXiv/OpenAlex 和 adaptive：候选 Recall 0.170、Recall@20 0.145、F1@20 0.0259、平均 API 5.7、平均延迟 54.61 秒；compact 执行 1/52，新增 20 个唯一候选且事后 gold 增量为 0。OpenAlex 出现两次最终超时和一次最终 429，来源错误率为 0.0526，HTTP 400 为 0；这次公开服务波动不应归因于 compact 请求。当前环境无 Semantic Scholar Key，未运行三源配置。

Runner 的 `--query-adapter-policy` 支持 `safe_original`、`hybrid` 与 `adaptive`，并把实际值写入 `config.json`。`adaptive` 先执行安全原查询，仅在候选数量、核心维度、约束覆盖或元数据不足时补充核心查询；预算、冷却、等价查询和低信息保留会阻止第二请求。compact 执行率、新增唯一候选与事后 gold 增量进入阶段诊断，gold 不参与在线决策。产品默认使用 `adaptive`。

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

当前真实运行覆盖原始顺序前 5 条链路 smoke、前 10 条开发诊断，以及 offset 10 的 5 条独立策略验证；三源 B3 仅完成 9/10。所有小样本都不能代表完整 Benchmark、比赛成绩或多源长期性能；完整 1000 条基线、重复运行、显著性检验和分领域切片尚未完成。
