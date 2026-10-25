#!/usr/bin/env python3
"""Deterministic rule + fixed-window baseline for the pilot benchmark."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


def rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def text_of(case: dict) -> str:
    source = case["input"]
    if source.get("pages"):
        return "\n".join(
            line for page in source["pages"] for line in page.get("numbered_lines", [])
        )
    return str(source.get("ocr_evidence") or source.get("ocr_text") or "")


def classify(text: str) -> tuple[str, str, str]:
    low = text.lower()
    identity = "confirmed" if re.search(r"adolphe\s+landry|landry\s*,?\s+adolphe", low) else (
        "probable" if "landry" in low else "false"
    )
    if re.search(r"scrutin|ont voté|n.?ont pas pris part|se sont abstenus|absents?", low):
        return identity, "vote_or_attendance", "D"
    if re.search(r"table des matières|sommaire|index alphabétique", low):
        return identity, "index_or_toc", "D"
    if re.search(r"m\.?\s+landry\s*[:.—-]", low):
        return identity, "formal_speech", "B"
    if re.search(r"rapport|commission|proposition de loi|projet de loi", low) and "landry" in low:
        return identity, "proposal_report_committee", "B"
    return identity, "uncertain", "C"


def fixed_ranges(case: dict) -> tuple[list[dict], list[dict]]:
    if "event_reconstruction" not in case.get("task_types", []):
        return [], []
    pages = case["input"].get("pages") or []
    page_map = {int(p["scan_page"]): p for p in pages if p.get("scan_page") is not None}
    hits = [int(x) for x in case["input"].get("hit_pages", []) if str(x).isdigit()]
    if not page_map or not hits:
        return [], []
    wanted = [p for p in page_map if min(hits) - 1 <= p <= max(hits) + 1]
    start, end = min(wanted), max(wanted)
    event = [{
        "scan_start": start,
        "start_line": 0,
        "scan_end": end,
        "end_line": max(0, len(page_map[end].get("numbered_lines", [])) - 1),
    }]
    landry = []
    for page_number in sorted(wanted):
        lines = page_map[page_number].get("numbered_lines", [])
        indexes = [i for i, line in enumerate(lines) if re.search(r"\blandry\b", line, re.I)]
        for index in indexes:
            landry.append({
                "scan_start": page_number, "start_line": index,
                "scan_end": page_number, "end_line": index,
            })
    return event, landry


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=Path("evaluation/data/pilot_30.jsonl"))
    parser.add_argument("--output", type=Path, default=Path("evaluation/results/baseline-v1/results.jsonl"))
    args = parser.parse_args()
    output = []
    for case in rows(args.input):
        identity, material_type, level = classify(text_of(case))
        event_ranges, landry_ranges = fixed_ranges(case)
        prediction = {
            "sample_id": case["sample_id"], "identity_status": identity,
            "material_type": material_type, "material_level": level,
            "event_ranges": event_ranges, "landry_ranges": landry_ranges,
            "speaker_before_landry": None, "landry_response_target": None,
            "responses_to_landry": [], "needs_review": True, "confidence": 0.5,
            "reason": "Deterministic lexical rules and a fixed hit-page ±1 window.",
        }
        output.append({
            "sample_id": case["sample_id"], "model_alias": "rule_fixed_window",
            "model": "rule-fixed-window-v1", "status": "success", "parse_status": "valid",
            "latency_ms": 0, "estimated_cost_usd": 0, "usage": {
                "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
            }, "prediction": prediction,
        })
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in output), encoding="utf-8")
    print(json.dumps({"records": len(output), "output": str(args.output), "api_calls": 0}, ensure_ascii=False))


if __name__ == "__main__":
    main()
