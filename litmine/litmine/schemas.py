"""Structured-output schemas and validators for every LLM stage.

Validation is strict about *shape* (so the runtime LLM cannot slip in free-form
answers) but never converts uncertainty into `yes`: any unknown/malformed
decision is coerced to `unclear` and flagged in `validation_notes`.
"""
from __future__ import annotations

from typing import Any

DECISIONS = ("yes", "no", "unclear")

# Criterion keys (PIPELINE.md §2), in order.
CRITERIA: list[tuple[str, str]] = [
    ("agentic", "agentic / interactive trajectories are used"),
    ("public_tasks", "tasks or environments are public"),
    ("multiple_teachers", ">= 2 teacher models generate trajectories"),
    ("same_tasks_across_teachers", "the same tasks are used across teachers"),
    ("teacher_identity_known", "teacher identity is known for each trajectory"),
    ("trajectories_public", "teacher trajectories are publicly available"),
    ("same_student_separate_sft", "the same pre-SFT student is trained separately on each teacher dataset"),
    ("sft_recipe_controlled", "SFT recipe and data amount are controlled across teachers"),
    ("per_teacher_scores", "downstream evaluation scores are reported separately for every teacher"),
]
CRITERION_KEYS = [k for k, _ in CRITERIA]

SCREENING_KEYS = ["agentic", "multiple_teachers", "same_student_separate_sft",
                  "per_teacher_scores"]


class SchemaError(ValueError):
    pass


def _str(v: Any, default: str = "") -> str:
    if v is None:
        return default
    if isinstance(v, (list, dict)):
        import json
        return json.dumps(v, ensure_ascii=False)
    return str(v)


def _num(v: Any) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        s = str(v).strip().replace("%", "").replace(",", "")
        return float(s)
    except ValueError:
        return None


def _int(v: Any) -> int | None:
    n = _num(v)
    return int(n) if n is not None and float(n).is_integer() else None


def _conf(v: Any) -> float | None:
    n = _num(v)
    if n is None:
        return None
    return max(0.0, min(1.0, n))


def validate_criterion(c: Any, notes: list[str], name: str) -> dict:
    """{decision, evidence, source_location, confidence, evidence_type}"""
    if not isinstance(c, dict):
        notes.append(f"{name}: criterion not an object -> unclear")
        return {"decision": "unclear", "evidence": "", "source_location": "",
                "confidence": None, "evidence_type": "none"}
    d = _str(c.get("decision")).strip().lower()
    if d not in DECISIONS:
        notes.append(f"{name}: decision {d!r} not in {DECISIONS} -> unclear")
        d = "unclear"
    ev = _str(c.get("evidence")).strip()
    if d != "unclear" and not ev:
        notes.append(f"{name}: '{d}' without evidence -> unclear")
        d = "unclear"
    et = _str(c.get("evidence_type")).strip().lower()
    if et not in ("explicit", "inferred", "none"):
        et = "explicit" if ev else "none"
    return {"decision": d, "evidence": ev,
            "source_location": _str(c.get("source_location")).strip(),
            "confidence": _conf(c.get("confidence")), "evidence_type": et}


def validate_screening(obj: Any) -> dict:
    if not isinstance(obj, dict):
        raise SchemaError("screening output must be a JSON object")
    notes: list[str] = []
    out = {"criteria": {}, "validation_notes": notes}
    crit = obj.get("criteria", obj)
    for k in SCREENING_KEYS:
        if k not in crit:
            raise SchemaError(f"screening output missing criterion {k!r}")
        out["criteria"][k] = validate_criterion(crit[k], notes, k)
    rel = _str(obj.get("relevant")).strip().lower()
    if rel not in DECISIONS:
        raise SchemaError("screening output must contain relevant: yes|no|unclear")
    out["relevant"] = rel
    out["summary"] = _str(obj.get("summary")).strip()
    out["candidate_experiments"] = [_str(x) for x in (obj.get("candidate_experiments") or [])
                                    if isinstance(x, (str, dict))]
    return out


def validate_evidenced_value(v: Any, notes: list[str], name: str, kind: str = "str") -> dict:
    """{value, evidence, source_location}; value None if unsupported."""
    if v is None:
        return {"value": None, "evidence": "", "source_location": ""}
    if not isinstance(v, dict):
        v = {"value": v, "evidence": "", "source_location": ""}
    val = v.get("value")
    if kind == "int":
        val = _int(val)
    elif kind == "num":
        val = _num(val)
    elif kind == "bool":
        s = _str(val).strip().lower()
        val = "yes" if s in ("true", "yes") else "no" if s in ("false", "no") else \
            "unclear" if s else None
    elif kind == "list":
        val = [_str(x).strip() for x in val] if isinstance(val, list) else \
            ([_str(val).strip()] if val not in (None, "") else [])
    else:
        val = _str(val).strip() or None
    ev = _str(v.get("evidence")).strip()
    if val not in (None, [], "") and not ev:
        notes.append(f"{name}: value given without evidence")
    return {"value": val, "evidence": ev, "source_location": _str(v.get("source_location")).strip()}


