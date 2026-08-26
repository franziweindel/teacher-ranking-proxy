import json

from conftest import make_responder
from litmine.multimodel import _align, diff_experiments, merge_with_adjudication, primary_scores, teacher_set
from litmine.schemas import validate_extraction

TABLE = "| T-Alpha | 30.0 | 52.0 |\n| T-Beta | 41.0 | 52.0 |\n| T-Gamma | 25.5 | 48.0 |"


def _exps(variant):
    return validate_extraction(make_responder(variant)("extract", "", TABLE))["experiments"]


def test_primary_scores_picks_declared_or_largest():
    e = _exps("openai")[0]
    assert primary_scores(e) == ("SynthBench", {"T-Alpha": 30.0, "T-Beta": 41.0, "T-Gamma": 25.5})
    e["primary_benchmark"] = ""
    e["downstream_scores"]["Other"] = {"T-Alpha": {"value": 1, "evidence": "", "source_location": ""}}
    assert primary_scores(e)[0] == "SynthBench"


def test_align_and_diff():
    a, b = _exps("openai"), _exps("deepseek")
    pairs = _align(a, b)
    assert sorted(pairs) == [(0, 0), (1, 1)]
    d0 = diff_experiments(a[0], b[0])
    assert d0 == {"downstream_scores.tbeta": {"a": 41.0, "b": 40.0}}
    d1 = diff_experiments(a[1], b[1])
    assert d1 == {"criteria.sft_recipe_controlled": {"a": "yes", "b": "unclear"}}
    # paraphrased descriptive text is not a dispute; different numbers are
    from litmine.multimodel import _cmp_loose
    assert _cmp_loose("8.1K trajectories per teacher", "8.1K trajectories")
    assert not _cmp_loose("8.1K trajectories", "1.7K trajectories") and not _cmp_loose(None, "x")
    # unmatched experiment surfaces as (i, None)
    assert (2, None) in _align(a + [dict(a[0], student_model={"value": "Other-7B", "evidence": "", "source_location": ""},
                                          teacher_models=[])], b)


def test_merge_applies_resolutions_and_flags_unresolved():
    a, b = _exps("openai"), _exps("deepseek")
    d0 = diff_experiments(a[0], b[0])
    adj = {"result": {"resolutions": {"downstream_scores.tbeta": {
        "decision": "deepseek", "resolved_value": None, "evidence": "q", "source_location": "T1", "confidence": .9}}}}
    m, unresolved = merge_with_adjudication(("openai", "deepseek"), a[0], b[0], d0, adj)
    assert unresolved == [] and m["downstream_scores"]["SynthBench"]["T-Beta"]["value"] == 40.0
    assert m["downstream_scores"]["SynthBench"]["T-Beta"]["adjudicated_from"] == "adjudicator:deepseek"
    # 'neither' with a corrected value
    adj["result"]["resolutions"]["downstream_scores.tbeta"].update(decision="neither", resolved_value=42.5)
    m, _ = merge_with_adjudication(("openai", "deepseek"), a[0], b[0], d0, adj)
    assert m["downstream_scores"]["SynthBench"]["T-Beta"]["value"] == 42.5
    # unclear -> value removed and listed as unresolved; original inputs untouched
    m, unresolved = merge_with_adjudication(("openai", "deepseek"), a[0], b[0], d0, None)
    assert unresolved == ["downstream_scores.tbeta"]
    assert "T-Beta" not in m["downstream_scores"]["SynthBench"]
    assert a[0]["downstream_scores"]["SynthBench"]["T-Beta"]["value"] == 41.0
    # criterion disagreement left unclear
    d1 = diff_experiments(a[1], b[1])
    m1, un1 = merge_with_adjudication(("openai", "deepseek"), a[1], b[1], d1, None)
    assert m1["criteria"]["sft_recipe_controlled"]["decision"] == "unclear" and un1
