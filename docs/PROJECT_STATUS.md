# TechPilot PROJECT_STATUS

## 当前版本

v0.7-dev

## 当前阶段

P3：只读 Code RAG / Harness — Day 19 EvidencePack + minimal Repo Explorer = PASS；P2 production boundary 继续 Dense-only

## 阶段状态

- P0 工程骨架：完成
- P1 文档 RAG：工程闭环完成，但质量 Gate = `FIX`
- Day 11：P1 answerable 质量复验完成，定位检索召回为主要瓶颈
- Day 12：BM25 实现、正式评测与 Dense/BM25 对比完成
- Day 13：RRF Hybrid Retrieval、真实评测、失败分析与 Agent Runtime ADR 完成
- Day 14：Cross Encoder Reranker、30-case 质量/延迟实验、候选深度边界修正与 ToolContract/ToolResult 字段冻结完成
- Day 15：Evidence Verifier、evidence-driven refusal、正式 6-case Evidence Eval 与未来 Tool Schema 冻结完成
- P2 高质量 RAG：capability Gate = PASS；production Retrieval = Dense-only
- P3 Code RAG：Day 19 EvidencePack + minimal Repo Explorer 完成
- 下一步：Day 20 lightweight AgentEvent trace foundation

## 已完成

### P0 / P1 核心能力

- FastAPI、PostgreSQL、Redis、Qdrant、SQLAlchemy、Alembic 工程骨架
- Markdown / 文本型 PDF 摄取与结构优先 Chunking
- 稳定 Chunk ID 与 PostgreSQL 权威正文存储
- `multilingual-e5-base` Dense Retrieval 与 Workspace 隔离
- DeepSeek 可信回答、Citation 绑定、证据不足拒答
- Document 软删除、Qdrant Best-effort Cleanup 与删除后隔离
- Upload -> Persist -> Index -> Retrieve -> Answer -> Cite -> Delete -> Refuse 生命周期验证

### Day 11：P1 质量复验

- 建立 7 条 `answerable=true` 生产评测样本
- Answer correctness：4/7
- Citation support：4/7
- Over-refusal：1/7
- Runtime errors：0
- 三个失败目标 Dense rank：56 / 1120 / 26
- 生产 Prompt 增加 `SOURCE_N` 输出隔离规则
- P1 Gate 最终更新为 `FIX`
- 结论：继续改 Prompt 不能弥补权威 Chunk 未进入 Top-K，后续优先做检索优化

### Day 12：BM25 Baseline

- 新增 BM25 tokenizer、DTO、Repository、Retrieval Service
- 中英混合 tokenizer 保留技术标识符和数字，中文使用 jieba
- 配置 `bm25_k1=1.5`、`bm25_b=0.75`
- BM25 合法候选集限定为：当前 Workspace、`deleted_at IS NULL`、Document 状态 COMPLETED/PARTIAL
- BM25 与 Dense 使用同一 Chunk 身份空间
- 新增 tokenizer、评分、参数、limit、Workspace 和语料过滤测试
- 新增 `scripts/bm25_retrieval_eval.py`
- 完整自动化回归：PASS

### Day 13：RRF Hybrid Retrieval

- 新增纯 RRF fusion：只使用 rank，不混合 Dense/BM25 原始 score
- 使用统一 `chunk_id` 做跨 Retriever dedupe；同一路重复 Chunk 不重复贡献
- 新增 `HybridSearchHit`，保留 `dense_rank`、`bm25_rank`、两路原始 score 与 `rrf_score`
- 新增独立 `HybridRetrievalService`
- 明确 `candidate_limit` 与最终 `limit` 的职责边界
- 对 Dense/BM25 共享 Chunk 做 identity consistency 校验
- 新增 RRF 与 Hybrid Service 自动化测试
- 新增 `scripts/hybrid_retrieval_eval.py`
- 评测脚本先校验 Golden 与当前 legal corpus，再运行 Dense/BM25/RRF
- 真实 Hybrid Service smoke：PASS
- 完整自动化回归：154 passed
- `git diff --check`：PASS
- 生产 `AnswerService` 保持 Dense-only；Day 13 未提前接入 Hybrid
- 新增 `docs/adr/ADR-001-agent-runtime.md`，冻结 Thin Agent / Thick Harness 与 v1/v2 边界；未实现 Agent Runtime

