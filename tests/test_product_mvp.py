import json
import tempfile
import unittest
from pathlib import Path

from french_history_workbench.core import ingest_local, run_pipeline


ROOT = Path(__file__).resolve().parents[1]


class ProductMVPTests(unittest.TestCase):
    def test_offline_demo_reaches_markdown(self):
        with tempfile.TemporaryDirectory() as directory:
            result = run_pipeline(
                ROOT / "examples/product_demo/project.json",
                ROOT / "examples/product_demo/records.jsonl",
                Path(directory), "demo", False,
            )
            self.assertEqual(result["completed"], 1)
            corpus = Path(result["output"])
            self.assertTrue(corpus.exists())
            self.assertIn("Adolphe Landry", corpus.read_text(encoding="utf-8"))

    def test_local_ingest_routes_text_and_scan(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "note.txt").write_text("M. Landry", encoding="utf-8")
            (root / "page.jpg").write_bytes(b"not-a-real-image")
            output = root / "records.jsonl"
            summary = ingest_local(root, output)
            rows = [json.loads(x) for x in output.read_text().splitlines()]
            self.assertEqual(summary["files"], 2)
            self.assertEqual({x["source_type"] for x in rows}, {"local_text", "local_scan"})


if __name__ == "__main__":
    unittest.main()
