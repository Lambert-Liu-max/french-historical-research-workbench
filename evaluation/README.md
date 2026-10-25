# Unified Model Benchmark Runner

The runner sends the same prompt, schema, and archive input to DeepSeek directly and to Qwen/GPT through AIHubMix. Gold labels are retained locally and are never sent to a model.

## Safe dry-run (default)

```bash
python3 evaluation/run_benchmark.py \
  --run-id pilot-plan \
  --split all \
  --limit 3
```

This creates a request plan and reports `api_calls: 0`. It does not require API keys.

## Explicit execution

```bash
export DEEPSEEK_API_KEY="..."
export AIHUBMIX_API_KEY="..."

python3 evaluation/run_benchmark.py \
  --run-id pilot-v1 \
  --models deepseek,qwen,gpt \
  --split development \
  --budget-usd 5 \
  --execute
```

Outputs are isolated under `evaluation/results/<run-id>/`:

- `plan.jsonl`: request hashes and model routing, without archive inputs or keys;
- `checkpoint.json`: persistent status for restart and cost accounting;
- `results.jsonl`: parsed predictions, usage, latency, cost, and errors;
- `raw/<model>/<sample>.json`: raw provider responses;
- `summary.json`: run totals.

Successful requests with the same hash are skipped on restart. Failed items do not block later items and are retried by a later identical command. Prices in `models.example.json` are estimates copied from the existing research systems and must be checked before a paid run.
