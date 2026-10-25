#!/usr/bin/env python3
"""Build a small, auditable benchmark candidate set from the existing Landry corpus.

This script never promotes machine-produced labels to gold. Seven manually curated
boundary cases are marked ``expert_reviewed_seed``; every reference remains outside
the strict benchmark until the historian explicitly signs it off.
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def compact_packet(packet: dict) -> dict:
    return {
        "event_id": packet.get("event_id"),
        "date": packet.get("date"),
        "chamber": packet.get("chamber"),
        "document_ark": packet.get("document_ark"),
        "document_url": packet.get("document_url"),
        "hit_pages": packet.get("hit_pages", []),
        "pages": [
            {
                "scan_page": page.get("scan_page"),
                "printed_page": page.get("printed_page"),
                "is_hit_page": page.get("is_hit_page"),
                "numbered_lines": page.get("numbered_lines", []),
            }
            for page in packet.get("pages", [])
        ],
    }


def speech_type(value: str | None) -> str:
    value = value or ""
    if value in {"reply", "interjection", "response"}:
        return "interjection_or_response"
    if value in {"formal_speech", "speech"}:
        return "formal_speech"
    return "uncertain"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    root, out = args.source_root, args.output_dir
    out.mkdir(parents=True, exist_ok=True)

    packets = {row["event_id"]: row for row in load_jsonl(root / "data/interim/boundary_packets.jsonl")}
    curated = load(root / "data/processed/event_boundaries.json")["events"]
    approved = load(root / "data/test_results/1931/1931-production-v1/approved_event_list.json")["events"]
    approved_by_id = {row["event_id"]: row for row in approved}
    d_packets = load(root / "data/test_results/1931/1931-production-v1/d_behavior_packets.json")["events"]
    d_records = load(root / "data/test_results/1931/1931-production-v1/d_behavior_records.json")["events"]
    d_by_id = {row["event_id"]: row for row in d_records}
    c_events = load(root / "data/test_results/1931/1931-production-v1/candidate_context_records.json")["events"]

    samples: list[dict] = []

    # 7 expert-curated anchors. They remain gold seeds until the historian signs off.
    for index, gold in enumerate(curated):
        event_id = gold["event_id"]
        if event_id not in packets:
            continue
        semantic = approved_by_id.get(event_id, {})
        samples.append({
            "sample_id": f"G{index+1:02d}",
            "task_types": ["classification", "event_reconstruction"],
            "stratum": "expert_boundary_anchor",
            "split": "development" if index < 3 else "test",
            "gold_status": "expert_reviewed_seed",
            "input": compact_packet(packets[event_id]),
            "gold": {
                "identity_status": gold.get("identity_status", "confirmed"),
                "material_type": speech_type(semantic.get("speech_type")),
                "material_level": semantic.get("event_level", "B"),
                "event_title": gold.get("event_title"),
                "event_ranges": gold.get("event_ranges", []),
                "landry_ranges": gold.get("landry_ranges", []),
                "speaker_before_landry": gold.get("speaker_before_landry"),
                "landry_response_target": gold.get("landry_response_target"),
                "responses_to_landry": gold.get("responses_to_landry", []),
                "needs_review": False,
                "annotation_note": "Previously curated complete event; used as an anchor case.",
            },
        })

    # 8 workflow-adjudicated events: useful candidates, not gold until historian review.
    existing = {row["input"]["event_id"] for row in samples}
    for semantic in approved:
        event_id = semantic["event_id"]
        if event_id in existing or event_id not in packets:
            continue
        samples.append({
            "sample_id": f"S{len([x for x in samples if x['stratum']=='semantic_silver'])+1:02d}",
            "task_types": ["classification", "event_reconstruction"],
            "stratum": "semantic_silver",
            "split": "development" if len(samples) < 10 else "test",
            "gold_status": "silver_needs_expert_review",
            "input": compact_packet(packets[event_id]),
            "gold": {
                "identity_status": semantic.get("identity", {}).get("status", "uncertain"),
                "material_type": speech_type(semantic.get("speech_type")),
                "material_level": semantic.get("event_level", "C"),
                "event_start": semantic.get("event_start"),
                "event_end": semantic.get("event_end"),
                "landry_ranges": semantic.get("landry_ranges", []),
                "speaker_before_landry": semantic.get("speaker_before_landry"),
                "landry_response_target": semantic.get("landry_response_target"),
                "responses_to_landry": [r.get("speaker") for r in semantic.get("responses", []) if r.get("speaker")],
                "needs_review": True,
                "annotation_note": "Existing semantic result; historian must accept or revise before scoring.",
            },
        })
        if len([x for x in samples if x["stratum"] == "semantic_silver"]) == 8:
            break

    # 8 D-level cases with concise evidence.
    for packet in d_packets[:8]:
        record = d_by_id.get(packet["event_id"], {})
        samples.append({
            "sample_id": f"D{len([x for x in samples if x['stratum']=='d_level'])+1:02d}",
            "task_types": ["classification"],
            "stratum": "d_level",
            "split": "test",
            "gold_status": "silver_needs_expert_review",
            "input": {
                "event_id": packet["event_id"], "date": packet.get("date"), "chamber": packet.get("chamber"),
                "document_ark": packet.get("ark"), "document_url": packet.get("document_url"),
                "hit_pages": packet.get("hit_pages", []), "ocr_evidence": packet.get("ocr_evidence", "")[:24000],
            },
            "gold": {
                "identity_status": "probable",
                "material_type": "vote_or_attendance",
                "material_level": "D",
                "landry_action": record.get("landry_action"),
                "needs_review": not bool(record),
                "annotation_note": "Existing D-level extraction; historian must verify list heading and action.",
            },
        })

    # 7 uncertain C-level contexts.
    for event in c_events[:7]:
        samples.append({
            "sample_id": f"C{len([x for x in samples if x['stratum']=='uncertain_c'])+1:02d}",
            "task_types": ["classification"],
            "stratum": "uncertain_c",
            "split": "test",
            "gold_status": "pending_review",
            "input": {
                "event_id": event["event_id"], "date": event.get("date"), "chamber": event.get("chamber"),
                "document_ark": event.get("document_ark"), "document_url": event.get("document_url"),
                "hit_pages": event.get("hit_pages", []), "scan_pages": event.get("scan_pages", []),
                "ocr_text": event.get("full_french_discussion", "")[:24000],
            },
            "gold": {
                "identity_status": "uncertain", "material_type": "uncertain", "material_level": "C",
                "needs_review": True, "annotation_note": "No gold decision yet; intentionally sampled as a difficult case.",
            },
        })

    if len(samples) != 30:
        raise SystemExit(f"expected 30 samples, built {len(samples)}")

    dataset = out / "pilot_30.jsonl"
    dataset.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in samples), encoding="utf-8")

    fields = ["sample_id", "stratum", "split", "gold_status", "event_id", "identity_status",
              "material_type", "material_level", "needs_review", "annotation_note", "reviewer_decision"]
    with (out / "pilot_30_human_review.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in samples:
            gold = row["gold"]
            writer.writerow({
                "sample_id": row["sample_id"], "stratum": row["stratum"], "split": row["split"],
                "gold_status": row["gold_status"], "event_id": row["input"]["event_id"],
                "identity_status": gold.get("identity_status"), "material_type": gold.get("material_type"),
                "material_level": gold.get("material_level"), "needs_review": gold.get("needs_review"),
                "annotation_note": gold.get("annotation_note"), "reviewer_decision": "",
            })

    print(json.dumps({
        "samples": len(samples),
        "expert_reviewed_seed": sum(row["gold_status"] == "expert_reviewed_seed" for row in samples),
        "silver": sum(row["gold_status"].startswith("silver") for row in samples),
        "pending_review": sum(row["gold_status"] == "pending_review" for row in samples),
        "dataset": str(dataset),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

