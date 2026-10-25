#!/usr/bin/env python3
"""Deterministically score benchmark predictions against reviewed annotations.

By default, only ``gold_status=expert_verified`` samples are scored.  Less
strict reference labels must be opted into explicitly and are reported as
non-strict results so that machine-produced silver labels cannot silently be
presented as expert gold.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import statistics
import sys
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable


STRICT_GOLD = "expert_verified"
REVIEWED_SEED = "expert_reviewed_seed"

IDENTITY_CLASSES = {"confirmed", "probable", "uncertain", "false"}
MATERIAL_TYPE_CLASSES = {
    "formal_speech",
    "interjection_or_response",
    "substantive_mention",
    "proposal_report_committee",
    "vote_or_attendance",
    "index_or_toc",
    "irrelevant_or_ocr_error",
    "uncertain",
}
MATERIAL_LEVEL_CLASSES = {"A", "B", "C", "D"}


def read_jsonl(path: Path) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Read object-valued JSONL while retaining parse diagnostics."""
    records: list[dict[str, Any]] = []
    diagnostics = {"nonblank_lines": 0, "parsed_objects": 0, "parse_errors": 0}
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            diagnostics["nonblank_lines"] += 1
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                diagnostics["parse_errors"] += 1
                continue
            if not isinstance(value, dict):
                diagnostics["parse_errors"] += 1
                continue
            value.setdefault("_source_line", line_number)
            records.append(value)
            diagnostics["parsed_objects"] += 1
    return records, diagnostics


def result_object(record: dict[str, Any]) -> dict[str, Any]:
    """Return the prediction payload, accepting common wrapper keys."""
    for key in ("prediction", "result", "output"):
        candidate = record.get(key)
        if isinstance(candidate, dict):
            return candidate
    return record


def record_id(record: dict[str, Any]) -> str | None:
    payload = result_object(record)
    for source in (record, payload):
        value = source.get("sample_id") or source.get("event_id")
        if isinstance(value, str) and value:
            return value
    return None


def prediction_index(records: Iterable[dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], list[str]]:
    indexed: dict[str, dict[str, Any]] = {}
    duplicates: list[str] = []
    for record in records:
        key = record_id(record)
        if key is None:
            continue
        if key in indexed:
            duplicates.append(key)
        indexed[key] = record
    return indexed, sorted(set(duplicates))


def prediction_for_sample(
    sample: dict[str, Any], indexed: dict[str, dict[str, Any]]
) -> dict[str, Any] | None:
    sample_id = sample.get("sample_id")
    event_id = sample.get("input", {}).get("event_id")
    return indexed.get(sample_id) or indexed.get(event_id)


def classification_metrics(gold: list[str], predicted: list[str]) -> dict[str, Any]:
    if len(gold) != len(predicted):
        raise ValueError("gold and predicted lengths differ")
    if not gold:
        return {"n": 0, "accuracy": None, "macro_f1": None, "per_class": {}}

    labels = sorted((set(gold) | set(predicted)) - {"__missing__"})
    per_class: dict[str, dict[str, float | int]] = {}
    f1_values: list[float] = []
    for label in labels:
        tp = sum(g == label and p == label for g, p in zip(gold, predicted))
        fp = sum(g != label and p == label for g, p in zip(gold, predicted))
        fn = sum(g == label and p != label for g, p in zip(gold, predicted))
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        support = sum(g == label for g in gold)
        per_class[label] = {
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "support": support,
            "predicted": sum(p == label for p in predicted),
        }
        f1_values.append(f1)

    return {
        "n": len(gold),
        "accuracy": sum(g == p for g, p in zip(gold, predicted)) / len(gold),
        "macro_f1": statistics.fmean(f1_values) if f1_values else None,
        "per_class": per_class,
    }


def page_number(point: Any) -> int | None:
    if not isinstance(point, dict):
        return None
    value = point.get("scan_page", point.get("page"))
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def pages_from_ranges(ranges: Any) -> set[int]:
    pages: set[int] = set()
    if not isinstance(ranges, list):
        return pages
    for item in ranges:
        if not isinstance(item, dict):
            continue
        start = item.get("scan_start", item.get("start_page"))
        end = item.get("scan_end", item.get("end_page"))
        if isinstance(start, int) and isinstance(end, int) and start <= end:
            pages.update(range(start, end + 1))
    return pages


def event_pages(record: dict[str, Any]) -> set[int]:
    pages = pages_from_ranges(record.get("event_ranges"))
    if pages:
        return pages
    start = page_number(record.get("event_start"))
    end = page_number(record.get("event_end"))
    if start is not None and end is not None and start <= end:
        return set(range(start, end + 1))
    return set()


