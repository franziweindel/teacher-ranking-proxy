# litmine — implementation & validation report (2026-08-26)

Implementation of `PIPELINE.md` lives in `teacher_ranking_proxy/litmine/`
(package `litmine/`, tests in `tests/`, README with usage). Own venv:
`litmine/.venv` (Python 3.12, pypdf, pytest). Work dir: `litmine/work/`
(cache, state, outputs — gitignored).

## Stage validation (PIPELINE.md §12)

| check | how it was tested | result |
|---|---|---|
| candidate discovery retrieves relevant papers | real run: 156 arXiv boolean queries + 9 OpenAlex queries + 6 HF + 3 GitHub queries + S2/OpenAlex citation expansion from seed 2606.03461 | 237 candidates; Terminal-Lego surfaced from an OpenAlex query independently of the seed; S2 gave 24 refs + 2 citations; HF/GitHub work but yield few arXiv-tagged hits |
| PDF/HTML retrieval | unit (fake HTTP: HTML→PDF→abstract fallback, truncation, 503 retry) + real arXiv HTML for 2606.03461 | 79 k chars, tables preserved as pipe rows (Table 1/2 of Terminal-Lego verified by eye) |
| paper text passed correctly to the LLM | unit: extraction prompt must contain the table row from the fixture; screening hints forwarded | pass |
| structured outputs validate against schemas | unit: invalid decisions → `unclear`, `yes` without evidence → `unclear`, non-numeric scores dropped, missing criteria → `unclear`, re-ask on invalid JSON | pass |
| unclear evidence remains unclear | unit (classify: unclear core criterion → `needs_review`) + real run (public_tasks/trajectories_public stay `unclear`; adjudicator returns `unclear` for student_model_type) | pass |
| multiple experiments per paper | unit (two students → two records) + real (Terminal-Lego: 8B/32B × 8.1K/1.7K = 4 records from both backends; DeepSeek additionally proposed 2 "5-teacher" variants → flagged `extracted by deepseek only`) | pass |
| artifact verification | unit (fake HF/GitHub APIs, 404 → review) + real: project page → GitHub `SWE-Lego/terminal-lego` → HF `StephYang/Terminal-Lego-15k` (judged *tasks*), `SWE-Lego/Terminal-Lego-Traj-Deepseek-V3-2-15k` (judged *trajectories*, one teacher → separability `unclear`), `StephYang/Terminal-Lego-Qwen3-8B` (model) | pass; gold correctly withheld |
| citations/evidence survive export | unit: snippets + table locations in CSV `paper_evidence`/`table_or_section`, full evidence in JSONL | pass |
| rankings derived from scores, ties preserved | unit (competition ranking `A > B = C > D`, rank columns) + real (`DeepSeek-V3.2 > GLM-5 = Qwen3.5-Plus > Claude Opus 4.6` for Qwen3-8B/8.1K) | pass |
| API failures retry safely | unit: 429/503 retried with backoff, 400 not retried, gives up after N, per-paper failure does not abort the run | pass |
| cache/resume | unit: second run makes 0 LLM calls; changing `EXTRACTION_VERSION` reruns only extraction; cached raw output is re-validated when a schema changes | pass |
| CSV/JSONL reproducible | unit: byte-identical across re-runs and export-only | pass |
| multi-model verification | real: OpenAI (gpt-5.5) vs DeepSeek (deepseek-v4-flash) disagreed on 4 fields per Terminal-Lego record; adjudicator (gpt-5.5) resolved 2–4 with quoted evidence, left `student_model_type` / `criteria.public_tasks` unresolved for 2 records → `needs_review` | pass |

Unit suite: `44 passed` (~6 s). Integration suite (real LLMs, structure-only
assertions, no hard-coded Terminal-Lego values): `5 passed`.

## Bounded end-to-end run (`run --seeds 2606.03461 --max-candidates 10`, `LLM_BACKEND=both`)

- 237 discovered → 237 prescreened (gpt-5.5 on title/abstract: 1 yes, 74 unclear, 162 no)
  → 10 full-text screened by both backends (seed + top prescreen) → 1 paper extracted
  → 6 records: **2 silver, 4 needs_review**, 0 gold.
- Terminal-Lego is not gold because (a) only the DeepSeek-V3.2 trajectories are
  released on HF, so per-teacher separability is `unclear`, and (b) the paper
  text never states public availability (`trajectories_public = unclear`).
- 9 other papers were screened out with evidence-based reasons (single teacher,
  RL-only teacher use, mixed data, no per-teacher SFT results). One worth a
  human glance: `2605.16604` (R2V Agent) — screening noted multiple teachers
  for TerminalBench trajectories but no controlled per-teacher SFT comparison.
- An earlier (pre-fix) run also extracted `2407.18219` and `2410.05434`
  (2-teacher / single-teacher setups): single-teacher records were `reject`ed
  on `multiple_teachers = no`; the 2-teacher ones went to `needs_review`
  (unverified artifacts, unresolved disagreements). Their per-paper state is
  cached under `work/state/papers/`.

## Known limitations / next steps

- The prescreen is inclusive by design (74 `unclear`), so a full run needs
  `--max-candidates` ≥ 75 (~2 × 75 full-text screenings ≈ 3–4 M input tokens).
- OpenAlex free-text search is noisy (motion-trajectory papers etc.); the
  prescreen absorbs this at small cost.
- DOI-only candidates (no arXiv id) are fetched from the landing page; many
  publishers block this, and such papers fall back to abstract-only screening.
- Semantic Scholar without `S2_API_KEY` is rate-limited; set it for reliable
  citation expansion. `GITHUB_TOKEN` raises the GitHub search quota.
