#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import statistics
from typing import Any


def _load_jsonl(path: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            records.append(json.loads(line))
    return records


def _mean(values: list[float]) -> float | None:
    if not values:
        return None
    return statistics.mean(values)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Summarize quality + efficiency metrics from trace JSONL.",
    )
    parser.add_argument("--input", required=True)
    parser.add_argument("--status", default="COMPLETED")
    parser.add_argument("--output", default=None)
    args = parser.parse_args(argv)

    traces = _load_jsonl(args.input)
    filtered: list[dict[str, Any]] = []
    for trace in traces:
        execution = trace.get("execution") or {}
        if args.status and execution.get("status") != args.status:
            continue
        filtered.append(trace)

    metrics = {
        "turns": [],
        "llm_subcalls": [],
        "span_chars": [],
        "scan_span_chars": [],
        "parse_errors": [],
        "step_errors": [],
        "answer_relevancy": [],
        "faithfulness": [],
    }
    for trace in filtered:
        m = trace.get("metrics") or {}
        metrics["turns"].append(float(m.get("turns") or 0))
        metrics["llm_subcalls"].append(float(m.get("llm_subcalls") or 0))
        metrics["span_chars"].append(float(m.get("span_chars") or 0))
        metrics["scan_span_chars"].append(float(m.get("scan_span_chars") or 0))
        metrics["parse_errors"].append(float(m.get("parse_errors") or 0))
        metrics["step_errors"].append(float(m.get("step_errors") or 0))
        evaluation = trace.get("evaluation") or {}
        answerer = (evaluation.get("judge_metrics") or {}).get("answerer") or {}
        if answerer.get("answer_relevancy") is not None:
            metrics["answer_relevancy"].append(float(answerer.get("answer_relevancy")))
        if answerer.get("faithfulness") is not None:
            metrics["faithfulness"].append(float(answerer.get("faithfulness")))

    summary = {
        "count": len(filtered),
        "avg_turns": _mean(metrics["turns"]),
        "avg_llm_subcalls": _mean(metrics["llm_subcalls"]),
        "avg_span_chars": _mean(metrics["span_chars"]),
        "avg_scan_span_chars": _mean(metrics["scan_span_chars"]),
        "avg_parse_errors": _mean(metrics["parse_errors"]),
        "avg_step_errors": _mean(metrics["step_errors"]),
        "avg_answer_relevancy": _mean(metrics["answer_relevancy"]),
        "avg_faithfulness": _mean(metrics["faithfulness"]),
    }

    output = json.dumps(summary, indent=2)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(output + "\n")
    else:
        print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
