# 评测说明

## 统一口径

离线评测与批量评测共同使用 `scholar_agent.evaluation` 中的结果选择、论文匹配和指标实现。

默认结果策略为 `highly_and_partial`：先取 `highly_relevant`，再取 `partially_relevant`，各类别内按原始 rank 稳定排序。`highly_only` 只取高度相关论文；弱相关、不相关和证据不足论文均不进入正式列表。

论文匹配提取双方全部稳定标识符，任意交集即匹配。支持 DOI、arXiv（忽略版本号，并识别 arXiv DOI）、OpenAlex、Semantic Scholar 和 PubMed 的常见 URL 或前缀。只有双方均无稳定标识符时才按规范化标题和年份匹配。重复预测只计一次，一条预测最多匹配一个 gold。

核心排名指标为 F1@K，同时输出 Precision@K、Recall@K、MRR 和 nDCG@K；默认 K 为 5、10、20。Precision@K 的分母固定为 K。

跨来源论文身份由 `scholar_agent.core.identity` 统一解析，并同时用于候选去重、API 结构化 ID 和离线 gold 匹配。DOI、arXiv（忽略版本号）、OpenAlex、Semantic Scholar 与 PubMed 的规范化稳定标识相交时直接判定同一论文；没有共同稳定标识时，只有规范标题、年份和至少一名共同作者同时一致才合并。稳定标识冲突始终保持分离。规则不使用 gold、查询文本或相似度阈值；去重函数为每次合并保留规则、共享标识和冲突标识审计证据。

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

## Judgement 小样本校准

规则校准只在冻结的 arXiv Retrieval Snapshot 候选上重算 Judgement 与既有 Reranker，不重新查询、不读取 gold 生成规则。开发集固定为 offset 0、limit 10；运行前冻结 128 个通用参数组合，以 F1@20、Recall@20、gold Judgement false negative、Precision@20、MRR、默认参数距离和配置哈希依次稳定选型。配置冻结后，验证集 offset 10、limit 5 只运行一次。

开发集选出的 `calibrated_rules_v1` 将高度相关阈值由 0.72 调为 0.68、标题主题权重由 0.12 调为 0.10。开发集 F1@20、Precision@20、Recall@20 和 gold false negative 均不变，MRR 为 0.027692→0.045000，平均返回量为 11.4→10.2；独立验证集全部排名指标和 gold false negative 均不变，平均返回量为 11.2→9.4。候选召回完全一致，Replay 的 HTTP、重试和网络等待均为 0。该结果只通过“无回归、返回量受控”的候选门槛，不证明 F1 或 false negative 已改善，产品默认保持 `current_rules`。

复现命令：

```bash
PYTHONPATH=src python scripts/calibrate_judgement.py \
  --dataset auto_scholar_query --offset 0 --limit 10 \
  --validation-offset 10 --validation-limit 5 \
  --snapshot-dir outputs/benchmark_snapshots/autoscholar_qe_gap_dev10_20260720 \
  --validation-snapshot-dir outputs/benchmark_snapshots/autoscholar_qe_gap_val5_20260720 \
  --output outputs/benchmark_runs/judgement_calibration --resume
```

输出包含 manifest、冻结配置、开发网格、阈值敏感性、开发/验证对比和候选级诊断。Benchmark 非 gold 候选只标为“非 gold 候选”，不能视为真实负例。

### 固定保留集复核

2026-07-20 又在未参与选参的 AutoScholarQuery `offset=20, limit=30` 上进行一次固定复核。实验使用 arXiv、`current_rules` 初始规划、adaptive、关闭 Query Evolution/RefChain/LLM、balanced、Top-20 和 `highly_and_partial`；两种 Judgement 复用同一 Retrieval Snapshot，候选指纹与 candidate Recall 完全一致，回放 HTTP、重试和网络等待均为 0。

65 个 gold 中只有 1 个进入初始候选，candidate Recall 为 0.008333。`current_rules` 的 F1@5/10/20 为 0.007407/0.004762/0.002778，Recall@20 为 0.008333、MRR 为 0.016667、nDCG@20 为 0.008210；`calibrated_rules_v1` 将唯一召回的 gold 判为弱相关，各项最终排名指标均为 0，gold Judgement false negative 为 1/1。30 条查询的配对 bootstrap 使用固定 seed 20260720 和 5000 次重采样，F1@20 差值为 -0.002778，95% 区间 `[-0.008333, 0]`；Recall@20、MRR、nDCG@20 的区间也都包含 0。该保留集不支持校准配置具有稳定优势，产品默认继续使用 `current_rules`；64/65 gold 在 Judgement 前已缺失，下一瓶颈是 Retrieval。

