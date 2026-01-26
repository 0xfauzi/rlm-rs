#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
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


def _extract_quality(trace: dict[str, Any]) -> float | None:
    evaluation = trace.get("evaluation") or {}
    judge_metrics = evaluation.get("judge_metrics") or {}
    answerer = judge_metrics.get("answerer") or {}
    relevancy = answerer.get("answer_relevancy")
    faithfulness = answerer.get("faithfulness")
    if relevancy is None or faithfulness is None:
        return None
    return (float(relevancy) + float(faithfulness)) / 2.0


def _compute_reward(
    trace: dict[str, Any],
    *,
    turn_penalty: float,
    subcall_penalty: float,
    span_penalty: float,
    scan_penalty: float,
    search_penalty: float,
) -> float | None:
    quality = _extract_quality(trace)
    if quality is None:
        return None
    metrics = trace.get("metrics") or {}
    turns = float(metrics.get("turns") or 0)
    subcalls = float(metrics.get("llm_subcalls") or 0)
    span_chars = float(metrics.get("span_chars") or 0)
    scan_chars = float(metrics.get("scan_span_chars") or 0)
    search_requests = float(metrics.get("search_requests") or 0)
    penalty = (
        turn_penalty * turns
        + subcall_penalty * subcalls
        + span_penalty * (span_chars / 1000.0)
        + scan_penalty * (scan_chars / 1000.0)
        + search_penalty * search_requests
    )
    return quality - penalty


def _trajectory_text(turns: list[dict[str, Any]]) -> str:
    blocks: list[str] = []
    for turn in turns:
        code = turn.get("repl_code")
        if not isinstance(code, str) or not code.strip():
            continue
        blocks.append(f"```repl\n{code}\n```")
    return "\n\n".join(blocks)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build SFT and preference datasets from trace JSONL.",
    )
    parser.add_argument("--input", required=True, help="Trace JSONL input.")
    parser.add_argument("--sft-output", default="finetune_sft.jsonl")
    parser.add_argument("--pref-output", default=None)
    parser.add_argument("--min-faithfulness", type=float, default=None)
    parser.add_argument("--min-relevancy", type=float, default=None)
    parser.add_argument("--turn-penalty", type=float, default=0.02)
    parser.add_argument("--subcall-penalty", type=float, default=0.03)
    parser.add_argument("--span-penalty", type=float, default=0.005)
    parser.add_argument("--scan-penalty", type=float, default=0.01)
    parser.add_argument("--search-penalty", type=float, default=0.02)
    args = parser.parse_args(argv)

    traces = _load_jsonl(args.input)
    if not traces:
        print("No traces found.", file=sys.stderr)
        return 1

    sft_records: list[dict[str, Any]] = []
    pref_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for trace in traces:
        execution = trace.get("execution") or {}
        status = execution.get("status")
        if status != "COMPLETED":
            continue
        evaluation = trace.get("evaluation") or {}
        judge_metrics = evaluation.get("judge_metrics") or {}
        answerer = judge_metrics.get("answerer") or {}
        faithfulness = answerer.get("faithfulness")
        relevancy = answerer.get("answer_relevancy")
        if args.min_faithfulness is not None and faithfulness is not None:
            if float(faithfulness) < args.min_faithfulness:
                continue
        if args.min_relevancy is not None and relevancy is not None:
            if float(relevancy) < args.min_relevancy:
                continue
        reward = _compute_reward(
            trace,
            turn_penalty=args.turn_penalty,
            subcall_penalty=args.subcall_penalty,
            span_penalty=args.span_penalty,
            scan_penalty=args.scan_penalty,
            search_penalty=args.search_penalty,
        )
        turns = trace.get("turns") or []
        for turn in turns:
            root_prompt = turn.get("root_prompt")
            repl_code = turn.get("repl_code")
            if not isinstance(root_prompt, str) or not isinstance(repl_code, str):
                continue
            sft_records.append(
                {
                    "input": root_prompt,
                    "output": repl_code,
                    "metadata": {
                        "execution_id": execution.get("execution_id"),
                        "session_id": execution.get("session_id"),
                        "turn_index": turn.get("turn_index"),
                        "root_prompt_version": turn.get("root_prompt_version"),
                        "reward": reward,
                        "metrics": trace.get("metrics"),
                    },
                }
            )
        question = execution.get("question") or ""
        if reward is not None:
            pref_groups[str(question)].append(
                {
                    "prompt": turns[0].get("root_prompt") if turns else "",
                    "response": _trajectory_text(turns),
                    "reward": reward,
                    "execution_id": execution.get("execution_id"),
                    "metrics": trace.get("metrics"),
                }
            )

    if not sft_records:
        print("No SFT records matched filters.", file=sys.stderr)
        return 2

    with open(args.sft_output, "w", encoding="utf-8") as handle:
        for record in sft_records:
            handle.write(json.dumps(record) + "\n")

    if args.pref_output:
        pref_records: list[dict[str, Any]] = []
        for question, records in pref_groups.items():
            if len(records) < 2:
                continue
            records_sorted = sorted(records, key=lambda r: r["reward"], reverse=True)
            chosen = records_sorted[0]
            rejected = records_sorted[-1]
            pref_records.append(
                {
                    "prompt": chosen["prompt"],
                    "chosen": chosen["response"],
                    "rejected": rejected["response"],
                    "metadata": {
                        "question": question,
                        "chosen_reward": chosen["reward"],
                        "rejected_reward": rejected["reward"],
                        "chosen_execution_id": chosen["execution_id"],
                        "rejected_execution_id": rejected["execution_id"],
                    },
                }
            )
        with open(args.pref_output, "w", encoding="utf-8") as handle:
            for record in pref_records:
                handle.write(json.dumps(record) + "\n")

    print(f"Wrote {len(sft_records)} SFT records to {args.sft_output}")
    if args.pref_output:
        print(f"Wrote preference pairs to {args.pref_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
