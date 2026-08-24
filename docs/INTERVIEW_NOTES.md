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


---

## Day 14：Reranker 与 latency-quality trade-off

### Retriever 和 Reranker 的职责有什么区别？

Retriever 面向大语料快速召回候选，优化 recall；Reranker 只对有限候选集做更昂贵的 query-document 联合打分，优化最终排序。Reranker 不能恢复根本没进入候选池的目标 Chunk。

### 为什么 Cross Encoder 通常更准但更慢？

Dense Retriever 分别编码 Query 和 Document，再做向量相似度；Cross Encoder 把 Query 和 Document 联合输入，让 Transformer 建模 token-level 交互，因此排序更精细，但每个 query-document pair 都要重新前向计算。

### Day 14 提升了多少？

```text
Hybrid             Recall@5=0.766667  MRR@5=0.588333
Hybrid+Reranker    Recall@5=0.866667  MRR@5=0.766667
```

Recall@5 绝对提升 10 个百分点，MRR@5 提升 0.178334；救回 3 条，0 regression，保留原 Hybrid 23/23 命中。

### 增加了多少延迟？

正式 depth=20、MPS、模型 warm-up 后：

```text
Hybrid candidate mean      682.99 ms
Rerank inference mean     2323.19 ms
Rerank inference P95      2956.97 ms
Reranked total mean       3009.45 ms
Reranked total P95        3699.76 ms
```

PostgreSQL 正文回查 mean 仅 3.22 ms，增量成本主要来自 Cross Encoder inference。

### 为什么不把 rerank_depth 调到 40？

depth=40 让 Cross Encoder 看完整 RRF union，但 Recall@5 / MRR@5 完全不变；inference mean 从 2323.19 ms 增到 3893.50 ms，P95 增到 5850.06 ms。更深候选池没有质量收益，只增加延迟。

### candidate_limit、rerank_depth、final_top_k 为什么必须分开？

`candidate_limit` 是 Dense/BM25 每一路召回深度；两路 union 理论上最多有 `2 * candidate_limit` 个不同 Chunk。`rerank_depth` 是 fusion 后进入 Cross Encoder 的数量，`final_top_k` 是最终输出数量。

### 为什么当前不把 Reranker 直接接入生产 AnswerService？

离线质量收益明确，但平均增加约 2.33 秒、P95 增加约 2.96 秒。生产决策必须同时看质量和 latency，不能因为 Recall/MRR 提升就自动切默认链路。
## Day 15：Evidence Verifier

### 为什么 Retriever / Reranker 已经给了高相关结果，还要 Evidence Verifier？

Retriever 和 Reranker 优化的是 relevance：哪些 Chunk 与 Query 更相关、排序更靠前。可信回答需要额外判断 sufficiency：Evidence 是否真的支持目标主体、询问属性和值，以及主体和属性之间的关系。

典型失败是 Entity Scope Mismatch：Chunk 与 Query 在语义上高度相关，但描述的是另一个主体。高 relevance 不能把别的主体上的事实自动归因给目标主体。

### 为什么 refusal 不能只依赖生成模型自己的 confidence？

模型 confidence 不是经过校准的 Evidence 状态，也不能证明关键事实对应哪段 Evidence。TechPilot 将拒答前移到独立 Evidence Verifier：`INSUFFICIENT / CONFLICTING` 在生成前直接终止；只有 `SUFFICIENT` 才进入 Answer LLM。

这样 refusal 的依据是可检查的 evidence state / reason / source identity，而不是模型“感觉自己是否确定”。

### 为什么 Verifier 判 sufficient 后还要缩小生成 Context？

如果 Verifier 只认可 `SOURCE_1`，但 Answer LLM 仍能看到 `SOURCE_2/3`，模型可能从未验证来源提取事实，再错误引用 `SOURCE_1`。因此生成阶段只接收 verified supporting sources。

Citation 最终需要同时满足三层约束：

1. 来源真实进入 Context。
2. Verifier 明确认可为 supporting。
3. Answer LLM 实际引用该 Source，Citation 元数据再由服务端构造。

### 为什么 `insufficient` 只保留一个 primary reason？

如果目标主体都不匹配，那么“目标属性不存在”和“目标关系不存在”只是主体错误的下游结果。把三者同时记为失败原因会使评测不可解释，也不利于未来 Agent Controller 决定下一动作。

Day 15 使用最小决定性 taxonomy：

```text
no_evidence
→ subject_mismatch
→ attribute_missing
→ relation_missing
```

冲突单独进入 `conflicting / conflicting_evidence`。


## Day 16：P2 Ablation 与可审计评测

### 为什么 P2 做 ablation 而不是继续调参？
固定数据和配置比较有/无组件，证明收益来自哪里；继续围绕小 Golden 搜参会增加过拟合风险。

### Reranker 的收益/代价？
Recall@5 `0.766667 -> 0.866667`，3 rescue、0 regression；但增加约 2.32s mean / 2.96s P95 latency，所以不自动切生产默认。

### 为什么 Evidence 非空不能作为 Gate？
Retriever 判断 relevance，不判断是否支持主体/属性/关系或是否冲突。6-case 中 non-empty gate unsafe accept=4，Verifier=0。

### 为什么同时记录 git_sha 和 git_dirty？
SHA 只标识 commit；dirty run 还包含未提交代码，二者一起记录才能避免假可复现性。

---

## Day 18：Thin Agent / Thick Harness 与 Code RAG Foundation

### Harness 和 Agent 的职责怎么分？

Agent 负责 control flow：理解任务、有限规划、选择下一工具、判断继续或终止。Harness 负责 capability execution、schema validation、permission、timeout、Evidence、Context 和 Trace 等确定性治理能力。这样 Tool / Retrieval / Evidence 能力可以脱离具体 Agent framework 独立测试和复用。

