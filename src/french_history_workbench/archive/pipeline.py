from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from typing import Callable

from ..core import read_json, stable_id, write_jsonl
from .ingest import atomic_json, derivative, inventory, now, read_json as read_state, windows
from .runtime import ModelRuntime
from .segmentation import boundary_needs_review, merge_windows, transcription_role, validate_boundary_result


RESOURCE_ROOT = Path(__file__).resolve().parents[1] / "resources"


def _resource(*parts: str) -> str:
    return RESOURCE_ROOT.joinpath(*parts).read_text(encoding="utf-8")


def _image_block(path: Path) -> dict:
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + encoded, "detail": "high"}}


def _boundary_request(config: dict, window: dict, images: list[Path], primary: dict | None = None) -> dict:
    schema = json.loads(_resource("schemas", "archive_boundary.json"))
    task = {
        "research_person": config["research"]["person"],
        "research_question": config["research"]["question"],
        "scan_order": window["scan_ids"], "window": [window["first_order"], window["last_order"]],
        "required_schema": schema,
    }
    if primary is not None:
        task["primary_result_to_review"] = primary
        task["review_instruction"] = "Correct only unsupported, inconsistent or uncertain classifications; preserve visible evidence."
    content = [{"type": "text", "text": json.dumps(task, ensure_ascii=False)}]
    content.extend(_image_block(path) for path in images)
    return {
        "max_tokens": 7000,
        "messages": [
            {"role": "system", "content": _resource("prompts", "archive_boundary.md")},
            {"role": "user", "content": content},
        ],
    }


def _transcription_request(document: dict, scans: list[str], images: list[Path], batch_number: int, batch_count: int) -> dict:
    task = {
        "document_id": document["document_id"], "script_type": document["script_type"],
        "full_document_scan_order": document["included_scans"], "this_batch": scans,
        "batch_number": batch_number, "batch_count": batch_count,
        "instruction": "Return only this batch. Do not infer text from preceding or following batches.",
    }
    content = [{"type": "text", "text": json.dumps(task, ensure_ascii=False)}]
    content.extend(_image_block(path) for path in images)
    return {
        "max_tokens": 16000,
        "messages": [
            {"role": "system", "content": _resource("prompts", "archive_transcription.md")},
            {"role": "user", "content": content},
        ],
    }


def _validate_transcription(result: dict, scans: list[str]) -> list[str]:
    pages = result.get("pages")
    if not isinstance(pages, list):
        return ["pages must be an array"]
    returned = [page.get("scan_id") for page in pages]
    errors = [f"unexpected scan: {scan}" for scan in returned if scan not in scans]
    errors.extend(f"missing scan: {scan}" for scan in scans if scan not in returned)
    for page in pages:
        if not isinstance(page.get("french_transcription"), str):
            errors.append(f"missing transcription: {page.get('scan_id')}")
    return errors


