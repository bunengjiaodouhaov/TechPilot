# TechPilot

> 面向开发者的技术调研、Document RAG、Code RAG 与 Agent 项目。

## 当前状态

- P0：完成
- P1：Document RAG（Day1–7）完成
- 当前版本：v0.6-dev
- 下一阶段：按照《TechPilot 强大模型应用开发项目总控手册》进入下一阶段。

## 已完成能力

- 文档上传（Markdown / PDF）
- Structure-aware Chunk
- Dense Retrieval
- Golden Dataset & Retrieval Evaluation
- Trusted Answer
- Citation
- Refused Answer
- Soft Delete
- Answer Evaluation
- Entity Scope Mismatch 修复

## 技术栈

FastAPI · PostgreSQL · Qdrant · SQLAlchemy · Alembic · SentenceTransformers · DeepSeek

## 快速启动

```bash
docker compose up -d
alembic upgrade head
uvicorn app.main:app --reload
```

更多运行说明见 `docs/RUNBOOK.md`。
