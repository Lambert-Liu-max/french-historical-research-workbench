from __future__ import annotations

import base64
import hashlib
import html
import json
import os
import re
import subprocess
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def write_jsonl(path: Path, values: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in values), encoding="utf-8")


def stable_id(value: Any, length: int = 16) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:length]


def curl_json(url: str, payload: dict, api_key: str, timeout: int = 300) -> dict:
    proc = subprocess.run(
        ["/usr/bin/curl", "--silent", "--show-error", "--fail-with-body",
         "--connect-timeout", "30", "--max-time", str(timeout), url,
         "-H", "Authorization: Bearer " + api_key,
         "-H", "Content-Type: application/json", "--data-binary", "@-"],
        input=json.dumps(payload, ensure_ascii=False), text=True, capture_output=True,
    )
    if proc.returncode:
        detail = (proc.stderr or proc.stdout).replace(api_key, "[REDACTED]")
        raise RuntimeError(f"model request failed: {detail[:800]}")
    return json.loads(proc.stdout)


def parse_model_json(text: str) -> dict:
    value = text.strip()
    if value.startswith("```"):
        lines = value.splitlines()[1:]
        if lines and lines[-1].strip() == "```":
            lines.pop()
        value = "\n".join(lines)
    start, end = value.find("{"), value.rfind("}")
    if start < 0 or end < start:
        raise ValueError("model did not return a JSON object")
    result = json.loads(value[start:end + 1])
    if not isinstance(result, dict):
        raise ValueError("model result is not an object")
    return result


def model_cost(config: dict, usage: dict) -> float:
    rates = config.get("rates_usd_per_million_tokens", {})
    input_tokens = int(usage.get("prompt_tokens") or 0)
    output_tokens = int(usage.get("completion_tokens") or 0)
    return (input_tokens * float(rates.get("input", 0)) + output_tokens * float(rates.get("output", 0))) / 1_000_000


ANALYSIS_SYSTEM = """You are an evidence-first assistant for French historical archives.
Use only the supplied source text and project research question. Never turn an inference into a source fact.
Classify the target person, material type and A/B/C/D research relevance. Reconstruct a compact but intelligible
event: retain the issue, the target person's intervention, direct responses and visible outcome. Provide a fluent
Chinese translation of the selected French evidence, a short Chinese synopsis, and explicit evidence quotes.
If evidence is damaged or incomplete, set needs_review=true. Return JSON only."""


def analyze_with_model(record: dict, config: dict) -> tuple[dict, dict]:
    model = config["model"]
    key = os.environ.get(model["api_key_env"], "").strip()
    if not key:
        raise RuntimeError(f"missing environment variable {model['api_key_env']}")
    request = {
        "research_question": config["research"]["question"],
        "target_person": config["research"]["person"],
        "relevance_levels": {
            "A": "direct evidence for the research question; preserve full event",
            "B": "important context or political action; preserve useful context",
            "C": "possible relevance or uncertain identity; retain for review",
            "D": "vote, attendance, index or list; preserve only the minimum verifiable evidence",
        },
        "required_output": {
            "identity_status": "confirmed|probable|uncertain|false",
            "material_type": "formal_speech|interjection_or_response|substantive_mention|proposal_report_committee|vote_or_attendance|index_or_toc|irrelevant_or_ocr_error|uncertain",
            "relevance_level": "A|B|C|D",
            "event_title": "string", "relevance_reason": "string",
            "selected_french_text": "verbatim evidence, preserving speaker order",
            "chinese_translation": "translation corresponding to selected_french_text",
            "chinese_synopsis": "short synopsis, not source text",
            "evidence": ["short verbatim quotations"],
            "people_and_institutions": ["string"],
            "needs_review": True, "confidence": 0.0,
        },
        "record": record,
    }
    payload = {
        "model": model["name"], "temperature": 0,
        "max_tokens": int(model.get("max_tokens", 4000)),
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": ANALYSIS_SYSTEM},
            {"role": "user", "content": json.dumps(request, ensure_ascii=False)},
        ],
    }
    if model.get("thinking"):
        payload["thinking"] = {"type": model["thinking"]}
    envelope = curl_json(model["base_url"].rstrip("/") + "/chat/completions", payload, key)
    message = envelope["choices"][0]["message"].get("content") or ""
    result = parse_model_json(message)
    usage = envelope.get("usage") or {}
    metadata = {
        "model": envelope.get("model", model["name"]), "usage": usage,
        "estimated_cost_usd": model_cost(model, usage),
    }
    return result, metadata


