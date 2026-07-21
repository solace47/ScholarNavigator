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

### 受约束 LLM 改写的离线因果审计

`scripts/audit_llm_constrained_rewrite.py` 只读取成对的 Benchmark Replay 与
Retrieval Snapshot，不初始化 SearchService、connector 或 LLM。审计按
`(case, source)` 重建原始查询、被替换派生查询和已接受改写的实际列表；只有
三者已执行请求均成功，且基线/实验中的原始查询终态与候选身份顺序一致时，
才计算独立候选、独立 gold 与反事实结果。fallback、校验拒绝、失败请求、
原始列表漂移均单独标记为不可归因。

反事实以基线去重后候选为冻结底座：先移除仅由被替换查询贡献的候选，再加入
改写列表，继续使用统一身份、`current_rules` judgement/reranker、候选预算、
正式结果过滤与 Top-20 指标。审计同时提供逐源单因素反事实；它们是诊断结果，
不得描述为线上可实现成绩。输出不含时间戳，两次相同输入应产生字节一致的
`case_audit.jsonl` 与 `aggregate.json`。

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

`local_bm25` 将相同词法语义作为默认关闭的正式 connector 接入统一 SearchService，但不会读取上述审计的 qrels、crosswalk 或结果。Benchmark 必须显式传入 JSONL 语料路径、`document_id/title/abstract` 字段及 document-ID 身份类型；SciFact 固定把官方 `_id` 原样映射为 S2ORC Corpus ID、摘要字段映射为 `text`。索引缓存与 Snapshot connector version 同时绑定语料 SHA-256、字段配置及 `k1=1.5、b=0.75、epsilon=0.25`，查询只来自 `current_rules` 的正常 subquery/adapter 路径。该来源与四个产品默认来源共享 200 篇全局候选预算、统一身份去重、Judgement、排序、过滤和 Top-20，不因本地语料增加预算。

固定 SciFact 50 条配对中，四源与五源组复用了逐 key 完全相同的 262 个冻结外部 required keys；五源组只增加 101 个本地检索 key。42 个 evaluator 可评估 gold 中，候选命中从 5 增至 32（宏平均 Candidate Recall `0.1220→0.7561`），其中本地来源独立新增 27 个；最终返回 gold 从 5 增至 20，宏平均 Recall@20 `0.1220→0.4634`、F1@20 `0.0116→0.0462`，可评估 query 的 Recall/F1 胜负均为 `14/27/0`。本地列表共返回 2,020 个候选，统一身份去重后保留 1,192 个；32 个本地命中 gold 中 20 个进入最终返回，6 个被判 weak、6 个被判 irrelevant 且排在 Top-20 外。最终安全 JSON/gzip 缓存实现的冷索引构建约 1.03 秒、缓存加载约 0.54 秒，101 次本地查询记录延迟合计约 2.49 秒；两次 Replay 都是 0 HTTP、0 Snapshot 写入且核心指标、逐 query 指标和逐 gold 诊断一致。由于冻结外部响应包含 2 个失败终态（OpenAlex/Semantic Scholar 各 1 个 required key），四源绝对值仍是外部可用性下界；但两组外部 key/响应完全配对，因此观察到的净增可归因于本地语料来源。该成绩表示“已提供 SciFact 官方语料”的封闭语料检索，不是开放网络成绩，connector 继续默认关闭。

SciFact 跨源可索引性审计位于 `scripts/audit_scifact_source_index.py` 和 `scholar_agent.evaluation.scifact_source_index_audit`，同样与产品检索路径隔离。它只对 crosswalk 状态为 success 的 gold 使用来源支持的稳定标识：arXiv `id_list`、OpenAlex 单 work lookup、Semantic Scholar paper stable-ID lookup、PubMed PMID EFetch；不发送标题或 query，不使用模糊匹配。四源结果通过统一 `identity.py` 验证，同类稳定标识冲突保持不匹配。`record-missing` 串行执行并为每个请求保存不可变 Snapshot；`replay` 严格校验 request/key/content hash，外部失败与 not-found 分开。总体互斥归因优先保留正常查询命中，其次为精确可定位但查询未召回；只有全部适用源明确 not-found 才归为未定位，任何失败均保持不可判定。该 oracle 仅量化来源收录与查询命中缺口，不向查询、Prompt、候选或生产 API 注入 gold。

