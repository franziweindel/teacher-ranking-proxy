#!/usr/bin/env python3
"""Error-category distributions behind SCRF, for inspection (PROXY_SPEC §9.1-9.3).

One panel per agent (the student + each teacher): per TB2.0 top-level
category, the number of failed commands, split into recovered / not recovered
by the K=3 recovery judge. Student counts come from
judge_cache/student_error_profile.json, teacher counts are aggregated from the
per-task components in proxy_scores/<student>/scrf<tag>.jsonl.

  python plot_scrf_errors.py --run-id terminal_lego-n200-s42 --judge-tag @gpt-oss-120b
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--student", default="Qwen/Qwen3-8B")
    ap.add_argument("--judge-tag", default="@gpt-oss-120b")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    run = (HERE / "runs" / args.run_id).resolve()
    rows = [json.loads(l) for l in open(
        run / "proxy_scores" / args.student.replace("/", "__") / f"scrf{args.judge_tag}.jsonl")]
    profile = json.loads((run / "judge_cache" / "student_error_profile.json").read_text())

    def top(key):  # "Category :: subcategory" -> "Category"
        return key.split(" :: ")[0]

    # teacher: category -> [failed, recovered]
    teachers = defaultdict(lambda: defaultdict(lambda: [0.0, 0.0]))
    for r in rows:
        n = r.get("cmd_segments") or 0
        for key, c in (r.get("components") or {}).items():
            occ = c["E_T"] * n
            teachers[r["teacher"]][top(key)][0] += occ
            teachers[r["teacher"]][top(key)][1] += c["R_T"] * occ
    student = defaultdict(lambda: [0.0, 0.0])
    for key, cnt in profile["student_error_counts"].items():
        rec = profile["student_local_recovery_rate"].get(key, 0.0)
        student[top(key)][0] += cnt
        student[top(key)][1] += cnt * rec
    panels = [("Student " + args.student, student)] + [
        (t, teachers[t]) for t in teachers]
    cats = sorted({c for _, d in panels for c in d},
                  key=lambda c: -sum(d[c][0] for _, d in panels))

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(len(panels), 1, figsize=(12, 3.2 * len(panels)), sharex=True)
    for ax, (name, d) in zip(axes, panels):
        failed = [d[c][0] for c in cats]
        recov = [d[c][1] for c in cats]
        unrec = [f - r for f, r in zip(failed, recov)]
        x = range(len(cats))
        ax.bar(x, recov, color="#4c9a2a", label="recovered (K=3 judge)")
        ax.bar(x, unrec, bottom=recov, color="#c0392b", label="not recovered")
        total = sum(failed)
        ax.set_title(f"{name}: {total:.0f} failed commands "
                     f"({sum(recov)/total*100 if total else 0:.0f}% recovered)")
        ax.set_ylabel("failed commands")
        ax.legend(loc="upper right", fontsize=8)
    axes[-1].set_xticks(range(len(cats)))
    axes[-1].set_xticklabels(cats, rotation=35, ha="right", fontsize=8)
    fig.suptitle(f"Command failures by TB2.0 category, judge {args.judge_tag.lstrip('@')}, run {args.run_id}")
    fig.tight_layout()
    out = Path(args.out or run / f"scrf_error_distributions{args.judge_tag}.png")
    fig.savefig(out, dpi=130)
    print(f"[plot] -> {out}")


if __name__ == "__main__":
    main()