def transcribe_image_with_model(path: Path, config: dict) -> tuple[dict, dict]:
    model = config.get("vision_model") or config["model"]
    key = os.environ.get(model["api_key_env"], "").strip()
    if not key:
        raise RuntimeError(f"missing environment variable {model['api_key_env']}")
    mime = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".tif": "image/tiff", ".tiff": "image/tiff"}.get(path.suffix.lower(), "application/octet-stream")
    image_url = f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode("ascii")
    instruction = {
        "task": "Classify this French archival page as printed, typed, handwriting, mixed, or unreadable; then transcribe it faithfully in French.",
        "output": {"document_type": "printed|typed|handwriting|mixed|unreadable", "transcription_fr": "string", "confidence": 0.0, "needs_review": True, "notes": "string"},
        "rules": ["Do not modernize spelling", "Mark illegible spans as [illisible]", "Do not translate or summarize"],
    }
    payload = {
        "model": model["name"], "temperature": 0, "max_tokens": int(model.get("max_tokens", 5000)),
        "response_format": {"type": "json_object"},
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": json.dumps(instruction, ensure_ascii=False)},
            {"type": "image_url", "image_url": {"url": image_url}},
        ]}],
    }
    envelope = curl_json(model["base_url"].rstrip("/") + "/chat/completions", payload, key, int(model.get("timeout_seconds", 900)))
    result = parse_model_json(envelope["choices"][0]["message"].get("content") or "")
    usage = envelope.get("usage") or {}
    return result, {"model": envelope.get("model", model["name"]), "usage": usage, "estimated_cost_usd": model_cost(model, usage)}


def record_text(record: dict) -> str:
    if record.get("pages"):
        return "\n\n".join(
            f"[scan {p.get('scan_page', '?')} / printed {p.get('printed_page', '?')}]\n{p.get('text', '')}"
            for p in record["pages"]
        )
    return str(record.get("text") or record.get("ocr_text") or "")


def rule_analysis(record: dict, config: dict) -> dict:
    text = record_text(record)
    low = text.casefold()
    variants = [x.casefold() for x in config["research"].get("name_variants", [])]
    keywords = [x.casefold() for x in config["research"].get("keywords", [])]
    names = [x for x in variants if x and x in low]
    topics = [x for x in keywords if x and x in low]
    vote = bool(re.search(r"scrutin|ont voté|n.?ont pas pris part|abstenu", low))
    speech = bool(re.search(r"m\.?\s+landry\s*[:.—-]|la parole est à m\.?\s+landry", low))
    level = "D" if vote else ("A" if topics and speech else "B" if names and topics else "C")
    material = "vote_or_attendance" if vote else "formal_speech" if speech else "substantive_mention" if names else "uncertain"
    return {
        "identity_status": "probable" if names else "uncertain", "material_type": material,
        "relevance_level": level, "event_title": record.get("title") or "Candidate archival event",
        "relevance_reason": "Local rule match: " + ", ".join(topics or names or ["uncertain"]),
        "selected_french_text": text, "chinese_translation": "[模型未启用：保留法文待译]",
        "chinese_synopsis": "[模型未启用：仅完成规则初筛]", "evidence": (topics + names)[:8],
        "people_and_institutions": [], "needs_review": True, "confidence": 0.5,
    }


def validate_analysis(result: dict) -> list[str]:
    errors = []
    if result.get("identity_status") not in {"confirmed", "probable", "uncertain", "false"}:
        errors.append("invalid identity_status")
    if result.get("relevance_level") not in {"A", "B", "C", "D"}:
        errors.append("invalid relevance_level")
    if not isinstance(result.get("needs_review"), bool):
        errors.append("needs_review must be boolean")
    if not str(result.get("selected_french_text") or "").strip():
        errors.append("selected_french_text is empty")
    return errors