复现命令：

```bash
PYTHONPATH=src python scripts/run_judgement_holdout.py \
  --snapshot-dir outputs/benchmark_snapshots/autoscholar_holdout30_20260720 \
  --output outputs/benchmark_runs/holdout30_baseline
```

产物包含 `comparison.json`、`comparison.md`、`per_query_diagnostics.jsonl`、`error_summary.json` 和 `snapshot_coverage.json`。查询切片只使用规则解析出的结构、方法、数据集、必要词、论文类型和查询长度，不使用 gold 定义分组；本次样本与极低候选召回不支持总体显著性或真实性能声明。

同一 holdout30 的事后 Retrieval 审计按 arXiv ID 冻结 65 个 gold 的标题、摘要、作者、年份、分类与 DOI，并对实际 adapted query、exact title、规范化标题和标题核心词分别 Record/Replay。ID 与 exact-title 可用率均为 65/65；当前查询扩到 Top-20/50/100 只找回 1/5/7，规范化标题找回 58/65，核心词在 Top-20/50/100 找回 60/64/64。64 个原始未召回 gold 的原因分布为：查询未匹配 25、过度约束 23、词汇错配 10、21–50 名截断 4、51–100 名低排位 2；adapter 术语丢失、元数据不匹配和 gold 级来源不可用均为 0。主导原因族是查询构造（48/64），只扩大候选深度最多额外恢复 6 个。

审计快照的 274 个必需键包含 269 个成功和 5 个冻结失败，记录 286 次请求、12 次重试；纯离线 Replay 命中 274 个键，HTTP、重试和网络等待均为 0。oracle 只用于 SearchService 完成后的诊断，不进入生产查询。复现时依次运行 `scripts/audit_retrieval_recall.py --mode plan`、`--mode record-missing` 和 `--mode replay`；最终产物位于 `outputs/benchmark_runs/retrieval_recall_audit/`。

## 数据格式

离线 fixture 的 `search_cases.jsonl` 每行包含 `query_id`、`query`、`gold_papers` 和 `top_k_values`。批量 qrels 每行包含 `case_id` 与 `relevant_papers`。gold 论文可提供上述任一稳定标识符；没有稳定标识符时必须同时提供标题和年份。`relevance_grade` 大于 0 表示相关，缺省为 1。

## 公开 Benchmark

当前接入 `benchmark/AutoScholarQuery_test.jsonl`：1000 条 test 查询、2403 条 gold 论文，所有 gold 均带 arXiv ID；原数据没有分级相关性，适配器按其二元 gold 关系使用 `relevance_grade=1`。查询、标题、标识符和源文件顺序均保持不变。仓库中的 LitSearch 目录目前只有代码，没有本地 query/corpus 数据，因此未建立 LitSearch Adapter。

### BEIR SciFact 泛化适配

`beir_scifact` 适配器只接受 BEIR 官方 TU Darmstadt `scifact.zip` 的解压目录或归档，固定使用 `qrels/test.tsv`，并按 query ID 的 SHA-256 升序稳定抽取 50 条 query。每条正相关关系保留 qrel grade；当前 SciFact test qrels 的正相关 grade 为 1，适配器仍将 grade 写入 gold metadata，便于离线敏感性复核。语料的 `_id` 同时写入 gold 的顶层 `s2orc_corpus_id` 与审计 metadata，并由统一身份实现规范化为 `s2orc:<corpus-id>`；Semantic Scholar connector 显式请求官方 `corpusId` 字段并保留来源返回的精确值，字段采集版本为 `search-v2`。该标识只允许精确 ID 匹配，候选缺失 Corpus ID 时不会通过标题推断。缺失语料映射会在加载阶段失败，不会被计为检索未命中。官方来源、固定 BEIR commit、归档校验值和抽样口径记录在 `benchmark/beir_scifact_manifest.json`；原始归档只保存在被忽略的本地输入目录中。

SciFact 的 evaluator 另使用版本化 `benchmark/beir_scifact_s2_crosswalk.json`：采集器只向 Semantic Scholar 官方 Graph API 发出 `CorpusId` 精确查询，并只保留响应中的 `paperId`、`corpusId` 与 `externalIds` 稳定标识。crosswalk 由 `scripts/build_scifact_crosswalk.py` 依次执行 `preflight`、串行 `record-missing` 和严格 `replay` 构建；它只丰富 `EvalGoldPaper`，不进入查询、Prompt、检索、排序、候选补全或生产 API。统一 `identity.py` 对 DOI、arXiv、PMID、Semantic Scholar ID 和 S2ORC Corpus ID 做精确规范化；任一同类标识冲突均阻止匹配，标题不会为带 Corpus ID 的记录兜底。官方精确查询的 `unavailable`/`failed` 终态在 gold 诊断中单列为 `identity_crosswalk_*`，不进入 Retrieval miss 或 Recall/F1 分母；旧数据不含 crosswalk metadata 时仍保持原有身份口径。

