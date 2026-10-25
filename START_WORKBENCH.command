#!/bin/zsh
set -e
ROOT_DIR="${0:A:h}"
cd "$ROOT_DIR"
export PYTHONPATH="$ROOT_DIR/src"
python3 -m french_history_workbench run \
  --config examples/product_demo/project.json \
  --input examples/product_demo/records.jsonl \
  --output demo_runs \
  --run-id offline-demo
echo ""
echo "完成：$ROOT_DIR/demo_runs/offline-demo/corpus.md"
echo "按回车键退出。"
read

