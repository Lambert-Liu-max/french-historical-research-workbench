from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET

from .query import fold


SRW = "http://www.loc.gov/zing/srw/"
DC = "http://purl.org/dc/elements/1.1/"


def validate_xml(data: bytes) -> None:
    prefix = data.lstrip()[:300].lower()
    if not data or b"<html" in prefix or b"<!doctype html" in prefix:
        raise ValueError("empty response or HTML security page")
    ET.fromstring(data)


def parse_sru_page(data: bytes, query_id: str) -> tuple[int, list[dict]]:
    root = ET.fromstring(data)
    total = int(root.findtext(f".//{{{SRW}}}numberOfRecords", default="0") or 0)
    records = []
    for node in root.findall(f".//{{{SRW}}}recordData"):
        values = {
            field: [x.text.strip() for x in node.findall(f".//{{{DC}}}{field}") if x.text]
            for field in ("title", "date", "source", "identifier")
        }
        raw_ark = next((x for x in values["identifier"] if "ark:/12148/" in x), "")
        if not raw_ark:
            continue
        ark = "ark:/12148/" + raw_ark.split("ark:/12148/", 1)[1].split("/", 1)[0]
        records.append({"ark": ark, **values, "query_ids": [query_id]})
    return total, records


def parse_content_page(data: bytes) -> tuple[int, list[dict]]:
    root = ET.fromstring(data)
    total = int(root.attrib.get("countResults") or 0)
    hits = []
    for item in root.findall(".//item"):
        match = re.search(r"(\d+)$", item.findtext("p_id", default=""))
        if not match:
            continue
        snippet = html.unescape(html.unescape(item.findtext("content", default="")))
        snippet = re.sub(r"<[^>]+>", "", snippet)
        hits.append({"scan_page": int(match.group(1)), "snippet": snippet.strip()})
    return total, hits


def parse_pagination(data: bytes) -> dict[int, str]:
    root = ET.fromstring(data)
    result = {}
    for page in root.findall(".//page"):
        order = page.findtext("ordre", default="")
        if order.isdigit():
            result[int(order)] = page.findtext("numero", default="")
    return result


def parse_alto(data: bytes) -> dict:
    try:
        decoded = data.decode("utf-8")
        decoded = re.sub(r'encoding=["\']ISO-8859-1["\']', 'encoding="UTF-8"', decoded, count=1, flags=re.I)
        root = ET.fromstring(decoded)
    except UnicodeDecodeError:
        root = ET.fromstring(data)
    lines, words = [], []
    for node in root.iter():
        if node.tag.rsplit("}", 1)[-1] != "TextLine":
            continue
        line_words = [
            child.attrib.get("CONTENT", "") for child in node
            if child.tag.rsplit("}", 1)[-1] == "String" and child.attrib.get("CONTENT")
        ]
        if line_words:
            lines.append(" ".join(line_words))
            words.extend(line_words)
    return {"text": "\n".join(lines), "lines": lines, "word_count": len(words)}


def match_name_variants(text: str, variants: list[str], radius: int = 2) -> list[dict]:
    """Match explicit name variants after accent/punctuation/line-break folding."""
    normalized = fold(text)
    lines = text.splitlines()
    results, seen = [], set()
    for variant in variants:
        needle = fold(variant)
        if not needle:
            continue
        expression = r"(?<![a-z0-9])" + r"\s+".join(re.escape(x) for x in needle.split()) + r"(?![a-z0-9])"
        if not re.search(expression, normalized):
            continue
        key = needle
        if key in seen:
            continue
        seen.add(key)
        tokens = needle.split()
        line_index = next((i for i, line in enumerate(lines) if any(token in fold(line).split() for token in tokens[-2:])), 0)
        context = "\n".join(lines[max(0, line_index - radius):min(len(lines), line_index + radius + 1)])
        results.append({"variant": variant, "normalized_variant": needle, "context": context})
    return results


def occurrence_type(text: str) -> str:
    value = fold(text)
    if re.search(r"ont vote|scrutin|n ont pas pris part|se sont abstenus", value):
        return "vote_or_attendance"
    if re.search(r"la parole est a m landry|m landry|adolphe landry", value):
        return "speech_or_interaction"
    if re.search(r"rapport de m landry|proposition de m landry|landry rapporteur", value):
        return "proposal_report_committee"
    return "mention_or_uncertain"


def cluster_hits(hits: list[dict], gap: int = 4) -> list[list[dict]]:
    ordered = sorted(hits, key=lambda row: int(row["scan_page"]))
    groups: list[list[dict]] = []
    for hit in ordered:
        if not groups or int(hit["scan_page"]) - int(groups[-1][-1]["scan_page"]) > gap:
            groups.append([hit])
        else:
            groups[-1].append(hit)
    return groups


def context_pages(hits: list[dict], radius: int = 1) -> list[int]:
    pages = {int(hit["scan_page"]) for hit in hits}
    expanded = {page + offset for page in pages for offset in range(-radius, radius + 1) if page + offset >= 1}
    return sorted(expanded)
