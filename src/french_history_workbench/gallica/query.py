from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import asdict, dataclass
from urllib.parse import urlencode


@dataclass(frozen=True)
class QuerySpec:
    query_id: str
    year: int
    chamber: str
    term: str
    term_type: str
    cql: str

    def to_dict(self) -> dict:
        return asdict(self)


def fold(value: str) -> str:
    text = "".join(
        char for char in unicodedata.normalize("NFKD", value.casefold())
        if not unicodedata.combining(char)
    )
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def cql_quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _configured_terms(config: dict) -> list[tuple[str, str]]:
    research = config["research"]
    discovery = config.get("discovery", {})
    raw = discovery.get("search_terms")
    if not raw:
        surname = research.get("surname") or research["person"].split()[-1]
        raw = [surname, research["person"]]
    result: list[tuple[str, str]] = []
    seen: set[str] = set()
    for item in raw:
        if isinstance(item, str):
            term, term_type = item, "name_variant"
        else:
            term = str(item.get("text") or "")
            term_type = str(item.get("type") or "name_variant")
        key = fold(term)
        if term.strip() and key not in seen:
            seen.add(key)
            result.append((term.strip(), term_type))
    return result


def person_match_terms(config: dict) -> list[str]:
    """Return explicit variants for local OCR verification.

    Local matching is deliberately broader than remote search. This catches
    line breaks and known OCR confusions without multiplying every SRU query.
    """
    discovery = config.get("discovery", {})
    research = config["research"]
    surname = research.get("surname") or research["person"].split()[-1]
    surname_key = fold(surname)
    values = [research["person"], surname]
    # Titles without the person's surname (for example "le ministre du
    # travail") are contextual clues, not safe identity variants.
    values += [value for value in research.get("name_variants", []) if surname_key in fold(str(value)).split()]
    values += list(discovery.get("ocr_variants", []))
    values += [term for term, _ in _configured_terms(config)]
    result, seen = [], set()
    for value in values:
        key = fold(str(value))
        if value and key and key not in seen:
            seen.add(key)
            result.append(str(value))
    return result


def build_query_plan(config: dict) -> list[QuerySpec]:
    period = config["period"]
    gallica = config["gallica"]
    chambers = config.get("discovery", {}).get("chambers") or gallica.get("chambers") or [""]
    title = gallica.get("title_query", "")
    result: list[QuerySpec] = []
    for year in range(int(period["start_year"]), int(period["end_year"]) + 1):
        for chamber in chambers:
            for term, term_type in _configured_terms(config):
                clauses = [f"gallica all {cql_quote(term)}", f"gallicapublication_date = {cql_quote(str(year))}"]
                if title:
                    clauses.append(f"dc.title all {cql_quote(title)}")
                if chamber:
                    clauses.append(f"dc.title all {cql_quote(chamber)}")
                cql = " and ".join(f"({clause})" for clause in clauses)
                digest = hashlib.sha256(f"{year}|{chamber}|{term_type}|{term}".encode()).hexdigest()[:10]
                result.append(QuerySpec(f"{year}-{digest}", year, chamber, term, term_type, cql))
    return result


def sru_url(endpoint: str, spec: QuerySpec, start_record: int, page_size: int) -> str:
    return endpoint.rstrip("?") + "?" + urlencode({
        "version": "1.2",
        "operation": "searchRetrieve",
        "query": spec.cql,
        "startRecord": start_record,
        "maximumRecords": page_size,
        "collapsing": "false",
        "suggest": "0",
    })
