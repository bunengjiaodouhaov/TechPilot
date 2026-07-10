# TechPilot RUNBOOK

## 每天开始开发
1. 打开 VS Code
2. `source .venv/bin/activate`
3. `which python`
4. `git pull`
5. `uvicorn app.main:app --reload`

验证：/health 与 /docs

## 每天结束开发
`pytest`
`git add .`
`git commit`
`git push`

## 常见问题
- No module named fastapi：`python -m pip install -r requirements.txt`
- requirements.txt 找不到：确认当前目录。