def landry_pages(record: dict[str, Any]) -> set[int]:
    return pages_from_ranges(record.get("landry_ranges"))


def page_iou(gold: set[int], predicted: set[int]) -> float:
    union = gold | predicted
    return len(gold & predicted) / len(union) if union else 1.0


def normalize_speaker(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    normalized = unicodedata.normalize("NFKD", value)
    normalized = "".join(char for char in normalized if not unicodedata.combining(char))
    tokens = re.findall(r"[a-z0-9]+", normalized.lower())
    while tokens and tokens[0] in {"m", "mr", "monsieur", "mme", "madame"}:
        tokens.pop(0)
    return " ".join(tokens)


def response_set(record: dict[str, Any]) -> set[str]:
    raw = record.get("responses_to_landry")
    if raw is None:
        raw = record.get("responses", [])
    values: list[Any] = raw if isinstance(raw, list) else []
    speakers: set[str] = set()
    for value in values:
        if isinstance(value, dict):
            value = value.get("speaker")
        speaker = normalize_speaker(value)
        if speaker:
            speakers.add(speaker)
    return speakers


def set_f1(gold: set[str], predicted: set[str]) -> float:
    if not gold and not predicted:
        return 1.0
    tp = len(gold & predicted)
    precision = tp / len(predicted) if predicted else 0.0
    recall = tp / len(gold) if gold else 0.0
    return 2 * precision * recall / (precision + recall) if precision + recall else 0.0


def has_key(record: dict[str, Any], *names: str) -> bool:
    return any(name in record for name in names)


def task_applies(sample: dict[str, Any], task: str) -> bool:
    """Respect explicit task routing while remaining compatible with older data."""
    configured = sample.get("task_types")
    if not isinstance(configured, list):
        return True
    aliases = {
        "classification": {"classification", "material_classification"},
        "event_reconstruction": {"event_reconstruction", "event_boundary", "boundary"},
    }
    return bool(set(configured) & aliases[task])


def ranges_are_valid(value: Any) -> bool:
    if not isinstance(value, list):
        return False
    return all(
        isinstance(item, dict)
        and isinstance(item.get("scan_start"), int)
        and isinstance(item.get("scan_end"), int)
        and item["scan_start"] <= item["scan_end"]
        for item in value
    )


def schema_pass(sample: dict[str, Any], prediction: dict[str, Any] | None) -> tuple[bool, list[str]]:
    if prediction is None:
        return False, ["missing_prediction"]
    payload = result_object(prediction)
    errors: list[str] = []

    classifications = (
        ("identity_status", IDENTITY_CLASSES),
        ("material_type", MATERIAL_TYPE_CLASSES),
        ("material_level", MATERIAL_LEVEL_CLASSES),
    )
    for field, allowed in classifications:
        if payload.get(field) not in allowed:
            errors.append(f"invalid_{field}")

    gold = sample.get("gold", {})
    boundary_required = task_applies(sample, "event_reconstruction") and bool(
        event_pages(gold) or landry_pages(gold) or has_key(gold, "event_start", "event_end")
    )
    if boundary_required:
        predicted_event_pages = event_pages(payload)
        if not predicted_event_pages:
            errors.append("invalid_or_missing_event_boundary")
        if not ranges_are_valid(payload.get("landry_ranges")):
            errors.append("invalid_or_missing_landry_ranges")
        if not has_key(payload, "responses_to_landry", "responses"):
            errors.append("missing_responses")

    return not errors, errors


def get_nested_number(record: dict[str, Any], paths: list[tuple[str, ...]]) -> float | None:
    for path in paths:
        value: Any = record
        for key in path:
            if not isinstance(value, dict) or key not in value:
                value = None
                break
            value = value[key]
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
            return float(value)
    return None


def percentile(values: list[float], probability: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(probability * len(ordered)) - 1)
    return ordered[index]


def production_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    cost_paths = [
        ("estimated_cost_usd",),
        ("cost_usd",),
        ("usage", "cost_usd"),
        ("metadata", "cost_usd"),
    ]
    latency_ms_paths = [("latency_ms",), ("metrics", "latency_ms"), ("metadata", "latency_ms")]
    latency_s_paths = [("latency_seconds",), ("metrics", "latency_seconds")]
    prompt_paths = [("usage", "prompt_tokens"), ("usage", "input_tokens"), ("input_tokens",)]
    completion_paths = [("usage", "completion_tokens"), ("usage", "output_tokens"), ("output_tokens",)]
    total_paths = [("usage", "total_tokens"), ("total_tokens",)]

    def summarize(group: list[dict[str, Any]]) -> dict[str, Any]:
        costs = [value for row in group if (value := get_nested_number(row, cost_paths)) is not None]
        latencies: list[float] = []
        for row in group:
            latency_ms = get_nested_number(row, latency_ms_paths)
            if latency_ms is None:
                latency_s = get_nested_number(row, latency_s_paths)
                latency_ms = latency_s * 1000 if latency_s is not None else None
            if latency_ms is not None:
                latencies.append(latency_ms)
        prompt = [value for row in group if (value := get_nested_number(row, prompt_paths)) is not None]
        completion = [value for row in group if (value := get_nested_number(row, completion_paths)) is not None]
        total = [value for row in group if (value := get_nested_number(row, total_paths)) is not None]
        return {
            "records": len(group),
            "cost": {
                "records_with_cost": len(costs),
                "total_usd": sum(costs),
                "mean_usd": statistics.fmean(costs) if costs else None,
            },
            "latency_ms": {
                "records_with_latency": len(latencies),
                "mean": statistics.fmean(latencies) if latencies else None,
                "p50": statistics.median(latencies) if latencies else None,
                "p95": percentile(latencies, 0.95),
            },
            "tokens": {
                "prompt": int(sum(prompt)),
                "completion": int(sum(completion)),
                "total": int(sum(total)),
            },
        }

    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in records:
        model = row.get("model")
        if not isinstance(model, str):
            model = row.get("metadata", {}).get("model") if isinstance(row.get("metadata"), dict) else None
        groups[model or "unknown"].append(row)
    return {"overall": summarize(records), "by_model": {name: summarize(rows) for name, rows in groups.items()}}


def mean_or_none(values: list[float]) -> float | None:
    return statistics.fmean(values) if values else None


def score(
    samples: list[dict[str, Any]],
    prediction_records: list[dict[str, Any]],
    prediction_diagnostics: dict[str, int],
    included_statuses: set[str],
    split: str | None = None,
) -> dict[str, Any]:
    eligible = [
        sample
        for sample in samples
        if sample.get("gold_status") in included_statuses
        and (split is None or sample.get("split") == split)
    ]
    indexed, duplicates = prediction_index(prediction_records)

    classification_pairs: dict[str, tuple[list[str], list[str]]] = {
        "identity_status": ([], []),
        "material_type": ([], []),
        "material_level": ([], []),
    }
    schema_results: list[bool] = []
    schema_errors: Counter[str] = Counter()
    boundary_ious: list[float] = []
    landry_coverages: list[float] = []
    response_f1s: list[float] = []
    matched_records: list[dict[str, Any]] = []

    for sample in eligible:
        prediction = prediction_for_sample(sample, indexed)
        payload = result_object(prediction) if prediction else {}
        gold = sample.get("gold", {})
        if prediction:
            matched_records.append(prediction)

        passed, errors = schema_pass(sample, prediction)
        schema_results.append(passed)
        schema_errors.update(errors)

        for field, (gold_values, predicted_values) in classification_pairs.items():
            if task_applies(sample, "classification") and field in gold:
                gold_values.append(str(gold[field]))
                predicted_values.append(str(payload.get(field, "__missing__")))

        gold_event_pages = event_pages(gold)
        if task_applies(sample, "event_reconstruction") and gold_event_pages:
            boundary_ious.append(page_iou(gold_event_pages, event_pages(payload)))

        gold_landry_pages = landry_pages(gold)
        if task_applies(sample, "event_reconstruction") and gold_landry_pages:
            predicted_landry_pages = landry_pages(payload)
            landry_coverages.append(len(gold_landry_pages & predicted_landry_pages) / len(gold_landry_pages))

        if task_applies(sample, "event_reconstruction") and has_key(gold, "responses_to_landry", "responses"):
            response_f1s.append(set_f1(response_set(gold), response_set(payload)))

    non_strict = sorted(status for status in included_statuses if status != STRICT_GOLD)
    parsed_lines = prediction_diagnostics["parsed_objects"]
    total_lines = prediction_diagnostics["nonblank_lines"]
    report = {
        "evaluation_scope": {
            "included_gold_statuses": sorted(included_statuses),
            "strict_gold_only": not non_strict,
            "non_strict_reference_statuses": non_strict,
            "warning": (
                None
                if not non_strict
                else "Includes expert-reviewed seed and/or silver references; these are diagnostic, not strict gold metrics."
            ),
            "split": split or "all",
            "eligible_samples": len(eligible),
        },
        "prediction_quality": {
            "json_parse_rate": parsed_lines / total_lines if total_lines else None,
            "json_lines": total_lines,
            "json_parse_errors": prediction_diagnostics["parse_errors"],
            "matched_samples": len(matched_records),
            "prediction_coverage": len(matched_records) / len(eligible) if eligible else None,
            "schema_pass_rate": sum(schema_results) / len(schema_results) if schema_results else None,
            "schema_errors": dict(sorted(schema_errors.items())),
            "duplicate_prediction_ids": duplicates,
        },
        "classification": {
            field: classification_metrics(gold_values, predicted_values)
            for field, (gold_values, predicted_values) in classification_pairs.items()
        },
        "event_reconstruction": {
            "page_iou": {"n": len(boundary_ious), "mean": mean_or_none(boundary_ious)},
            "landry_page_coverage": {
                "n": len(landry_coverages),
                "mean": mean_or_none(landry_coverages),
            },
            "response_set_f1": {"n": len(response_f1s), "mean": mean_or_none(response_f1s)},
        },
        "production": production_summary(matched_records),
    }
    if not eligible:
        report["evaluation_scope"]["warning"] = (
            "No samples matched the selected gold statuses/split. The current pilot may require expert sign-off "
            "or an explicit non-strict inclusion flag."
        )
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=Path("evaluation/data/pilot_30.jsonl"))
    parser.add_argument("--predictions", type=Path, required=True)
    parser.add_argument("--output", type=Path, help="Write JSON report here; stdout is always printed")
    parser.add_argument("--split", choices=("development", "test"))
    parser.add_argument(
        "--include-reviewed-seed",
        action="store_true",
        help="Also score expert_reviewed_seed as non-strict diagnostic references",
    )
    parser.add_argument(
        "--include-silver",
        action="store_true",
        help="Also score statuses beginning with 'silver' as non-strict diagnostic references",
    )
    parser.add_argument(
        "--include-status",
        action="append",
        default=[],
        help="Also include an exact gold_status (repeatable; non-expert_verified metrics are labelled non-strict)",
    )
    return parser.parse_args()