def _acquire_lock(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        try:
            pid = int(read_state(path).get("pid", -1))
            os.kill(pid, 0)
        except (ProcessLookupError, ValueError, json.JSONDecodeError):
            path.unlink(missing_ok=True)
        else:
            raise RuntimeError(f"archive run already active with PID {pid}")
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump({"pid": os.getpid(), "created_at": now()}, handle)


def run_archive_pipeline(
    config_path: Path,
    source: Path,
    project_root: Path,
    run_id: str,
    execute: bool = False,
    caller: Callable | None = None,
    image_deriver: Callable = derivative,
) -> dict:
    config = read_json(config_path)
    archive = config.get("archive", {})
    run_root = project_root / "archive_runs" / run_id
    run_root.mkdir(parents=True, exist_ok=True)
    lock = run_root / "run.lock"
    _acquire_lock(lock)
    try:
        manifest = inventory(source.resolve(), run_root / "manifest.json")
        file_map = {row["scan_id"]: row for row in manifest["files"]}
        window_plan = windows(
            manifest["files"], int(archive.get("window_size", 10)), int(archive.get("window_overlap", 2)),
        )
        atomic_json(run_root / "boundary_plan.json", {"windows": window_plan})
        if not execute:
            summary = {
                "run_id": run_id, "status": "planned", "scans": manifest["scan_count"],
                "boundary_windows": len(window_plan), "estimated_cost_usd": 0.0,
                "next_step": "rerun with --execute",
            }
            atomic_json(run_root / "summary.json", summary)
            return summary

        runtime = ModelRuntime(config, run_root, execute=True, caller=caller)
        resolved, failures = [], []
        derivative_paths: dict[str, Path] = {}
        for scan_id, item in file_map.items():
            derivative_paths[scan_id] = image_deriver(
                item, run_root / "derived" / f"{item['order']:06d}-{item['sha256'][:12]}.jpg",
                int(archive.get("derivative_max_pixels", 2048)),
            )
        for window in window_plan:
            images = [derivative_paths[scan] for scan in window["scan_ids"]]
            try:
                primary, primary_meta = runtime.call(
                    "boundary_primary", window["window_id"], _boundary_request(config, window, images),
                    float(archive.get("budget", {}).get("reserve_per_boundary_call_usd", 0.5)),
                )
                errors = validate_boundary_result(primary, set(window["scan_ids"]))
                if errors:
                    raise RuntimeError("; ".join(errors))
                chosen, metadata, weight = primary, primary_meta, 1
                if archive.get("boundary_review_enabled", True) and boundary_needs_review(primary):
                    try:
                        reviewed, review_meta = runtime.call(
                            "boundary_review", window["window_id"], _boundary_request(config, window, images, primary),
                            float(archive.get("budget", {}).get("reserve_per_boundary_call_usd", 0.5)),
                        )
                        errors = validate_boundary_result(reviewed, set(window["scan_ids"]))
                        if errors:
                            raise RuntimeError("; ".join(errors))
                        chosen, metadata, weight = reviewed, review_meta, 3
                    except Exception as exc:
                        failures.append({
                            "problem_type": "boundary_review_failure", "window_id": window["window_id"],
                            "scan_ids": window["scan_ids"], "fallback": "validated_primary_result",
                            "reason": f"{type(exc).__name__}: {exc}",
                        })
                resolved.append({"window": window, "result": chosen, "metadata": metadata, "weight": weight})
            except Exception as exc:
                failures.append({
                    "problem_type": "boundary_failure", "window_id": window["window_id"],
                    "scan_ids": window["scan_ids"], "reason": f"{type(exc).__name__}: {exc}",
                })

        if not resolved:
            summary = {
                "run_id": run_id, "status": "failed", "scans": manifest["scan_count"],
                "boundary_windows": len(window_plan), "boundary_failures": len(failures),
                "review_queue": len(failures), "estimated_cost_usd": runtime.ledger().get("spent_usd", 0),
            }
            write_jsonl(run_root / "review_queue.jsonl", failures)
            atomic_json(run_root / "summary.json", summary)
            return summary

        index = merge_windows(manifest, resolved)
        atomic_json(run_root / "document_index.json", index)
        review = list(index.get("review_items", [])) + failures
        transcribe_levels = set(archive.get("classes_to_transcribe", ["A", "B", "C"]))
        batch_size = int(archive.get("transcription_batch_size", 5))
        if batch_size <= 0:
            raise ValueError("archive.transcription_batch_size must be greater than zero")
        records, catalog, transcription_failures = [], [], []

        for document in index["documents"]:
            if document["relevance_level"] not in transcribe_levels:
                catalog.append(document)
                continue
            excluded = {"package_cover", "photo", "blank"}
            scans = [scan for scan in document["included_scans"] if document["page_types"].get(scan) not in excluded]
            scans = scans or document["included_scans"]
            chunks = [scans[i:i + batch_size] for i in range(0, len(scans), batch_size)]
            role = transcription_role(document)
            pages_by_scan, notes, needs_review = {}, [], bool(document.get("needs_review"))
            try:
                for number, chunk in enumerate(chunks, 1):
                    task_id = f"{document['document_id']}-part-{number:03d}"
                    result, _metadata = runtime.call(
                        role, task_id,
                        _transcription_request(document, chunk, [derivative_paths[scan] for scan in chunk], number, len(chunks)),
                        float(archive.get("budget", {}).get("reserve_per_transcription_call_usd", 0.5)),
                    )
                    errors = _validate_transcription(result, chunk)
                    if errors:
                        raise RuntimeError("; ".join(errors))
                    needs_review = needs_review or bool(result.get("needs_review"))
                    notes.extend(result.get("document_notes", []))
                    for page in result["pages"]:
                        pages_by_scan[page["scan_id"]] = page
            except Exception as exc:
                failure = {
                    "problem_type": "transcription_failure", "document_id": document["document_id"],
                    "scan_ids": scans, "reason": f"{type(exc).__name__}: {exc}",
                }
                transcription_failures.append(failure)
                review.append(failure)
                continue
            pages = []
            for scan in scans:
                page = pages_by_scan.get(scan)
                if page is None:
                    page = {"scan_id": scan, "french_transcription": "[illisible]", "notes": ["missing model page"], "needs_review": True}
                    needs_review = True
                pages.append({
                    "scan_page": scan, "text": page["french_transcription"],
                    "notes": page.get("notes", []), "needs_review": bool(page.get("needs_review")),
                })
            if needs_review:
                review.append({
                    "problem_type": "transcription_review", "document_id": document["document_id"],
                    "scan_ids": scans, "reason": "model or boundary marked this document for review",
                })
            records.append({
                "record_id": f"archive-{stable_id(run_id + '|' + document['document_id'])}",
                "source_type": "local_archive", "title": document.get("document_type") or document["document_id"],
                "date": document.get("date"), "pages": pages,
                "archive_metadata": {
                    **document, "run_id": run_id, "source_scan_ids": scans,
                    "transcription_role": role, "document_notes": notes,
                },
            })

        output = run_root / "archive_records.jsonl"
        write_jsonl(output, records)
        write_jsonl(run_root / "catalog_only.jsonl", catalog + index.get("catalog_only_scans", []))
        write_jsonl(run_root / "review_queue.jsonl", review)
        summary = {
            "run_id": run_id, "status": "complete_with_exceptions" if failures or transcription_failures else "complete",
            "scans": manifest["scan_count"], "boundary_windows": len(window_plan),
            "documents": len(index["documents"]), "transcribed_documents": len(records),
            "catalog_only": len(catalog) + len(index.get("catalog_only_scans", [])),
            "boundary_failures": len(failures), "transcription_failures": len(transcription_failures),
            "review_queue": len(review), "estimated_cost_usd": runtime.ledger().get("spent_usd", 0),
            "output": str(output),
        }
        atomic_json(run_root / "summary.json", summary)
        return summary
    finally:
        lock.unlink(missing_ok=True)
