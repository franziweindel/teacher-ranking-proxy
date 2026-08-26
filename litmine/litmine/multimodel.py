"""Multi-model verification (PIPELINE.md §8).

Two backends extract independently. Their experiment lists are aligned, the
comparable fields are diffed, and every disagreement is preserved verbatim and
sent to an adjudicator LLM together with the source text. The adjudicator's
evidence-grounded resolution is applied field by field; anything it leaves
`unclear` stays unresolved and is flagged for the human review queue.
"""
from __future__ import annotations

import json
import re
from typing import Any

from .llm import LLMClient
from .prompts import ADJUDICATION_SYSTEM, ADJUDICATION_VERSION
from .schemas import CRITERION_KEYS, validate_adjudication

# Free-text descriptive fields: two backends legitimately paraphrase these, so
# they only count as a disagreement when the numbers they mention differ or
# one side is empty and the other is not.
DESCRIPTIVE_FIELDS = {"task_dataset", "trajectory_dataset", "trajectories_per_teacher",
                      "sft_data_budget", "sft_recipe"}
VALUE_FIELDS = ["student_model", "student_hf_id", "student_model_type", "task_dataset",
                "task_dataset_hf_id", "environment_repo", "same_tasks_across_teachers",
                "trajectory_dataset", "trajectory_dataset_hf_id", "trajectories_public",
                "teacher_identity_per_trajectory", "num_tasks", "trajectories_per_teacher",
                "sft_data_budget", "sft_recipe_controlled", "github_repo"]