### 为什么权限不能只写在 Prompt 里？

Prompt 是行为约束，不是 security boundary。TechPilot 在 ToolRuntime 和 RepositoryReadBoundary 层强制 read-only；即使控制层产生错误决策，也不能因此获得 repository escape、shell、write 或 destructive 权限。

### `.gitignore` 为什么不能替代 RepositoryReadBoundary？

`.gitignore` 只决定 Git 是否跟踪文件，不限制 runtime filesystem read。`.env` 即使从不提交，也仍可能被程序读取，因此需要单独 runtime exclusion。

### 为什么 `search_code` 和 `search_symbol` 分开？

`search_code` 回答“字符串出现在哪里”，注释和字符串字面量也可能命中；`search_symbol` 使用 Python AST 回答“哪里真正定义了 class/function/method”。两者分别承担 lexical discovery 与 structural discovery。

### ChatGPT 理解代码是不是靠 AST？

不能这样等同。LLM 可以直接对源码 token 做语义理解；AST 在 Code RAG Harness 中提供确定性的代码结构解析。系统组合 LLM 语义推理和 AST/symbol tooling，而不是拿其中一个替代另一个。

### 为什么 Search Result 不能直接作为 CodeEvidence？

Search result 只表达候选定位/相关性。CodeEvidence 还需要稳定 provenance：repository、file path、symbol、line range 和 authoritative snippet。后续 EvidencePack / Verifier 才能检查结论来自哪段真实代码。

### 为什么 snippet 必须重新从 repository 构造？

如果让模型或调用方直接提交 snippet，会出现伪造、漂移或与文件版本不一致。CodeEvidenceBuilder 根据安全 path + line range 从真实 repository file 重建 snippet，把 provenance 绑定到事实来源。

---

## Day 19：EvidencePack 与 Repo Explorer

### Repo Explorer 为什么不是“再造一个 Agent”？

Repo Explorer 当前是 Thick Harness 内的确定性 repository investigation service。它只编排现有 read-only tools 并交付 EvidencePack，不负责 LLM planning、长期状态或自主决策。核心价值是 Context Isolation：仓库探索产生的大量候选和中间 Tool Result 不直接污染未来主上下文。

### 为什么 Search Result 不能直接进入 EvidencePack？

Search Result 表示“在哪里可能相关”，不是“这段 Evidence 的正文来源已经得到验证”。最终 CodeEvidence 必须重新通过 `read_file` 获取 authoritative repository content，再根据 path + line range 构造 snippet。

### 为什么 Repo Explorer 不能直接调用 filesystem？

如果 Explorer 直接 `Path.read_text()`，虽然可能复用安全 path，但它绕过 ToolRuntime 的 schema、permission、timeout、structured error 和 trace boundary。Day19 让 Explorer 只依赖 Registry/Runtime，从结构上限制 bypass。

### `provenance_integrity` 和 `incomplete` 有什么区别？

`provenance_integrity` 回答“已经交付的 Evidence 是否还能绑定真实 repository content”；`incomplete` 回答“这次探索是否完整”。例如 AST 有两个 parse error 时，已读出的 Evidence 仍可能来源可信，所以 integrity 可以为 true，但 exploration 必须 incomplete=true。

### 为什么 tool truncation 不能当成普通 0 results？

truncated 代表系统主动停止返回更多结果，不代表剩余仓库没有匹配。如果把它压成 0 results，下游可能错误地产生“仓库中不存在”的强结论。因此 truncation 必须进入 EvidencePack failure metadata。

### Day19 为什么没有实现更丰富的 key_files / relationships / confidence？

当前 Day19 目标是冻结最小、可测试的 evidence handoff contract。更丰富的 relationships、unresolved questions、confidence label 和 Context compression 属于后续 Context/Explorer 演进；在没有真实任务评测前提前加入会扩大抽象面。

### Day20：为什么 Agent 系统需要 step-level trace？

简答：最终答案错误时，只看普通 application log 很难判断问题发生在搜索、工具执行、证据形成还是后续推理。Day20 用 lightweight AgentEvent 把 TOOL_CALL、TOOL_RESULT、EVIDENCE_HANDOFF 串在同一个 trace_id 下，使一次 repository investigation 可以被复盘。

追问：为什么 trace 失败不能让业务失败？

答：Trace 是 observability 横切能力，不是 repository investigation 的业务结果。若工具本身成功，但 trace backend 暂时不可用，系统仍应返回正常 EvidencePack；否则监控系统会反过来成为业务单点故障。

追问：为什么不把完整输入输出都写进 trace？

答：代码仓库内容和用户参数可能较大或敏感。第一版只记录 argument keys、output keys、状态、耗时、错误码等摘要，既保留调试价值，又避免 trace 形成新的敏感数据副本和存储放大。

一句话：Day19 解决“怎么调查并交可信证据”，Day20 解决“这次调查过程能不能被复盘”。

## Day 21–24：Code RAG 面试要点

### Code RAG 和文档 RAG 的共同点是什么？

都遵循“结构化切块 -> 多路召回 -> 候选融合 -> 权威正文回查 -> 证据化”的主线。区别在于代码域要额外保留 symbol、文件路径、行号、模块依赖等结构信息。

### 为什么代码 Hybrid 不直接加权关键词分数和向量分数？

两路 score 的定义和尺度不同，直接相加会引入不可解释的尺度偏差。当前使用 RRF，只依赖各路排名。

### 为什么 Hybrid 结果仍不能直接进入 EvidencePack？

Retriever 输出的是候选。最终证据必须重新读取真实源码，并验证 file path 与 line range 一致后构造 CodeEvidence。

### 为什么 Day 23 没有单独实现一套“代码引用”？

