# Teacher-ranking proxy

**Goal:** compare existing proxies from the instruction / reasoning-distillation
literature that aim to predict which teacher produces the best SFT data for a
given student, from trajectories only, without running SFT.

**Relation to OpenThoughts-Agent.** This lives as `data/teacher_ranking_proxy/`
inside the `OpenThoughts-Agent` repo. Trajectory generation (Stage 2,
`generate_trajectories.py`, the `apptainer_patch/` shim, `bridge_deploy/`,
`harbor_patches.py`) imports Harbor and the repo's HPC stack, so it only runs
embedded there. The scoring and evaluation (Stages 3-4:
`compute_proxies.py`, `evaluate_ranking.py`, `judge.py`) are self-contained
(transformers/vLLM plus the pinned author repos under `upstream/`) and run on
existing trajectory files without the parent repo. The public standalone repo
is a snapshot/mirror of this folder.

## Implemented proxies

- TOR / EGS: https://arxiv.org/pdf/2606.03461
- GRAPE: https://arxiv.org/pdf/2502.04194
- LALP (local naturalness): https://arxiv.org/pdf/2510.03988
- ASLEC: https://arxiv.org/pdf/2604.06834
- RSR: https://aclanthology.org/2026.acl-long.1950/
- GRACE: https://arxiv.org/abs/2511.02833
- SCAS: https://arxiv.org/abs/2605.26872 (forward-only relative of GRACE: same gradient-decomposition idea, approximated from one forward pass)
- traj_length, teacher_bench, cmd_error (error rate), error_retry: simple controls / the Terminal-Bench 2.0 command-error taxonomy (https://arxiv.org/abs/2601.11868)

Listed in the spec but not implemented: CAR (https://arxiv.org/pdf/2411.07133,
needs an average reward we do not have with one trajectory per teacher/task),
LARK (https://arxiv.org/pdf/2605.30651), PerSyn (needs router
training).

For what each proxy computes and how, see `docs/PROXY_SPEC.md`.

## Proposed proxy for agentic tasks (SCRF)

Hypothesis: a useful teacher is one whose trajectories demonstrate
recovery behavior in the regions where the target student empirically
struggles.

Pipeline (same for student and teacher trajectories):

1. Extract (command, output) pairs from the trajectory.
2. Drop turns Harbor could not execute because the model's response was not valid Terminus-2 JSON (a parse error, no command ran); those are counted separately, not as command errors.
3. Classify each (command, output) with the Terminal-Bench 2.0 prompt and error taxonomy (11 categories, 91 subcategories) into one error category or "no error".
4. For the pairs classified as errors, a second LLM-as-judge call decides whether the model recovered, given the failed command and its output plus the next 3 commands and outputs (K=3).

This gives, per agent, a distribution over error categories and, within each, a
recovered / not-recovered split. A teacher is scored by how well its
recovered-error demonstrations cover the categories the student fails and does
not recover from.

Notation, per error category e: q_S(e) = fraction of the student's commands
that fail with e (student error density); E_T(e) = fraction of the teacher's
commands that fail with e (teacher error density); R_T(e) = fraction of the
teacher's e-errors judged recovered (a rate within category e; it ignores how
many e-errors there are). E_T(e)·R_T(e) = recovered e-errors / total commands =
how many recovery demonstrations per command the teacher actually provides for
e. 

Variations currently implemented (each at 11-category and 91-subcategory
granularity):

- **SCRF-all** = Σ_e q_S(e)·E_T(e)·R_T(e), with q_S(e) over all the student's failed commands. Weights every category the student errs in.
- **SCRF-unrecovered** = Σ_e q_S(e)·E_T(e)·R_T(e), with q_S(e) counting only the student's failed commands the K=3 judge marked NOT recovered. More targeted than SCRF-all: it weights only the categories the student cannot fix by itself, which is what the hypothesis is about.
- **SCRF-failed** = Σ_e q_S(e)·E_T(e)·R_T(e), with q_S(e) counting only commands from student trajectories that did not solve the task.
- **SCRF-KL** = -KL(P_S‖P_T), where P_S(e) = the student's not-recovered errors of category e / all its not-recovered errors, and P_T(e) = the teacher's recovered errors of category e / all its recovered errors (both normalized over categories, pooled at teacher level). Ranks teachers by how well their recovered-error distribution matches where the student gets stuck (`scrf_distribution_rank.py`, which also prints a plain overlap Σ_e P_S(e)·P_T(e) as a cross-check).



