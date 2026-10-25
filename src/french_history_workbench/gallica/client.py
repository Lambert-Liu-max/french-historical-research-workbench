from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .parsers import validate_xml


class FetchError(RuntimeError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class CachedResponse:
    content: bytes
    path: Path
    from_cache: bool


def curl_transport(url: str, timeout: int = 90, user_agent: str = "FrenchHistoricalResearchWorkbench/0.1") -> tuple[int, bytes, str]:
    proc = subprocess.run([
        "/usr/bin/curl", "--silent", "--show-error", "--max-time", str(timeout),
        "--connect-timeout", "30", "--user-agent", user_agent,
        "--header", "Accept: application/xml,text/xml;q=0.9",
        "--write-out", "\n__HTTP_STATUS__=%{http_code}", url,
    ], capture_output=True)
    if proc.returncode:
        return 0, b"", proc.stderr.decode("utf-8", errors="replace").strip()
    body, marker, raw_status = proc.stdout.rpartition(b"\n__HTTP_STATUS__=")
    status = int(raw_status or b"0") if marker else 0
    return status, body, proc.stderr.decode("utf-8", errors="replace").strip()


class CacheStore:
    """Validated, atomic and resumable XML cache with injectable transport."""

    def __init__(
        self,
        root: Path,
        transport: Callable[[str], tuple[int, bytes, str]] | None = None,
        delays: dict[str, float] | None = None,
        retries: int = 3,
        backoff_seconds: float = 2,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.root = root
        self.transport = transport or curl_transport
        self.delays = delays or {}
        self.retries = max(1, retries)
        self.backoff_seconds = backoff_seconds
        self.sleep = sleep
        self.last_request: dict[str, float] = {}
        self.manifest = root / "manifest.jsonl"

    def path_for(self, namespace: str, url: str) -> Path:
        return self.root / namespace / (hashlib.sha256(url.encode()).hexdigest() + ".xml")

    def _wait(self, namespace: str) -> None:
        delay = float(self.delays.get(namespace, 0))
        wait = delay - (time.time() - self.last_request.get(namespace, 0))
        if wait > 0:
            self.sleep(wait)

    def _append(self, row: dict) -> None:
        self.manifest.parent.mkdir(parents=True, exist_ok=True)
        with self.manifest.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")

    def get(self, namespace: str, url: str, validator: Callable[[bytes], None] = validate_xml) -> CachedResponse:
        target = self.path_for(namespace, url)
        if target.exists() and target.stat().st_size:
            try:
                content = target.read_bytes()
                validator(content)
                return CachedResponse(content, target, True)
            except Exception:
                pass
        last_error = "request failed"
        last_status: int | None = None
        for attempt in range(1, self.retries + 1):
            self._wait(namespace)
            status, content, error = self.transport(url)
            self.last_request[namespace] = time.time()
            last_status = status or None
            if status == 200:
                try:
                    validator(content)
                except Exception as exc:
                    last_error = f"invalid XML: {exc}"
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    temporary = target.with_suffix(target.suffix + f".tmp-{os.getpid()}")
                    temporary.write_bytes(content)
                    os.replace(temporary, target)
                    self._append({
                        "namespace": namespace, "url": url, "cache_path": str(target.relative_to(self.root)),
                        "retrieved_at": datetime.now(timezone.utc).isoformat(), "status": status,
                        "bytes": len(content), "sha256": hashlib.sha256(content).hexdigest(),
                    })
                    return CachedResponse(content, target, False)
            else:
                last_error = error or f"HTTP {status}"
            if status == 429:
                break
            if attempt < self.retries:
                self.sleep(self.backoff_seconds * (2 ** (attempt - 1)))
        self._append({
            "namespace": namespace, "url": url, "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "status": last_status, "error": last_error,
        })
        raise FetchError(last_error, last_status)
