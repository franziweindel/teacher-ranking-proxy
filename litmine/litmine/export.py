"""Export: CSV (one row per student x experiment), JSONL (full records),
review queue CSV and a Markdown summary. Pure functions over records; never
touches the LLM cache, so changing the format never triggers reruns."""
from __future__ import annotations

import csv
import json
from functools import partial
from collections import Counter
from pathlib import Path

_dumps = partial(json.dumps, ensure_ascii=False, sort_keys=True)

CSV_COLUMNS = [
    "paper_title", "arxiv_id", "paper_url", "year",
    "experiment_id", "status", "agent_domain",
    "student_model", "student_hf_id", "student_model_type",
    "num_teachers", "teacher_rank_1", "teacher_rank_2", "teacher_rank_3", "teacher_rank_4",
    "teacher_models_json", "teacher_scores_json",
    "task_dataset", "task_dataset_hf_id", "environment_repo", "same_tasks_across_teachers",
    "trajectory_dataset", "trajectory_dataset_hf_id", "trajectories_public",
    "teacher_identity_per_trajectory",
    "num_tasks", "trajectories_per_teacher", "sft_data_budget", "sft_recipe_controlled",
    "evaluation_benchmarks_json", "downstream_scores_json",
    "criterion_results_json",
    "paper_evidence", "table_or_section", "artifact_evidence", "github_repo",
    "notes",
]

REVIEW_COLUMNS = ["experiment_id", "paper_title", "arxiv_id", "paper_url", "status",
                  "review_reasons", "student_model", "teachers", "primary_benchmark",
                  "teacher_scores_json", "unresolved_fields", "disagreements_json",
                  "artifact_ids", "key_evidence", "notes"]


def _v(exp: dict, k: str):
    return (exp.get(k) or {}).get("value")


def _ev(exp: dict, k: str) -> str:
    e = exp.get(k) or {}
    loc, ev = e.get("source_location", ""), e.get("evidence", "")
    return f"[{loc}] {ev}" if loc and ev else (ev or loc or "")


def record_to_row(rec: dict) -> dict:
    exp, paper, rk = rec["experiment"], rec["paper"], rec["ranking"]
    art = rec.get("artifact_verification") or {}
    evidence_bits = [f"{k}: {_ev(exp, k)}" for k in ("student_model", "trajectory_dataset",
                                                       "sft_data_budget", "same_tasks_across_teachers")
                     if _ev(exp, k)]
    score_locs = sorted({s.get("source_location", "") for b in (exp.get("downstream_scores") or {}).values()
                         for s in b.values() if s.get("source_location")})
    art_ev = "; ".join(f"{c.get('kind')}:{c.get('id')} exists={c.get('exists')} accessible={c.get('accessible')}"
                       + (f" err={c.get('error')}" if c.get("error") else "")
                       for c in art.get("checked", []))
    row = {
        "paper_title": paper.get("title", ""), "arxiv_id": paper.get("arxiv_id") or "",
        "paper_url": paper.get("url", ""), "year": paper.get("year") or "",
        "experiment_id": rec["record_id"], "status": rec["classification"]["status"],
        "agent_domain": exp.get("agent_domain", ""),
        "student_model": _v(exp, "student_model") or "", "student_hf_id": _v(exp, "student_hf_id") or "",
        "student_model_type": _v(exp, "student_model_type") or "",
        "num_teachers": len(exp.get("teacher_models", [])),
        "teacher_models_json": _dumps([t["name"] for t in exp.get("teacher_models", [])]),
        "teacher_scores_json": _dumps(rk.get("scores", {})),
        "task_dataset": _v(exp, "task_dataset") or "", "task_dataset_hf_id": _v(exp, "task_dataset_hf_id") or "",
        "environment_repo": _v(exp, "environment_repo") or "",
        "same_tasks_across_teachers": _v(exp, "same_tasks_across_teachers") or "unclear",
        "trajectory_dataset": _v(exp, "trajectory_dataset") or "",
        "trajectory_dataset_hf_id": _v(exp, "trajectory_dataset_hf_id") or "",
        "trajectories_public": _v(exp, "trajectories_public") or "unclear",
        "teacher_identity_per_trajectory": _v(exp, "teacher_identity_per_trajectory") or "unclear",
        "num_tasks": _v(exp, "num_tasks") if _v(exp, "num_tasks") is not None else "",
        "trajectories_per_teacher": _v(exp, "trajectories_per_teacher") or "",
        "sft_data_budget": _v(exp, "sft_data_budget") or "",
        "sft_recipe_controlled": _v(exp, "sft_recipe_controlled") or "unclear",
        "evaluation_benchmarks_json": _dumps(_v(exp, "evaluation_benchmarks") or []),
        "downstream_scores_json": json.dumps({b: {t: s["value"] for t, s in ts.items()}
                                              for b, ts in (exp.get("downstream_scores") or {}).items()},
                                             ensure_ascii=False),
        "criterion_results_json": _dumps({k: c.get("decision") for k, c in exp.get("criteria", {}).items()}),
        "paper_evidence": " || ".join(evidence_bits),
        "table_or_section": "; ".join(score_locs),
        "artifact_evidence": art_ev, "github_repo": _v(exp, "github_repo") or "",
        "notes": "; ".join(filter(None, [exp.get("notes", ""), "; ".join(rec["classification"].get("reasons", [])),
                                         f"benchmark={rk.get('benchmark', '')}" if rk.get("benchmark") else ""])),
    }
    row.update(rk.get("rank_columns", {f"teacher_rank_{i}": "" for i in range(1, 5)}))
    return row