### Day 14：Cross Encoder Reranker

- 新增稳定 `RerankerProvider` Protocol，第三方模型只负责 `query + documents -> relevance scores`
- 新增 Sentence Transformers `CrossEncoder` adapter，正式模型使用 `BAAI/bge-reranker-v2-m3`
- `max_length=512`，正式实验设备为 Apple MPS
- 新增 `RerankCandidate` / `RerankedSearchHit`，保留原始 Hybrid diagnostics 与 reranker score/rank
- 新增独立 `RerankingService`
- Reranker 正文只从 PostgreSQL 权威 `Chunk` 回查，不把完整正文塞入 Qdrant / Hybrid DTO
- 新增 Provider、DTO、Service focused tests 与真实模型 smoke
- 修正深度语义：`candidate_limit` 是 Dense/BM25 各自召回深度；fusion/rerank pool 理论上最多为 `2 * candidate_limit`
- 正式配置冻结为 `candidate_limit=20 / rerank_depth=20 / top_k=5 / rrf_k=60`
- 30 条 strict Golden 上，Hybrid+Reranker：Recall@5 `0.866667`、MRR@5 `0.766667`、MISS 4
- 相比 Hybrid：Recall@5 绝对提升 `+0.100000`，MRR@5 提升 `+0.178334`
- Reranker rescue 3 条：case 11 `7 -> 1`、case 15 `9 -> 1`、case 26 `11 -> 2`
- Regression：0；原 Hybrid Top-5 命中保留 `23/23`
- `rerank_depth=40` 有界诊断没有新增质量收益，但显著增加延迟，因此拒绝采用
- 完整自动化回归：183 passed
- `git diff --check`：PASS
- 生产 `AnswerService` 仍保持 Dense-only；Day 14 没有因离线质量提升直接切生产路径
- ADR 冻结 `ToolContract` / `ToolResult` 最小字段，不实现 Tool Registry / Agent Runtime

### Day 15：Evidence Verifier / Evidence Sufficiency

- 新增 provider-neutral `EvidenceVerifierProvider`
- 新增 Pydantic `EvidenceItem` / `EvidenceVerificationInput` / `EvidenceVerificationResult`
- Evidence provenance 使用 `source_type / source_ref / title / locator / text`，不把 Verifier 契约绑死在 Document Chunk，可直接映射未来 Code/Web evidence
- Evidence state 固定为 `sufficient / insufficient / conflicting`
- 不充分原因固定为 `no_evidence / subject_mismatch / attribute_missing / relation_missing / conflicting_evidence`
- `insufficient` 采用单一 primary reason，避免主体错误继续级联为属性/关系缺失
- Prompt version 冻结为 `evidence-verifier-v2`
- Verifier 输出拒绝未知 Source、Source role 重叠、重复 Source、额外字段以及非法 state/reason 组合
- 生产 `AnswerService` 保持 Dense-only Retrieval，但生成前新增 Evidence Verifier gate
- `INSUFFICIENT / CONFLICTING` 在生成模型调用前直接拒答
- `SUFFICIENT` 后生成模型只能看到 Verifier 明确认可的 supporting sources
- 最终 Citation 只能绑定 Verifier 标记为 supporting 且真实进入 Context 的来源
- 生成模型若在 Verifier 判定 `SUFFICIENT` 后再次自行拒答，视为状态不一致错误，而不是合法 evidence decision
- 新增真实 DeepSeek smoke 和独立 Evidence Verifier evaluation
- 6-case reviewed local Golden 覆盖 2 sufficient / 3 insufficient / 1 conflicting
- 正式 Evidence Eval：state 6/6、primary reason 6/6、runtime errors 0
- 完整自动化回归：227 passed
- `git diff --check`：PASS
- Evidence Verifier I/O 已可导出 JSON Schema，满足未来 `verify_evidence` Tool Schema 复用要求
- Day 15 未实现 Tool Registry / Agent Runtime / Repo Explorer / Coding Agent

