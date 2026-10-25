#!/usr/bin/env python3
"""Resumable, OpenAI-compatible multi-model benchmark runner.

Dry-run is the default. Network calls happen only with ``--execute``.
Gold labels are never included in model requests.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = ROOT / "evaluation" / "data" / "pilot_30.jsonl"
DEFAULT_CONFIG = ROOT / "evaluation" / "config" / "models.example.json"
DEFAULT_PROMPT = ROOT / "evaluation" / "prompts" / "classification_boundary_v1.md"
DEFAULT_SCHEMA = ROOT / "evaluation" / "schemas" / "prediction_v1.json"
DEFAULT_RESULTS = ROOT / "evaluation" / "results"


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + f".tmp-{os.getpid()}")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + f".tmp-{os.getpid()}")
    with temporary.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    os.replace(temporary, path)


def read_cases(path: Path, split: str, limit: int | None) -> list[dict]:
    cases = []
    with path.open(encoding="utf-8") as handle:
        for number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                case = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{number}: invalid JSON: {exc}") from exc
            identifier = case.get("sample_id") or case.get("case_id")
            if not isinstance(identifier, str) or not isinstance(case.get("input"), dict):
                raise ValueError(f"{path}:{number}: sample_id/case_id and input are required")
            case["sample_id"] = identifier
            case["task_types"] = task_types_for(case)
            if split != "all" and case.get("split") != split:
                continue
            cases.append(case)
            if limit is not None and len(cases) >= limit:
                break
    return cases


def task_types_for(case: dict) -> list[str]:
    """Normalize explicit tasks, or infer the legacy pilot strata conservatively."""
    allowed = {"classification", "event_reconstruction"}
    explicit = case.get("task_types")
    if isinstance(explicit, list):
        normalized = [str(item) for item in explicit if str(item) in allowed]
        if normalized:
            return list(dict.fromkeys(normalized))
    identifier = str(case.get("sample_id") or case.get("case_id") or "")
    return ["classification", "event_reconstruction"] if identifier[:1] in {"G", "S"} else ["classification"]


def build_request(case: dict, model: dict, prompt: str, schema: dict, max_tokens: int) -> tuple[dict, str]:
    instruction = prompt.rstrip() + "\n\n# Output JSON Schema\n" + canonical(schema)
    user = {"sample_id": case["sample_id"], "task_types": task_types_for(case), "archive_input": case["input"]}
    common = {
        "messages": [
            {"role": "system", "content": instruction},
            {"role": "user", "content": canonical(user)},
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0,
        "max_tokens": max_tokens,
    }
    payload = {"model": model["model"], **common}
    if model.get("thinking"):
        payload["thinking"] = {"type": model["thinking"]}
    return payload, digest({"request_without_model": common, "user": user})


def extract_json(text: str) -> dict:
    value = text.strip()
    if value.startswith("```"):
        lines = value.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        value = "\n".join(lines).strip()
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        start, end = value.find("{"), value.rfind("}")
        if start < 0 or end <= start:
            raise
        parsed = json.loads(value[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("model content is not a JSON object")
    return parsed


def validate_prediction(prediction: dict, sample_id: str, case_input: dict) -> list[str]:
    errors = []
    required = {
        "sample_id", "identity_status", "material_type", "material_level", "event_ranges",
        "landry_ranges", "speaker_before_landry", "landry_response_target",
        "responses_to_landry", "needs_review", "confidence", "reason",
    }
    missing = sorted(required - prediction.keys())
    if missing:
        errors.append("missing fields: " + ", ".join(missing))
    if prediction.get("sample_id") != sample_id:
        errors.append("sample_id mismatch")
    if prediction.get("identity_status") not in {"confirmed", "probable", "uncertain", "false"}:
        errors.append("invalid identity_status")
    if prediction.get("material_type") not in {
        "formal_speech", "interjection_or_response", "substantive_mention",
        "proposal_report_committee", "vote_or_attendance", "index_or_toc",
        "irrelevant_or_ocr_error", "uncertain",
    }:
        errors.append("invalid material_type")
    if prediction.get("material_level") not in {"A", "B", "C", "D"}:
        errors.append("invalid material_level")
    if not isinstance(prediction.get("needs_review"), bool):
        errors.append("needs_review must be boolean")
    confidence = prediction.get("confidence")
    if not isinstance(confidence, (int, float)) or isinstance(confidence, bool) or not 0 <= confidence <= 1:
        errors.append("confidence must be between 0 and 1")
    if not isinstance(prediction.get("responses_to_landry"), list):
        errors.append("responses_to_landry must be an array")
    page_lines = {
        int(page["scan_page"]): len(page.get("numbered_lines", []))
        for page in case_input.get("pages", [])
        if isinstance(page, dict) and str(page.get("scan_page", "")).isdigit()
    }
    for field in ("event_ranges", "landry_ranges"):
        ranges = prediction.get(field)
        if not isinstance(ranges, list):
            errors.append(f"{field} must be an array")
            continue
        for index, span in enumerate(ranges):
            if not isinstance(span, dict):
                errors.append(f"{field}[{index}] must be an object")
                continue
            try:
                start_page, start_line = int(span["scan_start"]), int(span["start_line"])
                end_page, end_line = int(span["scan_end"]), int(span["end_line"])
            except (KeyError, TypeError, ValueError):
                errors.append(f"{field}[{index}] malformed")
                continue
            if start_page not in page_lines or end_page not in page_lines:
                errors.append(f"{field}[{index}] page outside input")
            else:
                if not 0 <= start_line < page_lines[start_page]:
                    errors.append(f"{field}[{index}] start_line outside input")
                if not 0 <= end_line < page_lines[end_page]:
                    errors.append(f"{field}[{index}] end_line outside input")
    return list(dict.fromkeys(errors))


def normalize_usage(envelope: dict) -> dict:
    usage = envelope.get("usage") if isinstance(envelope, dict) else {}
    usage = usage if isinstance(usage, dict) else {}
    details = usage.get("prompt_tokens_details")
    details = details if isinstance(details, dict) else {}
    prompt = int(usage.get("prompt_tokens") or 0)
    completion = int(usage.get("completion_tokens") or 0)
    cache_hit = int(usage.get("prompt_cache_hit_tokens") or details.get("cached_tokens") or 0)
    cache_miss = int(usage.get("prompt_cache_miss_tokens") or max(0, prompt - cache_hit))
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": int(usage.get("total_tokens") or prompt + completion),
        "cached_input_tokens": cache_hit,
        "uncached_input_tokens": cache_miss,
    }


def estimate_cost(model: dict, usage: dict) -> float:
    cached_rate = model.get("cached_input_usd_per_million_tokens")
    cached = usage["cached_input_tokens"] if cached_rate is not None else 0
    uncached = usage["uncached_input_tokens"] if cached_rate is not None else usage["prompt_tokens"]
    return (
        uncached * float(model["input_usd_per_million_tokens"])
        + cached * float(cached_rate or 0)
        + usage["completion_tokens"] * float(model["output_usd_per_million_tokens"])
    ) / 1_000_000


def call_api(url: str, api_key: str, payload: dict, timeout: int) -> dict:
    proc = subprocess.run(
        [
            "/usr/bin/curl", "--silent", "--show-error", "--fail-with-body",
            "--connect-timeout", "30", "--max-time", str(timeout), url,
            "-H", "Authorization: Bearer " + api_key,
            "-H", "Content-Type: application/json",
            "--data-binary", "@-",
        ],
        input=json.dumps(payload, ensure_ascii=False),
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip().replace(api_key, "[REDACTED]")
        raise RuntimeError(f"curl exit {proc.returncode}: {detail[:1000]}")
    if not proc.stdout.strip():
        raise RuntimeError("curl returned an empty response body")
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        preview = proc.stdout[:300].replace(api_key, "[REDACTED]")
        raise RuntimeError(f"non-JSON response body: {preview!r}") from exc


def response_content(envelope: dict) -> str:
    choices = envelope.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ValueError("response has no choices")
    message = choices[0].get("message")
    if not isinstance(message, dict) or not isinstance(message.get("content"), str):
        raise ValueError("response has no string message content")
    return message["content"]


def resolve_api_key(provider: dict) -> str:
    """Read a key from the environment, then macOS Keychain without printing it."""
    key = os.getenv(provider["api_key_environment"], "").strip()
    if key or not provider.get("keychain_service"):
        return key
    result = subprocess.run(
        [
            "security", "find-generic-password", "-a", os.environ.get("USER", ""),
            "-s", provider["keychain_service"], "-w",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def execute_call(url: str, api_key: str, payload: dict, timeout: int, attempts: int, retry_base: float) -> tuple[dict, int]:
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return call_api(url, api_key, payload, timeout), attempt
        except (RuntimeError, OSError, TimeoutError, ConnectionError, json.JSONDecodeError) as exc:
            last_error = exc
            if attempt < attempts:
                time.sleep(retry_base * (2 ** (attempt - 1)))
    assert last_error is not None
    raise last_error


def selected_models(config: dict, names: str) -> list[tuple[str, dict]]:
    requested = [name.strip() for name in names.split(",") if name.strip()]
    unknown = [name for name in requested if name not in config.get("models", {})]
    if unknown:
        raise ValueError("unknown model aliases: " + ", ".join(unknown))
    return [(name, config["models"][name]) for name in requested]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--prompt", type=Path, default=DEFAULT_PROMPT)
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    parser.add_argument("--results-dir", type=Path, default=DEFAULT_RESULTS)
    parser.add_argument("--run-id", default="pilot-dry-run")
    parser.add_argument("--models", default="deepseek,qwen,gpt")
    parser.add_argument("--split", choices=("development", "test", "all"), default="all")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--budget-usd", type=float)
    parser.add_argument("--max-tokens", type=int, default=8000)
    parser.add_argument("--execute", action="store_true", help="Actually call APIs; omitted means dry-run")
    args = parser.parse_args(argv)

    config = json.loads(args.config.read_text(encoding="utf-8"))
    prompt = args.prompt.read_text(encoding="utf-8")
    schema = json.loads(args.schema.read_text(encoding="utf-8"))
    cases = read_cases(args.input, args.split, args.limit)
    models = selected_models(config, args.models)
    run_dir = args.results_dir / args.run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    plan = []
    for alias, model in models:
        for case in cases:
            payload, content_hash = build_request(case, model, prompt, schema, args.max_tokens)
            plan.append({
                "sample_id": case["sample_id"], "model_alias": alias, "provider": model["provider"],
                "model": model["model"], "content_sha256": content_hash,
                "request_sha256": digest(payload), "split": case.get("split"),
                "gold_status": case.get("gold_status"), "task_types": task_types_for(case),
            })
    write_jsonl(run_dir / "plan.jsonl", plan)
    if not args.execute:
        print(json.dumps({
            "mode": "dry-run", "run_id": args.run_id, "cases": len(cases), "models": [x[0] for x in models],
            "planned_requests": len(plan), "plan": str(run_dir / "plan.jsonl"), "api_calls": 0,
        }, ensure_ascii=False, indent=2))
        return 0

    budget = float(args.budget_usd if args.budget_usd is not None else config["default_budget_usd"])
    reserve = float(config.get("reserve_per_call_usd", 0.25))
    checkpoint_path = run_dir / "checkpoint.json"
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8")) if checkpoint_path.exists() else {
        "schema_version": "1.0", "run_id": args.run_id, "created_at": now(), "records": {}, "actual_cost_usd": 0.0,
    }
    records = checkpoint.setdefault("records", {})
    providers = config["providers"]
    timeout = int(config.get("timeout_seconds", 300))
    attempts = int(config.get("max_attempts", 3))
    retry_base = float(config.get("retry_base_seconds", 2))
    budget_exhausted = False

    for alias, model in models:
        provider = providers[model["provider"]]
        key_name = provider["api_key_environment"]
        api_key = resolve_api_key(provider)
        for case in cases:
            payload, content_hash = build_request(case, model, prompt, schema, args.max_tokens)
            request_hash = digest(payload)
            record_key = alias + "::" + case["sample_id"]
            old = records.get(record_key, {})
            if old.get("status") in {"success", "completed_invalid"} and old.get("request_sha256") == request_hash:
                continue
            base_record = {
                "sample_id": case["sample_id"], "model_alias": alias, "provider": model["provider"],
                "model": model["model"], "content_sha256": content_hash, "request_sha256": request_hash,
                "split": case.get("split"), "gold_status": case.get("gold_status"),
                "task_types": task_types_for(case), "updated_at": now(),
                "attempt_count": int(old.get("attempt_count", 0)),
            }
            if not api_key:
                records[record_key] = {**base_record, "status": "failed", "parse_status": "not_attempted", "error": f"{key_name} is not set"}
                atomic_json(checkpoint_path, checkpoint)
                continue
            if float(checkpoint.get("actual_cost_usd", 0)) + reserve > budget:
                records[record_key] = {**base_record, "status": "skipped_budget", "parse_status": "not_attempted", "error": "budget reserve would be exceeded"}
                budget_exhausted = True
                atomic_json(checkpoint_path, checkpoint)
                continue
            url = provider["base_url"].rstrip("/") + "/chat/completions"
            started = time.perf_counter()
            envelope: dict | None = None
            raw_path = run_dir / "raw" / alias / f"{case['sample_id']}.json"
            try:
                envelope, used_attempts = execute_call(url, api_key, payload, timeout, attempts, retry_base)
                latency_ms = round((time.perf_counter() - started) * 1000, 3)
                atomic_json(raw_path, envelope)
                usage = normalize_usage(envelope)
                amount = estimate_cost(model, usage)
                prediction = extract_json(response_content(envelope))
                validation_errors = validate_prediction(prediction, case["sample_id"], case["input"])
                parse_status = "valid" if not validation_errors else "schema_error"
                status = "success" if not validation_errors else "completed_invalid"
                record = {
                    **base_record, "status": status, "parse_status": parse_status,
                    "validation_errors": validation_errors, "latency_ms": latency_ms,
                    "usage": usage, "estimated_cost_usd": amount, "prediction": prediction,
                    "raw_response_path": str(raw_path.relative_to(ROOT)),
                    "attempt_count": base_record["attempt_count"] + used_attempts,
                    "response_model": envelope.get("model"),
                }
                checkpoint["actual_cost_usd"] = round(float(checkpoint.get("actual_cost_usd", 0)) + amount, 10)
            except Exception as exc:  # A failed item must not stop the remaining benchmark.
                latency_ms = round((time.perf_counter() - started) * 1000, 3)
                record = {
                    **base_record, "status": "failed", "parse_status": "invalid_or_unavailable",
                    "latency_ms": latency_ms, "error": f"{type(exc).__name__}: {exc}"[:2000],
                    "raw_response_path": str(raw_path.relative_to(ROOT)) if envelope is not None else None,
                    "attempt_count": base_record["attempt_count"] + attempts,
                }
            records[record_key] = record
            checkpoint["updated_at"] = now()
            atomic_json(checkpoint_path, checkpoint)
            print(json.dumps({
                "sample_id": case["sample_id"], "model": alias, "status": record["status"],
                "parse_status": record["parse_status"], "cost_usd": record.get("estimated_cost_usd", 0),
            }, ensure_ascii=False), flush=True)

    result_rows = [records[key] for key in sorted(records)]
    write_jsonl(run_dir / "results.jsonl", result_rows)
    summary = {
        "run_id": args.run_id, "mode": "execute", "cases": len(cases), "models": [x[0] for x in models],
        "records": len(result_rows), "success": sum(x.get("status") == "success" for x in result_rows),
        "completed_invalid": sum(x.get("status") == "completed_invalid" for x in result_rows),
        "failed": sum(x.get("status") == "failed" for x in result_rows),
        "skipped_budget": sum(x.get("status") == "skipped_budget" for x in result_rows),
        "estimated_cost_usd": checkpoint.get("actual_cost_usd", 0), "budget_usd": budget,
        "budget_exhausted": budget_exhausted, "updated_at": now(),
    }
    atomic_json(run_dir / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"benchmark configuration error: {exc}", file=sys.stderr)
        raise SystemExit(2)