现有 `read_file -> CodeEvidence -> EvidencePack` 已经提供文件路径、symbol、行号和源码片段，并具备 provenance/incomplete 语义。再造一套引用对象会职责重复。

### Day 24 相比前面的 RAG 迁移新增了什么？

开始利用代码特有的静态结构：Python module、internal import dependency、top-level class/function。结构依赖与文本/向量相关性是不同信号，可用于回答“模块如何组织、谁依赖谁”这类问题。

---

## Day 25：静态调用关系

### 为什么模块 import dependency 还不够，还要 call relationship？

Import 只能说明模块之间存在静态依赖，不能说明某个函数内部具体调用了哪个函数/方法。Call clue 把粒度从 module-level dependency 下沉到 function/method call site，适合回答“这个入口往下调用了什么”“某个 service 如何串到 repository”这类 Code RAG 问题。

### 为什么叫 static call clue，而不叫完整 call graph？

因为 Python 是动态语言。AST 能确定源码里出现了 `foo()`、`obj.bar()` 这样的调用表达式，但不能仅凭源码保证运行时 `obj.bar` 实际绑定到哪个实现。依赖注入、多态、decorator、`getattr`、monkey patch 都可能改变真实运行路径。所以 TechPilot 明确把结果定义为静态调用线索，避免把可观察事实夸大成 runtime truth。

### 为什么 call clue 仍然不能直接作为 Evidence？

它首先是结构化 discovery signal。Repo Explorer 用它定位 call site 后，仍通过 `read_file` 获取真实源码，再按 file path + line range 构造 `CodeEvidence`。这样所有代码检索、symbol、module、call 能力最后共享同一 provenance boundary。

### 为什么没有直接上 graph database？

当前 P3 目标是支持文件/符号级检索和调用线索，不是建设通用程序分析平台。AST + deterministic DTO 已足以回答当前结构问题，也更容易测试。只有 Day26–28 的 Code RAG evaluation 证明多跳关系查询确实被当前实现卡住时，才有依据增加更复杂的 graph/index。

### 面试中一句话怎么讲 Day25？

“我没有把 Python AST 结果包装成完整调用图，而是保守地提取 function/method 内的 caller-callee call-site clues；这些线索只用于定位，最终源码证据仍必须通过统一的 read-only ToolRuntime 和 `read_file -> CodeEvidence` provenance 链路重建。”

## Day 27：为什么不能 query-time 全仓 AST 扫描

### 面试高频问法：你们的 Code RAG 怎么理解模块依赖和调用关系？

第一版先用 Python AST 实现 module/import/symbol/static-call 提取，用于验证结构能力本身。但在 Code RAG Golden evaluation 中发现，如果每个 query 都重新遍历、读取并 parse 整个 repository，这个方案只适合小仓库，不能作为真实检索主路径。

因此后续将 AST parsing 前移到 repository indexing 阶段，生成 structural snapshot 和倒排 postings。用户查询时先查结构索引得到少量相关 module/symbol/call candidates，再通过统一 `read_file → CodeEvidence → EvidencePack` 链路回查真实源码。

### 为什么还要 read_file？

索引只负责定位，可能过期、截断或存在结构解析局限。最终证据必须来自当前真实源码，所以 Retriever/structural index 的输出只是 candidate，不直接作为 authoritative Evidence。

### Day27 用什么数据证明改动有效？

同一套 12-case Golden Set：

- raw file hit：100%
- Explorer file hit：100%
- Evidence content hit：100%
- provenance integrity：100%
- module 目标文件从约第 73 位提升到第 1 位
- `.local/` 排除后 Python corpus 从 196 降到 161
- module Evidence noise 从 41 个 raw noise files 压缩到 7 个，压缩率约 80.95%

### 限制

当前 structural index 是内存 full rebuild，不是持久化增量索引。对更大 monorepo，下一阶段应考虑按 repository snapshot / git diff 做 incremental refresh；当前 P3 不提前引入图数据库或复杂基础设施。

## Day 28：P3 Gate 怎么讲

P3 Gate 没有因为 12 条样本全部命中就直接宣布 PASS。功能、回归、安全和 provenance 已经通过，但主计划要求更大的 Code RAG 评测覆盖，因此先给 CONDITIONAL PASS。

这体现两个原则：

1. 测试全绿不等于 Gate 自动通过；
2. 小 Golden 的 100% 不能被包装成系统在真实仓库问题上的 100% 准确率。

Repo Explorer 被保留，不是因为“更 Agent”，而是因为它有独立工程价值：隔离搜索噪声、统一 authoritative `read_file` 回查、构建 EvidencePack，并显式传播失败和 incomplete。

面试表达：

“P3 的 12-case 开发集最终实现了 file/Evidence/provenance 100% 命中，但我没有把这个数字直接当最终质量结论。Gate 时对照项目计划发现评测覆盖仍不足，所以将 P3 标成 conditional pass，下一步扩展到更大的人工 reviewed Golden，再决定最终关闭。这避免了在很小开发集上过拟合或夸大指标。”

<!-- DAY29_P3_FINAL_GATE -->
### P3 interview story — final (2026-08-16)

**Problem:** Same-repository Code RAG benchmarks can overstate generalization.

**What I did:** I froze 50 new TechPilot held-out cases, then evaluated two unfamiliar real
Python repositories with behavior-oriented queries that did not reveal expected symbols/paths.

**Results:**
- TechPilot: 48/50 file, 46/50 strict Evidence-content, 50/50 provenance.
- Buku first run: 12/15 file, 10/15 content, 15/15 provenance.
- yewtube fresh no-tuning: 9/10 file, 8/10 content, 10/10 provenance.

