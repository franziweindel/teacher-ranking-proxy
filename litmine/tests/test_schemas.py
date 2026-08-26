import pytest

from litmine.schemas import (SchemaError, validate_adjudication, validate_criterion,
                             validate_extraction, validate_screening, CRITERION_KEYS)


def test_unclear_stays_unclear_and_bad_decisions_become_unclear():
    notes = []
    assert validate_criterion({"decision": "unclear", "evidence": ""}, notes, "x")["decision"] == "unclear"
    assert validate_criterion({"decision": "probably", "evidence": "e"}, notes, "x")["decision"] == "unclear"
    assert validate_criterion("yes", notes, "x")["decision"] == "unclear"
    assert notes


def test_yes_without_evidence_is_downgraded():
    notes = []
    c = validate_criterion({"decision": "yes", "evidence": "", "confidence": 1.7}, notes, "k")
    assert c["decision"] == "unclear" and "without evidence" in notes[0]


def test_screening_requires_relevant_and_criteria():
    with pytest.raises(SchemaError):
        validate_screening({"relevant": "yes"})
    good = {"criteria": {k: {"decision": "yes", "evidence": "e"} for k in
                         ("agentic", "multiple_teachers", "same_student_separate_sft", "per_teacher_scores")},
            "relevant": "maybe"}
    with pytest.raises(SchemaError):
        validate_screening(good)
    good["relevant"] = "unclear"
    assert validate_screening(good)["relevant"] == "unclear"


def test_extraction_shape_and_score_cleaning():
    with pytest.raises(SchemaError):
        validate_extraction({"experiments": "nope"})
    out = validate_extraction({"experiments": [{
        "student_model": {"value": "S", "evidence": "s", "source_location": "1"},
        "teacher_models": ["A", {"name": "B", "evidence": "b"}],
        "num_tasks": {"value": "12", "evidence": "n"},
        "downstream_scores": {"bench": {"A": {"value": "31.8%", "evidence": "t", "source_location": "T1"},
                                        "B": "n/a", "C": 29.4}},
        "criteria": {"agentic": {"decision": "yes", "evidence": "x"}},
    }]})
    e = out["experiments"][0]
    assert [t["name"] for t in e["teacher_models"]] == ["A", "B"]
    assert e["num_tasks"]["value"] == 12
    assert e["downstream_scores"]["bench"]["A"]["value"] == 31.8
    assert "B" not in e["downstream_scores"]["bench"]          # non-numeric dropped
    assert e["downstream_scores"]["bench"]["C"]["value"] == 29.4
    assert set(e["criteria"]) == set(CRITERION_KEYS)
    assert all(e["criteria"][k]["decision"] == "unclear" for k in CRITERION_KEYS if k != "agentic")
    assert any("lacks provenance" in n for n in out["validation_notes"])


def test_adjudication_without_evidence_is_unclear():
    out = validate_adjudication({"resolutions": {"f": {"decision": "openai", "resolved_value": 1, "evidence": ""},
                                                 "g": {"decision": "deepseek", "evidence": "quoted"}}})
    assert out["resolutions"]["f"]["decision"] == "unclear"
    assert out["resolutions"]["g"]["decision"] == "deepseek"