### Day 16：P2 Ablation + Trace Identity

- 固定最小 ablation matrix，不继续对 30 条 Golden 做无界参数搜索。
- Dense `0.700000/0.500000`；BM25 `0.700000/0.567778`；Hybrid `0.766667/0.588333`；Hybrid+Reranker `0.866667/0.766667`（Recall@5/MRR@5）。
- Reranker vs Hybrid：Recall `+0.100000`、MRR `+0.178333`、rescues=3、regressions=0、retained=23/23。
- Reranker added latency mean `2318.09 ms` / P95 `2962.15 ms`；total mean `2990.90 ms` / P95 `3684.15 ms`。
- Evidence ablation：legacy non-empty gate accuracy `0.333333`、unsafe_accepts=4；Evidence Verifier accuracy `1.000000`、unsafe_accepts=0、over_refusals=0。
- Cost proxy：Generator calls `6 -> 2`，avoided=4；无 token/美元 telemetry，不伪造货币成本。
- Trace identity 已持久化到 `.local/day16/p2_ablation_summary.json`；baseline SHA `237780b6c8ab507a1d4dc95d94dc21b73eb1552d`，run 时 `git_dirty=true`。
- Full suite：234 passed；`git diff --check`：PASS。
- Day17 已完成 P2 Gate：`PASS`。

### Day 17：P2 Gate + Production Freeze

- P2 capability Gate：`PASS`
- v0.2-rag production Retrieval：继续 `Dense-only`
- Hybrid Answer A/B：`3/7 -> 4/7`
- Hybrid request-time Retrieval 平均增加约 `551 ms`
- P1 三个核心失败 target 均未进入 Hybrid Top-20 candidate pool
- 因此 Reranker 无法修复这些 candidate-generation failures
- Evidence Verifier source identity bug 已修复并增加 4 条回归测试
- full pytest：PASS；`git diff --check`：PASS
- P3 Harness backlog 已冻结；Day 17 不实现 P3

### Day 18：P3 Read-only Code RAG / Harness Foundation

- 新增 `RepositoryReadBoundary`，以 repository root 作为 runtime trust boundary。
- canonical path 必须保持在 root 内；拒绝 absolute/path escape、symlink、binary、oversized file。
- traversal 在进入目录前剪枝 `.git/.venv/venv/node_modules/cache/generated`，并排除 `.env/.env.*`；保留 `.env.example/.env.sample` 模板。
- 新增 minimal `ToolContract / ToolResult`、`ToolRuntime`、`ToolRegistry`。
- ToolRuntime 默认只允许 `READ / COMPUTE`，WRITE / DESTRUCTIVE 在 Harness 层拒绝。
- 新增首批 read-only repository tools：`tree / read_file / search_code / search_symbol`。
- `search_code` 只做 deterministic literal lexical matching。
- 新增 Python AST service；`search_symbol` 只识别 class/function/method，使用 exact name / exact qualified_name semantics。
- 单个 Python parse error 不终止全仓库 symbol search，通过 `parse_error_count` 暴露局部失败。
- 新增 `CodeEvidence`，最小 provenance 为 `repository / file_path / symbol / line_start / line_end / snippet`。
- snippet 根据安全 file path + line range 从 authoritative repository content 重建，不接受模型自由生成 provenance。
- Repository/Harness focused tests：34 passed。
- Full pytest：PASS。
- `git diff --check`：PASS。
- 既有 Starlette TestClient/httpx deprecation warning：非阻塞。
- Day18 继续遵守 v1 read-only boundary；没有 LangGraph、shell、edit_file、git write、worktree 或自动修复。