**What evaluation found:** Repository structure matters, exact Evidence granularity is harder
than file localization, and Hybrid is not automatically better than Dense.

**Failed experiment:** I hypothesized very large class chunks acted as semantic magnets.
A size guard passed tests but worsened external retrieval, so I reverted it instead of keeping
a plausible-looking change.

**Capability wording:** TechPilot provides evidence-grounded semantic code localization and
structural repository understanding through lexical+dense retrieval, AST/module/import/static
call clues and authoritative source re-reading. It does not claim complete control-flow,
data-flow or runtime program semantics.

**Key phrase:** `candidate != Evidence; index locates, source verifies.`

<!-- DAY31_33_P4_INTERVIEW_START -->
## P4 Day31–33 Interview Notes

### 1. 你怎么证明这不是固定 Workflow？

真实双机制任务要求同时解释 `RepoExplorer` 和 `ToolRuntime`。

Agent 第一轮先取得 `RepoExplorer` Evidence；第二轮看到当前 Evidence 仍不能覆盖 ToolRuntime permission/timeout 机制后，运行时动态选择了新的 `ToolRuntime` research action，最终 2 steps 完成。

第二步不是设计时固定路径，而是由当前 Evidence gap 决定。

### 2. 为什么后来不用 Planner / Verifier / Action Selector 三个 LLM？

这三个角色都有大量重叠的语义判断：理解当前任务、判断 Evidence 缺口、决定下一步。

主路径收敛为一个 Unified Reasoner，减少：

- 多次 LLM call；
- 多个 prompt 对 State 的不一致解释；
- 不必要的组件边界。

但 schema / permission / timeout / hard termination 等确定性职责没有交给 Unified Reasoner，而是继续留在 Harness。

面试表达：

> 我没有把所有逻辑都交给一个 LLM。我只把重叠的语义决策合并，硬约束仍然由 deterministic Harness 管。

### 3. 为什么不是所有任务都用最大模型？

Day33 将执行分成：

```text
Workflow
Light Agent
Research Agent
```

固定操作直接 Workflow，不付 LLM 成本。

聚焦任务使用低自由度 Light Agent：

- Flash；
- 小 step budget；
- 明确 symbol 先 deterministic search；
- 再让模型做必要语义判断。

复杂、多机制任务才给 Research Agent 更强模型和更高行动自由度。

核心不是“永远用最强模型”，而是：

> 根据任务复杂度配置足够的智能、行动自由度和预算。

### 4. 讲一个真实 Agent failure analysis

Light Agent 研究 ToolRuntime timeout 时已经命中正确文件，但仍达到 max steps。

最初怀疑 Flash 不够强。通过受控实验逐层排除：

1. 固定 Evidence 比较 Flash / Pro；
2. 固定第一次 action；
3. 抓真实第二轮 prompt；
4. identical prompt 多次重放；
5. 检查关键 supporting span 在实际 context 中的位置。

最后发现不是模型不会，也不是搜索不到，而是 Evidence 用固定 prefix 截断：

```text
wait_for / timeout_seconds 在 2200 chars 内
ToolErrorCode.TIMEOUT 在约 2249 chars
```

模型看不到完整 timeout-result handling，所以合理地继续检索。

在不增加 token、不升级模型的情况下，改成 query-focused Evidence window 后任务完成，step `2 → 1`，该次实验 latency 约 `3237ms → 1644ms`。

面试价值：

> Agent 失败不能一律归因于模型。需要区分 routing、tool selection、retrieval、Evidence materialization、decision context 和 control budget，并通过 trace + controlled A/B 定位。

### 5. 为什么 Day33 后新增 Decision Context Coverage？

因为：

```text
Source Coverage = 1
```

只说明找到了正确文件。

如果实际喂给模型的窗口没有关键事实，模型仍然无法 grounded completion。

所以分开评估：

- source coverage；
- decision-context coverage；
- grounded completion。

这能避免用“检索命中正确 source”掩盖真正的 context engineering failure。

### 一句话架构表达

> Router 决定任务值得花多少智能和预算；Reasoner 根据当前 Evidence 决定下一步想做什么；Harness 决定能不能安全执行；Evidence 决定事实；Evaluation 判断系统是否真的有效。
<!-- DAY31_33_P4_INTERVIEW_END -->

<!-- DAY34_37_INTERVIEW_START -->
## P4 Day34–37 Interview Notes

### 1. 你们怎么定位 Agent 失败，而不是都归因于模型？

我把失败拆成几个可观测层：

```text
task understanding
→ planning / obligation
→ tool selection / schema
→ provider/tool failure
→ retrieval candidate
→ authoritative Evidence
→ decision context
→ verifier
→ control / termination
→ final synthesis
→ evaluation contract
```

Day33–37 多次证明“正确文件已经找到”仍可能失败，所以不能把所有 failure 简化为 Retriever 或模型能力。

---

### 2. 为什么 Source Coverage 不够？

Source Coverage 只证明目标文件出现。

真实 Agent 还要问：

- 关键 span 是否被 materialize；
- 是否进入当前 decision context；
- 是否覆盖用户每个 requirement；
- final decision 是否真正 grounded。

所以后来分开：

```text
source coverage
decision-context coverage
semantic requirement coverage
grounded completion
```

---

### 3. 讲一个典型的 control-state bug

`RepoExplorer` 是 composite capability。

执行成功后：

```text
last_tool_result = None
evidence_pack = ...
```

Reasoner 把 `null` 误解成“上一动作没有结果”，即使 source coverage 已经是 1，仍重复相同 action。

我新增 `ActionExecutionOutcome`，把 primitive ToolResult 和 action-level result 分开。

同一 targeted case：

```text
permanent failure / 3 ACT / 5 LLM
→
completed / 2 ACT / 3 LLM
```

