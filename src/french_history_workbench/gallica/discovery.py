from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

from .client import CacheStore, FetchError
from .parsers import (
    cluster_hits,
    context_pages,
    match_name_variants,
    occurrence_type,
    parse_alto,
    parse_content_page,
    parse_pagination,
    parse_sru_page,
)
from .query import build_query_plan, person_match_terms, sru_url


def _atomic_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _jsonl(path: Path, values: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in values), encoding="utf-8")
    temporary.replace(path)


def _id(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:16]


def _merge_document(target: dict, incoming: dict) -> None:
    for field in ("title", "date", "source", "identifier", "query_ids"):
        target[field] = sorted(set(target.get(field, []) + incoming.get(field, [])))


def _content_hits(cache: CacheStore, ark: str, term: str, page_limit: int = 1000) -> tuple[list[dict], int]:
    identifier = ark.rsplit("/", 1)[-1]
    hits: dict[tuple[int, str], dict] = {}
    start, total, seen_pages = 0, None, set()
    while total is None or len({row[0] for row in hits}) < total:
        params = {"ark": identifier, "query": term}
        if start:
            params["startResult"] = start
        url = "https://gallica.bnf.fr/services/ContentSearch?" + urlencode(params)
        response = cache.get("content_search", url)
        page_total, rows = parse_content_page(response.content)
        total = page_total if total is None else max(total, page_total)
        signature = tuple((row["scan_page"], row["snippet"]) for row in rows)
        if signature in seen_pages:
            break
        seen_pages.add(signature)
        for row in rows:
            key = (int(row["scan_page"]), row["snippet"])
            hits[key] = {**row, "matched_terms": [term]}
        if not rows or len(seen_pages) >= page_limit or len({row[0] for row in hits}) >= total:
            break
        # The service is capped at ten items. Using the count already returned
        # is robust to both zero- and one-based interpretations: an overlap is
        # deduplicated, while no result can be skipped.
        start += len(rows)
    return list(hits.values()), int(total or 0)


def _local_cache_audit(
    cache: CacheStore,
    variants: list[str],
    represented: set[tuple[str, int]],
    allowed_arks: set[str] | None = None,
) -> list[dict]:
    if not cache.manifest.exists():
        return []
    latest = {}
    for line in cache.manifest.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("namespace") == "alto" and row.get("cache_path"):
            latest[row["url"]] = row
    review = []
    for url, row in latest.items():
        query = parse_qs(urlparse(url).query)
        identifier = (query.get("O") or [""])[0]
        raw_scan = (query.get("Deb") or [""])[0]
        if not identifier or not raw_scan.isdigit():
            continue
        ark, scan = f"ark:/12148/{identifier}", int(raw_scan)
        if allowed_arks is not None and ark not in allowed_arks:
            continue
        path = cache.root / row["cache_path"]
        try:
            parsed = parse_alto(path.read_bytes())
        except Exception:
            continue
        matches = match_name_variants(parsed["text"], variants)
        if matches and (ark, scan) not in represented:
            review.append({
                "review_id": _id(f"cache-miss|{ark}|{scan}"), "reason": "cached_ocr_name_not_returned_as_content_hit",
                "ark": ark, "scan_pages": [scan], "matched_variants": matches,
                "suggested_action": "inspect_cached_page_before_exclusion",
            })
    return review


def audit_cached_ocr(config: dict, project_root: Path, represented: set[tuple[str, int]] | None = None) -> list[dict]:
    cache = CacheStore(project_root / "cache/gallica")
    return _local_cache_audit(cache, person_match_terms(config), represented or set())


