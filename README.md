# ScholarNavigator

## 项目定位

ScholarNavigator 是面向华为企业赛题三“科研场景下复杂学术查询的智能论文搜索与推荐”的参赛项目入口。

## 核心能力

- 查询理解与子查询扩展。
- 默认使用 arXiv / Semantic Scholar 进行真实论文检索。
- PubMed / OpenAlex 可作为可选检索源。
- 相关性判断、重排序和结构化结果输出。
- Source Reliability、Cost / Efficiency 诊断，以及 batch / summary / evaluation 脚本。

## 快速启动

启动后端：

```bash
PYTHONPATH=src uvicorn scholar_agent.app.main:app --host 127.0.0.1 --port 8000
```

启动前端：

```bash
cd frontend
npm install
npm run dev
```

运行 batch / eval smoke：

```bash
PYTHONPATH=src python scripts/run_search_batch.py \
  --input datasets/eval_fixtures/manual_smoke/queries.jsonl \
  --output /tmp/scholarnav_smoke/results.jsonl \
  --sources arxiv,semantic_scholar \
  --top-k 5 \
  --run-profile fast \
  --dump-ranked-candidates

PYTHONPATH=src python scripts/summarize_search_batch.py \
  --input /tmp/scholarnav_smoke/results.jsonl \
  --output /tmp/scholarnav_smoke/summary.md

PYTHONPATH=src python scripts/evaluate_search_batch.py \
  --batch-results /tmp/scholarnav_smoke/results.jsonl \
  --gold datasets/eval_fixtures/manual_smoke/qrels.filled.jsonl \
  --output /tmp/scholarnav_smoke/eval.json \
  --k 5 \
  --include-partial
```