## Results

How we evaluate: score every teacher's trajectory for a fixed set of matched
Terminal-Lego tasks, aggregate per teacher, and compare the resulting teacher
ranking to the published SFT ranking for that student
(https://arxiv.org/pdf/2606.03461). Two task samples (200 and 1000 tasks,
seed 42) and two students with ground truth ranking: 

    Qwen3-8B:  DeepSeek-V3.2 > {GLM-5 ≈ Qwen3.5-Plus} > Claude Opus 4.6
    Qwen3-32B: DeepSeek-V3.2 > GLM-5 > Qwen3.5-Plus > Claude Opus 4.6

tau-b is Kendall correlation with the GT order (+1 identical, -1 reversed);
P = share of bootstrap draws (tasks resampled with replacement, all four
teachers' trajectories of a task drawn together, ranking recomputed) in
which the predicted best teacher is in the ground truth's top group. Two proxies use an LLM judge, cmd_error (did the command
fail) and SCRF (error category, recovered or not); all tables below use the
gpt-oss-120b judge unless stated, the judge ablation is its own section.
SCRF needs the student's own traces for q_S: the 8B tables use the 8B
student's n=200 traces, the 32B tables the 32B student's n=200 traces. tor,
egs_post and egs_loop are reported in one view,
`list_strict_prevturn` (all 24 views
in PROXY_SPEC 6.5). Metric definitions in `docs/PROXY_RESULTS_n200.md`.

### Qwen3-8B student

GT: DS > {GLM ≈ Q35} > CL. Because of the tie, tau-b saturates at +0.91 and
cannot separate a proxy that predicts GLM > Q35 from one that predicts
Q35 > GLM.

#### n=1000, all proxies, judge gpt-oss-120b

| proxy | tau-b | P(top) | predicted order |
|---|---|---|---|
| SCRF-all, 11-cat | +0.91 | 1.00 | DS > GLM > Q35 > CL |
| SCRF-all, 91-subcat | +0.91 | 0.97 | DS > GLM > Q35 > CL |
| SCRF-unrecovered, 11-cat | +0.91 | 1.00 | DS > GLM > Q35 > CL |
| SCRF-unrecovered, 91-subcat | +0.91 | 0.96 | DS > GLM > Q35 > CL |
| SCRF-failed, 11-cat | +0.91 | 0.99 | DS > GLM > Q35 > CL |
| SCRF-failed, 91-subcat | +0.91 | 0.90 | DS > GLM > Q35 > CL |
| cmd_error (more errors) | +0.91 | 1.00 | DS > GLM > Q35 > CL |
| tor | +0.91 | 0.99 | DS > Q35 > GLM > CL |
| egs_loop | +0.91 | 0.76 | DS > Q35 > GLM > CL |
| traj_length (more, tokens/turns) | +0.91 | 1.00 | DS > Q35 > GLM > CL |
| grace | +0.18 | 0.03 | Q35 > GLM > DS > CL |
| scas, egs_post | +0.18 | 0.00 | Q35 > GLM > DS > CL |
| global_nll (GRAPE), local_nll k1-8, aslec_drop, aslec_casl | -0.18 | 0.00 | Q35 > GLM > CL > DS |
| rsr | -0.18 | 0.00 | CL > DS > GLM > Q35 |
| teacher_bench, error_retry, cmd_error (fewer) | -0.91 | 0.00 | CL > ... > DS |

All six SCRF views, cmd_error, tor, egs_loop and traj_length reach the
ceiling; SCRF matches but cannot beat the cheaper cmd_error and traj_length
here, see the 32B student for the separation. Every likelihood-family proxy
except RSR puts Qwen3.5-Plus first (same-family bias); RSR puts Claude first.

#### n=200, all proxies, judge gpt-oss-120b

| proxy | tau-b | P(top) | predicted order |
|---|---|---|---|
| SCRF-unrecovered, 11-cat | +0.91 | 0.68 | DS > GLM > Q35 > CL |
| SCRF (other five views) | +0.91 | 0.47-0.67 | DS > GLM > Q35 > CL |
| traj_length (more) | +0.91 | 1.00 | DS > Q35 > GLM > CL |
| tor | +0.91 | 0.89 | DS > Q35 > GLM > CL |
| cmd_error (more errors) | +0.55 | 0.39 | GLM > DS > Q35 > CL |
| egs_loop | +0.55 | 0.41 | Q35 > DS > GLM > CL |
| grace | +0.55 | 0.35 | Q35 > DS > GLM > CL |
| scas, egs_post | +0.18 | 0.00-0.02 | Q35 > GLM > DS > CL |
| global_nll (GRAPE), local_nll k2-8, aslec_drop, aslec_casl | -0.18 | 0.00 | Q35 > GLM > CL > DS |
| local_nll k1 | -0.55 | 0.00 | Q35 > CL > GLM > DS |
| rsr | -0.18 | 0.00 | CL > DS > GLM > Q35 |
| teacher_bench, error_retry | -0.91 | 0.00 | CL > ... > DS |