SciFact BM25 上界审计位于 `scripts/audit_scifact_bm25.py` 和 `scholar_agent.evaluation.scifact_bm25_audit`，不接入 `SearchService`。索引固定覆盖官方 5,183 篇 corpus，文档文本为未加权的 `title + abstract`；查询只使用 manifest 中 50 条原始 query text。实现固定为 `rank_bm25==0.2.2` 的 `BM25Okapi` 默认参数（`k1=1.5`、`b=0.75`、`epsilon=0.25`），tokenizer 仅做 Unicode `\w+` 与 casefold，不使用停用词、词干、扩展或 gold 信息。20/50/100/200 指标均从同一次完整语料排序的前缀计算，以 Corpus ID 精确评测；外部基线交集使用冻结 Replay 的 `initial_retrieval` 候选和同一统一身份规则。配置、输入哈希、汇总与产物哈希记录在 `benchmark/beir_scifact_bm25_audit_manifest.json`。该结果仅表示语料已知时的离线词法召回上界，不是产品可实现成绩。

SciFact 跨源可索引性审计位于 `scripts/audit_scifact_source_index.py` 和 `scholar_agent.evaluation.scifact_source_index_audit`，同样与产品检索路径隔离。它只对 crosswalk 状态为 success 的 gold 使用来源支持的稳定标识：arXiv `id_list`、OpenAlex 单 work lookup、Semantic Scholar paper stable-ID lookup、PubMed PMID EFetch；不发送标题或 query，不使用模糊匹配。四源结果通过统一 `identity.py` 验证，同类稳定标识冲突保持不匹配。`record-missing` 串行执行并为每个请求保存不可变 Snapshot；`replay` 严格校验 request/key/content hash，外部失败与 not-found 分开。总体互斥归因优先保留正常查询命中，其次为精确可定位但查询未召回；只有全部适用源明确 not-found 才归为未定位，任何失败均保持不可判定。该 oracle 仅量化来源收录与查询命中缺口，不向查询、Prompt、候选或生产 API 注入 gold。

复现命令：

```bash
PYTHONPATH=src python scripts/audit_scifact_bm25.py \
  --dataset-path outputs/benchmark_inputs/beir_scifact/scifact.zip \
  --sample-manifest benchmark/beir_scifact_sample_manifest.json \
  --crosswalk benchmark/beir_scifact_s2_crosswalk.json \
  --external-run-dir outputs/benchmark_runs/beir_scifact_crosswalk_replay_commit_47c2168/current_rules_50_replay_r1 \
  --output-dir outputs/benchmark_runs/beir_scifact_bm25_audit_47c2168/run1
```

检查与运行示例（需先取得官方归档）：

```bash
PYTHONPATH=src python scripts/inspect_benchmark.py \
  --dataset beir_scifact \
  --path outputs/benchmark_inputs/beir_scifact/scifact.zip

PYTHONPATH=src python scripts/run_benchmark.py \
  --dataset beir_scifact \
  --dataset-path outputs/benchmark_inputs/beir_scifact/scifact.zip \
  --dataset-split test --run-id beir_scifact_current_rules_50 \
  --retrieval-mode record-missing \
  --snapshot-dir outputs/benchmark_snapshots/beir_scifact_current_rules_50
```

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

纯 `replay` 按冻结组中的请求 key 重放每个已记录终态，不读取或更新 live connector 的进程级/运行级 cooldown。一个失败快照仍以原错误返回并计为失败，但不会抑制后续已有成功快照；组内必需 key 缺少文件时明确失败，未进入冻结组的历史未执行路径记为 `snapshot_key_not_recorded`，不会伪装成缺失请求。`record` 与 `record-missing` 继续使用 live 限流、cooldown 和熔断语义。

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