---

### 4. 为什么 Provider 不自己 Retry？

如果 provider 内部 retry：

- control layer 看不到真实次数；
- 可能绕过 global retry/cost/time budget；
- trace 无法解释 amplification。

所以：

> Provider classifies; orchestrator owns retry policy.

Provider 返回：

```text
failure code + retryable
```

Control 决定：

```text
retry / retry exhausted / permanent failure
```

---

### 5. Composite capability 为什么要传播内部 failure？

RepoExplorer 最后可能仍返回 EvidencePack，但内部 `read_file` 可能 timeout。

Domain result 存在不代表 operational execution 健康。

因此 current-action retryable failure 必须上送 control，同时历史 issue 保留用于审计。

---

### 6. 为什么 max_steps 后还允许一次 Reasoner decision？

`max_steps` 应限制的是昂贵/有副作用的 ACT 次数。

最后一个 ACT 已经拿到 Evidence 后，如果马上 MAX_STEPS，系统连“证据已经足够”都没机会判断。

正确语义：

```text
N 次 ACT budget
+ 最后一次 final semantic decision
```

但不允许第 N+1 次 Tool 执行。

---

### 7. 讲一个真实的文件安全 bug

Repository binary check 固定读 8192 bytes。

中文 UTF-8 多字节字符刚好被截断时，strict decode 抛错，合法 Markdown 被误判 binary。

改成 incremental decoder `final=False`，只忽略 sample 尾部 incomplete sequence，仍拒绝 NUL 和真实 malformed UTF-8。

这是“安全机制 false positive”而不是业务解析 bug。

---

### 8. 为什么 exact path 不应该再走 Retrieval？

用户已经给出：

```text
app/research/unified_agent.py
```

此时 source uncertainty 已经消失。

直接：

```text
RepositoryReadBoundary
→ ToolRuntime
→ read_file
→ CodeEvidence
```

更确定、更便宜，而且仍然保留 permission/provenance/trace。

---

### 9. 为什么 tests 不能替代 production implementation Evidence？

Test 可以证明：

> 这个行为被验证过。

但它不能单独证明：

> 生产逻辑在哪里、如何 enforce。

Day37 的 provider-timeout case 曾经用 test assertions 回答 bounded retry implementation，final answer 看起来正确但 source role 错。

加入 source-role contract 后，该 unsafe COMPLETE 转为 safe NO_ACTIONABLE。

---

### 10. 为什么你们不把最后 2 个失败调成 6/6？

Day36 final canonical 已经：

- 0 false completion；
- 0 benchmark leakage；
- 0 benchmark exception；
- avg source coverage 91.7%。

剩余失败主要是：

- semantic source/query planning；
- obligation expansion / multi-obligation decomposition。

继续为具体 case 写 path heuristic 会过拟合 benchmark。

我选择记录 limitation 和 trace，进入下一真实业务阶段。

---

### 11. Tiered Agent 的价值是什么？

不是“便宜模型也能赢大模型”。

Day34 24-case 总体质量没有显著拉开。

真正结果是 Light subset 在相同质量下：

- calls 显著降低；
- tokens 显著降低；
- latency 显著降低；
- cost 显著降低。

所以：

> Routing / tiering 是把足够的智能分配给足够复杂的任务，而不是所有任务都最大模型。

---

### 12. Research success 和 Delivery success 为什么要分开？

旧 finalizer 只列 Evidence path。

这对内部 debug 可以，但用户真正需要的是：

```text
结论
+ 不确定性
+ Sources
```

Day37 新增 DecisionReportFinalizer。

现在可以单独评：

```text
research_success
delivery_success
```

这避免“内部已经找到证据”被错误当作产品功能完成。

---

### 13. 复杂业务 Case01 为什么失败有价值？

Release-readiness review 有 6 个明确 obligations，但 Research profile 只有 5 ACT steps。

Agent 没有 obligation-aware budget allocation，大量 step 都花在 API entry discovery。

这证明：

> 单机制 Agent 成功并不等于多目标业务 Agent 可用。

该失败会自然成为 P6 任务规划/依赖/优先级设计的真实输入，而不是现在临时堆 planner。

---

### 14. 一句话概括 P4

> 我们不是把 LangGraph 接通就算完成，而是用真实 workload 和 failure injection 把 Agent 拆成语义决策、确定性控制、Evidence、Trace 和 Evaluation 五个边界，并且专门修了 hidden retry、composite action state、false completion、benchmark leakage 和 final delivery 等生产问题。
<!-- DAY34_37_INTERVIEW_END -->

<!-- DAY37_5_PRODUCT_UI_START -->
## Day37.5 — Product UI / delivery boundary

**为什么没有直接上 React/Vite？**

当前项目的核心价值在 Python/FastAPI + RAG/Agent Harness，而不是前端框架。Day37.5 选择 FastAPI 直接托管零构建 UI，减少部署面和依赖，同时保留未来拆独立 SPA 的空间。

**如何避免 Demo UI 伪造能力？**

所有可操作功能都落到真实 API；没有 persistent document listing 时只展示 session-local source state；P5 JD backend 尚未存在时入口明确 disabled。UI 不把“将来会有”渲染成“现在已经有”。

**Workspace 为什么补 lifecycle API？**

原先 API 依赖已有 `workspace_id`，但真实用户不应该手工输入数据库 ID。补 `list/create/delete` 后，ID 退回 backend identity，用户只操作 workspace 名称与选择状态。

**为什么删除 Workspace 会 409？**

Workspace 下仍有 active documents 时直接删除会破坏 source lifecycle 语义。先通过 document delete 完成 PostgreSQL soft-delete + best-effort vector cleanup，再允许删除空 Workspace，属于 fail-closed product boundary。
<!-- DAY37_5_PRODUCT_UI_END -->

