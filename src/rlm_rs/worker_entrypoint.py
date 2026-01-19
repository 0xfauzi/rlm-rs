from __future__ import annotations

import os
import time

from rlm_rs.ingestion.worker import build_worker as build_ingestion_worker
from rlm_rs.orchestrator.providers import FakeLLMProvider
from rlm_rs.orchestrator.worker import build_worker as build_orchestrator_worker
from rlm_rs.settings import Settings


def _read_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if not value:
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc


def _read_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if not value:
        return default
    try:
        return float(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number") from exc


def _run_loop(run_once, *, limit: int | None, sleep_seconds: float) -> None:
    while True:
        processed = run_once(limit=limit)
        if processed == 0:
            time.sleep(sleep_seconds)


def _run_ingestion() -> None:
    batch_limit = _read_int("INGESTION_BATCH_LIMIT", 10)
    sleep_seconds = _read_float("WORKER_POLL_INTERVAL_SECONDS", 0.5)
    worker = build_ingestion_worker()
    try:
        _run_loop(worker.run_once, limit=batch_limit, sleep_seconds=sleep_seconds)
    finally:
        worker.close()


def _build_fake_provider(settings: Settings) -> FakeLLMProvider | None:
    provider_name = (settings.llm_provider or "fake").strip().lower()
    if provider_name != "fake":
        return None
    default_output = os.getenv("ORCHESTRATOR_FAKE_ROOT_OUTPUT")
    if default_output:
        return FakeLLMProvider(default_root_output=default_output)
    return FakeLLMProvider()


def _run_orchestrator() -> None:
    batch_limit = _read_int("ORCHESTRATOR_BATCH_LIMIT", 1)
    sleep_seconds = _read_float("WORKER_POLL_INTERVAL_SECONDS", 0.5)
    settings = Settings()
    provider = _build_fake_provider(settings)
    worker = build_orchestrator_worker(settings=settings, provider=provider)
    _run_loop(worker.run_once, limit=batch_limit, sleep_seconds=sleep_seconds)


def main() -> None:
    mode = os.getenv("WORKER_MODE")
    if not mode:
        raise ValueError("WORKER_MODE must be set to 'ingestion' or 'orchestrator'")
    normalized = mode.strip().lower()
    if normalized == "ingestion":
        _run_ingestion()
        return
    if normalized == "orchestrator":
        _run_orchestrator()
        return
    raise ValueError("WORKER_MODE must be set to 'ingestion' or 'orchestrator'")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
