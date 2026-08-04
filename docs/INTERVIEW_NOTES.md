# TechPilot INTERVIEW_NOTES

## Day 1

### FastAPI 的 `/health` 有什么作用？

检查应用进程是否能够正常响应。它不能证明数据库、缓存或向量库可用。

---

## Day 2

### PostgreSQL、Redis、Qdrant 分别承担什么职责？

- PostgreSQL：结构化业务数据和元数据
- Redis：高速缓存及后续异步任务支持
- Qdrant：Embedding 向量、Payload 和语义检索索引

### ORM 和 Alembic 有什么区别？

ORM 描述应用希望使用的数据结构；Alembic 管理真实数据库 Schema 从旧版本迁移到新版本的过程。

---

## Day 3：文档摄取与事务设计

### 为什么 Parser 和 Chunker 要分开？

Parser 尽量还原源文件结构；Chunker 生成适合检索的知识单元。二者变化原因不同，应独立测试和替换。

### 为什么 Document 要先提交 PENDING？

解析和入库可能失败。先提交 PENDING，可以在失败后保留 FAILED Document 和错误信息。

### 为什么标题不单独生成 Chunk？

真实文档验证显示 heading-only Chunk 会造成大量极短检索单元。标题应作为上下文注入正文。

---

## Day 4–5：基础检索

### 为什么要抽象 EmbeddingProvider？

上层服务只需要“文档向量化”和“查询向量化”能力，不应绑定 Sentence Transformers 的具体 API。

### PostgreSQL 和 Qdrant 谁是事实来源？

PostgreSQL 是事实来源，保存 Document 和 Chunk 正文；Qdrant 是可重建的向量索引。

### 为什么索引要在 PostgreSQL Commit 后执行？

避免 Qdrant 已经存在向量，但 PostgreSQL 事务随后回滚，产生无法追溯的孤立索引。

### 为什么 Qdrant 搜索必须强制带 workspace_id？

所有 Workspace 共用一个环境级 Collection。强制 Filter 才能保证租户数据隔离。

### Recall@5 是什么？

30 条评测问题中，只要目标 Chunk 出现在前 5 个结果内就算召回成功。当前为 0.866667。

### MRR@5 是什么？

对每条问题取目标 Chunk 排名的倒数，再对全部问题求平均。

### 为什么 Golden Dataset 必须人工标注？

检索质量不能由模型自己给自己定义答案。每条 Query 必须由人确认最相关目标 Chunk。

---

## Day 6：可信问答

### 为什么回答不能直接使用 Qdrant Payload 中的内容？

Qdrant 是可重建检索索引，不是事实来源。完整 Chunk 正文应从 PostgreSQL 回查。

### 为什么 Citation 不能完全交给 LLM 生成？

模型可能生成不存在、错位或不能支持结论的引用。系统应根据实际进入 Context 的 Chunk 构造 Citation。

### Context Builder 和 Retriever 的职责有什么区别？

Retriever 负责找出相关 Chunk；Context Builder 负责把 Chunk 排序、格式化和截断。

### 为什么无证据时应该拒答？

可信问答只在证据充分时回答。拒答可以降低幻觉和错误归因风险。

---

## Day 7：回答质量评测与文档删除

### 为什么 Document 使用软删除，而不是直接物理删除？

软删除可以保留审计信息和业务历史，并允许检索层立即通过状态过滤停止使用该文档。物理清理可以作为后续独立流程处理。

### 为什么先提交 PostgreSQL 软删除，再清理 Qdrant？

PostgreSQL 是事实来源。先提交数据库状态，可以保证业务上删除已经生效。Qdrant 是可重建索引，其清理失败不应让数据库删除事务回滚。

### Best-effort Cleanup 有什么风险？

Qdrant 可能短期保留已删除文档的孤立向量。当前通过 PostgreSQL 状态过滤保证回答链路不使用它们；长期可使用 Outbox Pattern 实现可靠异步重试。

### 什么是 Entity Scope Mismatch？

检索结果与问题在关键词或语义上相关，但证据描述的是另一个主体。模型把该事实错误归因给问题中的目标主体。

### 为什么 Retrieval Relevance 不等于 Evidence Sufficiency？

Retriever 优化的是语义相关性，而回答要求证据明确支持结论。证据必须同时匹配目标主体、询问属性，以及二者之间的关系。

### Day 7 的真实失败案例是什么？

问题询问 TechPilot 使用的 Embedding 模型，检索返回盘古平台文档。该文档只说明盘古平台提供 `Pangu-EmbeddingRank-zh`，不能证明 TechPilot 使用它。