### Day 19：EvidencePack + minimal Repo Explorer

- Day18 `ToolResult.truncated` propagation 增加 regression test，统一 Harness outer result 与 tool payload truncation 语义。
- 新增最小 `EvidencePack`：`query / task_intent / evidence / provenance_integrity / incomplete / issues`。
- `issues` 使用结构化 taxonomy，覆盖 tool unavailable、tool failure、tool truncation、AST parse error、evidence limit、provenance mismatch。
- 新增 deterministic `RepoExplorer`，只通过 `ToolRegistry + ToolRuntime` 调用 repository tools。
- `search_code / search_symbol` 仅提供 candidate location；最终 `CodeEvidence` 必须再次经 `read_file` 获取 authoritative repository content 后构造。
- Explorer 不接收 `Path` 或 `RepositoryReadBoundary`，避免直接 filesystem bypass。
- 同一文件的 authoritative read 在单次 explore 内复用，避免重复 read_file 调用。
- `provenance_integrity` 与 `incomplete` 分离：合法 evidence 可以保持来源完整，同时 exploration 仍可因 truncation / parse error / tool failure 标记为不完整。
- 未引入 LangGraph、Agent Control Layer、shell、edit_file、git write、worktree 或自动修复。
- Full pytest：PASS。
- `git diff --check`：PASS。
- 既有 Starlette TestClient/httpx deprecation warning：非阻塞。

## 验收证据

### Golden Dataset Integrity

Day 12 曾发现原 30 条 Golden 中有 6 条指向已删除旧文档，完成 corpus / label drift 修复。

Day 13 又发现一条 Golden 的 `expected_document_id` metadata 错误。Day 12 的 Retrieval HIT/MISS 实际只按 `expected_chunk_id` 判定，因此修复后 Dense/BM25 指标完全复现；但这暴露出 Day 12 的 Golden integrity check 对完整身份关系校验不足。

Day 13 已将正式前置校验收紧为：

- 当前 `workspace_id`
- 当前 legal Document：`deleted_at IS NULL`
- Document 状态 COMPLETED/PARTIAL
- `expected_document_id`
- `expected_document_name`
- `expected_chunk_id`
- `expected_chunk_index`
- `expected_section`

修复后：

- Golden：30/30 valid
- legal documents：5
- legal chunks：1153
- dataset SHA-256：`e65e8490ef8e23018673712b2e595c6779e842094bd49d5d433fc5641bcef7f5`
- corpus snapshot SHA-256：`1d393523789b235bcfc1f821491bf86c5bcd29f47e04da7ebb85362b9ad81b0e`

规则：任何 Retrieval Benchmark 前必须先检查 Golden 与当前 legal corpus snapshot 的一致性；不得用 Retriever 输出自动重标 stale case。

### Dense / BM25 / Hybrid（当前 corpus / 当前 Golden）

正式 Day 13 baseline：

| Retriever | Recall@5 | MRR@5 | MISS |
| --- | ---: | ---: | ---: |
| Dense | 0.700000 | 0.500000 | 9 |
| BM25 | 0.700000 | 0.567778 | 9 |
| Hybrid RRF | 0.766667 | 0.588333 | 7 |

正式 Hybrid 配置：

```text
candidate_limit = 20
top_k = 5
rrf_k = 60
```

Dense/BM25 Top-5 失败集合：

- Dense-only hit：4
- BM25-only hit：4
- Both hit：17
- Both miss：5
- 单路 Top-5 命中并集：25/30，理论 oracle upper bound = 0.833333

Hybrid：

- 实际命中：23/30
- Dense-only preserved：3/4
- BM25-only preserved：3/4
- Fusion losses：2
- Both candidate miss（Top-20）：3
- Hybrid rescues from both Top-5 miss：0

