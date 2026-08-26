from litmine.classify import classify, rank_columns, rank_from_scores, ranking_string
from litmine.schemas import CRITERION_KEYS


def crit(**over):
    d = {k: {"decision": "yes", "evidence": "e"} for k in CRITERION_KEYS}
    for k, v in over.items():
        d[k] = {"decision": v, "evidence": "e" if v != "unclear" else ""}
    return d


ART_OK = {"tasks": {"verified": True}, "trajectories": {"verified": True, "teacher_separable": "yes"}}


def test_ranking_is_deterministic_and_preserves_ties():
    scores = {"B": 29.4, "A": 31.8, "C": 29.4, "D": 24.7}
    r = rank_from_scores(scores)
    assert [(x["rank"], x["teacher"]) for x in r] == [(1, "A"), (2, "B"), (2, "C"), (4, "D")]
    assert ranking_string(r) == "A > B = C > D"
    cols = rank_columns(r)
    assert cols == {"teacher_rank_1": "A", "teacher_rank_2": "B = C", "teacher_rank_3": "", "teacher_rank_4": "D"}
    assert rank_from_scores(dict(reversed(list(scores.items())))) == r   # input order irrelevant
    assert rank_from_scores({"x": 1.0, "y": 2.0}, higher_is_better=False)[0]["teacher"] == "x"


def test_gold_requires_verified_artifacts():
    assert classify(crit(), num_teachers=4, scores_complete=True, artifact_verification=ART_OK)["status"] == "gold"
    # claims public but nothing verified -> silver at best, never gold
    r = classify(crit(), num_teachers=4, scores_complete=True,
                 artifact_verification={"tasks": {"verified": None}, "trajectories": {"verified": None}})
    assert r["status"] == "silver" and any("not fully verified" in x for x in r["reasons"])


def test_silver_for_two_teachers_or_unmatched_tasks():
    assert classify(crit(), num_teachers=2, scores_complete=True, artifact_verification=ART_OK)["status"] == "silver"
    r = classify(crit(same_tasks_across_teachers="no"), num_teachers=3, scores_complete=True,
                 artifact_verification=ART_OK)
    assert r["status"] == "silver"


def test_results_only_when_not_public():
    r = classify(crit(trajectories_public="no"), num_teachers=3, scores_complete=True, artifact_verification=None)
    assert r["status"] == "results_only"
    r = classify(crit(trajectories_public="unclear", public_tasks="unclear"), num_teachers=3,
                 scores_complete=True, artifact_verification=None)
    assert r["status"] == "results_only"


def test_reject_on_core_failure():
    assert classify(crit(same_student_separate_sft="no"), num_teachers=3, scores_complete=True,
                    artifact_verification=ART_OK)["status"] == "reject"
    assert classify(crit(), num_teachers=1, scores_complete=True, artifact_verification=ART_OK)["status"] == "reject"


def test_uncertainty_goes_to_review_not_forced():
    r = classify(crit(sft_recipe_controlled="unclear"), num_teachers=3, scores_complete=True,
                 artifact_verification=ART_OK)
    assert r["status"] == "needs_review" and "sft_recipe_controlled" in r["review_flags"][0]
    r = classify(crit(), num_teachers=3, scores_complete=False, artifact_verification=ART_OK)
    assert r["status"] == "needs_review" and "missing teacher-specific" in r["review_flags"][0]
    # claimed public but artifact missing
    r = classify(crit(), num_teachers=3, scores_complete=True,
                 artifact_verification={"tasks": {"verified": True}, "trajectories": {"verified": False}})
    assert r["status"] == "needs_review" and "could not be found" in r["review_flags"][0]
    # teacher identity not recoverable from released data
    r = classify(crit(), num_teachers=3, scores_complete=True,
                 artifact_verification={"tasks": {"verified": True},
                                        "trajectories": {"verified": True, "teacher_separable": "no"}})
    assert r["status"] == "needs_review" and "teacher identity" in r["review_flags"][0]
    r = classify(crit(), num_teachers=3, scores_complete=True, artifact_verification=ART_OK,
                 exclusion_flags=["teacher used only as judge"])
    assert r["status"] == "needs_review"
