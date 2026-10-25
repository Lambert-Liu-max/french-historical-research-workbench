from __future__ import annotations

import argparse
import json
from pathlib import Path

from .core import discover_gallica, ingest_local, run_pipeline, transcribe_local


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="fhrw", description="French Historical Research Workbench")
    sub = p.add_subparsers(dest="command", required=True)
    discover = sub.add_parser("discover", help="Discover and cache Gallica candidates")
    discover.add_argument("--config", type=Path, required=True); discover.add_argument("--project", type=Path, required=True)
    discover.add_argument("--limit", type=int, default=5); discover.add_argument("--execute", action="store_true")
    local = sub.add_parser("ingest-local", help="Inventory local archive files")
    local.add_argument("--source", type=Path, required=True); local.add_argument("--output", type=Path, required=True)
    vision = sub.add_parser("transcribe-local", help="Route and transcribe printed/handwritten scans")
    vision.add_argument("--config", type=Path, required=True); vision.add_argument("--input", type=Path, required=True)
    vision.add_argument("--output", type=Path, required=True); vision.add_argument("--execute", action="store_true")
    run = sub.add_parser("run", help="Classify, translate and render a research corpus")
    run.add_argument("--config", type=Path, required=True); run.add_argument("--input", type=Path, required=True)
    run.add_argument("--output", type=Path, required=True); run.add_argument("--run-id", required=True)
    run.add_argument("--execute", action="store_true", help="Call the configured model; omitted means local rules")
    return p


def main() -> None:
    a = parser().parse_args()
    if a.command == "discover":
        if not a.execute:
            raise SystemExit("discover is networked; add --execute explicitly")
        result = discover_gallica(a.config, a.project, a.limit)
    elif a.command == "ingest-local":
        result = ingest_local(a.source, a.output)
    elif a.command == "transcribe-local":
        result = transcribe_local(a.config, a.input, a.output, a.execute)
    else:
        result = run_pipeline(a.config, a.input, a.output, a.run_id, a.execute)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