def norm_name(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


def teacher_set(exp: dict) -> set[str]:
    return {norm_name(t["name"]) for t in exp.get("teacher_models", [])}


def primary_scores(exp: dict) -> tuple[str, dict[str, float]]:
    """Pick the benchmark used for ranking: the declared primary if present,
    else the benchmark with the most teacher scores (ties -> alphabetical)."""
    scores = exp.get("downstream_scores") or {}
    if not scores:
        return "", {}
    pb = exp.get("primary_benchmark") or ""
    if pb not in scores:
        pb = sorted(scores, key=lambda b: (-len(scores[b]), b))[0]
    return pb, {t: v["value"] for t, v in scores[pb].items()}


def _align(a: list[dict], b: list[dict]) -> list[tuple[int | None, int | None]]:
    """Greedy alignment of experiments by (student, teacher set, benchmark) similarity."""
    def sig(e):
        pb, _ = primary_scores(e)
        return norm_name((e.get("student_model") or {}).get("value") or ""), teacher_set(e), norm_name(pb)

    def score(x, y):
        sx, sy = sig(x), sig(y)
        s = 0
        if sx[0] and sx[0] == sy[0]:
            s += 3
        if sx[1] and sx[1] == sy[1]:
            s += 2
        elif sx[1] & sy[1]:
            s += 1
        if sx[2] and sx[2] == sy[2]:
            s += 1
        return s

    pairs, used_b = [], set()
    cands = sorted(((score(x, y), i, j) for i, x in enumerate(a) for j, y in enumerate(b)),
                   key=lambda t: (-t[0], t[1], t[2]))
    used_a: set[int] = set()
    for s, i, j in cands:
        if s < 3 or i in used_a or j in used_b:
            continue
        pairs.append((i, j))
        used_a.add(i)
        used_b.add(j)
    pairs += [(i, None) for i in range(len(a)) if i not in used_a]
    pairs += [(None, j) for j in range(len(b)) if j not in used_b]
    return pairs


def _numbers(s: str) -> list[str]:
    return sorted(re.findall(r"\d+(?:\.\d+)?[kKmM]?", s or ""))


def _cmp_loose(x: Any, y: Any) -> bool:
    if (x in (None, "")) != (y in (None, "")):
        return False
    if x in (None, ""):
        return True
    return _numbers(str(x)) == _numbers(str(y)) or norm_name(str(x)) == norm_name(str(y))


def _cmp_val(x: Any, y: Any) -> bool:
    if isinstance(x, (int, float)) and isinstance(y, (int, float)):
        return abs(float(x) - float(y)) < 1e-6
    if isinstance(x, str) and isinstance(y, str):
        return norm_name(x) == norm_name(y)
    return x == y


def diff_experiments(a: dict, b: dict) -> dict[str, dict]:
    """Field -> {"openai": value, "deepseek": value} for every disagreement.
    (Keys are the backend names passed by the caller; here positional a/b.)"""
    d: dict[str, dict] = {}
    for f in VALUE_FIELDS:
        va, vb = (a.get(f) or {}).get("value"), (b.get(f) or {}).get("value")
        same = _cmp_loose(va, vb) if f in DESCRIPTIVE_FIELDS else _cmp_val(va, vb)
        if not same:
            d[f] = {"a": va, "b": vb}
    ta, tb = teacher_set(a), teacher_set(b)
    if ta != tb:
        d["teacher_models"] = {"a": [t["name"] for t in a.get("teacher_models", [])],
                               "b": [t["name"] for t in b.get("teacher_models", [])]}
    pa, sa = primary_scores(a)
    pb_, sb = primary_scores(b)
    na = {norm_name(k): v for k, v in sa.items()}
    nb = {norm_name(k): v for k, v in sb.items()}
    if norm_name(pa) != norm_name(pb_) and na != nb:     # name-only differences are not a dispute
        d["primary_benchmark"] = {"a": pa, "b": pb_}
    for t in sorted(set(na) | set(nb)):
        if not _cmp_val(na.get(t), nb.get(t)):
            d[f"downstream_scores.{t}"] = {"a": na.get(t), "b": nb.get(t)}
    for k in CRITERION_KEYS:
        ca = (a.get("criteria", {}).get(k) or {}).get("decision")
        cb = (b.get("criteria", {}).get(k) or {}).get("decision")
        if ca != cb:
            d[f"criteria.{k}"] = {"a": ca, "b": cb}
    return d


def _set_field(exp: dict, field: str, value: Any, evidence: str, loc: str, src: str) -> None:
    if field in VALUE_FIELDS:
        exp[field] = {"value": value, "evidence": evidence, "source_location": loc,
                      "adjudicated_from": src}
    elif field.startswith("criteria."):
        k = field.split(".", 1)[1]
        v = str(value).lower() if value is not None else "unclear"
        exp["criteria"][k] = {"decision": v if v in ("yes", "no", "unclear") else "unclear",
                              "evidence": evidence, "source_location": loc, "confidence": None,
                              "evidence_type": "adjudicated", "adjudicated_from": src}
    elif field.startswith("downstream_scores."):
        t = field.split(".", 1)[1]
        pb, _ = primary_scores(exp)
        bench = exp["downstream_scores"].setdefault(pb or "primary", {})
        real = next((k for k in bench if norm_name(k) == t), t)
        if value is None:
            bench.pop(real, None)
        else:
            try:
                bench[real] = {"value": float(value), "evidence": evidence, "source_location": loc,
                               "adjudicated_from": src}
            except (TypeError, ValueError):
                bench.pop(real, None)
    elif field == "teacher_models" and isinstance(value, list):
        exp["teacher_models"] = [{"name": str(n), "evidence": evidence, "source_location": loc,
                                  "adjudicated_from": src} for n in value]
    elif field == "primary_benchmark" and isinstance(value, str):
        exp["primary_benchmark"] = value


def adjudicate(adjudicator: LLMClient, names: tuple[str, str], exp_a: dict | None, exp_b: dict | None,
               disagreements: dict[str, dict], source_text: str, doc_title: str) -> dict:
    """Ask the adjudicator to resolve each disagreement from the source text."""
    na, nb = names
    disputed = {f: {na: v["a"], nb: v["b"]} for f, v in disagreements.items()}
    user = (f"TITLE: {doc_title}\n\nDISPUTED FIELDS (JSON):\n{json.dumps(disputed, indent=1, ensure_ascii=False)}\n\n"
            f"FULL {na.upper()} RECORD:\n{json.dumps(exp_a, ensure_ascii=False)[:20000]}\n\n"
            f"FULL {nb.upper()} RECORD:\n{json.dumps(exp_b, ensure_ascii=False)[:20000]}\n\n"
            f"===== BEGIN SOURCE TEXT =====\n{source_text}\n===== END SOURCE TEXT =====")
    res = adjudicator.complete_json("adjudicate", ADJUDICATION_VERSION, ADJUDICATION_SYSTEM, user,
                                    validator=validate_adjudication, max_tokens=8000)
    return {"result": res.parsed, "llm": res.meta(), "disputed": disputed}


def merge_with_adjudication(names: tuple[str, str], exp_a: dict, exp_b: dict,
                            disagreements: dict[str, dict], adj: dict | None) -> tuple[dict, list[str]]:
    """Return (merged experiment, unresolved field list). Base = backend a."""
    merged = json.loads(json.dumps(exp_a))
    unresolved: list[str] = []
    resolutions = ((adj or {}).get("result") or {}).get("resolutions", {})
    na, nb = names
    for f, dv in disagreements.items():
        r = resolutions.get(f)
        if not r or r["decision"] == "unclear":
            unresolved.append(f)
            _set_field(merged, f, None, "", "", "unresolved")
            continue
        if r["decision"] == na:
            val = dv["a"]
        elif r["decision"] == nb:
            val = dv["b"]
        else:
            val = r.get("resolved_value")
        _set_field(merged, f, val, r["evidence"], r["source_location"], f"adjudicator:{r['decision']}")
    merged["adjudicated_fields"] = sorted(set(disagreements) - set(unresolved))
    merged["unresolved_fields"] = unresolved
    return merged, unresolved