def validate_experiment(e: Any, notes: list[str], idx: int) -> dict:
    if not isinstance(e, dict):
        raise SchemaError(f"experiment[{idx}] is not an object")
    out: dict[str, Any] = {}

    def plain(k: str) -> str:   # tolerate {"value": ...} wrappers on plain string fields
        v = e.get(k)
        if isinstance(v, dict) and "value" in v:
            v = v["value"]
        return _str(v).strip()
    out["experiment_label"] = plain("experiment_label") or f"experiment_{idx + 1}"
    out["agent_domain"] = plain("agent_domain")
    for k, kind in [("student_model", "str"), ("student_hf_id", "str"),
                    ("student_model_type", "str"), ("task_dataset", "str"),
                    ("task_dataset_hf_id", "str"), ("environment_repo", "str"),
                    ("same_tasks_across_teachers", "bool"), ("trajectory_dataset", "str"),
                    ("trajectory_dataset_hf_id", "str"), ("trajectories_public", "bool"),
                    ("teacher_identity_per_trajectory", "bool"), ("num_tasks", "int"),
                    ("trajectories_per_teacher", "str"), ("sft_data_budget", "str"),
                    ("sft_recipe", "str"), ("sft_recipe_controlled", "bool"),
                    ("evaluation_benchmarks", "list"), ("github_repo", "str"),
                    ("project_page", "str")]:
        out[k] = validate_evidenced_value(e.get(k), notes, f"exp{idx}.{k}", kind)
    # teachers: list of {name, evidence, source_location}
    teachers = []
    for t in e.get("teacher_models") or []:
        if isinstance(t, str):
            t = {"name": t}
        if not isinstance(t, dict):
            continue
        nm = _str(t.get("name")).strip()
        if nm:
            teachers.append({"name": nm, "evidence": _str(t.get("evidence")).strip(),
                             "source_location": _str(t.get("source_location")).strip()})
    out["teacher_models"] = teachers
    # scores: {benchmark: {teacher: {value, evidence, source_location}}}
    scores: dict[str, dict[str, dict]] = {}
    raw_scores = e.get("downstream_scores") or {}
    if isinstance(raw_scores, dict):
        for bench, per_teacher in raw_scores.items():
            if not isinstance(per_teacher, dict):
                continue
            scores[_str(bench).strip()] = {}
            for tname, sv in per_teacher.items():
                if isinstance(sv, dict):
                    val, ev, loc = _num(sv.get("value")), _str(sv.get("evidence")).strip(), \
                        _str(sv.get("source_location")).strip()
                else:
                    val, ev, loc = _num(sv), "", ""
                if val is None:
                    notes.append(f"exp{idx}: non-numeric score for {tname} on {bench} dropped")
                    continue
                if not ev and not loc:
                    notes.append(f"exp{idx}: score for {tname} on {bench} lacks provenance")
                scores[_str(bench).strip()][_str(tname).strip()] = {
                    "value": val, "evidence": ev, "source_location": loc}
    out["downstream_scores"] = scores
    out["primary_benchmark"] = _str(e.get("primary_benchmark")).strip()
    out["criteria"] = {}
    crit = e.get("criteria") or {}
    for k in CRITERION_KEYS:
        if k not in crit:
            notes.append(f"exp{idx}: criterion {k} missing -> unclear")
        out["criteria"][k] = validate_criterion(crit.get(k), notes, f"exp{idx}.{k}")
    out["exclusion_flags"] = [_str(x) for x in (e.get("exclusion_flags") or []) if x]
    out["notes"] = plain("notes")
    return out


def validate_extraction(obj: Any) -> dict:
    if not isinstance(obj, dict):
        raise SchemaError("extraction output must be a JSON object")
    if "experiments" not in obj or not isinstance(obj["experiments"], list):
        raise SchemaError("extraction output must contain an 'experiments' list")
    notes: list[str] = []
    exps = [validate_experiment(e, notes, i) for i, e in enumerate(obj["experiments"])]
    return {"experiments": exps, "paper_summary": _str(obj.get("paper_summary")).strip(),
            "no_qualifying_experiment_reason": _str(obj.get("no_qualifying_experiment_reason")).strip(),
            "validation_notes": notes}


def validate_artifact_judgment(obj: Any) -> dict:
    if not isinstance(obj, dict):
        raise SchemaError("artifact judgment must be a JSON object")
    notes: list[str] = []
    out = {"checks": {}, "validation_notes": notes}
    for k in ("teacher_identity_represented", "teacher_subsets_separable",
              "task_ids_recoverable", "matches_paper_description"):
        if k not in obj:
            notes.append(f"artifact check {k} missing -> unclear")
        out["checks"][k] = validate_criterion(obj.get(k), notes, k)
    out["teacher_names_found"] = [_str(x) for x in (obj.get("teacher_names_found") or [])]
    role = _str(obj.get("artifact_role")).strip().lower()
    if role not in ("trajectories", "tasks", "student_model", "code", "other", "unclear"):
        notes.append(f"artifact_role {role!r} -> unclear")
        role = "unclear"
    out["artifact_role"] = role
    out["notes"] = _str(obj.get("notes")).strip()
    return out


def validate_adjudication(obj: Any) -> dict:
    if not isinstance(obj, dict):
        raise SchemaError("adjudication must be a JSON object")
    notes: list[str] = []
    res = obj.get("resolutions")
    if not isinstance(res, dict):
        raise SchemaError("adjudication must contain 'resolutions' object")
    out = {"resolutions": {}, "validation_notes": notes,
           "summary": _str(obj.get("summary")).strip()}
    for field, r in res.items():
        if not isinstance(r, dict):
            continue
        out["resolutions"][field] = {
            "resolved_value": r.get("resolved_value"),
            "decision": _str(r.get("decision")).strip().lower() or "unclear",
            "evidence": _str(r.get("evidence")).strip(),
            "source_location": _str(r.get("source_location")).strip(),
            "confidence": _conf(r.get("confidence")),
        }
        d = out["resolutions"][field]
        if d["decision"] not in ("openai", "deepseek", "neither", "unclear"):
            notes.append(f"{field}: adjudication decision {d['decision']!r} -> unclear")
            d["decision"] = "unclear"
        if d["decision"] != "unclear" and not d["evidence"]:
            notes.append(f"{field}: adjudication without evidence -> unclear")
            d["decision"] = "unclear"
    return out
