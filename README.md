# ScholarNavigator

面向复杂学术查询的论文搜索、排序与结构化归纳系统。

## 环境要求

- Python 3.11 或以上
- Node.js 20 或以上

## 安装依赖

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cd frontend
npm install
```

## 配置环境变量

根据 [`.env.example`](.env.example) 在项目根目录创建本地 `.env`；变量含义和默认值以该模板为准。

## 启动后端

```bash
PYTHONPATH=src uvicorn scholar_agent.app.main:app --host 127.0.0.1 --port 8000
```

## 启动前端

```bash
cd frontend
npm run dev
```
