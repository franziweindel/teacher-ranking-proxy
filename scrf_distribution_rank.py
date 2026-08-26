#!/usr/bin/env python3
"""Teacher-level SCRF ranking by distribution distance (candidate to the
weighted-sum SCRF).

Two normalized distributions over TB2.0 error categories:
  P_S(e) = student errors of category e that the recovery judge marked NOT
           recovered, / total student not-recovered errors
  P_T(e) = teacher errors of category e that the judge marked recovered,
           / total teacher recovered errors  (pooled over all tasks)

A teacher is good if its recovered-error distribution covers the categories the
student fails and cannot fix. Reported per teacher, at 11-category and
91-subcategory granularity:
  overlap    = sum_e P_S(e) * P_T(e)                       (higher = better)
  kl         = KL(P_S || P_T_smoothed)                     (lower  = better)
  neg_kl     = -kl                                          (higher = better; ranked)
Smoothing: add-epsilon over the union of categories before normalizing P_T.

  python scrf_distribution_rank.py --run-id terminal_lego-n200-s42 --judge-tag @gpt-oss-120b
"""
from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _cat(key: str) -> str:
    return key.split(" :: ")[0]


def _norm(d: dict, keys, eps: float):
    z = sum(d.get(k, 0.0) + eps for k in keys)
    return {k: (d.get(k, 0.0) + eps) / z for k in keys}


def _kl(p: dict, q: dict) -> float:
    return sum(p[k] * math.log(p[k] / q[k]) for k in p if p[k] > 0)


def rank(run: Path, student: str, tag: str, eps: float = 1e-6):
    rows = [json.loads(l) for l in open(
        run / "proxy_scores" / student.replace("/", "__") / f"scrf{tag}.jsonl")]
    profile = json.loads((run / "judge_cache" / "student_error_profile.json").read_text())
    # student NOT-recovered counts per subcategory:
    # counts * (1 - local recovery rate)
    counts = profile["student_error_counts"]
    rrate = profile["student_local_recovery_rate_R_S"]
    student_unrec = {k: counts[k] * (1.0 - rrate.get(k, 0.0)) for k in counts}
    # teacher recovered counts per subcategory, pooled over tasks:
    # occurrences = E_T * cmd_segments; recovered = R_T * occurrences
    teacher_rec = defaultdict(lambda: defaultdict(float))
    for r in rows:
        n = r.get("cmd_segments") or 0
        for key, c in (r.get("components") or {}).items():
            occ = c["E_T"] * n
            teacher_rec[r["teacher"]][key] += c["R_T"] * occ
    out = {}
    for gran, keyfn in (("subcategory", lambda k: k), ("category", _cat)):
        def fold(d):
            f = defaultdict(float)
            for k, v in d.items():
                f[keyfn(k)] += v
            return f
        ps_counts = fold(student_unrec)
        teachers = {t: fold(teacher_rec[t]) for t in teacher_rec}
        keys = set(ps_counts) | {k for t in teachers.values() for k in t}
        ps = _norm(ps_counts, keys, eps)
        res = {}
        for t, tc in teachers.items():
            pt = _norm(tc, keys, eps)
            overlap = sum(ps[k] * pt[k] for k in keys)
            kl = _kl(ps, pt)
            res[t] = {"overlap": overlap, "kl": kl, "neg_kl": -kl}
        order_overlap = sorted(res, key=lambda t: -res[t]["overlap"])
        order_kl = sorted(res, key=lambda t: res[t]["kl"])
        out[gran] = {"per_teacher": res,
                     "order_by_overlap": order_overlap,
                     "order_by_kl": order_kl}
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--student", default="Qwen/Qwen3-8B")
    ap.add_argument("--judge-tag", default="@gpt-oss-120b")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    run = (HERE / "runs" / args.run_id).resolve()
    res = rank(run, args.student, args.judge_tag)
    out = Path(args.out or run / f"scrf_distribution_rank{args.judge_tag}.json")
    out.write_text(json.dumps(res, indent=2) + "\n")
    for gran, r in res.items():
        print(f"[{gran}] by overlap: {' > '.join(t.split()[0] for t in r['order_by_overlap'])}"
              f"  |  by KL: {' > '.join(t.split()[0] for t in r['order_by_kl'])}")
    print(f"[scrf-dist] -> {out}")


if __name__ == "__main__":
    main()
