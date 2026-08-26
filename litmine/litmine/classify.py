"""Deterministic post-processing: ranking derivation from published scores
and evidence-based status classification from structured criterion results.

No LLM is involved here; every rule is inspectable and unit-tested.
"""
from __future__ import annotations

from typing import Any

from .schemas import CRITERION_KEYS

# Criteria that must be satisfied for a valid controlled same-student comparison
CORE = ["agentic", "multiple_teachers", "teacher_identity_known",
        "same_student_separate_sft", "sft_recipe_controlled", "per_teacher_scores"]
# Criteria that decide usability of public artifacts for proxy evaluation
PUBLIC = ["public_tasks", "trajectories_public"]
PREFERRED = ["same_tasks_across_teachers"]

STATUSES = ("gold", "silver", "results_only", "reject", "needs_review")


def rank_from_scores(scores: dict[str, float], higher_is_better: bool = True) -> list[dict]:
    """Competition ranking ("1224") with ties preserved.

    Returns [{rank, teacher, score}] sorted by rank then teacher name. Ties are
    detected on the exact numeric value; callers who want a tolerance should
    round beforehand.
    """
    items = [(name, float(v)) for name, v in scores.items() if v is not None]
    items.sort(key=lambda x: ((-x[1] if higher_is_better else x[1]), x[0]))
    out, prev, rank = [], None, 0
    for i, (name, v) in enumerate(items):
        if prev is None or v != prev:
            rank = i + 1
        out.append({"rank": rank, "teacher": name, "score": v})
        prev = v
    return out


def rank_columns(ranking: list[dict], n: int = 4) -> dict[str, str]:
    """teacher_rank_k = teacher(s) holding competition rank k. Tied teachers
    share a cell joined by ' = ' and the following rank cell stays empty
    (A > B = C > D  ->  rank_1=A, rank_2='B = C', rank_3='', rank_4=D)."""
    by_rank: dict[int, list[str]] = {}
    for r in ranking:
        by_rank.setdefault(r["rank"], []).append(r["teacher"])
    return {f"teacher_rank_{i}": " = ".join(by_rank.get(i, [])) for i in range(1, n + 1)}


def ranking_string(ranking: list[dict]) -> str:
    """'A > B = C > D'"""
    groups: list[list[str]] = []
    last = None
    for r in ranking:
        if r["rank"] != last:
            groups.append([])
            last = r["rank"]
        groups[-1].append(r["teacher"])
    return " > ".join(" = ".join(g) for g in groups)


def decisions(criteria: dict[str, dict]) -> dict[str, str]:
    return {k: (criteria.get(k) or {}).get("decision", "unclear") for k in CRITERION_KEYS}


def classify(criteria: dict[str, dict], *, num_teachers: int, scores_complete: bool,
             artifact_verification: dict | None, exclusion_flags: list[str] | None = None) -> dict:
    """Derive status from criterion decisions + artifact checks.

    artifact_verification: {"tasks": {"verified": bool|None, ...},
                            "trajectories": {"verified": bool|None,
                                             "teacher_separable": "yes|no|unclear", ...}}
    Returns {status, reasons: [...], review_flags: [...]}.
    """
    d = decisions(criteria)
    reasons: list[str] = []
    review: list[str] = []
    exclusion_flags = exclusion_flags or []

    # 1. Hard rejects: any core criterion explicitly fails, or <2 teachers.
    failed = [k for k in CORE if d[k] == "no"]
    if failed:
        return {"status": "reject", "reasons": [f"core criterion '{k}' = no" for k in failed],
                "review_flags": review}
    if num_teachers < 2:
        return {"status": "reject", "reasons": [f"num_teachers={num_teachers} < 2"],
                "review_flags": review}

    # 2. Core uncertainty -> needs human review, never forced into a bucket.
    unclear_core = [k for k in CORE if d[k] == "unclear"]
    if unclear_core:
        review.extend(f"core criterion '{k}' unclear" for k in unclear_core)
    if not scores_complete:
        review.append("missing teacher-specific downstream scores for some teachers")
    if exclusion_flags:
        review.append("extractor raised exclusion flags: " + "; ".join(exclusion_flags))
    if review:
        return {"status": "needs_review", "reasons": review, "review_flags": review}

    # 3. Valid comparison established. Now decide artifact usability.
    av = artifact_verification or {}
    tasks_ok = (av.get("tasks") or {}).get("verified")
    traj = av.get("trajectories") or {}
    traj_ok = traj.get("verified")
    sep = traj.get("teacher_separable", "unclear")

    claims_public = d["public_tasks"] == "yes" and d["trajectories_public"] == "yes"
    if d["public_tasks"] == "no" or d["trajectories_public"] == "no":
        reasons.append("paper states tasks or trajectories are not public")
        return {"status": "results_only", "reasons": reasons, "review_flags": review}
    if claims_public and (tasks_ok is False or traj_ok is False):
        review.append("paper claims public artifacts but they could not be found/accessed")
        return {"status": "needs_review", "reasons": review, "review_flags": review}
    if traj_ok and sep == "no":
        review.append("teacher identity cannot be recovered from the released trajectory dataset")
        return {"status": "needs_review", "reasons": review, "review_flags": review}
    if not claims_public and not (tasks_ok and traj_ok):
        reasons.append("public availability of tasks/trajectories unclear and not verified")
        return {"status": "results_only", "reasons": reasons, "review_flags": review}

    limitations: list[str] = []
    if num_teachers < 3:
        limitations.append("only 2 teachers")
    if d["same_tasks_across_teachers"] != "yes":
        limitations.append(f"matched tasks = {d['same_tasks_across_teachers']}")
    if not (tasks_ok and traj_ok):
        limitations.append("artifacts claimed public but not fully verified")
    if sep != "yes":
        limitations.append(f"teacher separability in artifact = {sep}")
    if d["same_tasks_across_teachers"] == "unclear":
        review.append("uncertain task matching")
    if limitations:
        return {"status": "silver", "reasons": limitations, "review_flags": review}
    return {"status": "gold", "reasons": ["all criteria yes; artifacts verified"],
            "review_flags": review}
