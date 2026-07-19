# ScholarNavigator 技术报告

## 报告口径

本报告只记录当前代码中可以定位、可以测试的状态。赛题要求见 [赛题要求摘要](../contest/requirements.md)，系统结构见 [当前架构](../architecture.md)，评测口径见 [评测说明](../evaluation.md)。当前有 5 条链路 smoke、10 条开发诊断和 5 条独立策略验证；均不作为正式性能结论。

## 系统定位

ScholarNavigator 面向复杂学术查询，将自然语言问题转换为多子查询检索任务，合并多来源论文元数据，执行相关性判断、重排和证据约束归纳，并通过前后端分离界面展示结果与运行诊断。

系统采用真实检索单路径：产品前端调用异步 Real Search 生命周期接口，不使用产品级 fake 结果兜底。fake 数据只存在于离线评测与测试。

## 已实现并测试

### 检索与编排

- `SearchService` 串联查询理解、多子查询检索、跨源去重、相关性判断、重排、可选查询演化、可选单层 RefChain 和规则证据归纳。
- OpenAlex、arXiv、Semantic Scholar、PubMed connector 均已实现字段转换、超时或限速处理、错误诊断，并有隔离外网的测试。
- 去重保留 DOI、arXiv、OpenAlex、Semantic Scholar、PubMed 等标识符，并支持 title+year 后备键。
- 单个 connector 或子查询失败时保留其他来源结果，同时输出来源统计和 warning。
- 逻辑子查询在 connector 前生成安全原查询与可选核心补充查询；信息保留不足时回退，同一 run 的真正等价调用只执行一次，全部逻辑来源和用途保留在阶段 provenance 中。
- Semantic Scholar 429、arXiv 请求间隔、OpenAlex 非法查询、瞬态失败重试与 run 内连续失败熔断均有隔离外网的行为测试。

### LLM 边界

- 后端支持 OpenAI-compatible provider，默认关闭。
- 查询理解可使用 LLM JSON 增强；响应必须经过 Schema 校验和来源白名单过滤，失败时回到规则计划。
- 相关性判断可分批调用 LLM，但只提供候选论文元数据；失败批次回到规则判断。
- LLM 调用次数及 provider 返回的 prompt、completion、total token usage 可进入结果统计。

### API 与前端

- FastAPI 提供健康检查、运行配置、任务创建、状态、结果、SSE 和取消接口。
- 后台线程执行任务，run store 对终态任务执行 TTL 与数量清理。
- 前端支持来源选择、运行参数、状态轮询、SSE 事件、取消、结果卡片、证据归纳、引用图和本地 JSON/Markdown 导出。
- API mapper、Pydantic Schema 与前端 TypeScript 类型有对应测试或构建检查。

### 评测与工程检查

- fake fixture 离线 evaluator 可比较 baseline、仅查询演化、仅 RefChain 和两者同时启用四组配置。
- AutoScholarQuery Adapter 可确定性加载 1000 条 test 查询和 2403 条 arXiv gold；只读检查报告确认没有空 gold、无效案例或重复 qid。
- Benchmark Runner 复用真实 SearchService、统一结果选择、全标识符匹配、F1/Precision/Recall/MRR/nDCG 与 success-only/end-to-end 聚合，并支持原子输出和 resume。
- 可选阶段诊断追踪初始检索、去重、Judgement、Reranking、查询演化、RefChain 和最终返回，输出 gold 丢失原因、来源贡献和可测试的瓶颈标签；诊断不反向影响搜索。
- Query Evolution 与 RefChain 诊断记录逐 seed、逐扩展动作、唯一新增候选、事后 gold、分类分布、Judgement/Top-K 丢失、API 与阶段耗时，并用确定性规则给出小样本结论。
- 当前分支通过后端 pytest、前端 lint 和前端生产构建。

上述“测试”指单元测试、集成测试、mock connector 测试和构建检查，不代表外部检索质量或比赛指标已经验证。

## 已实现但未正式 benchmark 验证

| 能力 | 已有实现 | 尚缺证据 |
| --- | --- | --- |
| 多源检索 | 四个真实 connector、聚合与去重 | 未在完整公开或官方数据上验证召回率 |
| 查询演化 | 从高相关 seed 生成补充查询并再检索 | 未证明相对 baseline 稳定提升 F1 |
| RefChain | 通过 OpenAlex 做单层引用扩展 | 未量化新增召回与噪声、延迟的权衡 |
| LLM 查询理解与判断 | 可选 JSON 调用、校验和规则回退 | 未完成模型版本对比、消融和成本收益评测 |
| 规则重排 | 综合相关性、时效性、权威性和元数据等信号 | 未完成排序指标与阈值校准 |
| 证据归纳 | 从判断证据行生成带 citation key 的结论 | 未进行人工事实性与引用覆盖评审 |
| 运行效率 | 记录来源调用、缓存命中、LLM 用量和延迟 | 未按比赛口径形成可复现效率报告 |

AutoScholarQuery 原始顺序前 5 条已用 balanced、单一 arXiv 源完成真实 smoke：成功率 1.0，端到端 F1@5/10/20 为 0.0444/0.0286/0.0357，平均 API 请求 2.4、平均 Token 0、平均延迟 0.78 秒。样本很小且只使用单一来源，不能替代完整 Benchmark 或比赛成绩。