`controlled_relaxation` v1.4 的规则只在 AutoScholarQuery `offset=50, limit=20` 开发集确定，随后冻结，并在 `offset=70, limit=20` 独立验证集运行一次。配置仍为 arXiv-only、adaptive、balanced、Top-20、`highly_and_partial`，关闭 Query Evolution、RefChain 与 LLM；两种策略使用隔离快照，最终 Replay 的 HTTP、重试和网络等待均为 0。开发集上旧/新策略的候选 Recall、唯一 gold、F1@5/10/20、P@20、R@20、MRR、nDCG@20 均分别为 0.1750、4、0/0.0083/0.0093、0.0050、0.0750、0.0113、0.0229；平均记录 API 为 2.40/2.45，唯一候选为 626/682，重复率为 0.3479/0.2745。验证集两组的候选 Recall、唯一 gold、F1@5/10/20、P@20、R@20 均为 0.1500、4、0.0143/0.0167/0.0139、0.0075、0.1000；旧/新的 MRR 为 0.0219/0.0183，nDCG@20 为 0.0356/0.0329，平均记录 API 均为 2.80，唯一候选为 689/762，重复率为 0.3848/0.3196。补充查询在验证集新增 221 个独占候选但新增 gold 为 0，因此未通过“至少新增一个 gold”的首要门槛，默认继续使用 `current_rules`。分析产物位于 `outputs/benchmark_runs/controlled_relaxation_analysis/`；该 20+20 小样本不代表完整 Benchmark。

来源互补性实验固定 `current_rules`、adaptive、balanced、Top-20，关闭 Query Evolution、RefChain 与 LLM，在开发集 `offset=90, limit=20` 冻结流程后，只对独立验证集 `offset=110, limit=20` 评估一次。验证集 arXiv-only 与 arXiv+OpenAlex 的候选 Recall、F1@20、R@20、MRR、nDCG@20 完全相同，分别为 0.0792、0.0133、0.0792、0.0205、0.0330，均找回 3 个唯一 gold；OpenAlex 没有成功响应或独占 gold，其最终执行路径记录错误率为 1.0。双源没有新增 gold，未形成 `high_recall` 候选，产品默认不变。最终 Replay 的 HTTP、重试和网络等待均为 0；五项产物位于 `outputs/benchmark_runs/source_complementarity/`。该 20+20 小样本只能说明当前失败环境下没有互补性证据，不能推出 OpenAlex 长期无贡献。

`disjunctive_facets` v1.5 在来源无关的规划层保留原查询，再生成最多一条 4–8 个可靠 topic/method/dataset/task 词的 `any` 查询及一条可选的“topic + 最佳分面”查询；显式 must-have 始终在 OR 组外保持硬约束。规则在 AutoScholarQuery `offset=130, limit=20` 开发集冻结，并在 `offset=150, limit=20` 独立验证集只回放一次。验证集相对 `current_rules` 的候选 Recall 为 0.0750→0.1000、唯一 gold 为 2→3，F1@20 与 R@20 分别保持 0.0093 和 0.0750，平均记录 API 为 2.40→2.50，弱相关与无关候选占比为 0.5377→0.5254；全部预设门槛通过。OR 子查询产生 153 个独占候选但事后独占 gold 为 0，新增 gold 来自整体规划组合，故策略仍保持实验状态且产品默认继续使用 `current_rules`。四次最终 Replay 的 HTTP、重试和网络等待均为 0；分析产物位于 `outputs/benchmark_runs/disjunctive_facets_analysis/`，20+20 小样本不代表完整 Benchmark。

预注册的 AutoScholarQuery `offset=170, limit=40` 保留集在冷却后按原协议续跑完成。三轮动态计划最终补齐 `current_rules` 107 个键与 `disjunctive_facets` 99 个键；两组 Replay 均为 40/40 成功，执行期 HTTP、重试和网络等待为 0。相对基线，析取策略的候选 Recall 为 0.1104→0.1229，唯一 gold 均为 8，但 F1@20、Recall@20、MRR、nDCG@20 分别由 0.0154、0.1021、0.0516、0.0542 降至 0.0131、0.0896、0.0266、0.0389。OR 查询产生 276 个独占候选和 1 个事后独占 gold，但整体未净增 gold；预设门槛未通过，策略继续仅作实验选项，不进入 high_recall profile，也不据此保留集调参。固定种子成对 bootstrap 与逐查询诊断位于 `outputs/benchmark_runs/disjunctive_holdout40/`。

