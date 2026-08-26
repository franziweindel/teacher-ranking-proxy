"""CLI:  python -m litmine run --seeds 2606.03461 [--backend both] [--no-search]
        python -m litmine paper 2606.03461          # one paper end-to-end
        python -m litmine export                    # re-export from state (no LLM calls)
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from .config import Settings
from .discovery import Discovery
from .pipeline import Pipeline


def main(argv=None):
    ap = argparse.ArgumentParser(prog="litmine")
    ap.add_argument("--work-dir", type=Path, default=None)
    ap.add_argument("--backend", default=None, help="openai|deepseek|both (env LLM_BACKEND)")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="full pipeline")
    r.add_argument("--seeds", nargs="*", default=["2606.03461"])
    r.add_argument("--no-search", action="store_true", help="seeds + citation expansion only")
    r.add_argument("--no-github", action="store_true")
    r.add_argument("--citation-depth", type=int, default=None)
    r.add_argument("--max-candidates", type=int, default=None)
    r.add_argument("--force", action="store_true", help="ignore per-paper state (LLM cache still used)")
    p = sub.add_parser("paper", help="process a single arXiv id or URL")
    p.add_argument("ref")
    p.add_argument("--force", action="store_true")
    sub.add_parser("export", help="regenerate CSV/JSONL/review/summary from state")
    d = sub.add_parser("discover", help="discovery only")
    d.add_argument("--seeds", nargs="*", default=["2606.03461"])
    d.add_argument("--citation-depth", type=int, default=None)
    a = ap.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    s = Settings()
    if a.work_dir:
        s.work_dir = a.work_dir
    if a.backend:
        s.llm_backend = a.backend

    if a.cmd == "export":
        pipe = Pipeline(s)  # LLM clients are constructed lazily; export makes no calls
        paths = pipe.export()
        print(json.dumps({k: str(v) for k, v in paths.items()}, indent=1))
        return 0
    pipe = Pipeline(s)
    if a.cmd == "discover":
        cands = pipe.discover(a.seeds, citation_depth=a.citation_depth)
        print(json.dumps(pipe.stats.get("discovery_sources"), indent=1))
        print(f"{len(cands)} candidates -> {pipe.state_dir / 'candidates.json'}")
        return 0
    if a.cmd == "paper":
        disc = Discovery(pipe.fetcher, pipe.cache)
        cand = disc.add_seed(a.ref)
        res = pipe.process_candidate(cand, force=a.force)
        pipe.upsert_records(cand.arxiv_id or cand.url, res["records"])
        paths = pipe.export()
        print(json.dumps({k: str(v) for k, v in paths.items()}, indent=1))
        print(json.dumps({"decision": res.get("decision"), "n_records": len(res["records"]),
                          "records": [{"id": r["record_id"], "status": r["classification"]["status"],
                                       "student": (r["experiment"].get("student_model") or {}).get("value"),
                                       "ranking": r["ranking"]["ranking_string"],
                                       "benchmark": r["ranking"]["benchmark"]}
                                      for r in res["records"]]}, indent=1))
        return 0
    if a.cmd == "run":
        recs = pipe.run(a.seeds, run_search=not a.no_search, max_candidates=a.max_candidates,
                        citation_depth=a.citation_depth, use_github=not a.no_github, force=a.force)
        paths = pipe.export(recs)
        print(json.dumps({"records": len(recs), **{k: str(v) for k, v in paths.items()}}, indent=1))
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
