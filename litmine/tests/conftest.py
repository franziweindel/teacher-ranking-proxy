"""Shared fixtures: a synthetic two-student paper, a fake HTTP layer serving
arXiv / Hugging Face / GitHub endpoints, and a FakeLLM responder that answers
from the *supplied* text (so tests exercise the real prompt plumbing without
hard-coding any real paper's values)."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from litmine.cache import Cache  # noqa: E402
from litmine.config import Settings  # noqa: E402
from litmine.llm import FakeLLM  # noqa: E402
from litmine.retrieval import Fetcher  # noqa: E402

ARXIV_ID = "2501.00001"

SYNTH_HTML = """<html><head><title>Synthetic Teacher Study</title></head><body>
<nav><ul><li></li><li></li></ul></nav>
<div>License: CC-BY</div>
<h1>Synthetic Teacher Study: Which Teacher Should Train a Terminal Agent?</h1>
<h2>3 Setup</h2>
<p>We collect trajectories from three teachers (T-Alpha, T-Beta and T-Gamma)
on the same 500 public tasks of the SynthBench suite
(https://huggingface.co/datasets/synth-org/synthbench-tasks). Each teacher
produces exactly one trajectory per task. Trajectories are released at
https://huggingface.co/datasets/synth-org/synth-teacher-trajectories with a
<code>teacher</code> column. Code: https://github.com/synth-org/synth-agent.</p>
<h2>4 Results</h2>
<p>We fine-tune the base Stu-1B and Stu-3B models separately on each teacher's
trajectories with an identical recipe (3 epochs, lr 1e-5, 500 trajectories each).</p>
<table><caption>Table 1: SynthBench pass@1 of students trained on each teacher.</caption>
<tr><th>Teacher</th><th>Stu-1B</th><th>Stu-3B</th></tr>
<tr><td>T-Alpha</td><td>30.0</td><td>52.0</td></tr>
<tr><td>T-Beta</td><td>41.0</td><td>52.0</td></tr>
<tr><td>T-Gamma</td><td>25.5</td><td>48.0</td></tr>
</table>
<figure><span class="ltx_tabular"><span class="ltx_tr"><span class="ltx_td">Metric</span><span class="ltx_td">Value</span></span>
<span class="ltx_tr"><span class="ltx_td">Avg turns</span><span class="ltx_td">7.1</span></span></span></figure>
<h2>5 Discussion</h2>
<p>Paragraph 0: we discuss the training dynamics of students trained on each teacher, including loss curves, gradient norms, observation-action patterns and the role of environment-grounded supervision in terminal agent trajectories.</p>
<p>Paragraph 1: we discuss the training dynamics of students trained on each teacher, including loss curves, gradient norms, observation-action patterns and the role of environment-grounded supervision in terminal agent trajectories.</p>
<p>Paragraph 2: we discuss the training dynamics of students trained on each teacher, including loss curves, gradient norms, observation-action patterns and the role of environment-grounded supervision in terminal agent trajectories.</p>
<p>Paragraph 3: we discuss the training dynamics of students trained on each teacher, including loss curves, gradient norms, observation-action patterns and the role of environment-grounded supervision in terminal agent trajectories.</p>
<p>Paragraph 4: we discuss the training dynamics of students trained on each teacher, including loss curves, gradient norms, observation-action patterns and the role of environment-grounded supervision in terminal agent trajectories.</p>
<p>Paragraph 5: we discuss the training dynamics of students trained on each teacher, including loss curves, gradient norms, observation-action patterns and the role of environment-grounded supervision in terminal agent trajectories.</p>
<p>Paragraph 6: we discuss the training dynamics of students trained on each teacher, including loss curves, gradient norms, observation-action patterns and the role of environment-grounded supervision in terminal agent trajectories.</p>
<p>Paragraph 7: we discuss the training dynamics of students trained on each teacher, including loss curves, gradient norms, observation-action patterns and the role of environment-grounded supervision in terminal agent trajectories.</p>
<p>Paragraph 8: we discuss the training dynamics of students trained on each teacher, including loss curves, gradient norms, observation-action patterns and the role of environment-grounded supervision in terminal agent trajectories.</p>
<p>Paragraph 9: we discuss the training dynamics of students trained on each teacher, including loss curves, gradient norms, observation-action patterns and the role of environment-grounded supervision in terminal agent trajectories.</p>
<p>Paragraph 10: we discuss the training dynamics of students trained on each teacher, including loss curves, gradient norms, observation-action patterns and the role of environment-grounded supervision in terminal agent trajectories.</p>
<p>Paragraph 11: we discuss the training dynamics of students trained on each teacher, including loss curves, gradient norms, observation-action patterns and the role of environment-grounded supervision in terminal agent trajectories.</p>
<h2>References</h2>
<p>[1] Some other paper. arXiv:2409.12345.</p>
</body></html>"""

ATOM = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
<entry><id>http://arxiv.org/abs/{id}v1</id><published>2025-01-02T00:00:00Z</published>
<title>{title}</title><summary>{abstract}</summary><author><name>A. Author</name></author></entry>
</feed>"""


def atom_feed(entries: list[dict]) -> bytes:
    body = "".join(
        f"<entry><id>http://arxiv.org/abs/{e['id']}v1</id><published>{e.get('year', 2025)}-01-02T00:00:00Z</published>"
        f"<title>{e['title']}</title><summary>{e.get('abstract', '')}</summary></entry>" for e in entries)
    return f'<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">{body}</feed>'.encode()


class FakeFetcher(Fetcher):
    """Serves canned responses; records every URL requested. Routes are
    (predicate_or_prefix -> (status, body_bytes, headers))."""

    def __init__(self, cache: Cache, routes: dict | None = None):
        super().__init__(cache, "test-agent", timeout=1, min_interval=0, sleep=lambda s: None)
        self.routes = routes or {}
        self.requested: list[str] = []
        self.fail_first: dict[str, int] = {}   # url prefix -> number of 503s before success

    def _open(self, url, headers):
        self.requested.append(url)
        for prefix, n in list(self.fail_first.items()):
            if url.startswith(prefix) and n > 0:
                self.fail_first[prefix] = n - 1
                return 503, b"", {}
        for prefix, resp in self.routes.items():
            if url.startswith(prefix):
                resp = resp(url) if callable(resp) else resp
                status, body, hdr = resp
                if isinstance(body, (dict, list)):
                    body = json.dumps(body).encode()
                elif isinstance(body, str):
                    body = body.encode()
                return status, body, hdr
        return 404, b"not found", {}


def default_routes() -> dict:
    hf_traj = {"id": "synth-org/synth-teacher-trajectories", "tags": ["arxiv:" + ARXIV_ID],
               "siblings": [{"rfilename": "README.md"}, {"rfilename": "data/t_alpha.jsonl"},
                            {"rfilename": "data/t_beta.jsonl"}, {"rfilename": "data/t_gamma.jsonl"}],
               "cardData": {"configs": [{"config_name": "T-Alpha"}, {"config_name": "T-Beta"},
                                        {"config_name": "T-Gamma"}]}}
    hf_tasks = {"id": "synth-org/synthbench-tasks", "tags": [],
                "siblings": [{"rfilename": "README.md"}, {"rfilename": "tasks.jsonl"}]}
    return {
        f"https://export.arxiv.org/api/query?id_list={ARXIV_ID}": (
            200, ATOM.format(id=ARXIV_ID, title="Synthetic Teacher Study",
                             abstract="We compare three teachers for SFT of terminal agents."), {}),
        f"https://arxiv.org/html/{ARXIV_ID}": (200, SYNTH_HTML, {"Content-Type": "text/html"}),
        "https://huggingface.co/api/datasets/synth-org/synth-teacher-trajectories": (200, hf_traj, {}),
        "https://huggingface.co/datasets/synth-org/synth-teacher-trajectories/raw/main/README.md": (
            200, "# Teacher trajectories\nColumns: task_id, teacher (one of T-Alpha, T-Beta, T-Gamma), messages", {}),
        "https://huggingface.co/api/datasets/synth-org/synthbench-tasks": (200, hf_tasks, {}),
        "https://huggingface.co/datasets/synth-org/synthbench-tasks/raw/main/README.md": (200, "# tasks", {}),
        "https://huggingface.co/api/datasets?search=synth-teacher": (200, [], {}),
        "https://huggingface.co/api/datasets?search=synth": (200, [], {}),
        "https://huggingface.co/api/datasets?author=synth-org": (200, [
            {"id": "synth-org/synth-teacher-trajectories"}, {"id": "synth-org/synth-teacher-trajectories-extra"},
            {"id": "synth-org/synthbench-tasks"}], {}),
        "https://huggingface.co/api/datasets/synth-org/synth-teacher-trajectories-extra": (200, {
            "id": "synth-org/synth-teacher-trajectories-extra", "tags": [],
            "siblings": [{"rfilename": "README.md"}, {"rfilename": "t_alpha_more.jsonl"}]}, {}),
        "https://huggingface.co/datasets/synth-org/synth-teacher-trajectories-extra/raw/main/README.md": (
            200, "# extra T-Alpha trajectories", {}),
        "https://api.github.com/repos/synth-org/synth-agent/git/trees": (
            200, {"tree": [{"path": "README.md", "type": "blob"}, {"path": "train.py", "type": "blob"}]}, {}),
        "https://api.github.com/repos/synth-org/synth-agent/readme": (
            200, {"content": ""}, {}),
        "https://api.github.com/repos/synth-org/synth-agent": (
            200, {"full_name": "synth-org/synth-agent", "default_branch": "main", "description": "agent"}, {}),
    }


# --------------------------------------------------------------------------
# Fake LLM responders. They parse the *supplied* prompt so that a test can
# check the document text really reached the model.
def _crit(dec, ev, loc="Section 3", conf=0.9):
    return {"decision": dec, "evidence": ev, "source_location": loc, "confidence": conf,
            "evidence_type": "explicit" if ev else "none"}


def _scores_from_text(text: str) -> dict[str, dict[str, float]]:
    """Read 'Table 1' rows '| T-x | a | b |' out of the prompt -> per-student scores."""
    out = {"Stu-1B": {}, "Stu-3B": {}}
    for m in re.finditer(r"\|\s*(T-\w+)\s*\|\s*([\d.]+)\s*\|\s*([\d.]+)\s*\|", text):
        out["Stu-1B"][m.group(1)] = float(m.group(2))
        out["Stu-3B"][m.group(1)] = float(m.group(3))
    return out


def make_responder(variant: str = "openai", overrides: dict | None = None):
    """variant: 'openai' (reference answers) | 'deepseek' (one score differs,
    one criterion unclear) | 'irrelevant' (screens everything out)."""
    overrides = overrides or {}

    def responder(stage: str, system: str, user: str):
        if stage in overrides:
            return overrides[stage](user) if callable(overrides[stage]) else overrides[stage]
        if stage == "prescreen":
            rel = "no" if variant == "irrelevant" else "yes"
            return {"relevant": rel, "reason": "abstract mentions teachers", "confidence": 0.8}
        if stage == "screen":
            if variant == "irrelevant" or "T-Alpha" not in user:
                return {"criteria": {k: _crit("no", "no teachers mentioned") for k in
                                     ("agentic", "multiple_teachers", "same_student_separate_sft", "per_teacher_scores")},
                        "relevant": "no", "summary": "off topic", "candidate_experiments": []}
            return {"criteria": {
                "agentic": _crit("yes", "terminal agent trajectories"),
                "multiple_teachers": _crit("yes", "three teachers (T-Alpha, T-Beta and T-Gamma)"),
                "same_student_separate_sft": _crit("yes", "fine-tune ... separately on each teacher"),
                "per_teacher_scores": _crit("yes", "Table 1", "Table 1")},
                "relevant": "yes", "summary": "controlled teacher comparison",
                "candidate_experiments": ["Table 1: Stu-1B and Stu-3B on three teachers"]}
        if stage == "extract":
            scores = _scores_from_text(user)
            exps = []
            for student in ("Stu-1B", "Stu-3B"):
                st = {t: {"value": v, "evidence": f"{t} row", "source_location": "Table 1"}
                      for t, v in scores[student].items()}
                if variant == "deepseek" and student == "Stu-1B" and "T-Beta" in st:
                    st["T-Beta"]["value"] = 40.0     # deliberate disagreement
                crit = {k: _crit("yes", "see Section 3/4") for k in (
                    "agentic", "public_tasks", "multiple_teachers", "same_tasks_across_teachers",
                    "teacher_identity_known", "trajectories_public", "same_student_separate_sft",
                    "sft_recipe_controlled", "per_teacher_scores")}
                if variant == "deepseek" and student == "Stu-3B":
                    crit["sft_recipe_controlled"] = _crit("unclear", "")
                ev = lambda v, e="Section 3", l="Section 3": {"value": v, "evidence": e, "source_location": l}
                exps.append({
                    "experiment_label": f"{student} / 3 teachers", "agent_domain": "terminal",
                    "student_model": ev(student, "fine-tune the base Stu-1B and Stu-3B", "Section 4"),
                    "student_hf_id": ev(None, ""), "student_model_type": ev("base", "base Stu-1B"),
                    "teacher_models": [{"name": t, "evidence": "three teachers", "source_location": "Section 3"}
                                       for t in scores[student]],
                    "task_dataset": ev("SynthBench", "500 public tasks of the SynthBench suite"),
                    "task_dataset_hf_id": ev("synth-org/synthbench-tasks", "huggingface.co/datasets/synth-org/synthbench-tasks"),
                    "environment_repo": ev("https://github.com/synth-org/synth-agent", "Code:"),
                    "same_tasks_across_teachers": ev("yes", "on the same 500 public tasks"),
                    "trajectory_dataset": ev("synth teacher trajectories", "Trajectories are released"),
                    "trajectory_dataset_hf_id": ev("synth-org/synth-teacher-trajectories", "released at huggingface.co/..."),
                    "trajectories_public": ev("yes", "Trajectories are released"),
                    "teacher_identity_per_trajectory": ev("yes", "with a teacher column"),
                    "num_tasks": ev(500, "500 public tasks"),
                    "trajectories_per_teacher": ev("1 per task", "exactly one trajectory per task"),
                    "sft_data_budget": ev("500 trajectories", "500 trajectories each", "Section 4"),
                    "sft_recipe": ev("3 epochs, lr 1e-5", "identical recipe", "Section 4"),
                    "sft_recipe_controlled": ev("yes", "identical recipe", "Section 4"),
                    "evaluation_benchmarks": ev(["SynthBench"], "Table 1", "Table 1"),
                    "downstream_scores": {"SynthBench": st}, "primary_benchmark": "SynthBench",
                    "github_repo": ev("https://github.com/synth-org/synth-agent", "Code:"),
                    "project_page": ev(None, ""), "criteria": crit, "exclusion_flags": [],
                    "notes": "synthetic"})
            return {"paper_summary": "synthetic", "experiments": exps, "no_qualifying_experiment_reason": ""}
        if stage == "artifact":
            # decide from the artifact material (files/README), not the paper's teacher list
            material = user.split("ARTIFACT METADATA", 1)[-1]
            has_teacher = "t_alpha" in material or "T-Alpha" in material
            return {"teacher_identity_represented": _crit("yes" if has_teacher else "unclear",
                                                          "teacher column" if has_teacher else "", "README"),
                    "teacher_subsets_separable": _crit("yes" if has_teacher else "unclear",
                                                       "per-teacher files" if has_teacher else "", "files"),
                    "task_ids_recoverable": _crit("yes", "task_id column", "README"),
                    "matches_paper_description": _crit("yes", "id matches", "metadata"),
                    "teacher_names_found": ["T-Alpha", "T-Beta", "T-Gamma"] if has_teacher else [],
                    "artifact_role": "trajectories" if has_teacher else "tasks",
                    "notes": ""}
        if stage == "adjudicate":
            res = {}
            if "downstream_scores.tbeta" in user:
                res["downstream_scores.tbeta"] = {"decision": "openai", "resolved_value": 41.0,
                                                  "evidence": "| T-Beta | 41.0 | 52.0 |",
                                                  "source_location": "Table 1", "confidence": 0.95}
            if "criteria.sft_recipe_controlled" in user:
                res["criteria.sft_recipe_controlled"] = {"decision": "unclear", "resolved_value": None,
                                                         "evidence": "", "source_location": "", "confidence": 0.3}
            return {"resolutions": res, "summary": "resolved from Table 1"}
        raise AssertionError(f"unexpected stage {stage}")
    return responder


@pytest.fixture
def work(tmp_path):
    s = Settings()
    s.work_dir = tmp_path / "work"
    s.llm_backend = "fake"
    return s


@pytest.fixture
def cache(work):
    return Cache(work.cache_dir)


@pytest.fixture
def fetcher(cache):
    return FakeFetcher(cache, default_routes())


@pytest.fixture
def fake_openai(cache):
    return FakeLLM(cache, make_responder("openai"), name="openai")


@pytest.fixture
def fake_deepseek(cache):
    return FakeLLM(cache, make_responder("deepseek"), name="deepseek")