<!-- EVAL_BACKFILL_20260822_START -->
## Evaluation Backfill / 第一版简历面试补充

### 简历上的数字应该怎么解释

本轮数据用于证明“我有系统评测、failure attribution 和受控优化”，但不是所有集合都属于 clean heldout。

面试中如果被问数据来源，应主动说明：

> Document 400、Answer 180、Research Agent 36 都是冻结的工程评测集，但 lineage 中包含 assistant/machine validation，我不会把它们包装成人工独立 heldout。Code RAG 另外补了 30 条从真实工程问题出发的 task-oriented hard cases，用于检查自动生成 benchmark 的 construction bias。

这种表述比“我有几百条 Golden，全是人工”更可信。

---

### Q：RAG 这部分最硬的量化结果是什么？

我把 30 份技术文档冻结成 2345 个 canonical units，并建立 400-case frozen retrieval evaluation。

Dense baseline：

```text
Recall@5 61.5%
MRR@5    44.4%
nDCG@5   47.8%
```

最终使用：

```text
E5 Dense + BM25 + RRF + CrossEncoder
```

得到：

```text
Recall@5 83.4%
MRR@5    72.6%
nDCG@5   74.9%
Coverage 84.7%
P95      867.4ms
```

不是通过无限 grid search 得到的。我分别验证 chunking、fusion 和 reranking，只保留高信息量实验。1200-char/no-overlap 最终优于几个 overlap 方案，Hybrid 后再加 CrossEncoder 带来 Recall `+8.3pp`，代价约 `+125ms P95`，属于可解释质量/延迟 trade-off。

---

### Q：你怎么保证“回答正确”不是模型自己猜对？

回答链路是：

```text
Retriever
→ authoritative PostgreSQL Chunk
→ Context
→ Evidence Verifier
→ verified supporting sources only
→ Generator
→ Citation validation
```

Qdrant 只做可重建索引，不是正文事实来源。

180-case answer/evidence evaluation 中，146 条实际回答；assistant audit 的 full correct 为 `140/146 = 95.89%`。同时 strict citation precision / recall 单独统计，不把“答案看起来对”当成 evidence correctness。

20-case source-binding adversarial set 上，Verifier Policy v2 后 false-answer 为 `5%`。

---

### Q：Code RAG 为什么分成 150-case 和 hard-30？

因为我发现自动从 symbol/docstring 反向生成的代码题会天然偏容易。

所以没有删除 150-case，而是把它重新定义为：

> structural / regression benchmark

结果：

```text
File Hit@5    94.7%
Content Hit   89.3%
Symbol Hit    87.7%
nDCG@5        88.8%
Provenance   100%
```

然后另外从真实工程问题出发写 30 条 task-oriented natural-language query，不把 file path / symbol 暴露给 query：

```text
File Hit@5    93.3%
Content Hit   93.3%
Symbol Hit    80.0%
MRR           74.3%
Provenance   100%
```

hard-case review 还发现 4 个所谓 symbol miss 其实拿到了 enclosing class，说明 metric granularity 也要被审计，而不是只追分数。

---

### Q：Research Agent 最值得讲的 failure 是什么？

36-case backfill 第一轮只有 16.7% pass，而且 27/36 出现至少两次 zero-evidence action。

如果只看结果，很容易继续调 prompt。

我先查 action trace 和 capability surface，发现旧 Day34 evaluator 只注册：

```text
search_symbol
search_code
read_file
```

但当前 RepoExplorer 已经支持：

```text
dense
keyword
hybrid
module
call
path
```

这意味着第一轮主要测到的是 evaluator/integration harness 落后，不是最终 Agent。

保持 dataset / Golden 不变，只补齐 full Code RAG capability surface 后：

```text
case pass                16.7% -> 55.6%
source coverage           41.7% -> 78.3%
negative correctness      33.3% -> 100%
>=2 zero-evidence cases      27 -> 5
false completion                0
provenance                    100%
```

注意：16.7% 不是我对外宣称的产品 baseline，而是一个 wiring-mismatch diagnostic。

这次经历让我确认：

> 评测 Harness 的 capability parity 本身也必须被验证，否则会把 integration bug 错归因给模型。

---

### Q：Agent 现在还有什么明显问题？

我不会说 Research Agent 已经完全解决。

full-surface 36-case 中：

```text
source-role production authority     6/6
unsupported production claim         6/6
failure recovery                      3/6
known-source refinement               3/6
multi-obligation                      2/6
obligation persistence / goal drift   0/6
```

最明显的问题是复杂多义务任务。

Underlying Hybrid retrieval 已经有很强信号：36-case 中 Hybrid 调用 38 次，没有一次 zero-evidence。剩余 failure 更集中在：

```text
semantic planning
multi-obligation decomposition
unresolved obligation persistence
next evidence-gap selection
known-source refinement
```

我还做了一个 `PREFIX -> QUERY_FOCUSED` 的受控 context experiment，但 12 条 hardest cases 反而变差，所以直接 reject，没有为了把 benchmark 调漂亮继续 prompt sweep。

---

### Q：为什么 0 false completion 比 overall pass 更重要？

Agent 在 evidence 不足时最危险的不是“没做完”，而是“看起来做完了”。

我专门加入 6 条 repository 无法证明的 production claims，例如：

- 真实线上 uptime；
- 当前活跃用户；
- 本月云成本；
- 最新外部漏洞状态；
- 真实生产流量质量；
- 最近一次生产故障恢复时间。

结果：

```text
negative correctness 6/6
false completion     0
provenance integrity 100%
```

这说明当前系统的 fail-closed boundary 是成立的，即使复杂业务 task success 仍有提升空间。

---