SciFact 的 current-rules 查询深度审计位于 `scripts/audit_scifact_query_depth.py` 和 `scholar_agent.evaluation.scifact_query_depth_audit`。请求计划只读取冻结 Benchmark 的 `initial_retrieval.retrieval_calls`，保留全部 source-adapted query 及原顺序，不含 case 或 gold 字段。arXiv/OpenAlex 单次请求 200 条，Semantic Scholar/PubMed 固定读取 offset 0/100 两页；20/50/100/200 曲线只取同一有序结果的前缀。Record 对每页使用可终止进程隔离、父级来源限流和连续两次 429 熔断；Replay 严格校验全部 request/key，只在返回结果后以统一精确稳定标识匹配 gold。外部失败、部分分页和 source outage 均保持不可判定，不能转为深度 200 miss。候选曲线同时给出去重后未裁剪池与冻结 200 篇全局预算后的指标，最终 Top-20 继续使用现有 judgement/reranking。固定输入、采集终态、成本及结果哈希记录在 `benchmark/beir_scifact_query_depth_audit_manifest.json`。

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

`--ranking-policy rrf_fusion` 是默认关闭的纯排序消融。它不改变查询、请求、候选预算、Judgement、过滤或指标；对每个实际 `(source, adapted_query)` 响应列表使用固定 `k=60`、等权 RRF，同列表重复论文只贡献最佳名次，现有综合分只作同分裁决。正式比较必须对基线与实验的排序前候选及 Judgement 状态逐 case 做完全一致性校验；列表 provenance 缺失或非法时该集合不可评测，不得推造名次。

固定 SciFact 50 条、AutoScholarQuery 开发 0/10 与验证 10/5 的纯 Replay 消融中，两组排序前候选身份、数量和 Judgement 状态逐 case 完全一致，双 Replay 的核心指标与逐 gold 诊断一致，执行期 HTTP、重试、网络等待和快照写入均为 0。开发集候选 Recall/唯一候选 gold 均为 0.1500/3，但 Recall@20 与 F1@20 从 0.1250/0.0179 降至 0.0250/0.0083，逐查询为改善 0、持平 9、退化 1；验证集候选 Recall/唯一候选 gold、Recall@20、F1@20 均保持 0.2000/1、0.2000、0.0190，5 条全部持平。SciFact 的候选 Recall/唯一候选 gold 均为 0.1220/5，但 Recall@20 与 F1@20 从 0.1220/0.0116 降至 0.0976/0.0093；41 条身份可评估查询中改善 0、持平 40、退化 1，另 9 条保持身份不可评估。RRF 未在三个集合均不退化，也未形成稳定净增，因此继续默认关闭；正式产物位于 `outputs/benchmark_runs/rrf_b0c9636_formal/`。

`llm_semantic` 评测固定使用同一 arXiv-only、adaptive、balanced、Top-20 配置，开发集为 offset 0 的 10 条，独立验证集为 offset 10 的 5 条；每例最多一次温度为 0 的 LLM 调用和两条补充查询。LLM 规划快照与检索快照独立冻结，动态计划先补 LLM 键再发现检索键；最终比较必须同时使用 LLM replay 与 retrieval replay，执行期两类网络请求、重试和网络等待均为 0。当前进程的 LLM provider 为 disabled，因此只重放了 `current_rules` 基线，未运行或伪造 LLM 指标。开发/验证基线的候选 Recall、F1@20、Recall@20 分别为 0.1500/0.0179/0.1250 和 0.2000/0.0190/0.2000；分析产物位于 `outputs/benchmark_runs/llm_query_planning_analysis/`，验收状态为未执行。

