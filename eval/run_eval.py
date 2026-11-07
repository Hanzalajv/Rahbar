#!/usr/bin/env python3
"""Score a running RAHBAR against a golden set, and gate CI on the result (section 5).

    python eval/run_eval.py --api http://<ingress>                 # your corpus
    python eval/run_eval.py --api ... --golden eval/fixtures/golden.fixture.yaml
    python eval/run_eval.py --api ... --update-baseline            # after a deliberate improvement

Exit code 0 when every gate passes, 1 when any fails. CI relies on that exit code.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import httpx
import yaml

HERE = Path(__file__).resolve().parent

# The gate from section 5.4.
MAX_RECALL5_DROP = 0.05        # recall@5 may fall at most 5 points below the baseline
MIN_CITATION_VALIDITY = 0.95   # at least 95% of citations must match retrieved passages
REQUIRED_ABSTENTION = 1.0      # every must-abstain question must be declined


def load_golden(path: Path) -> list[dict]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    questions = data.get("questions") or []
    if not questions:
        sys.exit(f"no questions in {path}")
    return questions


def ask(client: httpx.Client, api: str, question: str) -> dict:
    """POST one question and return the AskResponse as a dict (see api/app/schemas.py)."""
    response = client.post(f"{api.rstrip('/')}/api/ask", json={"question": question})
    response.raise_for_status()
    return response.json()


def recall_at_k(relevant: list[dict], retrieved: list[dict], k: int) -> float | None:
    """Fraction of the relevant (source, section) pairs found in the top k retrieved.
    Return None when the question has no relevant passages (must-abstain questions)."""
    if not relevant:
        return None

    top_k_retrieved = retrieved[:k]
    hits = 0
    for target in relevant:
        target_src = str(target.get("source", "")).lower()
        target_sec = str(target.get("section", "")).lower()

        found = any(
            target_src in str(r.get("source", "")).lower()
            and (
                not target_sec
                or target_sec in str(r.get("section", "")).lower()
                or str(r.get("section", "")).lower() in target_sec
                or target_sec in str(r.get("text", "")).lower()
            )
            for r in top_k_retrieved
        )
        if found:
            hits += 1

    return hits / len(relevant)


def citation_validity(response: dict) -> float | None:
    """Fraction of the response's citations that match a passage in response['retrieved'].
    Return None when there are no citations."""
    citations = response.get("citations") or []
    if not citations:
        return None

    retrieved = response.get("retrieved") or []
    valid = 0
    for c in citations:
        c_src = str(c.get("source", "")).lower()
        c_sec = str(c.get("section", "")).lower()

        match = any(
            c_src in str(r.get("source", "")).lower()
            and (c_sec in str(r.get("section", "")).lower() or str(r.get("section", "")).lower() in c_sec)
            for r in retrieved
        )
        if match:
            valid += 1

    return valid / len(citations)


def summarise(rows: list[dict]) -> dict:
    """Overall recall@3, recall@5, citation validity and abstention correctness, plus
    recall@5 broken down by question type. This dict becomes results.json."""
    r3_scores: list[float] = []
    r5_scores: list[float] = []
    by_type: dict[str, list[float]] = {}
    citation_scores: list[float] = []

    abstain_total = 0
    abstain_correct = 0

    for row in rows:
        resp = row.get("response") or {}
        retrieved = resp.get("retrieved") or []
        relevant = row.get("relevant") or []
        q_type = row.get("type", "ordinary")

        if row.get("must_abstain"):
            abstain_total += 1
            if not resp.get("answered_from_context", True):
                abstain_correct += 1
        else:
            r3 = recall_at_k(relevant, retrieved, 3)
            r5 = recall_at_k(relevant, retrieved, 5)
            if r3 is not None:
                r3_scores.append(r3)
            if r5 is not None:
                r5_scores.append(r5)
                by_type.setdefault(q_type, []).append(r5)

        c_val = citation_validity(resp)
        if c_val is not None:
            citation_scores.append(c_val)

    overall_r3 = round(sum(r3_scores) / len(r3_scores), 4) if r3_scores else 0.0
    overall_r5 = round(sum(r5_scores) / len(r5_scores), 4) if r5_scores else 0.0
    overall_c_val = round(sum(citation_scores) / len(citation_scores), 4) if citation_scores else 1.0
    abstention_accuracy = round(abstain_correct / abstain_total, 4) if abstain_total > 0 else 1.0

    breakdown_by_type = {
        t: round(sum(scores) / len(scores), 4) if scores else 0.0
        for t, scores in by_type.items()
    }

    return {
        "recall@3": overall_r3,
        "recall@5": overall_r5,
        "citation_validity": overall_c_val,
        "abstention_correctness": abstention_accuracy,
        "by_type": breakdown_by_type,
    }


def gate(results: dict, baseline: dict | None) -> list[str]:
    """Return one message per failed gate (section 5.4); an empty list means pass."""
    failures: list[str] = []

    # 1. Abstention correctness: must be 100%
    abstention = results.get("abstention_correctness", 0.0)
    if abstention < REQUIRED_ABSTENTION:
        failures.append(
            f"Abstention correctness failed: {abstention:.1%} (required: {REQUIRED_ABSTENTION:.1%}). "
            f"Every must-abstain question must be declined."
        )

    # 2. Citation validity: must be >= 95%
    c_val = results.get("citation_validity", 0.0)
    if c_val < MIN_CITATION_VALIDITY:
        failures.append(
            f"Citation validity failed: {c_val:.1%} (required: {MIN_CITATION_VALIDITY:.1%}). "
            f"Every citation must strictly match a retrieved passage."
        )

    # 3. Regression against baseline
    if baseline is not None:
        current_r5 = results.get("recall@5", 0.0)
        baseline_r5 = baseline.get("recall@5", 0.0)
        allowed_floor = baseline_r5 - MAX_RECALL5_DROP
        if current_r5 < allowed_floor:
            failures.append(
                f"Recall@5 regression: current {current_r5:.2%} fell below baseline floor "
                f"{allowed_floor:.2%} (baseline: {baseline_r5:.2%}, drop limit: {MAX_RECALL5_DROP:.2%})"
            )

    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate RAHBAR against a golden set.")
    parser.add_argument("--api", required=True, help="base URL, e.g. http://192.168.49.2")
    parser.add_argument("--host", help="Host header, when your Ingress rule names a host")
    parser.add_argument("--golden", type=Path, default=HERE / "golden.yaml")
    parser.add_argument("--baseline", type=Path, default=HERE / "baseline.json")
    parser.add_argument("--out", type=Path, default=HERE / "results.json")
    parser.add_argument("--update-baseline", action="store_true",
                        help="write these results as the new baseline")
    args = parser.parse_args()

    questions = load_golden(args.golden)
    rows = []
    headers = {"Host": args.host} if args.host else {}
    with httpx.Client(timeout=120.0, headers=headers) as client:
        for q in questions:
            started = time.perf_counter()
            response = ask(client, args.api, q["question"])
            rows.append({
                "id": q["id"], "type": q.get("type", "ordinary"),
                "must_abstain": bool(q.get("must_abstain", False)),
                "relevant": q.get("relevant") or [],
                "expect": q.get("expect"),
                "response": response,
                "seconds": round(time.perf_counter() - started, 2),
            })
            print(f"  {q['id']:<5} {rows[-1]['seconds']:>6.2f}s  {q['question'][:60]}")

    results = summarise(rows)
    baseline = json.loads(args.baseline.read_text(encoding="utf-8")) if args.baseline.exists() else None
    failures = gate(results, baseline)

    results["golden"] = str(args.golden)
    results["questions"] = len(rows)
    args.out.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"\nresults written to {args.out}")
    if args.update_baseline:
        args.baseline.write_text(json.dumps(results, indent=2), encoding="utf-8")
        print(f"baseline updated: {args.baseline}")

    if failures:
        print("\nGATE FAILED")
        for message in failures:
            print(f"  - {message}")
        return 1
    print("\ngate passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