### Q：你做 Evaluation Backfill 的核心目的是什么？

不是单纯把 case 数做大，而是解决三个问题：

1. 简历上的数字有没有可追溯来源；
2. 指标失败到底属于 retrieval、context、control、provider 还是 evaluation contract；
3. 每个优化有没有 before/after，而且有没有被真实失败支持。

所以本轮最后并没有继续追求更高 benchmark 分数，而是把 Document / Answer / OCR / Code RAG / Research Agent 都关到一个“有数字、有 failure、有 limitation”的状态，然后进入 P5 真实 JD 业务。

---

### 第一版简历推荐表述（项目经历）

**TechPilot｜基于 RAG、Code RAG 与 Agent Harness 的开发者技术调研平台**

- 构建 FastAPI + PostgreSQL + Qdrant 的 evidence-grounded RAG 链路，围绕 30 份技术文档建立 400-case frozen retrieval evaluation；将 Dense baseline 升级为 **E5 Dense + BM25 + RRF + CrossEncoder**，Recall@5 **61.5%→83.4%**、MRR@5 **44.4%→72.6%**、nDCG@5 **47.8%→74.9%**，最终 P95 约 **867ms**。
- 设计 Evidence Verifier / source binding / citation gate，将检索相关性与证据充分性解耦；180-case Answer/Evidence 评测中 146 条实际回答，assistant audit full-correct **95.9%**，20-case source-binding adversarial set 最终 false-answer **5%**；Provider 层实现 bounded transient retry 与 structured-output repair。
- 实现只读 **Code RAG + Thick Harness**：RepositoryReadBoundary、typed ToolRuntime/Registry、authoritative `read_file -> CodeEvidence -> EvidencePack`、keyword/dense/hybrid 与 AST module/call 检索；150-case structural regression File Hit@5 **94.7%**、Content Hit **89.3%**、provenance **100%**，30-case realistic task set File/Content Hit **93.3%**。
- 基于 LangGraph 构建 bounded Research Agent，将 semantic reasoner、ToolRuntime、Evidence、Trace、retry/termination 分层；36-case backfill 对 unsupported production claims **6/6 正确拒绝、0 false completion、100% provenance**，并通过 failure attribution 定位 multi-obligation decomposition / goal drift 为当前主要瓶颈，而非继续对 Retriever/Prompt 做无界调参。

> 简历正文建议优先放前三条或四条；若版面只有 3 bullets，保留第 1、3、4 条，第 2 条压缩进第 1 条。

---

### 30 秒项目介绍

> TechPilot 是一个面向开发者技术调研和代码理解的 evidence-grounded LLM 系统。我从文档 RAG 做起，自己实现了 Dense/BM25/RRF/Reranker、Evidence Verifier 和 Citation binding；之后做了只读 Code RAG，把 AST 结构检索、语义检索和 authoritative source materialization 接进统一 Tool Harness；P4 再用 LangGraph 做 bounded Research Agent。这个项目我比较强调评测和 failure attribution，比如 400-case retrieval 上 Recall@5 从 61.5% 提到 83.4%，Code RAG realistic hard set File/Content Hit 是 93.3%，Agent 对无法由仓库证明的 production claims 做到了 0 false completion。评测回填已完成；Job Intelligence 真实业务 prototype 已冻结；下一阶段先论证 AI Coding 相对 Codex/Claude Code/Cursor 的差异化价值。

---

### 2 分钟项目介绍骨架

1. **业务问题**：开发者技术调研、代码仓库理解，以及独立验证过的 Job Intelligence 业务 prototype。
2. **RAG**：PostgreSQL 是事实来源，Qdrant 是可重建索引；Dense → BM25/RRF → CrossEncoder；Evidence Verifier 决定能不能回答。
3. **Code RAG**：不能把 retrieval hit 当 evidence；所有 candidate 必须再走 `read_file`，绑定 file/symbol/line/snippet；AST 只提供 structural clue。
4. **Agent**：LLM 只负责 semantic next action；Harness 负责 schema/permission/timeout；Control 负责 max steps/retry/termination；Evidence 决定事实。
5. **评测**：分 source coverage、decision-context coverage、grounded completion；避免把“找到了文件”包装成“任务成功”。
6. **一个真实失败**：Research backfill 先发现 evaluator capability wiring 落后，修复后 source coverage 大幅提升；之后 targeted query-focused experiment 变差，明确 reject，剩余问题定位为 multi-obligation planning。
7. **当前边界**：不是通用 Coding Agent，不执行 shell/write/git；Research Agent 对复杂 goal drift 仍有限制；下一步进入 JD 结构化与能力证据业务。

<!-- EVAL_BACKFILL_20260822_END -->

<!-- P5_DAY38_20260824_START -->
## Day 38：JD Structured Output / Job Intelligence

### Q：为什么 JD extraction 不直接做成 Agent？

因为当前控制流是确定的：

```text
JD text
→ LLM structured extraction
→ schema validation
→ bounded repair
→ result / fail
```

它没有“根据 evidence gap 动态决定下一工具”的必要。把固定 workflow 包成 Agent 只会增加 state、tool、trace 和 failure surface。

我的原则是：

> Dynamic decision 才值得 Agent；固定转换优先 constrained workflow。

### Q：为什么模型返回 JSON 还需要 Pydantic？

JSON 只说明语法可能有效，不说明业务 contract 有效。

例如模型可能返回：

- 非法 enum；
- `evidence_span` 类型错误；
- required/preferred 值超出允许集合；
- 缺字段；
- 多余字段。

所以模型输出进入 domain 前必须：

```text
raw model output
→ JSON decode
→ Pydantic validation
→ bounded repair
→ final typed object
```

### Q：bounded repair 和 retry 有什么区别？

