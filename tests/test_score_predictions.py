import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from score_predictions import (  # noqa: E402
    classification_metrics,
    event_pages,
    landry_pages,
    normalize_speaker,
    score,
    set_f1,
)


class ScorePredictionsTests(unittest.TestCase):
    def test_classification_metrics(self):
        metrics = classification_metrics(["A", "A", "B"], ["A", "B", "B"])
        self.assertAlmostEqual(metrics["accuracy"], 2 / 3)
        self.assertAlmostEqual(metrics["per_class"]["A"]["recall"], 0.5)
        self.assertAlmostEqual(metrics["per_class"]["B"]["precision"], 0.5)

    def test_page_helpers(self):
        self.assertEqual(event_pages({"event_start": {"scan_page": 2}, "event_end": {"scan_page": 4}}), {2, 3, 4})
        self.assertEqual(
            landry_pages({"landry_ranges": [{"scan_start": 3, "scan_end": 4}]}),
            {3, 4},
        )

    def test_speaker_normalization_and_f1(self):
        self.assertEqual(normalize_speaker("M. Émile Béron"), "emile beron")
        self.assertEqual(set_f1({"emile beron"}, {"emile beron"}), 1.0)
        self.assertEqual(set_f1(set(), set()), 1.0)

    def test_default_scope_is_strict_gold(self):
        samples = [
            {
                "sample_id": "strict",
                "gold_status": "expert_verified",
                "gold": {"identity_status": "confirmed", "material_type": "formal_speech", "material_level": "A"},
            },
            {
                "sample_id": "seed",
                "gold_status": "expert_reviewed_seed",
                "gold": {"identity_status": "confirmed", "material_type": "formal_speech", "material_level": "B"},
            },
        ]
        predictions = [
            {"sample_id": "strict", "identity_status": "confirmed", "material_type": "formal_speech", "material_level": "A"},
            {"sample_id": "seed", "identity_status": "false", "material_type": "uncertain", "material_level": "D"},
        ]
        report = score(
            samples,
            predictions,
            {"nonblank_lines": 2, "parsed_objects": 2, "parse_errors": 0},
            {"expert_verified"},
        )
        self.assertEqual(report["evaluation_scope"]["eligible_samples"], 1)
        self.assertTrue(report["evaluation_scope"]["strict_gold_only"])
        self.assertEqual(report["classification"]["material_level"]["accuracy"], 1.0)

    def test_boundary_and_production_metrics(self):
        samples = [{
            "sample_id": "x",
            "gold_status": "expert_verified",
            "gold": {
                "identity_status": "confirmed",
                "material_type": "formal_speech",
                "material_level": "A",
                "event_ranges": [{"scan_start": 2, "scan_end": 4}],
                "landry_ranges": [{"scan_start": 3, "scan_end": 3}],
                "responses_to_landry": ["M. Émile Béron"],
            },
        }]
        predictions = [{
            "sample_id": "x",
            "model": "demo",
            "identity_status": "confirmed",
            "material_type": "formal_speech",
            "material_level": "A",
            "event_start": {"scan_page": 3},
            "event_end": {"scan_page": 4},
            "landry_ranges": [{"scan_start": 3, "scan_end": 3}],
            "responses": [{"speaker": "Emile Beron"}],
            "cost_usd": 0.01,
            "latency_ms": 100,
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }]
        report = score(
            samples,
            predictions,
            {"nonblank_lines": 1, "parsed_objects": 1, "parse_errors": 0},
            {"expert_verified"},
        )
        self.assertAlmostEqual(report["event_reconstruction"]["page_iou"]["mean"], 2 / 3)
        self.assertEqual(report["event_reconstruction"]["landry_page_coverage"]["mean"], 1.0)
        self.assertEqual(report["event_reconstruction"]["response_set_f1"]["mean"], 1.0)
        self.assertEqual(report["prediction_quality"]["schema_pass_rate"], 1.0)
        self.assertEqual(report["production"]["overall"]["cost"]["total_usd"], 0.01)

    def test_task_types_skip_boundary_metrics(self):
        samples = [{
            "sample_id": "classification-only",
            "gold_status": "expert_verified",
            "task_types": ["classification"],
            "gold": {
                "identity_status": "probable",
                "material_type": "vote_or_attendance",
                "material_level": "D",
                "event_ranges": [{"scan_start": 1, "scan_end": 3}],
            },
        }]
        predictions = [{
            "sample_id": "classification-only",
            "identity_status": "probable",
            "material_type": "vote_or_attendance",
            "material_level": "D",
        }]
        report = score(
            samples,
            predictions,
            {"nonblank_lines": 1, "parsed_objects": 1, "parse_errors": 0},
            {"expert_verified"},
        )
        self.assertEqual(report["prediction_quality"]["schema_pass_rate"], 1.0)
        self.assertEqual(report["event_reconstruction"]["page_iou"]["n"], 0)


if __name__ == "__main__":
    unittest.main()