### 如何修复主体错配？

System Prompt 要求回答前检查：

1. 问题主体与证据主体是否一致。
2. 证据是否支持询问的属性。
3. 证据是否明确表达主体与属性之间的关系。
4. 任一条件缺失时拒答且不返回 Citation。

### 如何验证删除和拒答链路？

删除支持 TechPilot 项目事实的文档后，运行真实 `AnswerService` 评测。三条问题均返回 `refused=True`、`citations=0`，结果为 3/3 PASS。

---

## Day 8：引用可追溯性回归

### 如何证明 Citation 不是模型编造的？

测试必须覆盖完整数据链路，而不只是检查最终 JSON 字段：Parser 提取页码或标题路径，Chunker 保留结构，Context Builder 为实际进入 Prompt 的 Chunk 分配 `SOURCE_N`，AnswerService 再根据该映射构造 Citation。LLM 只返回内部来源标识，不能决定文档名、页码、章节或原文。

### 为什么要测试 Markdown 标题路径和 PDF 页码的跨层传播？

单元测试只能证明某一层能格式化字段，不能证明真实来源元数据在多层转换中没有丢失。跨层回归测试可以验证来源结构从解析阶段一直到用户看到的 Citation 都保持一致。

### 为什么被 Context Budget 排除的来源必须无法引用？

被排除的 Chunk 没有实际进入 LLM Prompt，不能成为回答证据。Context Builder 不会为其建立有效的来源映射；如果模型返回对应 `SOURCE_N`，AnswerService 必须将其视为未知来源并拒绝，而不是生成看似合法的 Citation。

### Day 8 为什么没有修改生产代码？

代码审计显示现有设计已经满足服务端引用映射、页码与章节传播、未知来源拒绝等边界。Day 8 的缺口是缺少跨层验收证据，因此最小且正确的改动是补回归测试，而不是为了体现开发量重写稳定逻辑。

---

## Day 9：P1 集成验证与 Gate 证据

### 为什么 126 个测试通过仍不能直接等于 P1 Gate 通过？

测试数量只说明已执行的断言通过，不能证明 Gate 所需的所有边界都被覆盖。Gate 还要检查真实基础设施、完整生命周期、可复现命令、评测指标、失败语义、已知限制和文档证据。最终 PASS / CONDITIONAL PASS / FIX 必须基于退出条件，而不是测试总数。

### 为什么生命周期集成测试使用 Fake LLM，而不是直接调用 DeepSeek？

生命周期测试要稳定验证上传、持久化、索引、检索、Context、Citation、删除和拒答编排。确定性 Fake LLM 可以明确检查 Prompt 是否真的包含 `SOURCE_1` 和上传证据，避免网络、费用、速率限制和模型随机性使测试不稳定。真实 DeepSeek Provider 和请求链路由 Day 6 的真实 E2E 单独覆盖。

### 为什么 Fake LLM 不能无条件返回 `SOURCE_1`？

无条件返回会让测试在 Context 没有建立来源映射、Prompt 漏掉证据或来源格式错误时仍然通过。Fake LLM 必须检查实际 Prompt，才能成为链路断言的一部分，而不只是一个永远成功的桩。

### 为什么 10 条全部正确拒答不能证明有答案质量？

这 10 条全部是 `answerable=false`，只能衡量证据不足时是否拒答、是否编造答案以及是否生成虚假引用。它们不能衡量有证据时是否正确回答、是否过度拒答、答案是否完整或 Citation 是否直接支持结论。因此 `over_refusal_rate` 必须是 `n/a`，不能写成 `0%`。

### 有答案样本怎样才算通过？

至少同时满足：`refused=false`、答案正确、答案完整、没有文档外推测、Citation 直接支持全部关键陈述，并且没有错误或多余引用。仅仅“没有拒答”不代表通过。

### 答案正确但 Citation 不支持，为什么仍然失败？

这可能是模型依赖参数知识或猜测后碰巧答对。TechPilot 的目标是可验证回答，不是只看最终文本。因此答案正确性和证据支持性必须分别评估，任一失败都不能算可信回答。

### 为什么运行错误不能算作正确拒答？

运行错误表示系统没有完成一次有效评测。把错误计入拒答会人为降低错误回答率并掩盖稳定性问题。因此指标分母只使用成功执行且产生明确 `refused` 值的样本，运行错误单独报告。

---

## Day 10：P1 Gate Review

### 为什么 P1 是 CONDITIONAL PASS，而不是直接 PASS？