def render_markdown(config: dict, results: list[dict], run_id: str) -> str:
    research = config["research"]
    lines = [
        f"# {config['project']['name']}", "", f"运行：`{run_id}`", "",
        f"- 研究对象：{research['person']}", f"- 问题意识：{research['question']}",
        f"- 事件数：{len(results)}", "",
    ]
    for index, row in enumerate(results, 1):
        analysis = row["analysis"]
        source = row["source"]
        lines += [
            f"## {index}. {analysis.get('event_title') or source.get('title') or source['record_id']}", "",
            "### 基本信息", "",
            f"- 日期：{source.get('date') or '待核'}", f"- 来源：{source.get('source_type', 'unknown')}",
            f"- ARK/URL：{source.get('url') or source.get('ark') or '本地文件'}",
            f"- 人物判断：{analysis.get('identity_status')}", f"- 材料类型：{analysis.get('material_type')}",
            f"- 研究等级：{analysis.get('relevance_level')}", f"- 置信度：{analysis.get('confidence')}",
            f"- 人工复核：{'是' if analysis.get('needs_review') else '否'}", "",
            "### 与问题意识的关系", "", str(analysis.get("relevance_reason") or ""), "",
            "### 法文证据", "", str(analysis.get("selected_french_text") or ""), "",
            "### 中文翻译", "", str(analysis.get("chinese_translation") or ""), "",
            "### 梗概", "", str(analysis.get("chinese_synopsis") or ""), "",
            "### 证据定位", "",
        ]
        lines += [f"- {x}" for x in analysis.get("evidence", [])] or ["- 待复核"]
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def run_pipeline(config_path: Path, input_path: Path, output_root: Path, run_id: str, execute: bool) -> dict:
    config = read_json(config_path)
    records = read_jsonl(input_path)
    run_dir = output_root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    state_path = run_dir / "state.json"
    state = read_json(state_path) if state_path.exists() else {"run_id": run_id, "records": {}, "cost_usd": 0.0}
    for source in records:
        source = dict(source)
        source.setdefault("record_id", stable_id(source))
        key = source["record_id"]
        fingerprint = stable_id({
            "pipeline_schema": config.get("schema_version", "1.0"),
            "source": source,
            "research": config["research"],
            "model": config.get("model") if execute else "local-rules",
            "execute": execute,
        })
        old = state["records"].get(key, {})
        if old.get("status") == "success" and old.get("fingerprint") == fingerprint:
            continue
        try:
            analysis, meta = analyze_with_model(source, config) if execute else (rule_analysis(source, config), {"model": "local-rules", "usage": {}, "estimated_cost_usd": 0})
            errors = validate_analysis(analysis)
            status = "success" if not errors else "needs_review"
            state["cost_usd"] = round(float(state.get("cost_usd", 0)) + float(meta.get("estimated_cost_usd", 0)), 10)
            state["records"][key] = {"status": status, "fingerprint": fingerprint, "source": source, "analysis": analysis, "model_metadata": meta, "errors": errors}
        except Exception as exc:
            state["records"][key] = {"status": "failed", "fingerprint": fingerprint, "source": source, "error": f"{type(exc).__name__}: {exc}"}
        state["updated_at"] = datetime.now(timezone.utc).isoformat()
        write_json(state_path, state)
    completed = [x for x in state["records"].values() if x.get("analysis")]
    review = [x for x in completed if x["analysis"].get("needs_review") or x.get("errors")]
    failed = [x for x in state["records"].values() if x.get("status") == "failed"]
    write_jsonl(run_dir / "results.jsonl", completed)
    write_jsonl(run_dir / "review_queue.jsonl", review + failed)
    (run_dir / "corpus.md").write_text(render_markdown(config, completed, run_id), encoding="utf-8")
    summary = {"run_id": run_id, "input_records": len(records), "completed": len(completed), "review_queue": len(review) + len(failed), "failed": len(failed), "estimated_cost_usd": state.get("cost_usd", 0), "output": str(run_dir / "corpus.md")}
    write_json(run_dir / "summary.json", summary)
    return summary


def curl_bytes(url: str, timeout: int = 90) -> bytes:
    proc = subprocess.run(["/usr/bin/curl", "--silent", "--show-error", "--fail-with-body", "--connect-timeout", "30", "--max-time", str(timeout), url], capture_output=True)
    if proc.returncode:
        raise RuntimeError(proc.stderr.decode(errors="replace")[:800])
    return proc.stdout


def cached_fetch(url: str, cache: Path, delay: float) -> bytes:
    target = cache / (hashlib.sha256(url.encode()).hexdigest() + ".xml")
    if target.exists() and target.stat().st_size:
        return target.read_bytes()
    data = curl_bytes(url)
    ET.fromstring(data)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    time.sleep(delay)
    return data


def parse_sru(data: bytes) -> list[dict]:
    root = ET.fromstring(data)
    ns = {"srw": "http://www.loc.gov/zing/srw/", "dc": "http://purl.org/dc/elements/1.1/"}
    output = []
    for node in root.findall(".//srw:recordData", ns):
        identifiers = [x.text.strip() for x in node.findall(".//dc:identifier", ns) if x.text]
        ark_value = next((x for x in identifiers if "ark:/12148/" in x), "")
        if not ark_value:
            continue
        ark = "ark:/12148/" + ark_value.split("ark:/12148/", 1)[1].split("/", 1)[0]
        output.append({"ark": ark, "url": "https://gallica.bnf.fr/" + ark,
                       "title": " | ".join(x.text.strip() for x in node.findall(".//dc:title", ns) if x.text),
                       "date": " | ".join(x.text.strip() for x in node.findall(".//dc:date", ns) if x.text)})
    return output


def parse_content_search(data: bytes) -> list[int]:
    root = ET.fromstring(data)
    pages = []
    for item in root.findall(".//item"):
        match = re.search(r"(\d+)$", item.findtext("p_id", default=""))
        if match:
            pages.append(int(match.group(1)))
    return sorted(set(pages))