n=200 is right but not bootstrap-stable for the proxies that carry signal;
n=1000 resolves that (P(top) 0.90-1.00).

#### Judge ablation, n=200

Same commands, same student traces, three judges. Failure detection agrees
across judges (cmd_error is identical); the fine error taxonomy that SCRF
needs does not.

| proxy | gpt-oss-120b | Qwen3-32B | GLM-4.6-FP8 |
|---|---|---|---|
| cmd_error (more errors) | +0.55, GLM > DS > Q35 > CL, P 0.39 | +0.55, same order, P 0.41 | +0.55, same order, P 0.33 |
| SCRF-all, 11-cat | +0.91, DS > GLM > Q35 > CL, P 0.69 | +0.18, GLM > DS > CL > Q35, P 0.34 | +0.18, GLM > Q35 > DS > CL, P 0.13 |
| SCRF-unrecovered, 11-cat | +0.91, DS first, P 0.67 | +0.18, GLM first, P 0.33 | +0.18, GLM first, P 0.12 |
| SCRF-failed, 11-cat | +0.91, DS first, P 0.65 | +0.55, GLM > DS > Q35 > CL, P 0.30 | +0.18, Q35 first, P 0.11 |
| SCRF, 91-subcat views | +0.91, DS first, P 0.47-0.50 | -0.18, GLM first, P 0.11-0.18 | -0.18, GLM first, P 0.06-0.11 |

SCRF recovers the ranking only with the gpt-oss-120b judge; with Qwen3-32B
or GLM-4.6 it degrades to GLM-5 first. Label-level agreement of the three judges is under Additional findings.

### Qwen3-32B student

GT: DS > GLM > Q35 > CL, no tie, so tau-b can reach +1.0 and the proxies
tied at +0.91 above can be told apart. Judge gpt-oss-120b; q_S(32B) from the
32B student's n=200 traces.

#### n=1000

| proxy | tau-b | P(top) | predicted order | GLM > Q35 right? |
|---|---|---|---|---|
| SCRF (all six views) | +1.00 | 0.97-0.99 | DS > GLM > Q35 > CL | yes |
| cmd_error (more errors) | +1.00 | 1.00 | DS > GLM > Q35 > CL | yes |
| traj_length (more, tokens/turns) | +0.67 | 1.00 | DS > Q35 > GLM > CL | no |
| tor | +0.67 | 0.99 | DS > Q35 > GLM > CL | no |
| egs_loop, egs_post | pending (not evaluated against the 32B GT yet) | | | |
| global_nll (GRAPE), local_nll k1-4 | -0.33 | 0.00 | Q35 > GLM > CL > DS | - |
| local_nll k8, aslec_drop, aslec_casl, rsr, scas | pending (job 4151812) | | | |
| grace | not computed for 32B (fp32 TRAK gradients risk OOM) | | | |
| teacher_bench | -0.67 | 0.00 | CL > GLM > Q35 > DS | - |
| error_retry (raw / turn-aware) | -1.00 / -0.67 | 0.00 | CL first | - |

SCRF and cmd_error recover the full 32B ranking; traj_length and tor get top
and bottom right but swap the middle pair.

#### n=200

| proxy | tau-b | P(top) | predicted order |
|---|---|---|---|
| global_nll (GRAPE), local_nll k1-8, aslec_drop, aslec_casl | -0.33 | 0.00 | Q35 > GLM > CL > DS |
| scas | 0.00 | 0.00 | Q35 > GLM > DS > CL |
| rsr | 0.00 | 0.00 | CL > DS > GLM > Q35 |
| SCRF, cmd_error, tor, egs, traj_length at n=200 vs the 32B GT | not tabulated (student-independent ones are the n=200 8B numbers re-scored against the 32B order; SCRF-32B exists only at n=1000) | | |


### TOR caveat