`llm_constrained_rewrite` 的正式配对口径固定为 SciFact 50 条、AutoScholarQuery development offset 0/10 与 validation offset 10/5。基线和实验组均使用产品默认来源、adaptive、balanced、Top-20，并关闭 Query Evolution、RefChain、concept projection、RRF 及其他 LLM 能力；实验组每例至多一次温度为 0 的严格 JSON 调用，原始查询保持首位，唯一改写只替换最低优先级派生查询，因此子查询数和计划请求预算不变。LLM Record 仅报告真实请求、Token、延迟和失败，正式召回/F1 只取同一 LLM 与 retrieval 快照的 Replay；Schema 失败、本地拒绝、fallback 和来源失败必须单列，fallback 不得计作改写收益。只有三个集合均不退化且至少两个集合的最终 Recall@20 或 F1@20 严格提升，才可建议继续评估；否则策略保持默认关闭。

`scripts/audit_current_rules_subqueries.py` 对冻结的 `current_rules` Replay 做纯离线子查询边际审计。它按 planning 的优先级以及 `(case, source, subquery)` 重建实际 Snapshot 列表，使用统一身份去重，再在 gold 仅进入 evaluator 后计算独立候选、独立 gold、首次命中和计划顺序边际。`failed`、缺失 Snapshot 与 `not_started` 分别记录；失败或缺失列表不会被记作零贡献。反事实固定使用现有去重、候选预算、规则 Judgement、排序、过滤和 Top-20：一组只保留 `original_query`，另一组每次全局移除一种通用派生 purpose。共享 Snapshot key 只有在所有 owner 都被移除时才计请求节省。输出不调用 SearchService、connector 或 LLM，并将网络请求、LLM 请求和 Snapshot 写入固定记录为 0。

`scripts/audit_current_rules_sources.py` 在同一冻结输入上按 query 计划顺序和来源顺序重建各来源候选，并分别计算单源保留与 leave-one-out。全量结果明确标记为已观测 Snapshot 下界；严格来源子集只纳入该 case 中至少一个实际来源请求成功、且该来源没有 failed、缺失或终态不一致的记录。未启动适配请求不产生候选或请求成本，但整项来源都未启动时不进入严格子集。独立候选/gold 只有在同 case 四源均成功时才具有严格归因资格；否则只输出 `observed` 值，不作为安全删除证据。请求、重试、延迟和限流节省按唯一 Snapshot key 聚合，离线执行继续固定为 0 网络、0 LLM、0 Snapshot 写入。

`scripts/audit_cross_strategy_union.py` 对已冻结的 `current_rules`、`concept_projection`、`llm_constrained_rewrite`、Query Evolution 与 `llm_semantic` 产物做跨策略候选联合上界审计。每个实际执行的 `(strategy, source, adapted_query)` 必须追溯到 Snapshot key 和终态；没有完整数据集产物的策略在输入 manifest 中显式排除，LLM fallback/rejected case 回退到同 case 的 `current_rules` 候选且不计策略贡献。全部成功候选先按统一论文身份合并，再分别报告当前规则排序的真实可转化 Recall@20/F1@20 与 gold 优先的候选池 oracle；后者只表示候选集合上界，不是可实现成绩。跨策略相同 `(source, query)` 的响应顺序或终态不一致会使 case 离开严格可比子集，防止把外部响应漂移计作查询策略收益。最小查询集合使用 gold 的时间点仅限离线审计，在已执行查询列表上做确定性精确 set cover，不接入生产规划。该命令不导入 SearchService 或 connector，并固定记录 0 网络、0 LLM、0 Snapshot 写入。