def model_group_name(record: dict[str, Any]) -> str:
    for key in ("model_alias", "model", "response_model"):
        value = record.get(key)
        if isinstance(value, str) and value:
            return value
    metadata = record.get("metadata")
    if isinstance(metadata, dict) and isinstance(metadata.get("model"), str):
        return metadata["model"]
    return "unknown"


def main() -> None:
    args = parse_args()
    samples, dataset_diagnostics = read_jsonl(args.dataset)
    predictions, prediction_diagnostics = read_jsonl(args.predictions)

    statuses = {STRICT_GOLD, *args.include_status}
    if args.include_reviewed_seed:
        statuses.add(REVIEWED_SEED)
    if args.include_silver:
        statuses.update(
            str(sample.get("gold_status"))
            for sample in samples
            if str(sample.get("gold_status", "")).startswith("silver")
        )

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for prediction in predictions:
        grouped[model_group_name(prediction)].append(prediction)

    model_reports = {
        model: score(
            samples,
            records,
            {"nonblank_lines": len(records), "parsed_objects": len(records), "parse_errors": 0},
            statuses,
            args.split,
        )
        for model, records in sorted(grouped.items())
    }
    report = {
        "scoring_policy": {
            "default_gold_status": STRICT_GOLD,
            "included_gold_statuses": sorted(statuses),
            "strict_gold_only": statuses == {STRICT_GOLD},
            "warning": (
                None
                if statuses == {STRICT_GOLD}
                else "Non-expert-verified references are included for diagnostic use and are not strict gold metrics."
            ),
        },
        "dataset": {
        "path": str(args.dataset),
        "samples_parsed": dataset_diagnostics["parsed_objects"],
        "parse_errors": dataset_diagnostics["parse_errors"],
        "status_counts": dict(sorted(Counter(str(row.get("gold_status")) for row in samples).items())),
        },
        "prediction_file": {
            "path": str(args.predictions),
            **prediction_diagnostics,
            "json_parse_rate": (
                prediction_diagnostics["parsed_objects"] / prediction_diagnostics["nonblank_lines"]
                if prediction_diagnostics["nonblank_lines"]
                else None
            ),
        },
        "models": model_reports,
    }
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    print(rendered)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    warnings = [report["scoring_policy"]["warning"]]
    warnings.extend(model_report["evaluation_scope"]["warning"] for model_report in model_reports.values())
    for warning in dict.fromkeys(item for item in warnings if item):
        print(f"WARNING: {warning}", file=sys.stderr)


if __name__ == "__main__":
    main()