The paper's own TOR values (Table 3) order the four teachers exactly like
the 32B ground truth, but its code is unreleased and none of our 24
predeclared operationalizations reproduces them: Claude comes out at the
paper's level, the other three teachers 2-3x too high, and GLM-5 never above
Qwen3.5-Plus. tor's +0.67 on the 32B ground truth is therefore a property of
our reimplementation, not established for the paper's metric. Definitions,
per-view numbers and what was tried: PROXY_SPEC 6.5.

## Additional findings

- **Length is a confound.** traj_length reaches the 8B ceiling (+0.91)
  simply because DeepSeek writes the longest trajectories; on the tie-free
  32B ranking it swaps GLM-5 and Qwen3.5-Plus (+0.67) while SCRF and
  cmd_error, which are rates, get them right (+1.00).
- **Same-family bias.** Every student-likelihood proxy except RSR (GRAPE,
  LALP, ASLEC, SCAS, GRACE) puts Qwen3.5-Plus first for both Qwen students.
  RSR (lower = better) is the mirror image: Claude first, Qwen3.5-Plus last.
- **SCRF works, stabilizes with n, and is judge-dependent.** All six views
  reproduce the 8B ranking and recover the full 32B ranking, matching but not
  beating cmd_error. At n=200 it is right but not bootstrap-stable (P 0.5-0.7),
  at n=1000 it is (P 0.90-1.00). Only with the gpt-oss-120b judge: Qwen3-32B
  and GLM-4.6 degrade it to GLM-5 first, although the three judges agree
  98.5 % on whether a command failed, 85 % on recovery, 64 % on the 11
  categories and 59 % on the 91 subcategories (n=200; pairwise 99 / 88-91 /
  71-77 / 68-73 %). Failure and recovery labels are judge-robust, the
  category labels are not, and the disagreements shift the per-teacher error
  profiles rather than cancelling out.
- **n matters.** At n=200 most proxies' own teacher ranking is not reproduced
  in 80 % of task-bootstrap resamples (within-teacher score variance is as
  large as between-teacher variance). n=1000 fixes this for the proxies with
  signal (SCRF, cmd_error: P 0.90-1.00; cmd_error goes from +0.55 to +0.91)
  and leaves the wrong ones wrong.

## Layout

Stage 1-2 (data):
- `prepare_dataset.py`: builds the matched task list and pins the teacher trajectory files.
- `generate_trajectories.py`: runs the student on the sampled tasks to produce its traces.

Stage 3 (score each teacher trajectory):
- `compute_proxies.py`: per (teacher, task) score for every proxy.
- `judge.py`: the LLM judge used by cmd_error and SCRF.

Stage 4 (rank and analyze):
- `evaluate_ranking.py`: teacher rankings, bootstrap, sample-efficiency.

Supporting scripts:
- `plot_scrf_errors.py`: error-category distributions behind SCRF.
- `scrf_distribution_rank.py`: SCRF-KL / overlap teacher ranking, derived (no rerun) from the per-task scrf components; separate from compute_proxies because it is a teacher-level score over pooled distributions, not a per-(teacher, task) score (like GRACE's teacher-level step).

- `slurm/`: cluster launch scripts. `docs/`: specs and the results write-up.
- `runs/`, `venv/`, `upstream/` (the cloned author repos) are under `$WS_ROOT/teacher_ranking_proxy/`.

## To dos

In flight (2026-09-10):
- 8B SCRF with q_S from the 1000-trace student run (job 4152502); the
  earlier attempt hit the score cache. Then the q_S sample-size comparison
  against `scrf@gpt-oss-120b__qS200.jsonl`.
- 32B n=1000: local_nll_k8, ASLEC, RSR, SCAS (job 4151812, third attempt
  after two node failures). Its RSR file needs the sign flip applied by hand
  (job started before the fix).
- Evaluate egs_post / egs_loop against the 32B ground truth.

Open:
- TOR: no operationalization reproduces the paper's Table 3 (PROXY_SPEC 6.5);
  ask the authors for their script, then rerun (teacher-only, seconds).
- GRACE for the 32B student (fp32 TRAK gradients need more than one GPU).
- More ground-truth student/teacher rankings on agentic tasks, so proxies
  are validated on more than Terminal-Lego: `litmine/` (spec in
  `docs/PIPELINE.md`).
- Ground truths that are FLOPs/token-budget controlled, so a teacher does
  not count as better only because its trajectories are longer.
