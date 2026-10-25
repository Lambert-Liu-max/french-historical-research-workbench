from __future__ import annotations

from collections import Counter, defaultdict


PAGE_TYPES = {"document_page", "envelope", "package_cover", "index_card", "photo", "blank", "mixed", "uncertain"}
SCRIPT_TYPES = {"printed", "typed", "handwritten", "mixed", "uncertain", "not_applicable"}
LEVELS = {"A", "B", "C", "D"}


def validate_boundary_result(result: dict, known_scans: set[str]) -> list[str]:
    errors = []
    if not isinstance(result.get("pages"), list) or not isinstance(result.get("documents"), list):
        return ["pages and documents must be arrays"]
    returned_scans = [page.get("scan_id") for page in result["pages"]]
    missing = sorted(known_scans - set(returned_scans))
    duplicates = sorted(scan for scan, count in Counter(returned_scans).items() if scan and count > 1)
    if missing:
        errors.append("missing page classifications: " + ", ".join(missing))
    if duplicates:
        errors.append("duplicate page classifications: " + ", ".join(duplicates))
    for page in result["pages"]:
        if page.get("scan_id") not in known_scans:
            errors.append(f"unknown scan_id: {page.get('scan_id')}")
        if page.get("page_type") not in PAGE_TYPES:
            errors.append(f"invalid page_type: {page.get('page_type')}")
        if page.get("script_type") not in SCRIPT_TYPES:
            errors.append(f"invalid script_type: {page.get('script_type')}")
        if page.get("confidence") not in {"high", "medium", "low"}:
            errors.append(f"invalid page confidence: {page.get('confidence')}")
    for document in result["documents"]:
        scans = document.get("included_scans")
        if not isinstance(scans, list) or not scans or any(scan not in known_scans for scan in scans):
            errors.append(f"invalid included_scans for {document.get('local_id')}")
        if document.get("script_type") not in SCRIPT_TYPES - {"not_applicable"}:
            errors.append(f"invalid document script_type: {document.get('script_type')}")
        if document.get("relevance_level") not in LEVELS:
            errors.append(f"invalid relevance_level: {document.get('relevance_level')}")
        if document.get("boundary_confidence") not in {"high", "medium", "low"}:
            errors.append(f"invalid boundary confidence: {document.get('boundary_confidence')}")
    return errors


def boundary_needs_review(result: dict) -> bool:
    if result.get("review_items"):
        return True
    if any(
        page.get("confidence") != "high"
        or page.get("page_type") in {"mixed", "uncertain"}
        or page.get("script_type") in {"mixed", "uncertain"}
        or page.get("physical_page_count") not in {1, None}
        for page in result.get("pages", [])
    ):
        return True
    return any(document.get("boundary_confidence") != "high" or document.get("needs_review") for document in result.get("documents", []))


def merge_windows(manifest: dict, resolved: list[dict]) -> dict:
    scan_order = [row["scan_id"] for row in manifest["files"]]
    position = {scan: index for index, scan in enumerate(scan_order)}
    page_observations: dict[str, list] = defaultdict(list)
    pair_votes: dict[tuple[str, str], list] = defaultdict(list)
    document_candidates, review = [], []

    for item in resolved:
        data, weight = item["result"], int(item.get("weight", 1))
        membership: dict[str, set] = defaultdict(set)
        for page in data.get("pages", []):
            if page.get("scan_id") in position:
                page_observations[page["scan_id"]].append((weight, page))
        for document in data.get("documents", []):
            scans = [scan for scan in document.get("included_scans", []) if scan in position]
            if not scans:
                continue
            document_candidates.append((weight, set(scans), document))
            for scan in scans:
                membership[scan].add(document.get("local_id"))
        ids = item["window"]["scan_ids"]
        for left, right in zip(ids, ids[1:]):
            pair_votes[(left, right)].append((weight, bool(membership[left] & membership[right])))
        review.extend(data.get("review_items", []))

    page_types, script_types = {}, {}
    for scan in scan_order:
        page_votes, script_votes = [], []
        for weight, page in page_observations.get(scan, []):
            page_votes.extend([page.get("page_type", "uncertain")] * weight)
            script_votes.extend([page.get("script_type", "uncertain")] * weight)
        page_types[scan] = Counter(page_votes).most_common(1)[0][0] if page_votes else "uncertain"
        script_types[scan] = Counter(script_votes).most_common(1)[0][0] if script_votes else "uncertain"

    catalog_types = {"blank", "photo", "package_cover"}
    catalog = [
        {"scan_id": scan, "page_type": page_types[scan], "script_type": script_types[scan], "action": "catalog_only"}
        for scan in scan_order if page_types[scan] in catalog_types
    ]
    active = [scan for scan in scan_order if page_types[scan] not in catalog_types]
    groups, current = [], []
    for scan in active:
        if not current:
            current = [scan]
            continue
        left = current[-1]
        consecutive = position[scan] == position[left] + 1
        votes = pair_votes.get((left, scan), []) if consecutive else []
        yes = sum(weight for weight, same in votes if same)
        no = sum(weight for weight, same in votes if not same)
        if consecutive and yes > no:
            current.append(scan)
        else:
            if votes and yes == no:
                review.append({"scan_ids": [left, scan], "problem_type": "boundary_tie", "reason": "conservative split"})
            groups.append(current)
            current = [scan]
    if current:
        groups.append(current)

    documents = []
    for number, group in enumerate(groups, 1):
        group_set = set(group)
        matches = []
        for weight, scans, document in document_candidates:
            overlap = len(group_set & scans)
            if overlap:
                matches.append((overlap / len(group_set), overlap, weight, document))
        matches.sort(key=lambda row: (row[0], row[1], row[2]), reverse=True)
        best = matches[0][3] if matches else {}
        script_votes = [script_types[scan] for scan in group if script_types[scan] != "not_applicable"]
        script = best.get("script_type") or (Counter(script_votes).most_common(1)[0][0] if script_votes else "uncertain")
        documents.append({
            "document_id": f"document-{number:04d}", "start_scan": group[0], "end_scan": group[-1],
            "included_scans": group, "page_types": {scan: page_types[scan] for scan in group},
            "script_type": script, "date": best.get("date"), "author": best.get("author"),
            "recipient": best.get("recipient"), "document_type": best.get("document_type"),
            "boundary_evidence": best.get("boundary_evidence", "overlapping-window adjacency vote"),
            "boundary_confidence": best.get("boundary_confidence", "low"),
            "relevance_level": best.get("relevance_level", "C"),
            "relevance_reason": best.get("relevance_reason", "insufficient evidence"),
            "keywords": best.get("keywords", []), "needs_review": bool(best.get("needs_review", not matches)),
        })
    return {
        "schema_version": "1.0", "documents": documents, "catalog_only_scans": catalog,
        "review_items": review, "merge_policy": "review weight 3; primary weight 1; ties split conservatively",
    }


def transcription_role(document: dict) -> str:
    if document.get("script_type") in {"handwritten", "mixed", "uncertain"}:
        return "transcription_difficult"
    if document.get("needs_review") or document.get("boundary_confidence") != "high":
        return "transcription_difficult"
    return "transcription_clear"
