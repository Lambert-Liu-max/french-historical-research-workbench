import json
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from french_history_workbench.gallica.client import CacheStore
from french_history_workbench.gallica.discovery import discover
from french_history_workbench.gallica.parsers import (
    cluster_hits,
    context_pages,
    match_name_variants,
    parse_content_page,
    parse_pagination,
    parse_sru_page,
)
from french_history_workbench.gallica.query import build_query_plan, person_match_terms


def sru(records, total):
    bodies = "".join(
        f"""<srw:record><srw:recordData><oai_dc:dc>
        <dc:title>{title}</dc:title><dc:date>1931-01-01</dc:date>
        <dc:identifier>https://gallica.bnf.fr/ark:/12148/{identifier}</dc:identifier>
        </oai_dc:dc></srw:recordData></srw:record>"""
        for identifier, title in records
    )
    return f"""<?xml version="1.0"?><srw:searchRetrieveResponse
      xmlns:srw="http://www.loc.gov/zing/srw/"
      xmlns:dc="http://purl.org/dc/elements/1.1/"
      xmlns:oai_dc="http://www.openarchives.org/OAI/2.0/oai_dc/">
      <srw:numberOfRecords>{total}</srw:numberOfRecords><srw:records>{bodies}</srw:records>
      </srw:searchRetrieveResponse>""".encode()


def content(pages, total):
    items = "".join(
        f"<item><p_id>PAG_{page}</p_id><content>M. &amp;lt;span&amp;gt;Landry&amp;lt;/span&amp;gt;</content></item>"
        for page in pages
    )
    return f'<results countResults="{total}"><items>{items}</items></results>'.encode()


def alto(text):
    words = "".join(f'<String CONTENT="{word}"/>' for word in text.split())
    return f'<alto><Layout><Page><TextBlock><TextLine>{words}</TextLine></TextBlock></Page></Layout></alto>'.encode()