`current_plus_disjunctive` v1.6 不替换旧规划：先按原顺序执行全部 `current_rules` 查询，只在高置信词和剩余候选预算允许时追加一条 OR，旧候选优先。AutoScholarQuery 开发集 `offset=210, limit=20` 中，候选 Recall、唯一 gold、F1@5/10/20、R@20 均保持 0.1250、4、0.0595/0.0341/0.0184、0.1250；OR 新增 107 个独占候选但无新增 gold，MRR 为 0.1167→0.1083。规则冻结后在独立验证集 `offset=230, limit=20` 只评估一次：候选 Recall、唯一 gold、F1@5/10/20、R@20、MRR、nDCG@20 均分别保持 0.0625、2、0.0278/0.0162/0.0089、0.0625、0.0350、0.0391；全部 2 个基线 gold 均保留，OR 增加 105 个独占候选但净新增 gold 为 0。平均记录 API 为 2.45→3.40，弱相关与无关率为 0.6860→0.6930；候选未通过“至少新增 1 个 gold”，停止继续扩展 OR，默认保持 `current_rules`。四组最终 Replay 的 HTTP、重试和网络等待均为 0，产物位于 `outputs/benchmark_runs/current_plus_disjunctive_analysis/final_analysis/`。

`facet_union` v1.7 完整保留基线，再按 dataset、method、task、topic 的稳定优先级追加至多一条独立分面查询。开发集 `offset=250, limit=20` 的候选 Recall 和唯一 gold 均保持 0.1625 和 6；分面查询新增 387 个独占候选但独占 gold 为 0。冻结后在验证集 `offset=270, limit=20` 只评估一次：候选 Recall、唯一 gold、F1@5/10/20、R@20 均保持 0.1625、5、0.0587/0.0337/0.0228、0.1625，基线 5 个 gold 全部保留，但 MRR 为 0.0979→0.0965、nDCG@20 为 0.0933→0.0923；391 个独占候选没有新增 gold。平均记录 API 为 2.40→3.55，弱相关与无关率为 0.5562→0.6596。预设验收未通过，`facet_union` 仅保留为实验策略，产品默认继续使用 `current_rules`；规则式 OR、放宽和分面组合规划至此冻结，后续转向语义规划或其他语义检索。四组最终 Replay 的 HTTP、重试和网络等待均为 0，产物位于 `outputs/benchmark_runs/facet_union_analysis/final_analysis/`。

`concept_projection` v1.8.1 是默认关闭的固定预算实验：它只按原文顺序选取规则式 Query Analysis 已有的 must-have/topic 片段，按大小写和边界标点去重，排除否定、格式、时间与数量约束，并替换最低优先级派生查询。SciFact 50 条、AutoScholarQuery 开发 0/10 与验证 10/5 的每个配对 case 都保持相同规划查询数且原查询始终首位；投影分别实际替换 20/10/5 条。正式 Replay 中，SciFact 的候选 Recall/Recall@20/F1@20/唯一 gold 在旧新策略间同为 0.1220/0.1220/0.0116/5，验证集同为 0.2000/0.2000/0.0190/1。开发集候选 Recall 与唯一 gold 同为 0.1500/3，但 Recall@20 从 0.1250 降至 0.0250、F1@20 从 0.0179 降至 0.0083；唯一退化查询的已召回 gold 从第 13 位降到第 24 位，该查询在双方来源调用均完整的配对子集中。SciFact/开发/验证双方完整子集分别为 49/50、8/10、4/5，其余结果保留为外部失败下界；全部两轮 Replay 的 HTTP、重试和网络等待均为 0，逐 gold 产物字节一致。策略没有跨数据集净增且开发集退化，继续默认关闭；正式产物位于 `outputs/benchmark_runs/concept_projection_0a0feb6_v3/`。

`llm_semantic` 评测固定使用同一 arXiv-only、adaptive、balanced、Top-20 配置，开发集为 offset 0 的 10 条，独立验证集为 offset 10 的 5 条；每例最多一次温度为 0 的 LLM 调用和两条补充查询。LLM 规划快照与检索快照独立冻结，动态计划先补 LLM 键再发现检索键；最终比较必须同时使用 LLM replay 与 retrieval replay，执行期两类网络请求、重试和网络等待均为 0。当前进程的 LLM provider 为 disabled，因此只重放了 `current_rules` 基线，未运行或伪造 LLM 指标。开发/验证基线的候选 Recall、F1@20、Recall@20 分别为 0.1500/0.0179/0.1250 和 0.2000/0.0190/0.2000；分析产物位于 `outputs/benchmark_runs/llm_query_planning_analysis/`，验收状态为未执行。

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

当前真实运行还包括一次 offset 20 的 30 条固定保留集复核，但只使用 arXiv，且候选阶段只召回 1/65 gold。所有子集都不能代表完整 Benchmark、比赛成绩或多源长期性能；完整 1000 条基线、重复运行和稳定的多领域统计尚未完成。