Retry 通常重新执行同一 provider operation；repair 是针对一个已返回但结构不合法的候选结果做一次受约束修复。

关键是：

- repair 次数有限；
- repair 只能修能确定的结构问题；
- 不为了过 schema 猜测缺失业务语义；
- repair 仍然必须再次 validation。

### Q：为什么 evidence span 比 normalized skill 更重要？

`normalized_skill="RAG"` 是系统归一化结果，不是 JD 原文事实。

必须保留：

```text
original JD
→ exact evidence span
→ normalized capability
```

这样才能回答：

- 这个 requirement 真的是 JD 写的吗？
- required/preferred 判断基于哪句话？
- evaluator 如何检测 hallucination？
- 后续 UI 如何展示来源？

### Q：为什么 Job matching 不接 Code RAG？

这是两个不同问题。

Job matching：

```text
JD requirements
↔ user capability profile
```

Code RAG：

```text
repository query
→ authoritative code evidence
```

P5 不规划 repository evidence workflow。它的正式用户需求只有：岗位要求→岗位推荐、简历→岗位推荐、简历+JD→匹配分析。Code RAG 属于独立的代码理解产品能力，不进入这些链路。

### Q：为什么 Mock provider 测试通过仍不能算真实 Job Discovery？

Mock 只能证明：

- provider interface；
- normalize/filter/dedupe；
- service composition；
- failure-free contract。

它不能证明：

- 当前网页/API 能找到真实岗位；
- JD 是否完整；
- source 是否稳定；
- location/salary/title parsing 是否可靠；
- 页面变化/anti-bot/过期岗位如何处理。

所以：

> contract test ≠ real-world product validation。

### Q：这次最值得讲的工程失败是什么？

P5 一度因为“项目已经有 Agent Harness”而错误扩展为第二套 Job Agent，并把 Job JD 包装进 Code Evidence。

我最终回到业务控制流重新划边界：

```text
JD extraction = constrained workflow
Job discovery = provider abstraction
Code RAG = independent repository evidence capability
```

然后删掉重复 Agent/runtime 模块并做全量回归。

这个失败说明我现在会先判断：

1. 业务是否需要动态决策；
2. domain boundary 是否清楚；
3. 是否在错误复用基础设施；
4. tests 是否只证明 contract 还是已经证明真实业务。

### Q：全量测试过程中还发现了什么基础设施问题？

两个比较典型：

1. localhost Qdrant 被系统 HTTP proxy 转发，容器正常但应用请求返回 502。最终仅对 loopback URL 禁用 proxy env。
2. dependency health 使用业务 asyncpg pool，sync TestClient loop 创建的 connection 被另一个 pytest asyncio loop 复用，触发 `Future attached to a different loop`。最终 health probe 使用独立 `NullPool`，业务 pool 保留。

这两个问题都属于：

> dependency 进程“在运行”不等于 application connectivity / async lifecycle 正确。

### Day38 当前面试表述边界

可以说：

- 实现了 typed JD structured extraction；
- 有 Pydantic validation / bounded repair / exact evidence binding；
- 建立了 provider-neutral Job Discovery 与 optional profile matching foundation；
- full regression 已通过。

暂时不要说：

- 已完成真实岗位推荐系统；
- 已覆盖 30–50 份真实 JD；
- production Job Discovery 已上线；
- P5 Gate 已 PASS；
- Job Agent 已完成。

这些都缺真实业务证据。
<!-- P5_DAY38_20260824_END -->

<!-- TECHPILOT_JOB_INTELLIGENCE_INTERVIEW_START -->
## Job Intelligence closeout — interview framing

### Q: P5 最后做完了吗？

不能说“完整做完”。

准确表述：

> 我把 Job Intelligence 做到了真实业务 prototype：牛客和实习僧的真实岗位能完成 acquisition -> 中文 JD structured extraction -> evidence binding -> Flow A 推荐链路；但 Resume Flow B/C 的真实 E2E 没有关闭，BOSS 的稳定 full-JD discovery 也没有解决，所以我没有把它包装成 production-ready P5 PASS。

### Q: 为什么没有继续把 BOSS 爬通？

> 因为真实验证后，系统的主要瓶颈已经从模型/JD matching 转移到了招聘站数据获取。继续投入会把项目重心变成 DOM、登录态、security challenge 和页面适配。我做了 browser connector 验证这种路径能拿到 listing，但它削弱了“系统主动帮用户找岗位”的产品价值。因此我选择冻结 prototype，把这个 failure 当作产品边界，而不是为了完成 roadmap 强行继续。

### Q: 这个阶段最有价值的技术问题是什么？

- 不信任模型字符 offset，应用确定性绑定 evidence；
- source failure 不能伪装成 zero match；
- internet real-data gate 不能要求任意前 N 条 100%；
- batch analysis 必须隔离单条失败；
- full regression 不能替代 real-business validation；
- source coverage 是独立于模型质量的产品指标。

### Q: Job Intelligence 和 Code RAG 是不是连在一起？

不是。

Job Intelligence 的输入是岗位意图/简历/JD；Code RAG 是仓库理解能力。没有 `JD requirement -> repository evidence` 的 P5 产品链路。

### Q: 下一步为什么考虑 AI Coding？

因为它自然复用 TechPilot 已经完成的 Code RAG、RepositoryReadBoundary、ToolRuntime、EvidencePack 和 bounded agent control。

但真正的第一题不是“能不能写代码”，而是：

> 为什么用户不用 Codex / Claude Code / Cursor？

如果差异只剩“也会 search/edit/test”，这个方向就不成立。必须先证明 TechPilot 在某类 coding workload 上有可评测的独特价值，再进入 implementation。
<!-- TECHPILOT_JOB_INTELLIGENCE_INTERVIEW_END -->
