# TechPilot

TechPilot 是一个面向开发者的技术调研与代码理解平台。

当前仓库仅完成 Day 1：项目范围冻结与 FastAPI 最小工程初始化。

## 当前已实现

- 产品基线文档
- P0–P7 里程碑定义
- FastAPI 最小服务
- `GET /health` 健康检查接口
- 健康检查自动化测试

## 环境要求

- Python 3.11+

## 本地运行

### macOS / Linux

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

### Windows PowerShell

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --reload
```

访问：

- 健康检查：http://127.0.0.1:8000/health
- API 文档：http://127.0.0.1:8000/docs

预期健康检查响应：

```json
{
  "status": "ok",
  "service": "techpilot"
}
```

## 运行测试

```bash
pytest
```

## Day 1 验收

参见 [`docs/day1-checklist.md`](docs/day1-checklist.md)。