失败分析：

- case 11：Dense rank 2、BM25 candidate miss；较大 `k` 下被多条双路中等排名候选压出 Hybrid Top-5
- case 09：Dense rank 3、BM25 rank 14；较小 `k` 会过度强化单路头部，反而损失该跨路共识命中
- case 15：BM25-only rank 5，在双路候选合并后的 final Top-5 中天然脆弱
- case 25：BM25 candidate rank 17、Dense Top-20 miss，属于候选召回/排序问题，不是 RRF 能自动修复的问题

有边界参数实验：

| candidate_limit | rrf_k | Hybrid Recall@5 | Hybrid MRR@5 | 结论 |
| ---: | ---: | ---: | ---: | --- |
| 20 | 60 | 0.766667 | 0.588333 | 正式 baseline |
| 10 | 60 | 0.766667 | 0.595556 | 不增 Recall，candidate both-miss 由 3 增至 4 |
| 20 | 20 | 0.766667 | 0.588333 | 与 k=60 Top-5 结果相同 |
| 20 | 1 | 0.766667 | 0.586111 | 不增 Recall，fusion loss 从 case 11 转移到 case 09 |

结论：RRF 确认利用了 Dense/BM25 的互补性，但不能保证达到单路成功集合的 oracle union；继续针对 30 条 Golden 搜索 `k` 会造成过拟合，因此停止调参。

### Dense / BM25 / Hybrid / Hybrid+Reranker（Day 14）

同一份 30-case strict Golden、同一 legal corpus：

| Pipeline | Recall@5 | MRR@5 | MISS |
| --- | ---: | ---: | ---: |
| Dense | 0.700000 | 0.500000 | 9 |
| BM25 | 0.700000 | 0.567778 | 9 |
| Hybrid RRF | 0.766667 | 0.588333 | 7 |
| Hybrid + Reranker | 0.866667 | 0.766667 | 4 |

正式 Reranker 配置：

```text
model = BAAI/bge-reranker-v2-m3
candidate_limit = 20
rerank_depth = 20
top_k = 5
rrf_k = 60
max_length = 512
device = mps
```

质量变化：
- rescues：3
- regressions：0
- retained Hybrid Top-5 hits：23/23
- source both-candidate miss：3
- rerank candidate miss：4
- case 25 的目标在 RRF full rank 29，`rerank_depth=20` 时不会进入 Cross Encoder

正式 latency（模型 warm-up 后）：

| Stage | Mean | P50 | P95 |
| --- | ---: | ---: | ---: |
| Hybrid candidate | 682.99 ms | 666.42 ms | 755.31 ms |
| PostgreSQL fetch | 3.22 ms | 3.05 ms | 3.72 ms |
| Rerank inference | 2323.19 ms | 2351.03 ms | 2956.97 ms |
| Rerank added | 2326.42 ms | 2354.05 ms | 2960.38 ms |
| Reranked total | 3009.45 ms | 3043.61 ms | 3699.76 ms |

有界 `rerank_depth=40` 诊断：
- Recall@5 / MRR@5 与 depth=20 完全相同：`0.866667 / 0.766667`
- case 25 虽进入 rerank pool，仍未进入最终 Top-5
- Rerank inference mean `3893.50 ms` / P95 `5850.06 ms`
- Reranked total mean `4616.67 ms` / P95 `6562.46 ms`
- 结论：扩大到完整 RRF union 没有额外质量收益，正式配置保留 depth=20

生产判断：质量收益明确，但当前 Cross Encoder 增量 mean 约 `2.33s`、P95 约 `2.96s`，不直接作为交互式生产默认路径。

### 测试