def write_csv(records: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        w.writeheader()
        for rec in sorted(records, key=lambda r: r["record_id"]):
            w.writerow(record_to_row(rec))


def write_jsonl(records: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for rec in sorted(records, key=lambda r: r["record_id"]):
            f.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")


def review_row(rec: dict) -> dict | None:
    flags = list(rec["classification"].get("review_flags", [])) + list(rec.get("review_flags", []))
    if rec["classification"]["status"] != "needs_review" and not flags:
        return None
    exp, rk = rec["experiment"], rec["ranking"]
    art = rec.get("artifact_verification") or {}
    key_ev = []
    for k in ("student_model", "same_tasks_across_teachers", "trajectories_public", "sft_data_budget"):
        if _ev(exp, k):
            key_ev.append(f"{k}: {_ev(exp, k)}")
    for k, c in exp.get("criteria", {}).items():
        if c.get("decision") == "unclear":
            key_ev.append(f"criterion {k} unclear: {c.get('evidence') or '(no evidence)'}")
    return {
        "experiment_id": rec["record_id"], "paper_title": rec["paper"].get("title", ""),
        "arxiv_id": rec["paper"].get("arxiv_id") or "", "paper_url": rec["paper"].get("url", ""),
        "status": rec["classification"]["status"], "review_reasons": " | ".join(dict.fromkeys(flags)),
        "student_model": _v(exp, "student_model") or "",
        "teachers": ", ".join(t["name"] for t in exp.get("teacher_models", [])),
        "primary_benchmark": rk.get("benchmark", ""),
        "teacher_scores_json": _dumps(rk.get("scores", {})),
        "unresolved_fields": ", ".join(exp.get("unresolved_fields", [])),
        "disagreements_json": _dumps(rec.get("disagreements") or {})[:4000],
        "artifact_ids": "; ".join(f"{c.get('kind')}:{c.get('id')}({'ok' if c.get('accessible') else c.get('error')})"
                                  for c in art.get("checked", [])),
        "key_evidence": " || ".join(key_ev)[:4000], "notes": exp.get("notes", ""),
    }


def write_review_queue(records: list[dict], path: Path) -> int:
    rows = [r for r in (review_row(rec) for rec in sorted(records, key=lambda r: r["record_id"])) if r]
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=REVIEW_COLUMNS)
        w.writeheader()
        w.writerows(rows)
    return len(rows)


def build_summary(records: list[dict], stats: dict) -> str:
    st = Counter(r["classification"]["status"] for r in records)
    students = {(_v(r["experiment"], "student_model") or "").lower() for r in records
                if r["classification"]["status"] in ("gold", "silver", "results_only")}
    students.discard("")
    valid = [r for r in records if r["classification"]["status"] in ("gold", "silver", "results_only")]
    nt = lambda r: len(r["experiment"].get("teacher_models", []))
    usable = [r for r in records if r["classification"]["status"] in ("gold", "silver")]
    lines = [
        "# External teacher-ranking ground truths — summary", "",
        f"pipeline version: {stats.get('pipeline_version', '')}  |  git: {stats.get('git_commit', '')}  |  "
        f"generated: {stats.get('generated_at', '')}", "",
        "## Pipeline counts", "",
        f"- papers discovered: {stats.get('papers_discovered', 0)}",
        f"- papers pre-screened (title/abstract): {stats.get('papers_prescreened', 0)}",
        f"- papers screened (full text): {stats.get('papers_screened', 0)}",
        f"- papers sent to extraction: {stats.get('papers_extracted', 0)}",
        f"- experiments extracted: {len(records)}", "",
        "## Classification", "",
        f"- gold: {st.get('gold', 0)}", f"- silver: {st.get('silver', 0)}",
        f"- results_only: {st.get('results_only', 0)}", f"- reject: {st.get('reject', 0)}",
        f"- needs_review: {st.get('needs_review', 0)}", "",
        "## Ground truths", "",
        f"- distinct students (gold/silver/results_only): {len(students)}",
        f"- teacher-ranking ground truths (valid experiments with a derived ranking): "
        f"{sum(1 for r in valid if r['ranking'].get('ranking'))}",
        f"- with >=2 teachers: {sum(1 for r in valid if nt(r) >= 2)}",
        f"- with >=3 teachers: {sum(1 for r in valid if nt(r) >= 3)}",
        f"- with >=4 teachers: {sum(1 for r in valid if nt(r) >= 4)}",
        f"- with matched teacher tasks: {sum(1 for r in valid if _v(r['experiment'], 'same_tasks_across_teachers') == 'yes')}",
        f"- with fully public, verified trajectories: "
        f"{sum(1 for r in valid if (r.get('artifact_verification') or {}).get('trajectories', {}).get('verified'))}",
        "",
        "## Datasets immediately usable for proxy evaluation (gold/silver)", "",
    ]
    if not usable:
        lines.append("_none_")
    for r in sorted(usable, key=lambda r: r["record_id"]):
        e, rk, av = r["experiment"], r["ranking"], r.get("artifact_verification") or {}
        lines.append(
            f"- **{r['record_id']}** [{r['classification']['status']}] {r['paper'].get('title', '')} — "
            f"student `{_v(e, 'student_model')}`; ranking on {rk.get('benchmark', '?')}: "
            f"{rk.get('ranking_string', '')}; trajectories: "
            f"{', '.join(av.get('trajectories', {}).get('ids') or []) or _v(e, 'trajectory_dataset_hf_id') or '?'}; "
            f"tasks: {', '.join(av.get('tasks', {}).get('ids') or []) or _v(e, 'task_dataset_hf_id') or '?'}")
    lines += ["", "## All extracted experiments", ""]
    for r in sorted(records, key=lambda r: r["record_id"]):
        e, rk = r["experiment"], r["ranking"]
        lines.append(f"- {r['record_id']} [{r['classification']['status']}] {r['paper'].get('title', '')[:80]} — "
                     f"{_v(e, 'student_model')} / {len(e.get('teacher_models', []))} teachers / "
                     f"{rk.get('ranking_string', '') or 'no ranking'}")
    if stats.get("rejected_papers"):
        lines += ["", "## Papers screened out (no qualifying experiment)", ""]
        for p in stats["rejected_papers"]:
            lines.append(f"- {p['arxiv_id'] or p['url']}: {p['title'][:90]} — {p['reason'][:160]}")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Compact ground-truth view: one entry per student x trajectory distribution
def _norm(s: str) -> str:
    import re
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


def _teacher_tokens(name: str) -> list[str]:
    """Distinctive tokens of a teacher name for matching against dataset ids:
    'DeepSeek-V3.2' -> ['deepseek', 'v32'], 'Claude Opus 4.6' -> ['claude','opus','46']."""
    import re
    toks = [t for t in re.split(r"[\s\-_/():]+", name.lower()) if t]
    return [re.sub(r"[^a-z0-9]", "", t) for t in toks if re.sub(r"[^a-z0-9]", "", t)]


def _key_tokens(name: str) -> list[str]:
    """Distinctive tokens: >=3 chars and containing a letter ('T-Gamma' -> ['gamma'],
    'Claude Opus 4.6' -> ['claude','opus'], 'Qwen3.5-Plus' -> ['qwen35','plus'])."""
    import re
    return [t for t in _teacher_tokens(name) if len(t) >= 3 and re.search(r"[a-z]", t)]


def _dedicated(text: str, t: str, teachers: list[str]) -> bool:
    """text mentions one of t's key tokens and none of the other teachers' key tokens."""
    n = _norm(text)
    mine = _key_tokens(t)
    others = {o for x in teachers if x != t for o in _key_tokens(x)} - set(mine)
    return any(k in n for k in mine) and not any(o in n for o in others)


def _trajectory_links(rec: dict) -> tuple[dict[str, str | None], list[dict]]:
    """Map each teacher to a verified HF trajectory dataset URL (or None)."""
    exp = rec["experiment"]
    teachers = [t["name"] for t in exp.get("teacher_models", [])]
    traj_sets = []
    for c in (rec.get("artifact_verification") or {}).get("checked", []):
        if c.get("kind") != "datasets" or not c.get("accessible"):
            continue
        role = c.get("judged_role") or c.get("role")
        if role not in ("trajectories",):
            continue
        if any(t["hf_id"] == (c.get("canonical_id") or c["id"]) or t.get("canonical_id") == (c.get("canonical_id") or c["id"])
               for t in traj_sets):
            continue                                    # same dataset under a renamed org
        j = ((c.get("llm") or {}).get("judgment") or {})
        traj_sets.append({"hf_id": c["id"], "canonical_id": c.get("canonical_id") or c["id"],
                          "url": c["url"], "files": c.get("files") or [],
                          "teacher_identity_represented": j.get("checks", {}).get("teacher_identity_represented", {}).get("decision", "unclear"),
                          "teacher_subsets_separable": j.get("checks", {}).get("teacher_subsets_separable", {}).get("decision", "unclear"),
                          "task_ids_recoverable": j.get("checks", {}).get("task_ids_recoverable", {}).get("decision", "unclear"),
                          "teacher_names_found": j.get("teacher_names_found", []),
                          "teacher_mentions": c.get("teacher_mentions", {})})
    def file_for(ds: dict, t: str) -> str | None:
        """A file inside a combined dataset dedicated to teacher t: its name
        contains t's vendor token and no other teacher's vendor token."""
        for f in ds["files"]:
            if _dedicated(f.rsplit("/", 1)[-1], t, teachers):
                return f
        return None

    links: dict[str, str | None] = {}
    for t in teachers:
        hit = None
        for ds in traj_sets:
            f = file_for(ds, t)
            if f:
                hit = f"{ds['url']}/resolve/main/{f}"
                break
        for ds in ([] if hit else traj_sets):
            if _dedicated(ds["hf_id"].split("/")[-1], t, teachers):   # dataset dedicated to this teacher
                hit = ds["url"]
                break
        if hit is None:
            # a combined dataset that the LLM/heuristics say contains this teacher
            for ds in traj_sets:
                if ds["teacher_subsets_separable"] == "yes" and (
                        any(_norm(t) == _norm(x) for x in ds["teacher_names_found"]) or ds["teacher_mentions"].get(t)):
                    hit = ds["url"] + "  (combined dataset; filter by teacher)"
                    break
        links[t] = hit
    return links, traj_sets


def ground_truth_entry(rec: dict) -> dict:
    exp, rk, paper = rec["experiment"], rec["ranking"], rec["paper"]
    av = rec.get("artifact_verification") or {}
    links, traj_sets = _trajectory_links(rec)
    task_ids = av.get("tasks", {}).get("ids") or ([_v(exp, "task_dataset_hf_id")] if _v(exp, "task_dataset_hf_id") else [])
    bench = rk.get("benchmark", "")
    score_src = sorted({s.get("source_location", "") for s in (exp.get("downstream_scores") or {}).get(bench, {}).values()
                        if s.get("source_location")})
    return {
        "id": rec["record_id"],
        "status": rec["classification"]["status"],
        "student": {"model": _v(exp, "student_model"), "hf_id": _v(exp, "student_hf_id"),
                    "type": _v(exp, "student_model_type")},
        "task_distribution": {"name": _v(exp, "task_dataset"), "hf_datasets": task_ids,
                              "hf_urls": [f"https://huggingface.co/datasets/{i}" for i in task_ids],
                              "environment_repo": _v(exp, "environment_repo") or _v(exp, "github_repo"),
                              "num_tasks": _v(exp, "num_tasks"),
                              "same_tasks_across_teachers": _v(exp, "same_tasks_across_teachers")},
        "trajectories": {"description": _v(exp, "trajectory_dataset"),
                         "per_teacher_hf_url": links,
                         "verified_trajectory_datasets": [{k: d[k] for k in (
                             "hf_id", "url", "teacher_identity_represented", "teacher_subsets_separable",
                             "task_ids_recoverable")} for d in traj_sets],
                         "trajectories_per_teacher": _v(exp, "trajectories_per_teacher"),
                         "sft_data_budget": _v(exp, "sft_data_budget"),
                         "all_teachers_public": all(links.values()) if links else False},
        "evaluation": {"benchmark": bench, "scores": rk.get("scores", {}),
                       "all_benchmarks": {b: {t: s["value"] for t, s in ts.items()}
                                          for b, ts in (exp.get("downstream_scores") or {}).items()}},
        "teacher_ranking": [{"rank": r["rank"], "teacher": r["teacher"], "score": r["score"]} for r in rk.get("ranking", [])],
        "teacher_ranking_string": rk.get("ranking_string", ""),
        "source": {"paper_title": paper.get("title"), "arxiv_id": paper.get("arxiv_id"), "url": paper.get("url"),
                   "table_or_section": "; ".join(score_src)},
        "caveats": list(dict.fromkeys(x for x in rec["classification"].get("reasons", []) + rec.get("review_flags", []) if x)),
        "notes": exp.get("notes", ""),
    }


def write_ground_truths(records: list[dict], path: Path) -> int:
    """All records with >=2 scored teachers, sorted with usable ones (gold/silver) first."""
    order = {"gold": 0, "silver": 1, "needs_review": 2, "results_only": 3, "reject": 4}
    entries = [ground_truth_entry(r) for r in records if len(r["ranking"].get("scores", {})) >= 2]
    entries.sort(key=lambda e: (order.get(e["status"], 9), e["id"]))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(entries, indent=1, ensure_ascii=False, sort_keys=False) + "\n", encoding="utf-8")
    return len(entries)


def export_all(records: list[dict], out_dir: Path, stats: dict) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {"csv": out_dir / "external_teacher_rankings.csv",
             "jsonl": out_dir / "external_teacher_rankings.jsonl",
             "review": out_dir / "review_queue.csv",
             "summary": out_dir / "external_teacher_rankings_summary.md",
             "ground_truths": out_dir / "ground_truths.json"}
    write_csv(records, paths["csv"])
    write_ground_truths(records, paths["ground_truths"])
    write_jsonl(records, paths["jsonl"])
    write_review_queue(records, paths["review"])
    paths["summary"].write_text(build_summary(records, stats), encoding="utf-8")
    return paths