固定产物审计中，SciFact 没有完整的 Query Evolution/`llm_semantic` Replay，故只纳入另外三种策略；Auto 开发/验证均纳入五种。全部可观察下界中，SciFact 的候选 Recall/候选 gold 从 0.1220/5 提高到 0.1463/6，但当前排序后的 Recall@20/F1@20 仍为 0.1220/0.0116，gold 优先 oracle 才达到 0.1463/0.0139；Auto 开发候选 Recall/gold 保持 0.1500/3，当前排序却从 0.1250/0.0179 降到 0.0250/0.0083；Auto 验证候选 Recall/gold 从 0.2000/1 提高到 0.3000/2，且当前排序转化为 0.3000/0.0372。严格可比 case 仅为开发 1/10、验证 1/5、SciFact 2/50，其中只有验证集观察到新增 gold；其余受 LLM fallback、来源失败或重复查询响应漂移限制。审计 oracle 覆盖这三个并集分别最少需要 2/2、2/2、6/12 个查询/记录请求，但不得据此修改线上计划。两次产物字节一致，逐 case 与 aggregate SHA-256 分别为 `8d4b5353c7ea3c285308d4a5a2ed8d37ed98cefe4fa29f8766399f87fc85b459`、`85341d30b68959777a43109746d7df32b322b39595a44ec99822ed9b015155ab`。现有同类查询改写没有形成跨集合稳定净增，后续应转向新的召回范式；SciFact 的 1 个未转化 gold 同时说明排序转化仍是独立瓶颈。

`prf_v1` 是默认关闭的固定预算伪相关反馈实验。它与 Query Evolution 不等价：后者在完整初始检索后根据 judgement/coverage gap 开启额外演化轮次，PRF 则先只执行原始查询，以现有规则排序后的前 5 篇唯一候选为 seed，从标题与摘要抽取跨至少 2 篇 seed 出现的 unigram/bigram，并以固定倒数排名折扣选取最多 6 项；第二查询替换最低优先级派生查询，计划子查询数、每来源 adapter 上限和全局请求预算均不增加。重复 seed 使用统一论文身份合并；数字、年份、URL、论文标识、停用词及原查询词全部排除。首轮全失败、没有 seed/反馈词或没有可替换派生查询时完整回退 `current_rules`。

固定 SciFact 50 条、AutoScholarQuery development 0/10 与 validation 10/5 的正式 Replay 中，PRF 分别应用于 47/50、10/10、5/5 个 case，SciFact 另有 3 个 `no_derived_query_to_replace` 回退。全部可观察下界的候选 Recall/Recall@20/F1@20/唯一 gold：SciFact 旧新均为 0.1220/0.1220/0.0116/5；开发集为 0.1500/0.1250/0.0179/3 → 0.0500/0.0250/0.0083/2；验证集为 0.2000/0.2000/0.0190/1 → 0/0/0/0。逐查询改善/持平/退化分别为 0/50/0、0/9/1、0/4/1。来源终态双方均完整的严格子集只有 38/50、5/10、3/5，三个子集的候选 Recall、Recall@20 与 F1@20 均逐查询持平，因此全量两次退化不能归因于 PRF，而是来源终态不一致的下界差异。

PRF 查询新增的独占候选分别为 537/115/61，但三个集合的独占 gold 均为 0；SciFact 的 PRF 查询虽重复命中 2 个既有 gold，也没有形成边际召回。快照记录成本旧→新分别为：SciFact 请求 419→339、重试 29→2、错误 14→2、延迟 599.43→481.88 秒；开发请求 120→104、重试 12→4、错误 5→4、延迟 206.66→159.46 秒；验证请求 38→53、重试 4→6、错误均为 2、延迟 63.46→83.35 秒。实际调用数会因 adaptive adapter 的等价/低保留跳过和来源 cooldown 在固定上限内变化，不能把单次外部可用性差异解释为 PRF 效率收益。两轮 Replay 均为 0 HTTP、0 快照写入、0 missing；逐 gold 与 PRF 逐 case 审计字节一致，分析产物三文件 SHA-256 分别为 `051c54d1fed9f07814e91704badf40e07bf28efb0a3fce69f603e0f5ac005727`、`1ded9b10507e60762144ba8df9eebbfc9feaa0518e20f26e55f1838a2e66cb27`、`d37affe99b580de97d611ca256686a079e78c70e300b8b8c2fa856412e48602b`。策略未在三个集合均不退化，也没有任何最终 Recall/F1 净增，故保持默认关闭。

