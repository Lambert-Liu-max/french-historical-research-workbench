from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from french_history_workbench.archive.ingest import inventory, ordered_images, windows
from french_history_workbench.archive.pipeline import run_archive_pipeline
from french_history_workbench.archive.runtime import ModelRuntime
from french_history_workbench.archive.segmentation import transcription_role, validate_boundary_result


def envelope(result: dict, model: str = "vision-test") -> dict:
    return {
        "model": model,
        "choices": [{"message": {"content": json.dumps(result, ensure_ascii=False)}}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 50},
    }


class ArchivePipelineTest(unittest.TestCase):
    def config(self, path: Path) -> Path:
        payload = {
            "schema_version": "1.0",
            "project": {"name": "Archive test"},
            "research": {"person": "Adolphe Landry", "question": "How did ideas become action?"},
            "vision_model": {
                "base_url": "https://example.invalid/v1", "api_key_env": "TEST_KEY",
                "name": "vision-test", "rates_usd_per_million_tokens": {"input": 1, "output": 2},
            },
            "archive": {
                "window_size": 3, "window_overlap": 1, "transcription_batch_size": 5,
                "classes_to_transcribe": ["A", "B", "C"], "boundary_review_enabled": True,
                "model_roles": {
                    "boundary_primary": "vision_model", "boundary_review": "vision_model",
                    "transcription_clear": "vision_model", "transcription_difficult": "vision_model",
                },
                "budget": {"hard_stop_usd": 5, "reserve_per_boundary_call_usd": 0.1,
                           "reserve_per_transcription_call_usd": 0.1},
            },
        }
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_natural_inventory_and_source_change_protection(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); source = root / "source"; source.mkdir()
            for name in ("scan10.jpg", "scan2.jpg", "scan1.jpg"):
                (source / name).write_bytes(name.encode())
            self.assertEqual([x.name for x in ordered_images(source)], ["scan1.jpg", "scan2.jpg", "scan10.jpg"])
            manifest_path = root / "state" / "manifest.json"
            manifest = inventory(source, manifest_path)
            self.assertEqual(manifest["scan_count"], 3)
            self.assertEqual(len(windows(manifest["files"], 2, 1)), 2)
            (source / "scan2.jpg").write_bytes(b"changed")
            with self.assertRaisesRegex(RuntimeError, "source inventory changed"):
                inventory(source, manifest_path)

    def test_identical_source_can_be_relocated_without_stale_absolute_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); original = root / "original"; original.mkdir()
            (original / "scan1.jpg").write_bytes(b"same-private-scan")
            manifest_path = root / "state/manifest.json"
            inventory(original, manifest_path)
            relocated = root / "relocated"
            original.rename(relocated)
            manifest = inventory(relocated, manifest_path)
            self.assertEqual(manifest["source_folder"], str(relocated.resolve()))
            self.assertEqual(manifest["files"][0]["source_path"], str((relocated / "scan1.jpg").resolve()))

    def test_explicit_script_route_and_schema_validation(self):
        self.assertEqual(transcription_role({"script_type": "handwritten", "needs_review": False,
                                             "boundary_confidence": "high"}), "transcription_difficult")
        self.assertEqual(transcription_role({"script_type": "typed", "needs_review": False,
                                             "boundary_confidence": "high"}), "transcription_clear")
        bad = {"pages": [{"scan_id": "scan1.jpg", "page_type": "document_page",
                           "script_type": "cursive", "confidence": "high"}], "documents": []}
        self.assertTrue(validate_boundary_result(bad, {"scan1.jpg"}))

    def test_end_to_end_resume_without_repeating_model_calls(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); source = root / "source"; source.mkdir()
            for number in range(1, 4):
                (source / f"scan{number}.jpg").write_bytes(f"private-scan-{number}".encode())
            config = self.config(root / "project.json")
            calls = []

            def caller(_profile, payload):
                calls.append(payload)
                system = payload["messages"][0]["content"]
                if "cautious cataloguer" in system:
                    scan_ids = [f"scan{number}.jpg" for number in range(1, 4)]
                    return envelope({
                        "window": {"first_scan": scan_ids[0], "last_scan": scan_ids[-1]},
                        "pages": [{"scan_id": scan, "page_type": "document_page",
                                   "script_type": "handwritten", "physical_page_count": 1,
                                   "document_local_ids": ["letter-1"], "confidence": "high", "notes": None}
                                  for scan in scan_ids],
                        "documents": [{"local_id": "letter-1", "included_scans": scan_ids,
                                       "date": "1931-01-01", "author": "A", "recipient": "B",
                                       "document_type": "letter", "script_type": "handwritten",
                                       "boundary_evidence": "continuous letter", "boundary_confidence": "high",
                                       "relevance_level": "A", "relevance_reason": "direct evidence",
                                       "keywords": ["population"], "needs_review": False}],
                        "review_items": [],
                    })
                user = payload["messages"][1]["content"][0]["text"]
                task = json.loads(user)
                return envelope({
                    "document_id": task["document_id"],
                    "pages": [{"scan_id": scan, "french_transcription": f"Texte de {scan}",
                               "notes": [], "needs_review": False} for scan in task["this_batch"]],
                    "document_notes": [], "needs_review": False,
                })

            def fake_deriver(item, output, _max_pixels):
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_bytes(b"fake-jpeg")
                return output

            first = run_archive_pipeline(config, source, root / "project", "test-run", True, caller, fake_deriver)
            self.assertEqual(first["status"], "complete")
            self.assertEqual(first["transcribed_documents"], 1)
            self.assertEqual(len(calls), 2)
            rows = [json.loads(line) for line in Path(first["output"]).read_text().splitlines()]
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["archive_metadata"]["transcription_role"], "transcription_difficult")
            self.assertEqual(len(rows[0]["pages"]), 3)

            second = run_archive_pipeline(config, source, root / "project", "test-run", True, caller, fake_deriver)
            self.assertEqual(second["status"], "complete")
            self.assertEqual(len(calls), 2, "completed model jobs must be resumed from checkpoints")
            ledger = json.loads((root / "project/archive_runs/test-run/cost_ledger.json").read_text())
            self.assertEqual(len(ledger["calls"]), 2)

    def test_paid_invalid_response_is_recorded_before_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); config_path = self.config(root / "project.json")
            config = json.loads(config_path.read_text())

            def invalid(_profile, _payload):
                return {"model": "vision-test", "choices": [{"message": {"content": "not json"}}],
                        "usage": {"prompt_tokens": 20, "completion_tokens": 10}}

            runtime = ModelRuntime(config, root / "run", True, invalid)
            with self.assertRaises(ValueError):
                runtime.call("boundary_primary", "bad", {"messages": [], "max_tokens": 10}, 0.1)
            ledger = json.loads((root / "run/cost_ledger.json").read_text())
            self.assertEqual(len(ledger["calls"]), 1)
            job = json.loads((root / "run/jobs/boundary_primary/bad.json").read_text())
            self.assertEqual(job["status"], "failed")

    def test_review_failure_keeps_validated_primary_boundary(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); source = root / "source"; source.mkdir()
            (source / "scan1.jpg").write_bytes(b"private-scan")
            config = self.config(root / "project.json")

            def caller(_profile, payload):
                system = payload["messages"][0]["content"]
                if "cautious cataloguer" in system:
                    task = json.loads(payload["messages"][1]["content"][0]["text"])
                    if "primary_result_to_review" in task:
                        return envelope({"invalid": True})
                    return envelope({
                        "window": {"first_scan": "scan1.jpg", "last_scan": "scan1.jpg"},
                        "pages": [{"scan_id": "scan1.jpg", "page_type": "document_page",
                                   "script_type": "typed", "physical_page_count": 1,
                                   "document_local_ids": ["doc"], "confidence": "medium", "notes": None}],
                        "documents": [{"local_id": "doc", "included_scans": ["scan1.jpg"], "date": None,
                                       "author": None, "recipient": None, "document_type": "note",
                                       "script_type": "typed", "boundary_evidence": "single sheet",
                                       "boundary_confidence": "medium", "relevance_level": "A",
                                       "relevance_reason": "direct", "keywords": [], "needs_review": True}],
                        "review_items": [],
                    })
                task = json.loads(payload["messages"][1]["content"][0]["text"])
                return envelope({"document_id": task["document_id"],
                                 "pages": [{"scan_id": "scan1.jpg", "french_transcription": "Texte",
                                            "notes": [], "needs_review": False}],
                                 "document_notes": [], "needs_review": False})

            def fake_deriver(_item, output, _max_pixels):
                output.parent.mkdir(parents=True, exist_ok=True); output.write_bytes(b"image"); return output

            result = run_archive_pipeline(config, source, root / "project", "review-fallback", True,
                                          caller, fake_deriver)
            self.assertEqual(result["status"], "complete_with_exceptions")
            self.assertEqual(result["boundary_failures"], 1)
            self.assertEqual(result["transcribed_documents"], 1)
            review = [json.loads(x) for x in (root / "project/archive_runs/review-fallback/review_queue.jsonl").read_text().splitlines()]
            self.assertTrue(any(x.get("fallback") == "validated_primary_result" for x in review))


if __name__ == "__main__":
    unittest.main()