- RRF focused tests：PASS
- Hybrid Service tests：PASS
- Hybrid real-service smoke：PASS
- Reranker Provider / Service focused tests：PASS
- Reranker real-model smoke：PASS
- Evidence Verifier real DeepSeek smoke：PASS
- Evidence Verifier formal eval：6/6 state + 6/6 primary reason
- Full Test Suite：227 passed
- `git diff --check`：PASS
- 非阻塞警告：Starlette TestClient / httpx deprecation warning
- HF Hub 未认证警告不影响当前 smoke 结果

## 架构文档

系统架构、数据流和 Dense/BM25/Hybrid 当前边界统一维护在 `docs/ARCHITECTURE.md`。

Agent 工程边界维护在 `docs/adr/ADR-001-agent-runtime.md`；Day 13 只做设计冻结，不启动 Agent Runtime。

## 已知非阻塞问题

- 生产 `AnswerService` 仍使用 Dense Top-K；Hybrid / Reranker 当前是独立 Retriever / evaluation path
- Hybrid+Reranker 仍有 4/30 MISS，其中 3 条在 Dense/BM25 Top-20 候选中都未出现
- 当前 Cross Encoder 在 MPS 上增加约 2.33s mean / 2.96s P95，尚不适合作为无条件生产默认路径
- RRF final Top-K 会产生 fusion truncation loss，不能等同于 Dense/BM25 oracle union
- BM25 当前按合法 PostgreSQL Chunk 集构建请求级 baseline，尚未做持久化 lexical index / cache
- Context Budget 使用字符数，不是真实 tokenizer token 数
- 仅支持文本型 PDF，不支持 OCR
- Qdrant 删除采用 Best-effort Cleanup
- Starlette/httpx TestClient 弃用警告仍存在
- PR #2 仍为 Draft / Open / 未合并

## 下一步

Day 15 主线：Evidence Verifier / Evidence Sufficiency。继续区分 Retrieval Relevance 与 Evidence Sufficiency，并保持生产 `AnswerService` 不因离线 Reranker 指标自动切换。

按 Agent Harness 补充说明，Day 15 仅让 Evidence Verifier 的输入输出能够映射到未来 Tool Schema；不实现 Tool Registry，不启动 Coding Agent。

### Day 20：lightweight AgentEvent trace foundation

- 新增轻量 `AgentEvent` contract，不实现 Agent Control Layer。
- 最小事件类型：`TOOL_CALL / TOOL_RESULT / EVIDENCE_HANDOFF`。
- `trace_id` 关联同一次 Repo Explorer 调查中的 ToolRuntime 与 Evidence handoff 事件。
- ToolRuntime 对每次调用记录调用摘要、结果摘要、延迟、错误码和 truncation 状态。
- RepoExplorer 在最终返回 EvidencePack 前记录 evidence handoff 摘要。
- Trace 只记录参数名和输出字段摘要，不复制完整 tool input、文件正文或 Evidence payload。
- Event sink 采用 best-effort；trace backend 失败不得改变正常 ToolResult / EvidencePack 结果。
- Trace 与业务状态分离；EvidencePack 不增加 trace_id。
- 未引入 LangGraph、LLM decision loop、shell、edit_file、git write 或 worktree modification。
- Full pytest：PASS。
- `git diff --check`：PASS。
- 既有 Starlette TestClient/httpx deprecation warning：非阻塞。

## Day 21–24：Code RAG 检索与结构能力

- Day 21：完成 Code Chunk、Keyword Code Retrieval、Dense Code Retrieval。
- Day 22：完成 RRF Code Hybrid Retrieval 与 `truncated` 完整性传播。
- Day 23：不单独重复实现；文件级代码证据已由 `read_file -> CodeEvidence -> EvidencePack` 覆盖。
- Day 24：完成 Python module、内部 import dependency、top-level symbol 提取并接入 Repo Explorer。
- 最新验证：
  - Full pytest：PASS
  - `git diff --check`：PASS
- 当前 working tree 累计包含 Day 19–24 的未提交改动。
- 下一真实能力缺口：静态调用关系 / 调用链线索。