`semantic_seed_expansion` 是默认关闭的 Semantic Scholar 推荐候选扩展。正式配对必须先验证基线与实验的 `initial_retrieval`、`initial_deduplicated` 和 `initial_reranked` 候选及顺序逐 case 一致；Snapshot 组把实验的初始 retrieval key 冻结为同查询策略 baseline 的 required-key 集合，目录内其他既有 key 不得进入实验首轮。对没有 Paper ID 的首轮候选，实验只使用来源已有的 DOI、arXiv ID、PMID 或 S2ORC Corpus ID 构造一次官方 paper-batch 请求；返回映射必须经统一身份规则确认共享精确稳定标识且无同类冲突，不能使用标题等软证据，也不补写普通检索候选。直接与解析 seed 合并后仍只按首轮排名取最多 3 个，并执行至多 1 个、limit 100 的官方批量推荐请求。推荐候选使用统一身份去重和既有候选预算、Judgement、Reranker；重复候选、无 seed、冲突映射、失败或 fallback 不计为收益。阶段诊断分别记录解析候选/请求/成功/冲突/缺失、直接与解析 seed、两个 Snapshot key、推荐原始/唯一/新增候选、首轮与扩展后 Candidate Recall、独立 gold、首轮 gold 丢失和 Top-20 转化。正式指标只取 Replay；解析与推荐的 Record 成本归入 reference 请求，来源失败另报告，并同时给出排除失败请求的严格可比子集。

加入官方精确 ID 解析后的固定 SciFact 50 条、AutoScholarQuery development 0/10 与 validation 10/5 正式 Replay，三个初始阶段分别达到 50/50、10/10、5/5 完全一致。相对旧扩展每组只有 1 个 case/3 个 seed，新版具有 seed 的 case/最终 seed 分别增至 47/141、9/27、5/15，其中解析 seed 为 140/25/12；批量解析候选中分别有 1748/367/149 个通过精确稳定标识与冲突校验，另有 25/11/3 个同类标识冲突保持拒绝。推荐产生 3999/799/498 个新增唯一候选，但三个集合的独立 gold 均为 0。

全部可观察结果中，SciFact 的候选 Recall 与候选唯一 gold 保持 0.1220/5，Recall@20、F1@20 与最终返回 gold 从 0.1220、0.0116、5 降至 0.0976、0.0093、4；Auto 开发保持候选 0.1500/3，但最终三项从 0.1250、0.0179、2 降至 0.0250、0.0083、1；验证集保持 0.2000、0.2000、0.0190、1。F1@20 逐查询改善/持平/退化为 0/40/1、0/9/1、0/5/0：SciFact case 146 的既有 gold 由 rank 6 降至 57，开发 case 4 由 rank 13 降至 38，均被挤出 Top-20。排除解析或推荐 source failure 后的严格可比集为 32/41、8/10、5/5 个可评测 case，前两组仍退化、验证持平。新增 reference 实际请求/重试/错误/记录延迟分别为 133/36/10/359.33 秒、27/8/2/72.63 秒、12/2/0/35.97 秒。两次正式 Replay 都是 0 HTTP、0 Snapshot 写入、0 missing，逐 gold SHA-256 分别稳定为 `1b5c6dd68eb9645a9061b07dabe493ea505dad2040dfc0b1ff97436ca26ce045`、`f2899df23aede582b6f49432a5f7fb1fb265271c99e7b272acd812a1faa832da`、`fbb11f251bb1baf8ad1a853c40a6aaf36cb6aa5e246997859be4675160d71c3a`。解析显著扩大 seed 覆盖，却没有新增 gold，且在两个集合造成排序挤出，故策略继续默认关闭。

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