def discover(config: dict, project_root: Path, document_limit: int = 0, transport=None) -> dict:
    """Discover Gallica material with provenance, paging, merging and recall audit."""
    gallica = config["gallica"]
    discovery = config.get("discovery", {})
    delays = {
        "sru": float(gallica.get("sru_delay_seconds", 5)),
        "content_search": float(gallica.get("content_search_delay_seconds", 5)),
        "pagination": float(gallica.get("pagination_delay_seconds", 20)),
        "alto": float(gallica.get("alto_delay_seconds", 20)),
    }
    cache = CacheStore(
        project_root / "cache/gallica", transport=transport, delays=delays,
        retries=int(discovery.get("network_retries", 3)),
        backoff_seconds=float(discovery.get("retry_backoff_seconds", 3)),
    )
    specs = build_query_plan(config)
    page_size = int(discovery.get("sru_page_size", 50))
    per_query_limit = int(discovery.get("max_records_per_query", 1000))
    query_manifest, miss_review = [], []
    documents: dict[str, dict] = {}

    for spec in specs:
        if document_limit and len(documents) >= document_limit:
            query_manifest.append({
                **spec.to_dict(), "reported_records": 0, "fetched_records": 0,
                "status": "not_run_preview_limit", "error": "",
            })
            continue
        start, total, fetched, status, error = 1, None, 0, "success", ""
        while total is None or start <= total:
            if document_limit and len(documents) >= document_limit:
                status = "truncated_by_preview_limit"
                break
            if per_query_limit and fetched >= per_query_limit:
                status = "truncated_by_config"
                break
            requested = min(page_size, per_query_limit - fetched if per_query_limit else page_size)
            if document_limit:
                requested = min(requested, max(1, document_limit - len(documents)))
            url = sru_url(gallica["sru_endpoint"], spec, start, requested)
            try:
                response = cache.get("sru", url)
                page_total, rows = parse_sru_page(response.content, spec.query_id)
            except Exception as exc:
                status, error = "failed", f"{type(exc).__name__}: {exc}"
                miss_review.append({
                    "review_id": _id(f"query|{spec.query_id}|{start}"), "reason": "sru_query_failed",
                    "query_id": spec.query_id, "term": spec.term, "year": spec.year,
                    "chamber": spec.chamber, "error": error, "suggested_action": "rerun_from_cache_or_retry",
                })
                break
            total = page_total
            for row in rows:
                if row["ark"] in documents:
                    _merge_document(documents[row["ark"]], row)
                else:
                    documents[row["ark"]] = row
                if document_limit and len(documents) >= document_limit:
                    break
            fetched += len(rows)
            if not rows:
                break
            start += len(rows)
        query_manifest.append({**spec.to_dict(), "reported_records": total or 0, "fetched_records": fetched, "status": status, "error": error})

    selected_documents = list(sorted(documents.values(), key=lambda row: row["ark"]))
    if document_limit:
        selected_documents = selected_documents[:document_limit]
    remote_terms = [item["term"] for item in query_manifest if item["term"]]
    remote_terms = list(dict.fromkeys(remote_terms))
    variants = person_match_terms(config)
    radius = int(gallica.get("context_pages", 1))
    gap = int(discovery.get("event_merge_gap_pages", 4))
    records, represented = [], set()
    documents_with_hits = 0

    for document in selected_documents:
        ark = document["ark"]
        merged_hits: dict[int, dict] = {}
        content_failures = []
        for term in remote_terms:
            try:
                rows, reported = _content_hits(cache, ark, term, int(discovery.get("content_page_limit", 1000)))
            except Exception as exc:
                content_failures.append({"term": term, "error": f"{type(exc).__name__}: {exc}"})
                continue
            for row in rows:
                target = merged_hits.setdefault(int(row["scan_page"]), {"scan_page": int(row["scan_page"]), "snippets": [], "matched_terms": []})
                target["snippets"].append(row["snippet"])
                target["matched_terms"] = sorted(set(target["matched_terms"] + row["matched_terms"]))
        if content_failures:
            miss_review.append({
                "review_id": _id(f"content-failure|{ark}"), "reason": "content_search_incomplete",
                "ark": ark, "failures": content_failures, "suggested_action": "retry_failed_terms",
            })
        if not merged_hits:
            miss_review.append({
                "review_id": _id(f"no-hit|{ark}"), "reason": "sru_document_without_content_hit",
                "ark": ark, "query_ids": document.get("query_ids", []), "suggested_action": "retain_as_search_discrepancy",
            })
            continue
        documents_with_hits += 1
        identifier = ark.rsplit("/", 1)[-1]
        pagination = {}
        try:
            pagination_url = "https://gallica.bnf.fr/services/Pagination?" + urlencode({"ark": identifier})
            pagination = parse_pagination(cache.get("pagination", pagination_url).content)
        except Exception as exc:
            miss_review.append({
                "review_id": _id(f"pagination|{ark}"), "reason": "pagination_unavailable", "ark": ark,
                "error": f"{type(exc).__name__}: {exc}", "suggested_action": "retain_scan_page_citation",
            })
        groups = cluster_hits(list(merged_hits.values()), gap)
        for group in groups:
            hit_pages = sorted({int(row["scan_page"]) for row in group})
            event_key = f"{ark}|{','.join(map(str, hit_pages))}"
            event_id = "gallica-" + _id(event_key)
            pages, missing_pages, local_matches = [], [], []
            for scan in context_pages(group, radius):
                alto_url = "https://gallica.bnf.fr/RequestDigitalElement?" + urlencode({"O": identifier, "E": "ALTO", "Deb": scan})
                try:
                    parsed = parse_alto(cache.get("alto", alto_url).content)
                except Exception as exc:
                    missing_pages.append({"scan_page": scan, "error": f"{type(exc).__name__}: {exc}"})
                    continue
                matches = match_name_variants(parsed["text"], variants)
                if matches:
                    local_matches.extend({"scan_page": scan, **row} for row in matches)
                pages.append({"scan_page": scan, "printed_page": pagination.get(scan, ""), "text": parsed["text"]})
            for scan in hit_pages:
                represented.add((ark, scan))
            if missing_pages:
                miss_review.append({
                    "review_id": _id(f"alto|{event_id}"), "reason": "event_context_incomplete", "event_id": event_id,
                    "ark": ark, "missing_pages": missing_pages, "suggested_action": "retry_only_missing_pages",
                })
            if not any(row["scan_page"] in hit_pages for row in local_matches):
                miss_review.append({
                    "review_id": _id(f"verify|{event_id}"), "reason": "content_hit_not_verified_in_alto",
                    "event_id": event_id, "ark": ark, "scan_pages": hit_pages,
                    "suggested_action": "review_ocr_or_false_positive",
                })
            text_for_type = "\n".join(page["text"] for page in pages if page["scan_page"] in hit_pages)
            records.append({
                "record_id": event_id, "source_type": "gallica", "ark": ark,
                "url": "https://gallica.bnf.fr/" + ark,
                "date": " | ".join(document.get("date", [])),
                "title": " | ".join(document.get("title", [])),
                "source": document.get("source", []), "query_ids": document.get("query_ids", []),
                "hit_pages": hit_pages, "pages": pages,
                "discovery": {
                    "matched_terms": sorted({term for row in group for term in row.get("matched_terms", [])}),
                    "snippets": [snippet for row in group for snippet in row.get("snippets", [])],
                    "local_name_matches": local_matches,
                    "preliminary_material_type": occurrence_type(text_for_type),
                    "missing_context_pages": missing_pages,
                },
            })

    miss_review.extend(_local_cache_audit(cache, variants, represented, {row["ark"] for row in selected_documents}))
    unique_review = {row["review_id"]: row for row in miss_review}
    audit_dir = project_root / "audit"
    input_path = project_root / "inputs/gallica_records.jsonl"
    _jsonl(input_path, records)
    _jsonl(audit_dir / "query_manifest.jsonl", query_manifest)
    _jsonl(audit_dir / "miss_review.jsonl", list(unique_review.values()))
    _jsonl(audit_dir / "discovery_records.jsonl", selected_documents)
    summary = {
        "queries": len(specs), "queries_failed": sum(row["status"] == "failed" for row in query_manifest),
        "documents_found": len(documents), "documents_processed": len(selected_documents),
        "documents_with_hits": documents_with_hits, "candidate_events": len(records),
        "miss_review": len(unique_review), "output": str(input_path),
        "audit": str(audit_dir / "miss_review.jsonl"),
    }
    _atomic_json(audit_dir / "discovery_summary.json", summary)
    return summary