def parse_alto(data: bytes) -> str:
    root = ET.fromstring(data)
    lines = []
    for node in root.iter():
        if node.tag.rsplit("}", 1)[-1] != "TextLine":
            continue
        words = [x.attrib.get("CONTENT", "") for x in node if x.tag.rsplit("}", 1)[-1] == "String"]
        if any(words):
            lines.append(" ".join(x for x in words if x))
    return "\n".join(lines)


def discover_gallica(config_path: Path, project_root: Path, limit: int) -> dict:
    config = read_json(config_path)
    g = config["gallica"]
    research = config["research"]
    surname = research.get("surname") or research["person"].split()[-1]
    cache = project_root / "cache/gallica"
    found: dict[str, dict] = {}
    for year in range(int(config["period"]["start_year"]), int(config["period"]["end_year"]) + 1):
        clauses = [f'gallica all "{surname}"', f'gallicapublication_date = "{year}"']
        if g.get("title_query"):
            clauses.append(f'dc.title all "{g["title_query"]}"')
        query = " and ".join(f"({x})" for x in clauses)
        url = g["sru_endpoint"] + "?" + urlencode({"version": "1.2", "operation": "searchRetrieve", "query": query, "maximumRecords": min(limit, 50), "startRecord": 1})
        for row in parse_sru(cached_fetch(url, cache / "sru", float(g.get("sru_delay_seconds", 5)))):
            found[row["ark"]] = row
            if len(found) >= limit:
                break
        if len(found) >= limit:
            break
    records = []
    context = int(g.get("context_pages", 1))
    for document in list(found.values())[:limit]:
        identifier = document["ark"].rsplit("/", 1)[-1]
        search_url = "https://gallica.bnf.fr/services/ContentSearch?" + urlencode({"ark": identifier, "query": surname})
        hits = parse_content_search(cached_fetch(search_url, cache / "content_search", float(g.get("content_search_delay_seconds", 5))))
        for hit in hits[: int(g.get("max_hits_per_document", 10))]:
            pages = []
            for scan in range(max(1, hit - context), hit + context + 1):
                alto_url = "https://gallica.bnf.fr/RequestDigitalElement?" + urlencode({"O": identifier, "E": "ALTO", "Deb": scan})
                text = parse_alto(cached_fetch(alto_url, cache / "alto", float(g.get("alto_delay_seconds", 20))))
                pages.append({"scan_page": scan, "text": text})
            records.append({"record_id": f"{identifier}-f{hit}", "source_type": "gallica", **document,
                            "hit_page": hit, "pages": pages})
    target = project_root / "inputs/gallica_records.jsonl"
    write_jsonl(target, records)
    return {"documents": len(found), "candidate_events": len(records), "output": str(target)}


def ingest_local(source: Path, output: Path) -> dict:
    records = []
    supported_text = {".txt", ".md"}
    images = {".jpg", ".jpeg", ".png", ".tif", ".tiff"}
    for path in sorted(x for x in source.rglob("*") if x.is_file()):
        if path.suffix.lower() in supported_text:
            records.append({"record_id": stable_id(str(path.resolve())), "source_type": "local_text", "title": path.name, "local_path": str(path), "text": path.read_text(encoding="utf-8", errors="replace")})
        elif path.suffix.lower() in images | {".pdf"}:
            records.append({"record_id": stable_id(str(path.resolve())), "source_type": "local_scan", "title": path.name, "local_path": str(path), "text": "[OCR/HTR required]", "processing_route": "vision_ocr" if path.suffix.lower() in images else "pdf_ocr"})
    write_jsonl(output, records)
    return {"files": len(records), "output": str(output), "needs_ocr_or_htr": sum(x["source_type"] == "local_scan" for x in records)}


def transcribe_local(config_path: Path, input_path: Path, output_path: Path, execute: bool) -> dict:
    records = read_jsonl(input_path)
    config = read_json(config_path)
    completed, review, cost = [], 0, 0.0
    for record in records:
        if record.get("source_type") != "local_scan" or record.get("processing_route") == "pdf_ocr":
            completed.append(record)
            review += int(record.get("source_type") == "local_scan")
            continue
        if not execute:
            record["text"] = "[vision model not executed]"
            record["document_type"] = "unclassified"
            record["needs_review"] = True
        else:
            result, metadata = transcribe_image_with_model(Path(record["local_path"]), config)
            record["text"] = result.get("transcription_fr", "")
            record["document_type"] = result.get("document_type", "unreadable")
            record["transcription_metadata"] = metadata
            record["needs_review"] = bool(result.get("needs_review", True))
            cost += float(metadata.get("estimated_cost_usd", 0))
        review += int(record.get("needs_review", False))
        completed.append(record)
    write_jsonl(output_path, completed)
    return {"records": len(completed), "review_queue": review, "estimated_cost_usd": round(cost, 10), "output": str(output_path)}