P1 核心工程链路、检索 Baseline、Citation 可追溯性、删除隔离和无答案安全性都有证据，但当前 10 条回答评测全部是 `answerable=false`。它们不能量化有答案时的正确性、Citation 支持性和过度拒答。因此工程主链路可以通过，但最终质量结论仍需一组包含正常样本与易混淆样本的 `answerable=true` 定量证据。

### CONDITIONAL PASS 的唯一条件是什么？

建立 `answerable=true` 定量评测集，覆盖正常问题和易混淆问题，并记录 answer correctness、citation support 和 over-refusal。失败案例必须保留并审查，不能用 `refused=false`、答案看起来合理或单次真实 E2E 代替质量判断。

### 为什么 OCR 不属于 P1 条件？

P1 已明确以 Markdown 和文本型 PDF 为摄取范围。OCR 会引入识别准确率、版面恢复、置信度和引用定位等新的质量维度，应作为独立摄取增强评估，而不是在 P1 Gate 临时扩大范围。

### 为什么 Hybrid Retrieval 和 Reranker 不属于 P1 条件？

P1 的目标是建立可测量的 Dense Retrieval Baseline 和可信回答闭环；当前 Recall@5、MRR@5 及失败案例已经记录。Hybrid Retrieval 和 Reranker 是基于 Baseline 的 P2 优化项，不应反向改变 P1 的退出条件。

### 有答案质量为什么要同时记录 answer correctness 和 citation support？

答案可能依赖模型参数知识而碰巧正确，但 Citation 并不支持它；也可能引用正确，但答案遗漏关键条件或加入文档外推测。可信回答要求答案正确、完整且被证据直接支持，因此两个维度必须分开记录。

### over-refusal 说明什么？

over-refusal 衡量系统面对有充分证据的问题却选择拒答的比例。只优化无答案拒答可能让系统过度保守，因此最终 P1 质量证据需要同时观察幻觉风险和可用性损失。

## Day 11：为什么 P1 最终是 FIX？

### 为什么继续修改 Prompt 不能修复 Day 11 的三个主要失败？

三个失败样本的权威 Chunk 在 Dense 中分别排到 56、1120 和 26，均未进入生产 Top-5。生成模型看不到缺失证据时，Prompt 最多约束它“不要乱答”，不能凭空补回权威事实。因此根因优先归为 Retrieval recall，而不是生成 Prompt。

### 为什么 answer correctness 和 Citation support 要分开统计？

模型可能依赖参数知识碰巧答对，但引用并不支持答案；也可能引用正确但回答遗漏关键条件。可信 RAG 要求“答案正确”和“证据直接支持”同时成立，因此必须分别评估。

### 为什么诊断性的 BM25/RRF 实验不能直接算 P2 已实现？

诊断脚本可以验证方向，但通常没有稳定接口、配置、隔离约束、测试和生产生命周期。项目验收需要正式模块和可复现评测，不能用一次性实验替代生产能力。

## Day 12：BM25 与 Retrieval Evaluation

### Dense 和 BM25 各自依赖什么信号？

Dense 把 Query 和 Chunk 映射到 embedding 空间，主要利用语义相似度；BM25 基于分词后的词项频率、逆文档频率和文档长度归一化，主要利用 lexical overlap。技术专名、API 名、错误码和代码标识符常给 BM25 很强的精确匹配信号，而跨语言或改写问题通常更依赖 Dense。

### 为什么 BM25 和 Dense 必须使用同一个 Chunk identity？

Fusion 需要判断两路结果是否是同一个知识单元并去重，同时最终还要回查同一份权威正文。统一 identity 解决的是实体对齐，不是要求两种算法的 score 或 rank 相同。

### 为什么 BM25 filter 必须在候选集合阶段生效？

如果先对全库计算 Top-K，再删除其他 Workspace、软删除或 FAILED 文档，合法候选可能已经被非法结果挤出 Top-K，既会污染指标，也可能造成租户隔离风险。正确做法是先定义合法 corpus，再评分排序。

### 为什么 BM25 要排除 FAILED Document，而不仅是 deleted Document？

TechPilot 的 Ingestion 会先提交 Document/Chunk，再执行向量索引；如果索引失败，Chunk 可能已经存在而 Document 最终为 FAILED。Dense 正常生产语料不会把这种失败文档视为有效知识，因此 BM25 也必须只接受 COMPLETED/PARTIAL，保持语料语义一致。

### 为什么 Day 12 没把 BM25 直接接到 AnswerService？

Day 12 的目标是建立独立、可验证的 BM25 Retriever 和 baseline。直接替换或并联 AnswerService 会提前引入 Hybrid 的公共 hit interface、fusion 和排序职责，跨入 Day 13。先冻结单路 retriever 边界可以让后续融合做清晰的 ablation。