class GallicaDiscoveryTests(unittest.TestCase):
    def config(self):
        return {
            "research": {
                "person": "Adolphe Landry", "surname": "Landry",
                "name_variants": ["M. Landry", "A. Landry"], "keywords": ["population"],
                "question": "test",
            },
            "period": {"start_year": 1931, "end_year": 1931},
            "discovery": {
                "chambers": ["Chambre des députés"],
                "search_terms": [{"text": "Landry", "type": "surname"}],
                "ocr_variants": ["Landri", "Landy", "L. Andry"],
                "sru_page_size": 1, "max_records_per_query": 20,
                "event_merge_gap_pages": 2, "network_retries": 1,
            },
            "gallica": {
                "sru_endpoint": "https://example.test/SRU",
                "title_query": "Journal officiel", "context_pages": 1,
                "sru_delay_seconds": 0, "content_search_delay_seconds": 0,
                "pagination_delay_seconds": 0, "alto_delay_seconds": 0,
            },
        }

    def test_query_plan_expands_variants_and_chambers(self):
        config = self.config()
        config["discovery"]["chambers"] = ["Chambre des députés", "Sénat"]
        config["discovery"]["search_terms"] = [
            "Landry", "Adolphe Landry", "A. Landry", "M. Landry", "Landri", "Landy", "Landry"
        ]
        plan = build_query_plan(config)
        self.assertEqual(len(plan), 12)
        self.assertEqual(len({row.query_id for row in plan}), 12)
        self.assertTrue(all("population" not in row.cql for row in plan))
        self.assertTrue(any('dc.title all "Sénat"' in row.cql for row in plan))
        config["research"]["name_variants"].append("M. le ministre du travail")
        self.assertNotIn("M. le ministre du travail", person_match_terms(config))

    def test_parsers_and_content_paging_metadata(self):
        total, rows = parse_sru_page(sru([("bpt6ka", "Issue A")], 2), "q1")
        self.assertEqual((total, rows[0]["ark"]), (2, "ark:/12148/bpt6ka"))
        total, hits = parse_content_page(content([1, 2], 12))
        self.assertEqual((total, [x["scan_page"] for x in hits]), (12, [1, 2]))
        pagination = parse_pagination(b"<livre><pages><page><numero>175</numero><ordre>20</ordre></page></pages></livre>")
        self.assertEqual(pagination[20], "175")

    def test_validated_cache_reuses_and_repairs(self):
        calls = []

        def transport(url):
            calls.append(url)
            return 200, b"<root/>", ""

        with tempfile.TemporaryDirectory() as directory:
            store = CacheStore(Path(directory), transport=transport, retries=1)
            path = store.path_for("sru", "https://example.test/x")
            path.parent.mkdir(parents=True)
            path.write_bytes(b"<html>blocked</html>")
            first = store.get("sru", "https://example.test/x")
            second = store.get("sru", "https://example.test/x")
            self.assertFalse(first.from_cache)
            self.assertTrue(second.from_cache)
            self.assertEqual(len(calls), 1)

    def test_ocr_tolerance_and_event_merge(self):
        text = "M. Adolphe\nLANDRY répond. Plus loin Landri, Landy et L. Andry."
        variants = ["Adolphe Landry", "Landry", "Landri", "Landy", "L. Andry"]
        matched = {row["variant"] for row in match_name_variants(text, variants)}
        self.assertEqual(matched, set(variants))
        self.assertFalse(match_name_variants("M. Laundry et M. Landes", ["Landry"]))
        hits = [{"scan_page": value} for value in (20, 21, 23, 40)]
        groups = cluster_hits(hits, gap=2)
        self.assertEqual([[x["scan_page"] for x in group] for group in groups], [[20, 21, 23], [40]])
        self.assertEqual(context_pages(groups[0], 1), [19, 20, 21, 22, 23, 24])

    def test_end_to_end_discovery_pages_contentsearch_and_audits(self):
        calls = []

        def transport(url):
            calls.append(url)
            parsed = urlparse(url)
            params = parse_qs(parsed.query)
            if parsed.path.endswith("/SRU"):
                start = int(params["startRecord"][0])
                row = [("bpt6kdoc1", "Issue one")] if start == 1 else [("bpt6kdoc2", "Issue two")]
                return 200, sru(row, 2), ""
            if parsed.path.endswith("/ContentSearch"):
                if params["ark"][0] == "bpt6kdoc2":
                    return 200, content([], 0), ""
                start = int(params.get("startResult", ["0"])[0])
                return (200, content(range(20, 30), 12), "") if start == 0 else (200, content([29, 30, 31], 12), "")
            if parsed.path.endswith("/Pagination"):
                pages = "".join(f"<page><numero>{155 + n}</numero><ordre>{n}</ordre></page>" for n in range(19, 33))
                return 200, f"<livre><pages>{pages}</pages></livre>".encode(), ""
            if parsed.path.endswith("/RequestDigitalElement"):
                scan = int(params["Deb"][0])
                return 200, alto("M. Landry parle" if 20 <= scan <= 31 else "texte voisin"), ""
            raise AssertionError(url)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = discover(self.config(), root, transport=transport)
            self.assertEqual(result["documents_found"], 2)
            self.assertEqual(result["candidate_events"], 1)
            record = json.loads((root / "inputs/gallica_records.jsonl").read_text().splitlines()[0])
            self.assertEqual(record["hit_pages"], list(range(20, 32)))
            self.assertEqual([page["scan_page"] for page in record["pages"]], list(range(19, 33)))
            self.assertTrue(any("startResult=10" in url for url in calls))
            self.assertEqual(len([url for url in calls if "RequestDigitalElement" in url]), 14)
            reviews = [json.loads(line) for line in (root / "audit/miss_review.jsonl").read_text().splitlines()]
            self.assertTrue(any(row["reason"] == "sru_document_without_content_hit" for row in reviews))
            # A rerun is entirely cache-backed.
            before = len(calls)
            rerun = discover(self.config(), root, transport=transport)
            self.assertEqual(rerun["candidate_events"], 1)
            self.assertEqual(len(calls), before)


if __name__ == "__main__":
    unittest.main()
