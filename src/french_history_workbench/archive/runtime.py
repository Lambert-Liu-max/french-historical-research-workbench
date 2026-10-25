from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Callable

from ..core import curl_json, model_cost, parse_model_json, stable_id
from .ingest import atomic_json, now, read_json


class ModelRuntime:
    """Checkpointed model calls with raw responses, budget and cost ledger."""

    def __init__(self, config: dict, root: Path, execute: bool, caller: Callable | None = None):
        self.config = config
        self.root = root
        self.execute = execute
        self.caller = caller
        self.ledger_path = root / "cost_ledger.json"

    def profile(self, role: str) -> dict:
        archive = self.config.get("archive", {})
        profile_name = archive.get("model_roles", {}).get(role, "vision_model")
        profile = self.config.get(profile_name)
        if not isinstance(profile, dict):
            raise RuntimeError(f"model profile not found for {role}: {profile_name}")
        return profile

    def ledger(self) -> dict:
        if self.ledger_path.exists():
            return read_json(self.ledger_path)
        return {"schema_version": "1.0", "spent_usd": 0.0, "calls": []}

    def budget_guard(self, reserve: float) -> None:
        hard = float(self.config.get("archive", {}).get("budget", {}).get("hard_stop_usd", 5))
        if float(self.ledger().get("spent_usd", 0)) + reserve > hard:
            raise RuntimeError(f"archive model budget would exceed hard stop: ${hard:.2f}")

    def _request(self, profile: dict, payload: dict) -> dict:
        if self.caller:
            return self.caller(profile, payload)
        key = os.environ.get(profile["api_key_env"], "").strip()
        if not key:
            raise RuntimeError(f"missing environment variable {profile['api_key_env']}")
        attempts = int(self.config.get("archive", {}).get("network_attempts", 5))
        for attempt in range(1, attempts + 1):
            try:
                return curl_json(
                    profile["base_url"].rstrip("/") + "/chat/completions", payload, key,
                    int(profile.get("timeout_seconds", 900)),
                )
            except Exception:
                if attempt == attempts:
                    raise
                time.sleep(min(60, 2 ** attempt))
        raise RuntimeError("model request failed")

    def call(self, role: str, task_id: str, request: dict, reserve: float = 0.25) -> tuple[dict, dict]:
        profile = self.profile(role)
        fingerprint = stable_id({"role": role, "profile": profile, "request": request}, 24)
        checkpoint = self.root / "jobs" / role / f"{task_id}.json"
        if checkpoint.exists():
            saved = read_json(checkpoint)
            if saved.get("fingerprint") == fingerprint and saved.get("status") == "success":
                return saved["result"], saved["metadata"]
        if not self.execute:
            raise RuntimeError("model execution disabled")
        self.budget_guard(reserve)
        payload = {
            "model": profile["name"], "temperature": 0,
            "max_tokens": int(request.get("max_tokens", profile.get("max_tokens", 6000))),
            "response_format": {"type": "json_object"},
            "messages": request["messages"],
        }
        envelope = self._request(profile, payload)
        raw_path = self.root / "raw_responses" / role / f"{task_id}.json"
        atomic_json(raw_path, envelope)
        usage = envelope.get("usage") or {}
        metadata = {
            "role": role, "model": envelope.get("model", profile["name"]), "usage": usage,
            "estimated_cost_usd": model_cost(profile, usage), "raw_response": str(raw_path),
        }
        ledger = self.ledger()
        ledger["spent_usd"] = round(float(ledger.get("spent_usd", 0)) + metadata["estimated_cost_usd"], 10)
        ledger["calls"].append({"timestamp": now(), "task_id": task_id, **metadata})
        atomic_json(self.ledger_path, ledger)
        try:
            result = parse_model_json(envelope["choices"][0]["message"].get("content") or "")
        except Exception as exc:
            atomic_json(checkpoint, {
                "status": "failed", "fingerprint": fingerprint,
                "error": f"{type(exc).__name__}: {exc}", "metadata": metadata,
            })
            raise
        atomic_json(checkpoint, {"status": "success", "fingerprint": fingerprint, "result": result, "metadata": metadata})
        return result, metadata