### Day 12 的 Dense/BM25 结果说明了什么？

在修复后的同一 30-case Golden 上，两者 Recall@5 都是 0.70，但 Dense-only hit 有 4 条、BM25-only hit 也有 4 条，共同 MISS 5 条。说明单路总体指标相同但错误分布明显不同；两路命中并集达到 25/30，为 Hybrid 提供了实证动机。

### 为什么不能继续使用旧的 Dense 0.866667 和 BM25 0.60 做对比？

评测发现 30 条 Golden 中有 6 条仍指向已删除旧文档的 Chunk，这些标签在当前 corpus 中不可能命中。旧 Dense 与新 BM25 还来自不同 corpus snapshot，横向比较无效。修复 Golden 为 30/30 valid 后必须同时重跑两种 Retriever。

### corpus 更新后，Retrieval Benchmark 应怎样防止假性性能下降？

先做 Golden integrity check：每个 `expected_chunk_id` 必须仍属于当前 Workspace、未删除且可检索的 Document。若知识被替换，应人工重新标注新权威 Chunk；若知识已经从 corpus 移除，应替换问题。不能把 stale label 当作 Retriever 的 MISS，也不能让被评测 Retriever 自动选择自己的新标签。

---
## Day 13：RRF Hybrid Retrieval

### 为什么不能直接把 Dense score 和 BM25 score 相加？

两种 score 的尺度和含义不同。Dense similarity 由 Embedding 模型和向量距离定义；BM25 score 受查询词、文档长度和当前 corpus 统计影响。直接相加等价于隐式假设两种分数已经校准，而当前系统没有这个保证。

### RRF 为什么使用 rank？

RRF 只使用每个 Retriever 内部的相对排序，因此避免跨 Retriever 原始 score calibration。一个 Chunk 如果同时被多路召回，会累加多个 rank contribution。

### `k` 的作用是什么？

`k` 控制 rank 差异的敏感程度。较大 `k` 让不同 rank 的贡献更平，增强跨路共识；较小 `k` 更强调每一路头部结果。Day 13 实验显示不能简单靠调小 `k` 消除 fusion loss：`k=1` 只是把损失从一个 case 转移到另一个 case。

### 为什么区分 `candidate_limit` 和最终 `limit`？

`candidate_limit` 决定每一路有多少候选有资格进入 Fusion；最终 `limit` 决定用户得到多少 Hybrid 结果。如果每一路只取最终 Top-5，RRF 无法利用 rank 6–N 的补充候选。候选过浅会损失覆盖，过深则会引入更多低质量共识候选，因此必须实验记录。

### 为什么 Hybrid Recall 没有达到 Dense/BM25 union 的 25/30？

25/30 是一个 oracle success union：只表示“至少有一路在自己的 Top-5 命中”。RRF 还必须把两路候选重新排成一个 final Top-5，所以单路命中可能被其他跨路候选挤出。Day 13 的 Hybrid 实际是 23/30，并出现 2 个 fusion truncation loss。

### 为什么正式 baseline 选择 `candidate_limit=20, k=60`？

`candidate_limit=10` 没有提高 Recall，并让 both-candidate-miss 从 3 增至 4；`k=20` 与 `k=60` 结果相同；`k=1` Recall 不变且 MRR 更低。继续围绕 30 条 Golden 搜参容易过拟合，因此保留常见且稳定的 `k=60` 与更完整的 Top-20 candidate pool。

### RRF 能解决什么，不能解决什么？

它能利用 Dense 与 lexical Retrieval 的互补候选并重新排序。它不能恢复两路 candidate pool 中都不存在的 Chunk，也不能保证所有单路 Top-5 命中在融合后仍留在 final Top-5。

### Day 12 Golden integrity 暴露了什么问题？

仅验证 `chunk_id` 存在还不够。Golden 应验证 Chunk 是否仍属于正确 workspace、正确 active/legal Document，以及 document id/name、chunk index、section 是否一致。Day 13 修正一条错误 `expected_document_id` 后单路指标完全复现，说明 Day 12 指标没有被污染，但原 integrity acceptance 不够严格。

### 为什么采用 Thin Agent / Thick Harness？

Agent 控制层只负责有限规划、能力选择和终止；Tool Runtime、Context、Evidence/Verification、Trace/Evaluation 和权限边界作为可独立测试的 Harness 能力。这样 P3/P4 不需要把 Retriever 或 Verifier 重写成 LangGraph 节点，也避免 Day 13 提前变成 Coding Agent 项目。
