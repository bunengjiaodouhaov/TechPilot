# Day 1 执行与验收清单

## A. 本地工程

- [ ] 解压项目并进入目录
- [ ] 创建 Python 虚拟环境
- [ ] 安装 `requirements.txt`
- [ ] 执行 `pytest`，测试通过
- [ ] 执行 `uvicorn app.main:app --reload`
- [ ] 打开 `/health`，确认返回 `status: ok`

## B. GitHub

- [ ] 新建仓库 `techpilot`
- [ ] 上传本项目全部文件
- [ ] 按 `docs/milestones.md` 创建 P0–P7 Milestones
- [ ] 不提前创建 Day 2 及之后的功能 Issue

## C. Git 提交

```bash
git init
git add .
git commit -m "chore: initialize TechPilot project baseline"
git branch -M main
git remote add origin <你的仓库地址>
git push -u origin main
```

## D. 完成证据

- GitHub 仓库地址
- `docs/product-baseline.md`
- P0–P7 Milestones 页面截图
- `/health` 响应截图
- `pytest` 通过截图
- 首次 Git commit

## Day 1 边界

今天不安装 PostgreSQL、Redis、Qdrant，不实现文档上传、RAG 或 `/health/dependencies`。这些属于 Day 2 之后的任务。
