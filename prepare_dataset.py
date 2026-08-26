#!/usr/bin/env python3
"""Build the canonical task manifest for the teacher-ranking proxy benchmark.

Downloads the released teacher trajectories, discovers their actual layout and
schema, validates the task sets across teachers, resolves every task against
the runnable-task repo, and writes a persistent manifest:

    python data/teacher_ranking_proxy/prepare_dataset.py --dataset terminal_lego

With --n-tasks/--seed it additionally draws the deterministic task sample for a
run and writes runs/<run_id>/sampled_task_ids.json (EXPERIMENT_SPEC.md §3).

The dataset registry lives at the top of this file (EXPERIMENT_SPEC.md §15.1);
HF repo IDs appear here and nowhere else.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_MANIFEST = SCRIPT_DIR / "artifacts" / "task_manifest.jsonl"
# Storage root for caches/venv/runs; override on other hosts (e.g. HPC scratch).
BIG_DISK = Path(os.environ.get("WS_ROOT",
                               "/mnt/hdd_pool_bigsur/userdata/franziska"))
DEFAULT_RUNS_TARGET = BIG_DISK / "teacher_ranking_proxy" / "runs"
RUNS_LINK = SCRIPT_DIR / "runs"

MANIFEST_FORMAT = "jsonl"  # one header object, then one object per task
SAMPLE_FORMAT = "json"


# ---------------------------------------------------------------------------
# Dataset registry (EXPERIMENT_SPEC.md §15.1). Registry entries are experiment
# definitions: do not edit them to make a run succeed.
# ---------------------------------------------------------------------------

@dataclass
class DatasetSpec:
    traj_repo: str
    task_repo: str
    default_teachers: list
    published_rankings: dict
    published_scores: dict | None = None


DATASETS = {
    "terminal_lego": DatasetSpec(
        traj_repo="SWE-Lego/Terminal-Lego-Traj-8k",
        task_repo="SWE-Lego/Terminal-Lego-15k",
        default_teachers=[
            "DeepSeek-V3.2", "GLM-5", "Qwen3.5-Plus", "Claude Opus 4.6",
        ],
        published_rankings={
            "Qwen/Qwen3-8B": [["DeepSeek-V3.2"], ["GLM-5", "Qwen3.5-Plus"],
                              ["Claude Opus 4.6"]],
            "Qwen/Qwen3-32B": [["DeepSeek-V3.2"], ["GLM-5"],
                               ["Qwen3.5-Plus"], ["Claude Opus 4.6"]],
        },
        # Downstream SFT benchmark scores per teacher — Terminal-Lego paper
        # (arXiv:2606.03461) Table 1, §4.1: student performance after SFT on
        # 8.1k matched trajectories. These are the gains behind
        # published_rankings (note the exact 8.6 = 8.6 tie for Qwen3-8B).
        published_scores={
            "Qwen/Qwen3-8B": {"DeepSeek-V3.2": 10.5, "GLM-5": 8.6,
                              "Qwen3.5-Plus": 8.6, "Claude Opus 4.6": 5.6},
            "Qwen/Qwen3-32B": {"DeepSeek-V3.2": 20.6, "GLM-5": 19.5,
                               "Qwen3.5-Plus": 17.2, "Claude Opus 4.6": 15.5},
        },
    ),
    # "ot_agent": DatasetSpec(...)   # added later, no script changes required
}


# ---------------------------------------------------------------------------
# Environment: HF cache on the big disk, runs/ symlink (docs/system_prompt.md)
# ---------------------------------------------------------------------------

def ensure_big_disk_env() -> dict:
    """Point HF caches at the big disk if unset; return the resolved paths."""
    os.environ.setdefault("HF_HOME", str(BIG_DISK / "hf_cache"))
    os.environ.setdefault(
        "HF_DATASETS_CACHE", str(Path(os.environ["HF_HOME"]) / "datasets"))
    return {
        "HF_HOME": os.environ["HF_HOME"],
        "HF_DATASETS_CACHE": os.environ["HF_DATASETS_CACHE"],
    }


def ensure_runs_root() -> Path:
    """Idempotently create the big-disk runs dir and the repo-side symlink."""
    DEFAULT_RUNS_TARGET.mkdir(parents=True, exist_ok=True)
    if RUNS_LINK.is_symlink() or RUNS_LINK.exists():
        resolved = RUNS_LINK.resolve()
        if resolved != DEFAULT_RUNS_TARGET.resolve():
            raise SystemExit(
                f"{RUNS_LINK} exists but resolves to {resolved}, expected "
                f"{DEFAULT_RUNS_TARGET}; refusing to overwrite.")
    else:
        RUNS_LINK.symlink_to(DEFAULT_RUNS_TARGET)
    return RUNS_LINK.resolve()


# ---------------------------------------------------------------------------
# Deterministic sampling (EXPERIMENT_SPEC.md §3 — algorithm is pinned)
# ---------------------------------------------------------------------------

def draw_sample(task_ids, n_tasks: int, seed: int) -> list:
    ids = sorted(set(task_ids))
    if n_tasks > len(ids):
        raise SystemExit(f"--n-tasks {n_tasks} exceeds available tasks ({len(ids)})")
    return random.Random(seed).sample(ids, n_tasks)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def write_or_check_sample(run_dir: Path, dataset: str, task_ids, n_tasks: int,
                          seed: int, manifest_sha256: str) -> list:
    """Write sampled_task_ids.json, or reuse+cross-check an existing one."""
    sample_path = run_dir / "sampled_task_ids.json"
    if sample_path.exists():
        existing = json.loads(sample_path.read_text())
        for key, want in [("n_tasks", n_tasks), ("seed", seed),
                          ("manifest_sha256", manifest_sha256),
                          ("dataset", dataset)]:
            if existing.get(key) != want:
                raise SystemExit(
                    f"{sample_path} exists but {key}={existing.get(key)!r} "
                    f"disagrees with requested {want!r}. Refusing to resample; "
                    f"pass an explicit --run-id for a fresh directory.")
        print(f"[sample] reusing existing {sample_path}")
        return existing["task_ids"]
    sampled = draw_sample(task_ids, n_tasks, seed)
    run_dir.mkdir(parents=True, exist_ok=True)
    sample_path.write_text(json.dumps({
        "task_ids": sampled, "n_tasks": n_tasks, "seed": seed,
        "manifest_sha256": manifest_sha256, "dataset": dataset,
    }, indent=2) + "\n")
    print(f"[sample] wrote {sample_path}")
    return sampled


# ---------------------------------------------------------------------------
# Terminal-Lego dataset handler: layout discovery + schema inspection
# ---------------------------------------------------------------------------

def _normalize(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def discover_teacher_files(api, spec: DatasetSpec, teachers) -> tuple:
    """Map requested teacher names onto per-teacher files in the traj repo.

    Terminal-Lego-Traj-8k releases one JSON file per teacher. We do not assume
    that: we enumerate the repo and branch on what is actually there. If the
    file slugs do not map 1:1 onto the requested teachers, stop and report.
    """
    info = api.dataset_info(spec.traj_repo)
    files = api.list_repo_files(spec.traj_repo, repo_type="dataset", revision=info.sha)
    data_files = [f for f in files if f.endswith((".json", ".jsonl", ".parquet"))]
    print(f"[layout] {spec.traj_repo}@{info.sha[:12]}: {len(files)} files, "
          f"data files: {data_files}")
    if not data_files:
        raise SystemExit(f"no data files found in {spec.traj_repo}; layout changed?")

    mapping = {}
    for teacher in teachers:
        matches = [f for f in data_files if _slug_matches(teacher, f)]
        if len(matches) != 1:
            raise SystemExit(
                f"cannot map teacher {teacher!r} onto exactly one repo file "
                f"(candidates: {matches}, all data files: {data_files}). "
                f"Stopping rather than guessing (EXPERIMENT_SPEC.md §2).")
        mapping[teacher] = matches[0]
    print(f"[layout] per-teacher files (layout = one file per teacher):")
    for t, f in mapping.items():
        print(f"    {t:18s} -> {f}")
    return mapping, info.sha


def _slug_matches(teacher: str, filename: str) -> bool:
    """True if the filename slug corresponds to the teacher name.

    'terminal-lego-deepseek-v3-2-8k.json' -> 'deepseekv328k';
    'DeepSeek-V3.2' -> 'deepseekv32'. We require the normalized teacher name
    (ignoring vendor prefixes like 'Claude') to be a substring.
    """
    slug = _normalize(Path(filename).stem)
    t = _normalize(teacher)
    for prefix in ("claude",):  # 'Claude Opus 4.6' is released as 'opus-4-6'
        if t.startswith(prefix):
            t = t[len(prefix):]
    return t in slug


TASK_ID_FIELD_CANDIDATES = ("oracle_passed_task", "task_id", "task", "task_name")


def inspect_records(teacher: str, path: Path) -> dict:
    """Load one teacher file, inspect its actual schema, extract task IDs."""
    with open(path) as f:
        data = json.load(f)
    if not isinstance(data, list) or not data:
        raise SystemExit(f"{path}: expected a non-empty JSON list, got {type(data)}")
    rec = data[0]
    record_keys = sorted(rec.keys())
    meta_keys = sorted(rec.get("metadata", {}).keys()) if "metadata" in rec else []
    id_field = None
    for cand in TASK_ID_FIELD_CANDIDATES:
        if cand in rec.get("metadata", {}):
            id_field = ("metadata", cand)
            break
        if cand in rec:
            id_field = (cand,)
            break
    if id_field is None:
        raise SystemExit(
            f"{path}: no task-id field among {TASK_ID_FIELD_CANDIDATES}; "
            f"record keys={record_keys}, metadata keys={meta_keys}")

    def get_id(r):
        v = r
        for k in id_field:
            v = v[k]
        return v

    roles = set()
    for r in data[:50]:
        for turn in r["conversations"]:
            roles.add(turn.get("from") or turn.get("role"))
    ids = [get_id(r) for r in data]
    return {
        "teacher": teacher,
        "n_records": len(data),
        "record_keys": record_keys,
        "metadata_keys": meta_keys,
        "task_id_field": ".".join(id_field),
        "conversation_roles": sorted(roles),
        "task_ids": ids,
        "n_unique": len(set(ids)),
    }


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------

def build_manifest(args) -> Path:
    from huggingface_hub import HfApi, hf_hub_download

    spec = DATASETS[args.dataset]
    teachers = args.teachers or spec.default_teachers
    api = HfApi()

    teacher_files, traj_sha = discover_teacher_files(api, spec, teachers)

    # Download (idempotent via HF cache) and inspect each teacher file.
    audits = {}
    for teacher, fname in teacher_files.items():
        local = hf_hub_download(spec.traj_repo, fname, repo_type="dataset",
                                revision=traj_sha)
        audits[teacher] = inspect_records(teacher, Path(local))
        a = audits[teacher]
        print(f"[audit] {teacher:18s} records={a['n_records']} "
              f"unique_ids={a['n_unique']} id_field={a['task_id_field']} "
              f"roles={a['conversation_roles']}")
        if a["n_records"] != a["n_unique"]:
            print(f"[audit]   WARNING: duplicate task IDs present")

    id_sets = {t: set(a["task_ids"]) for t, a in audits.items()}
    union = set.union(*id_sets.values())
    inter = set.intersection(*id_sets.values())
    sets_match = all(s == union for s in id_sets.values())
    print(f"[audit] union={len(union)} intersection={len(inter)} "
          f"exact_match={sets_match}")
    missing_per_teacher = {}
    for t, s in id_sets.items():
        missing = sorted(union - s)
        missing_per_teacher[t] = missing
        if missing:
            print(f"[audit] {t}: missing {len(missing)} IDs vs union "
                  f"(first 5: {missing[:5]})")

    is_default_teachers = sorted(teachers) == sorted(spec.default_teachers)
    if not sets_match and is_default_teachers and not args.allow_intersection:
        raise SystemExit(
            "Teacher task sets do not match exactly for the default teacher "
            "set; this is a hard error (EXPERIMENT_SPEC.md §2). Re-run with "
            "--allow-intersection to build the manifest from the intersection.")

    # Resolve every intersection task against the runnable-task repo.
    task_info = api.dataset_info(spec.task_repo)
    task_files = api.list_repo_files(spec.task_repo, repo_type="dataset",
                                     revision=task_info.sha)
    task_dirs = {f.split("/")[0] for f in task_files if f.startswith("task_")}
    print(f"[audit] {spec.task_repo}@{task_info.sha[:12]}: "
          f"{len(task_dirs)} task directories")
    unresolvable = sorted(inter - task_dirs)
    if unresolvable:
        print(f"[audit] {len(unresolvable)} matched tasks unresolvable in "
              f"{spec.task_repo} (first 5: {unresolvable[:5]}) — dropped")
    matched = sorted(inter - set(unresolvable))
    print(f"[audit] final matched runnable task set: {len(matched)}")

    # Per-teacher record index so records are retrievable without duplication.
    index_of = {t: {tid: i for i, tid in enumerate(a["task_ids"])}
                for t, a in audits.items()}

    header = {
        "kind": "task_manifest_header",
        "format": MANIFEST_FORMAT,
        "dataset": args.dataset,
        "traj_repo": spec.traj_repo,
        "traj_revision": traj_sha,
        "task_repo": spec.task_repo,
        "task_revision": task_info.sha,
        "teachers": teachers,
        "teacher_files": teacher_files,
        "published_rankings": spec.published_rankings,
        "published_scores": spec.published_scores,
        "layout": "one JSON file per teacher; records are "
                  "{conversations:[{from,value}...], metadata:{...}}",
        "task_id_field": {t: a["task_id_field"] for t, a in audits.items()},
        "records_per_teacher": {t: a["n_records"] for t, a in audits.items()},
        "unique_ids_per_teacher": {t: a["n_unique"] for t, a in audits.items()},
        "union_size": len(union),
        "intersection_size": len(inter),
        "task_sets_match_exactly": sets_match,
        "allow_intersection": bool(args.allow_intersection),
        "missing_vs_union_counts": {t: len(m) for t, m in missing_per_teacher.items()},
        "missing_vs_union_ids": missing_per_teacher,
        "unresolvable_in_task_repo": unresolvable,
        "matched_task_count": len(matched),
    }

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w") as f:
        f.write(json.dumps(header, sort_keys=True) + "\n")
        for tid in matched:
            row = {"task_id": tid,
                   "task_dir": tid,
                   "teacher_record_index": {t: index_of[t][tid] for t in teachers}}
            f.write(json.dumps(row, sort_keys=True) + "\n")
    print(f"[manifest] wrote {out} ({len(matched)} tasks, sha256={sha256_file(out)[:16]}…)")
    return out


def load_manifest(path: Path) -> tuple:
    with open(path) as f:
        header = json.loads(f.readline())
        rows = [json.loads(line) for line in f if line.strip()]
    if header.get("kind") != "task_manifest_header":
        raise SystemExit(f"{path}: first line is not a manifest header")
    return header, rows


# ---------------------------------------------------------------------------


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dataset", required=True, choices=sorted(DATASETS))
    p.add_argument("--teachers", nargs="+", default=None,
                   help="Override the registry's default teacher list.")
    p.add_argument("--allow-intersection", action="store_true",
                   help="Proceed on task-set mismatch by intersecting (§2).")
    p.add_argument("--out", default=str(DEFAULT_MANIFEST))
    p.add_argument("--n-tasks", type=int, default=None)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--run-id", default=None)
    args = p.parse_args()

    env = ensure_big_disk_env()
    runs_root = ensure_runs_root()
    print(f"[env] HF_HOME={env['HF_HOME']}")
    print(f"[env] runs/ -> {runs_root}")

    manifest_path = build_manifest(args)

    if args.n_tasks is not None:
        header, rows = load_manifest(manifest_path)
        run_id = args.run_id or f"{args.dataset}-n{args.n_tasks}-s{args.seed}"
        run_dir = runs_root / run_id
        sampled = write_or_check_sample(
            run_dir, args.dataset, [r["task_id"] for r in rows],
            args.n_tasks, args.seed, sha256_file(manifest_path))
        print(f"[sample] run_id={run_id} task_ids={sampled}")


if __name__ == "__main__":
    main()