固定前 10 条的阶段诊断完成 arXiv-only 和三源 baseline。三源配置把初始候选 Recall 从 0.150 提高到 0.170、F1@20 从 0.0179 提高到 0.0259，但平均 API 请求由 2.7 增至 8.2、平均延迟由 4.01 秒增至 29.78 秒，并出现 0.402 的来源错误率。规则标签指向检索召回、Judgement false negative 和来源可靠性；没有发现已保留 gold 被排到 Top 20 外的 Reranking 瓶颈。其余三组因持续 429/超时安全中止，不能声明 Query Evolution 或 RefChain 的收益。

同一固定前 10 条上的 A2/B2 只改变来源查询适配与可靠性策略。A2 相对 A 的平均 API 由 2.7 降至 2.4、错误率保持 0，但候选 Recall 由 0.150 降至 0.025、F1@20 由 0.0179 降至 0.0083，arXiv 礼貌间隔使平均延迟增至 10.57 秒。B2 相对 B 的平均 API 由 8.2 降至 5.1、错误率由 0.402 降至 0.059、平均延迟由 29.78 降至 19.93 秒，OpenAlex 未再出现 400，Semantic Scholar 请求由 51 次降至 8 次；候选 Recall 由 0.170 降至 0.045、F1@20 由 0.0259 降至 0.0163。该结果只支持可靠性改善，不支持召回质量提升。

回归修复后的 A3 在相同前 10 条、arXiv-only 配置中使用安全原查询保底和有限核心补充：候选 Recall 为 0.250、Recall@20 为 0.225、F1@20 为 0.0274、平均 API 3.6、平均延迟 32.73 秒，来源错误率为 0。独立 5 条验证中，hybrid 与 safe-original 的候选 Recall、Recall@20 和 F1@5/10/20 相同。三源 B3 因 Semantic Scholar 连续最终 429 在 9/10 安全停止，只生成明确标注的部分指标，不据此声明完整多源质量收益。

本阶段增加 adaptive：先执行安全原查询，只在首轮候选量、核心或约束覆盖、元数据完整性不足且预算与来源状态允许时执行核心补充。A4 开发集上 adaptive 与 safe 的候选 Recall、Recall@20、F1@20 相同（0.150/0.125/0.0179），平均 API 2.7、延迟 25.37 秒；hybrid 为 0.250/0.225/0.0274、3.5 次 API、30.96 秒。adaptive 的 compact 执行率为 3.85%，事后 gold 增量为 0。

独立 V2 五条在三组执行完成后统一查看：safe、hybrid、adaptive 的候选 Recall、Recall@20 和 F1@5/10/20 完全一致；adaptive 使用 2.2 次平均 API、50.50 秒平均延迟，hybrid 为 3.0 次、54.00 秒，adaptive 未执行 compact。该结果通过本轮“不低于 safe”门槛并支持将 adaptive 作为产品默认，但不构成完整 Benchmark 的质量或延迟结论。

无 Semantic Scholar Key 的 M4 双源 adaptive 已完成：候选 Recall 0.170、Recall@20 0.145、F1@20 0.0259、平均 API 5.7、延迟 54.61 秒，compact 执行率 1.92%。OpenAlex 最终发生两次超时和一次 429，HTTP 400 为 0；结果如实保留，不能声明双源可靠性已稳定达标。

本轮按统一 120 秒预算启动 Query Evolution/RefChain 四组双源消融。开发集 baseline 的候选 Recall、Recall@20、F1@5/10/20 为 0.150、0.125、0.0222/0.0143/0.0179，平均 API 3.1、延迟 32.81 秒；OpenAlex 在 10 条中 0 次成功，并在 timeout 重试、HTTP 429 后持续 cooldown。依照预设保护条件，其余三组及独立验证集未启动；产物明确标记为不完整，当前不能判断两个模块的独立或组合边际收益，也不据此建议默认开启。

## 未实现

- 完整 1000 条 AutoScholarQuery 的固定配置基线、重复实验和正式报告。
- 全文 PDF 获取、段落检索和可定位到原文片段的证据链。
- 持久化任务队列、跨进程 run store、共享缓存和服务重启恢复。
- 强制中止已经发出的 connector 请求。
- 对重排序、查询演化、RefChain 和归纳阶段的 LLM 实现；当前这些阶段为规则逻辑。

## 可靠性与可解释性

外部请求具有超时、有限重试或限速处理。错误不会被替换成示例论文，而是进入 `source_stats`、`warnings`、`missing_evidence` 和 SSE 事件。检索缓存与来源冷却可减少短期重复调用，但均为进程内机制。

最终归纳只消费已有候选和证据行；没有证据时返回证据不足或限制信息。当前证据来自标题、摘要、venue 与元数据，因此“带引用”表示引用到返回论文记录，不表示系统核验了全文。

## 验证结论

当前版本形成了可运行、可测试的真实检索工程闭环，但尚不能声明达到任何正式检索质量、效率或比赛成绩。下一阶段工作及验收标准见 [路线图](../roadmap.md)。
