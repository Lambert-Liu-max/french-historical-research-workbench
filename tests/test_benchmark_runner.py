import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "evaluation" / "run_benchmark.py"
SPEC = importlib.util.spec_from_file_location("benchmark_runner", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class BenchmarkRunnerTests(unittest.TestCase):
    def setUp(self):
        self.case = {
            "sample_id": "T01",
            "input": {"pages": [{"scan_page": 4, "numbered_lines": ["0: A", "1: Landry"]}]},
        }
        self.model = {"model": "m", "input_usd_per_million_tokens": 1, "output_usd_per_million_tokens": 2}

    def test_gold_never_enters_request(self):
        case = {**self.case, "gold": {"material_level": "B"}}
        payload, _ = MODULE.build_request(case, self.model, "prompt", {"type": "object"}, 50)
        serialized = json.dumps(payload)
        self.assertNotIn('"gold"', serialized)
        self.assertIn("T01", serialized)

    def test_content_hash_is_model_neutral(self):
        first, h1 = MODULE.build_request(self.case, {**self.model, "model": "a"}, "p", {}, 50)
        second, h2 = MODULE.build_request(self.case, {**self.model, "model": "b"}, "p", {}, 50)
        self.assertEqual(h1, h2)
        self.assertNotEqual(MODULE.digest(first), MODULE.digest(second))

    def test_case_id_and_task_inference(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cases.jsonl"
            path.write_text(json.dumps({"case_id": "G99", "input": {"pages": []}}) + "\n")
            cases = MODULE.read_cases(path, "all", None)
        self.assertEqual(cases[0]["sample_id"], "G99")
        self.assertEqual(cases[0]["task_types"], ["classification", "event_reconstruction"])
        self.assertEqual(MODULE.task_types_for({"case_id": "D99"}), ["classification"])

    def test_extracts_fenced_json(self):
        self.assertEqual(MODULE.extract_json("```json\n{\"a\":1}\n```"), {"a": 1})

    def test_coordinate_validation(self):
        prediction = {
            "sample_id": "T01", "identity_status": "confirmed", "material_type": "formal_speech",
            "material_level": "B", "event_ranges": [{"scan_start": 4, "start_line": 0, "scan_end": 4, "end_line": 1}],
            "landry_ranges": [], "speaker_before_landry": None, "landry_response_target": None,
            "responses_to_landry": [], "needs_review": False, "confidence": 0.9, "reason": "evidence",
        }
        self.assertEqual(MODULE.validate_prediction(prediction, "T01", self.case["input"]), [])
        prediction["event_ranges"][0]["end_line"] = 2
        self.assertIn("event_ranges[0] end_line outside input", MODULE.validate_prediction(prediction, "T01", self.case["input"]))

    def test_cached_token_cost(self):
        model = {
            "input_usd_per_million_tokens": 2,
            "cached_input_usd_per_million_tokens": 0.5,
            "output_usd_per_million_tokens": 4,
        }
        usage = {"prompt_tokens": 100, "cached_input_tokens": 40, "uncached_input_tokens": 60, "completion_tokens": 10}
        self.assertAlmostEqual(MODULE.estimate_cost(model, usage), (60 * 2 + 40 * 0.5 + 10 * 4) / 1_000_000)


if __name__ == "__main__":
    unittest.main()
