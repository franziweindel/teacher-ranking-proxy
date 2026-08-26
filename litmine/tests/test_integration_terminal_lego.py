"""Integration test on a real paper (Terminal-Lego, arXiv:2606.03461) with the
real LLM backend(s). Runs only with LITMINE_INTEGRATION=1 and API keys.

It checks that the automated pipeline *recovers the required structure* from
the source; it does not assert any specific published number or model name.
"""
import os
import re

import pytest

from litmine.config import Settings
from litmine.discovery import Discovery
from litmine.multimodel import norm_name
from litmine.pipeline import Pipeline

pytestmark = pytest.mark.skipif(os.environ.get("LITMINE_INTEGRATION") != "1",
                                reason="set LITMINE_INTEGRATION=1 (needs network + API keys)")

SEED = os.environ.get("LITMINE_SEED", "2606.03461")


@pytest.fixture(scope="module")
def result():
    s = Settings()
    if os.environ.get("LITMINE_INTEGRATION_WORK_DIR"):
        s.work_dir = __import__("pathlib").Path(os.environ["LITMINE_INTEGRATION_WORK_DIR"])
    p = Pipeline(s)
    cand = Discovery(p.fetcher, p.cache).add_seed(SEED)
    return p, p.process_candidate(cand)


def test_full_text_retrieved(result):
    p, res = result
    assert res["text_format"] in ("arxiv_html", "pdf")


def test_screening_is_structured_and_positive(result):
    p, res = result
    assert res["decision"] == "extract"
    for name, sc in res["screening"].items():
        assert sc["relevant"] in ("yes", "unclear")
        for k, c in sc["criteria"].items():
            assert c["decision"] in ("yes", "no", "unclear")
            if c["decision"] != "unclear":
                assert c["evidence"], f"{name}:{k} decided without evidence"


def test_experiments_recovered_with_scores_and_provenance(result):
    p, res = result
    recs = res["records"]
    assert len(recs) >= 1
    doc_text = p.retrieve(Discovery(p.fetcher, p.cache).add_seed(SEED))["text"]
    norm_doc = norm_name(doc_text)
    for r in recs:
        e = r["experiment"]
        assert e["student_model"]["value"] and e["student_model"]["evidence"]
        teachers = [t["name"] for t in e["teacher_models"]]
        assert len(teachers) >= 2
        # every extracted teacher/student name must literally occur in the source (grounding)
        for name in teachers + [e["student_model"]["value"]]:
            core = re.sub(r"\s*\(.*?\)\s*", "", name)     # extractor may qualify, e.g. "X (passed)"
            assert norm_name(core) in norm_doc, f"{name!r} not found in source text"
        bench, scores = r["ranking"]["benchmark"], r["ranking"]["scores"]
        assert bench and len(scores) >= 2
        assert all(isinstance(v, float) for v in scores.values())
        for t, s in e["downstream_scores"][bench].items():
            assert s["source_location"] or s["evidence"], f"score for {t} lacks provenance"
            assert re.search(re.escape(f"{s['value']:g}"), doc_text), f"score {s['value']} for {t} not in source"
        # ranking derived from the scores, ties preserved
        ordered = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
        assert [x["teacher"] for x in r["ranking"]["ranking"]] == [k for k, _ in ordered]
        assert r["classification"]["status"] in ("gold", "silver", "results_only", "needs_review", "reject")
        assert r["provenance"]["doc_hash"] and r["provenance"]["llm"]


def test_multiple_students_become_multiple_records_if_present(result):
    """If the paper reports the comparison for several students, each must be
    its own record (structure check; count is not hard-coded)."""
    p, res = result
    students = {r["experiment"]["student_model"]["value"] for r in res["records"]}
    assert len(students) == len({norm_name(s) for s in students})
    labels = [r["experiment"]["experiment_label"] for r in res["records"]]
    assert len(labels) == len(set(labels))


def test_artifacts_were_actually_checked(result):
    p, res = result
    for r in res["records"]:
        e, av = r["experiment"], r["artifact_verification"]
        claims = (e["trajectories_public"]["value"] == "yes" or e["trajectory_dataset_hf_id"]["value"]
                  or e["github_repo"]["value"])
        if claims:
            assert av["checked"], "paper claims artifacts but nothing was inspected"
            for c in av["checked"]:
                assert c["url"] and c["exists"] in (True, False)
