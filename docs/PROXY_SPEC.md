# Teacher-Ranking Proxy Specification

This stage begins only after the Terminal-Lego task/trajectory generation pipeline is validated.

## Goal

Test whether inexpensive trajectory-level proxies can predict which teacher produces the most useful SFT data **without actually running SFT**.

There are two related questions:

1. **Teacher ranking:** Can a proxy identify which teacher is best on average?
2. **Selection granularity:** Is teacher identity itself the right granularity, or would it be better to select the best trajectory separately for each task?

**Do not run SFT in this stage.**

**Upstream code first.** For every published proxy, use the original authors' implementation where usable and pin the commit. Reimplement only if no usable release exists. In that case: read the paper → inspect official code/repository → implement faithfully → document every adaptation required for terminal-agent trajectories.

---

# 1. Evaluation target and scope

Teacher quality is **conditional on the task distribution, student, harness, and training setup**. A published teacher ranking is therefore ground truth only for trajectories from the same task/data distribution on which that ranking was established.

Do **not** evaluate proxy scores from one task distribution against an SFT ranking obtained on a different one.

## 1.1 Primary experiment: Terminal-Lego

For the initial benchmark, use matched Terminal-Lego tasks and the published Terminal-Lego teacher rankings.

### Qwen3-8B

```text
DeepSeek-V3.2 > GLM-5 ≈ Qwen3.5-Plus > Claude Opus 4.6
```

### Qwen3-32B

```text
DeepSeek-V3.2 > GLM-5 > Qwen3.5-Plus > Claude Opus 4.6
```

The target is **teacher ranking**, not exact downstream-performance prediction.

Do not tune proxy definitions, hyperparameters, aggregation rules, or implementation choices to maximize agreement with these labels.

## 1.2 Later external validation

If the same proxy benchmark is later applied to another task distribution, e.g. OpenThoughts-Agent, evaluate it against the teacher ranking published for **that task distribution**, not against Terminal-Lego.

Treat each task distribution as a separate benchmark.

A strong proxy should ideally generalize across distributions and correctly predict that the best teacher may change depending on the data.

---

# 2. Critical matched-task requirement

Teacher comparisons must use the **exact same task IDs for every teacher**.

For each sampled task:

```text
task_i
├── Claude Opus 4.6 trajectory
├── Qwen3.5-Plus trajectory
├── GLM-5 trajectory
└── DeepSeek-V3.2 trajectory
```

Include a task in the primary benchmark only if the required trajectory exists for **all four teachers**.

This is critical: never compare teacher averages computed from different sets of tasks, because differences in task difficulty could otherwise be mistaken for differences in teacher trajectory quality.

Use the **same 200 matched task IDs for every proxy** whenever the proxy permits it.

### Primary trajectory unit

For the primary comparison assume:

```text
1 task × 1 trajectory per teacher
```

so each of the 200 tasks contributes exactly four teacher trajectories.

If multiple trajectories for the same `(task, teacher)` are available, do not cherry-pick. Use a predefined deterministic rule or fixed seed and document it.

---

# 3. Primary proxy benchmark: fixed 200 tasks

Start by sampling a **single seeded set of approximately 200 matched Terminal-Lego tasks**.

Do not initially run a task-count sweep.

For every proxy, score the corresponding teacher trajectory individually.

Retain at minimum:

```text
task_id
teacher
student              # if proxy is student-dependent
trajectory_id
proxy_score
proxy_components
```

Then aggregate the trajectory scores by teacher to obtain the proxy's teacher ranking.

For example:

```text
task_001:
    Claude      score
    Qwen        score
    GLM         score
    DeepSeek    score

...

teacher aggregate:
    Claude      mean(...)
    Qwen        mean(...)
    GLM         mean(...)
    DeepSeek    mean(...)
```

Use the aggregation defined by the original method where applicable. Otherwise default to the arithmetic mean across matched tasks and report median as a diagnostic.

---

# 4. Evaluation metrics

With only four teachers, ranking statistics are necessarily coarse. Treat results as **exploratory** and do not make unsupported significance claims.

Represent the Qwen3-8B ranking using tie groups:

```python
[
    ["DeepSeek-V3.2"],
    ["GLM-5", "Qwen3.5-Plus"],
    ["Claude Opus 4.6"],
]
```

For every proxy and student report:

```text
Kendall tau-b       primary
Spearman rho        secondary
pairwise accuracy
```

For Qwen3-8B, exclude the tied GLM-5 / Qwen3.5-Plus pair from pairwise accuracy.

Proxy scores should remain continuous. Do not create artificial ties by rounding.

Also report computational cost:

```text
wall-clock runtime
GPU-seconds
requires student weights?        yes / no
requires student trajectories?   yes / no
requires LLM/API judge?          yes / no
estimated API cost
```

---

# 5. Uncertainty on the primary 200-task benchmark

Uncertainty comes from **which tasks were sampled**, not from teacher sampling.

Use seeded task-level bootstrap resampling.

For each bootstrap replicate:

1. resample the 200 task IDs with replacement;
2. retain all four teacher trajectories belonging to each sampled task;
3. recompute teacher-level proxy aggregates;
4. recompute the predicted ranking and ranking metrics.

Report:

```text
bootstrap CI for Kendall tau-b
bootstrap CI for pairwise accuracy
distribution of predicted teacher rankings
```

When comparing proxies, use the **same bootstrap task samples**.

Also report the bootstrap distribution of differences in Kendall tau-b between proxies.

---

# 6. Teacher-only proxies

These depend only on teacher trajectories.

## 6.1 Teacher benchmark performance

Use the teacher's underlying agent benchmark performance as the simplest baseline.

This tests the hypothesis:

```text
stronger solver → better teacher
```

against the published SFT ranking.

---

## 6.2 Trajectory length

Simple control, not a published teacher-selection method.

For every trajectory compute:

```text
assistant/action turns
assistant-generated tokens
```

Aggregate by teacher.

Evaluate both predefined directions:

```text
more = better
less = better
```

Do not choose direction after observing agreement with the target ranking.

---

## 6.3 Command error rate

Reference:

```text
Terminal-Bench 2.0
arXiv:2601.11868
Section 4.5 / Appendix E.2
```

Originally designed for command-level failure analysis, not teacher selection.

Use its published failure-detection prompt (Appendix E.3) to estimate the
trajectory-level command error rate = failed commands / commands.

NOTE: cmd_error is TAXONOMY-AGNOSTIC. Its score is just the failure rate (the
binary E.3 "is this a failure?" judgment); it does NOT use the 11-category /
91-subcategory taxonomy or any notion of which errors or recovery. The error
TAXONOMY (Appendix E.4) and recovery are used only by SCRF, which weights errors
by category and by whether the teacher recovered. In the code the same judge
(judge.py `judge_segment`) runs E.3 then E.4-on-failures for both proxies, so
cmd_error stores per-category counts as reporting metadata, but nothing in the
cmd_error number depends on them.

Evaluate both:

```text
fewer errors = better teacher
more errors  = better teacher
```

Report both rather than selecting whichever matches the SFT ranking.

### How (command, output) segments are extracted

Both cmd_error and SCRF score one **(command, output)** pair at a time. We build
them per command (not per turn), anchored on the agent's own JSON:

1. Each Terminus-2 assistant turn is JSON with a `commands` list; each entry's
   `keystrokes` string is what the agent typed. It can hold several commands.
2. Split each `keystrokes` string into commands, one per newline `\n` — but a
   newline does NOT start a new command while it is "inside" something
   unfinished: an open quote (`"` or `'`), a line-continuation (a trailing
   `\`), or a heredoc body (between `<<EOF` and the closing `EOF`). So
   `cat <<EOF … EOF`, a quoted multi-line string, and `foo \`\n`bar` each stay
   as one command. We keep each command's first line (what the shell echoes).
3. Take the terminal screen returned after that turn and find the prompt-line
   echoes (`user@hostid:cwd#` in teacher data, `Apptainer>` in student runs).
   Match the JSON commands to those echoes in order. The match must be on a
   prompt line, so a command-looking string sitting in some output is never
   mistaken for a command.
4. A matched command's output = screen text from its echo to the next matched
   command's echo (or screen end); a ">10 KB omitted" marker truncates it so it
   cannot absorb a later command. A command whose echo is absent (scrolled off /
   inside the omitted middle) is marked `observed=False` and dropped.

(Code: `_split_keystrokes` / `_turn_commands` / `_command_segments` in
`compute_proxies.py`.)

---

## 6.4 Error-Retry

Reference:

```text
A Systematic Evaluation of Trajectory Data Curation
for LoRA Fine-Tuning of Code Agents
arXiv:2607.17205
B2: Error-Retry
```

Originally designed to penalize repetitive:

```text
action → error → similar action
```

patterns during trajectory filtering.

Compute the published trajectory-level Error-Retry score and aggregate by teacher.

Use upstream code where available.

### Terminus adaptations (documented)

Published B2 (inlined verbatim as `_compute_b2_error_retry`): one named-tool call
per step; count a cycle when a step's observation has an error (keyword match,
`_obs_has_error`) and the next step reuses the same tool. Higher cycle count =
worse (they filter such trajectories out), so for teachers **fewer = better**.
Score is the **raw cycle count**, negated so higher = better (matching upstream,
which keeps raw counts). Two adaptations for Terminus, in `compute_error_retry`:

1. **Tool name → first word of the command** (`_first_cmd_word`). Terminus has no
   named tools, only a shell, so the tool is the program run (`python`, `pytest`).

2. **Turn-aware, keyed on the erroring command, retry must fail again.** B2
   assumes one command per agent step, so it just compares each command to the
   next. But a Terminus turn (one agent JSON) can run several commands at once,
   all typed *before* the agent sees any output -- so if one of them errors and a
   later command in the *same* turn uses the same tool, that isn't a retry (the
   agent hadn't seen the error yet). We therefore only compare across turns: we
   count a cycle when a tool whose command errored in turn t is run again and
   **errors again in the very next turn t+1** (the first turn after the agent saw
   the error). Two smaller changes fall out of this: because a turn has many
   commands we track the tool of the command that actually *failed* (not just the
   turn's first command), and we require the repeat to fail again (B2 counts any
   same-tool reuse, even one that succeeded).

Primary score is this turn-aware count (`cross_turn_persist`);
`cross_turn_persist_rate` (length-normalized) and `verbatim_old` (the literal
per-command B2) are kept as extra views.

---

## 6.5 TOR / environment grounding

Reference:

```text
Terminal-Lego
arXiv:2606.03461
```

Originally designed to quantify environment-grounded behavior and relate it to downstream teaching effectiveness.

Compute **Targeted Observation Ratio (TOR)** separately for every trajectory and then aggregate by teacher.

Conceptually:

```text
TOR =
number of actions supported by an earlier path-aligned observation
-----------------------------------------------------------------
total number of actions
```

Higher TOR corresponds to more:

```text
inspect → act
```

behavior.

### TOR implementation

No upstream code exists (the paper says "available upon acceptance"), so this
is our reimplementation from the paper text, with every choice the paper does
not make written down here. Code: `_trajectory_events`, `_paths_aligned`,
`_trajectory_grounding_components` and `compute_tor` in `compute_proxies.py`.

**What the paper fixes.** The observation set (`cat ls find grep head wc diff
stat`), the formula (supported actions / actions), that the observation must
come before the action, and three examples of alignment: inspecting a file
before editing it, listing a directory before creating a file inside it,
reading a script before running it. Table 3 reports TOR per teacher:
DeepSeek-V3.2 13.4 %, GLM-5 7.3 %, Qwen3.5-Plus 6.5 %, Claude Opus 4.6 2.5 %.

**What the paper leaves open, and the options implemented.** Five choices
are not fixed by the paper. For the first three we implemented several
alternatives, and every view picks one of each; the view name spells out the
picks in that order, `<actions>_<align>_<window>`, so `list_exact_turn2` is
the `list` action set with `exact` alignment and a two-turn window. For the
last two choices there is a single implementation and nothing to pick, so
they do not appear in the view names.

- *Which commands count as actions.* `list` = only the state-changing
  programs (file edits, installs, script runs, or a `>` redirect); `all` =
  every command that is not an observation and not a bare `cd`.
- *How close two paths must be to count as aligned.* `exact` = identical
  path; `strict` = identical, or the observed path is a directory containing
  the action's path (the paper's three examples); `loose` = strict plus
  reverse containment, where the observation targets a file and the action
  targets the directory that contains it, plus the same file name in
  different directories (the original operationalization).
- *Which earlier turns an observation may come from.* Observations in the
  same response as the action never count: Terminus-2 types a response's
  whole command list and only then returns the screen, so the agent had not
  seen that output when it chose the action. `prevturn` = any earlier
  response; `turn1`, `turn2`, `turn3` = one of the k responses before the
  action's.
- *How a command's target path is found.* One implementation, below.
- *Per-trajectory mean or pooled ratio.* The teacher score is the mean of
  per-trajectory ratios (a trajectory with 2 actions weighs as much as one
  with 40). The pooled ratio over all of a teacher's actions was computed
  once for comparison: it shifts values by a few points and changes no
  ordering, so it is not a view.

**How commands are classified.** Commands are the per-command screen
segments of cmd_error (§6.3). Classification is rule-based, no judge: the
command's program name (first word after `sudo`, `env`, `timeout` and
`VAR=` prefixes) is looked up in fixed lists. A command whose program is in
the paper's observation list (`cat ls find grep head wc diff stat`, plus
`pwd`) is an *observation*. What counts as an *action* depends on the
action-set option: with `list`, only commands whose program is in our list
of state-changing programs (`sed tee cp mv rm mkdir touch chmod tar pip apt
npm make gcc python node bash git patch ...`) or that write through a `>`
redirect; with `all`, every command that is not an observation, except a
bare `cd`. Commands that are neither are ignored.

**How the target path is found.** The command line is split into shell
tokens. A token is taken as a path if it contains `/`, is `.` or `..`, ends
in a file suffix, directly follows a `>`, `>>` or `<` redirect, or is a
positional operand of a program that takes paths (`cat`, `ls`, `cp`, `mv`,
`python`, ...; for `sed` only the last operand, since its program text
contains slashes). Relative paths are made absolute with the cwd of that
command, `~` is expanded, trailing slashes are dropped, and `-`, `/dev/null`
and option flags are ignored. Tokens containing shell expansions (`$`,
backticks, parentheses) are discarded rather than guessed. A command can
give several paths; an action is supported if any of them aligns with any
path of an earlier observation.

**Comparison with the paper (n=1000, per-teacher mean, %).**

| view | DS | GLM | Q35 | CL | order |
|---|---|---|---|---|---|
| paper Table 3 | 13.4 | 7.3 | 6.5 | 2.5 | DS > GLM > Q35 > CL |
| original (list, loose, same-response counted; retired) | 56.0 | 48.9 | 55.4 | 33.3 | DS > Q35 > GLM > CL |
| list_loose_prevturn | 33.7 | 21.2 | 30.6 | 7.1 | DS > Q35 > GLM > CL |
| list_loose_turn1 | 22.2 | 15.2 | 22.9 | 4.8 | Q35 > DS > GLM > CL |
| list_loose_turn2 | 28.4 | 18.5 | 27.3 | 6.2 | DS > Q35 > GLM > CL |
| list_loose_turn3 | 31.3 | 19.6 | 29.1 | 6.6 | DS > Q35 > GLM > CL |
| list_strict_prevturn | 32.8 | 20.8 | 30.0 | 6.7 | DS > Q35 > GLM > CL |
| list_strict_turn1 | 21.1 | 14.7 | 22.3 | 4.5 | Q35 > DS > GLM > CL |
| list_strict_turn2 | 27.3 | 18.0 | 26.7 | 5.9 | DS > Q35 > GLM > CL |
| list_strict_turn3 | 30.3 | 19.2 | 28.6 | 6.2 | DS > Q35 > GLM > CL |
| list_exact_prevturn | 28.4 | 16.4 | 25.7 | 4.9 | DS > Q35 > GLM > CL |
| list_exact_turn1 | 18.9 | 11.7 | 19.3 | 3.2 | Q35 > DS > GLM > CL |
| list_exact_turn2 | 24.1 | 14.2 | 22.9 | 4.3 | DS > Q35 > GLM > CL |
| list_exact_turn3 | 26.7 | 15.0 | 24.4 | 4.6 | DS > Q35 > GLM > CL |
| all_loose_prevturn | 28.1 | 22.8 | 30.9 | 6.6 | Q35 > DS > GLM > CL |
| all_loose_turn1 | 18.9 | 16.1 | 22.4 | 4.3 | Q35 > DS > GLM > CL |
| all_loose_turn2 | 23.9 | 20.3 | 27.3 | 5.8 | Q35 > DS > GLM > CL |
| all_loose_turn3 | 26.1 | 21.5 | 29.2 | 6.1 | Q35 > DS > GLM > CL |
| all_strict_prevturn | 27.4 | 22.4 | 30.3 | 6.4 | Q35 > DS > GLM > CL |
| all_strict_turn1 | 18.3 | 15.7 | 21.7 | 4.2 | Q35 > DS > GLM > CL |
| all_strict_turn2 | 23.3 | 19.9 | 26.6 | 5.6 | Q35 > DS > GLM > CL |
| all_strict_turn3 | 25.4 | 21.0 | 28.6 | 6.0 | Q35 > DS > GLM > CL |
| all_exact_prevturn | 23.7 | 17.9 | 25.7 | 4.8 | Q35 > DS > GLM > CL |
| all_exact_turn1 | 16.2 | 12.7 | 18.7 | 3.1 | Q35 > DS > GLM > CL |
| all_exact_turn2 | 20.4 | 15.8 | 22.6 | 4.2 | Q35 > DS > GLM > CL |
| all_exact_turn3 | 22.2 | 16.6 | 24.2 | 4.5 | Q35 > DS > GLM > CL |

Excluding same-response observations brings Claude to the paper's level;
alignment changes little; the `all` action set and the one-turn window put
Qwen3.5-Plus first. No view reproduces the paper's GLM-5 > Qwen3.5-Plus, and
the three non-Claude teachers stay 2-3x above Table 3. TOR is therefore the
one proxy here without a validated implementation; the remaining difference
lies in the paper's action definition or path parsing.

---

## 6.6 Extended EGS proxy

TOR covers inspect → act. Terminal-Lego describes environment-grounded
supervision more broadly as inspect → act → verify → adapt, so two further
components are computed in the same pass as TOR, with the same observation
list, action set, path finding, alignment and window options (§6.5), and
reported separately, never combined into a weighted score:

- **egs_post**, act → verify. An action counts as verified if a later
  response inside the window contains either an observation whose path
  aligns with the action's path, or any test or build command (`pytest`,
  `tox`, `unittest`, `make`, `ctest`), the one rule TOR does not have. The
  window is the TOR window mirrored forward: `prevturn` = any later
  response, `turnK` = one of the K responses after the action's. A command in
  the same response as the action never counts. Score is verified actions
  over actions, per trajectory, averaged per teacher.
- **egs_loop**, inspect → act → verify. An action counts if it satisfies both
  the TOR condition and the egs_post condition of the same view. Same
  aggregation.

Both write the same 24 `score_views` as tor.

n=1000, per-teacher mean (%), `list` actions:

| component / view | DS | GLM | Q35 | CL | order |
|---|---|---|---|---|---|
| egs_post strict_prevturn | 34.5 | 39.4 | 43.1 | 8.2 | Q35 > GLM > DS > CL |
| egs_post strict_turn1 | 21.4 | 24.6 | 18.9 | 3.9 | GLM > DS > Q35 > CL |
| egs_post exact_prevturn | 29.6 | 28.8 | 30.1 | 6.5 | Q35 > DS > GLM > CL |
| egs_post exact_turn1 | 18.2 | 17.6 | 12.2 | 3.1 | DS > GLM > Q35 > CL |
| egs_post exact_turn2 | 24.5 | 22.5 | 19.6 | 5.1 | DS > GLM > Q35 > CL |
| egs_post exact_turn3 | 26.6 | 25.6 | 24.2 | 5.9 | DS > GLM > Q35 > CL |
| egs_loop strict_prevturn | 14.5 | 8.9 | 13.7 | 2.0 | DS > Q35 > GLM > CL |
| egs_loop strict_turn1 | 7.4 | 4.7 | 5.6 | 0.6 | DS > Q35 > GLM > CL |
| egs_loop exact_prevturn | 11.0 | 4.9 | 6.3 | 1.2 | DS > Q35 > GLM > CL |
| egs_loop exact_turn1 | 6.0 | 2.9 | 2.7 | 0.4 | DS > GLM > Q35 > CL |
| egs_loop exact_turn2 | 8.8 | 4.0 | 5.0 | 1.0 | DS > Q35 > GLM > CL |
| egs_loop exact_turn3 | 9.9 | 4.4 | 5.8 | 1.2 | DS > Q35 > GLM > CL |

Verification frequency alone (egs_post) does not follow the ground truth:
with strict alignment GLM-5 or Qwen3.5-Plus lead, whatever the window. With
exact alignment, egs_post gives the full paper order DS > GLM > Q35 > CL
when the verification must come within the next 1, 2 or 3 responses
(`turn1`-`turn3`) but not when any later response counts (`prevturn`);
egs_loop gives it only for `turn1`. In those views the GLM-5 / Qwen3.5-Plus
gap is under one point, so this is not evidence of separation (bootstrap in
`ranking_report`).

---

# 7. Student-dependent published proxies

These depend on the **pre-SFT student**.

None was originally designed specifically for Terminal-Lego trajectories, so document every adaptation explicitly.

---

## 7.1 Global student NLL / GRAPE

Reference:

```text
GRAPE
arXiv:2502.04194
```

Originally designed to select instruction-tuning responses that are compatible with a particular student using response likelihood under the student/base model.

Here:

Score each existing teacher trajectory using the likelihood of the teacher-generated assistant tokens under the target student.

Loss applies only to teacher-generated assistant tokens, not task instruction, system text, terminal observations, environment outputs. 

Normalization as in the paper: add up the log-probabilities of the
teacher's assistant tokens and divide by the number of assistant tokens,
i.e. the mean log-probability per assistant token.

---

## 7.2 CAR

Reference:

```text
Stronger Models are NOT Stronger Teachers for Instruction Tuning
arXiv:2411.07133
```
CAR (Compatibility-Adjusted Reward) combines:

teacher response quality
+
student compatibility / likelihood

The published formulation is:

\frac{r(D_T)}
{1+\beta L(D_T,\theta_S)}
]

where:

r(D_T) = average response-quality / reward score
L(D_T, θ_S) = student NLL on the teacher responses

Only included here for completness. DO NOT IMPLEMENT, as currently we onyl sample 1 trajectory per teacher and task so we do not have a metric for average response-quality / reward score (woul donyl be 0/1 reward of that one trajectory and many trajetcory datasets filtered to only include sucessfull trajetcories)
---

## 7.3 Local Naturalness / local-turn likelihood (LALP) 

Reference:

```text
The Signal is in the Steps / Local Naturalness
arXiv:2510.03988
```
Originally designed for multi-teacher reasoning distillation.

The paper argues that a single mean over the whole trajectory (GRAPE, §7.1)
becomes unreliable for long traces, because later tokens are conditioned on
the entire preceding teacher output, and proposes Local Average Log
Probability (LALP) instead: score each reasoning step on its own and give
every step equal weight.

Implementation (`compute_local_nll`): each teacher assistant turn is one
step. For every assistant turn, the student scores that turn's tokens the
GRAPE way, mean log-probability per token, but conditioned only on the task
prompt plus the previous k assistant turns with their terminal outputs, not
the full history. The trajectory score is the plain mean of the per-turn
means, so a short turn weighs as much as a long one. Terminal outputs are
context, never scored. So compared to GRAPE two things change: the mean is
taken per turn and then over turns instead of once over all tokens, and the
context is truncated to the last k turns.

Two things differ from the paper: it sets k as a share of the preceding
steps (5 % to 75 %), we use absolute k ∈ {1, 2, 4, 8} because agent
trajectories have few, long turns, one proxy per k (`local_nll_k1` ...
`local_nll_k8`); and it segments a response into reasoning steps itself,
whereas we take the assistant turns as the steps.

---

## 7.4 ASLEC

Reference:

```text
On the Step Length Confounding in LLM Reasoning Data Selection
arXiv:2604.06834

ASLEC-DROP
ASLEC-CASL
```

Argues that naturalness-based selection, including Local LP, is confounded by **reasoning-step length** because low-probability first-step tokens are increasingly diluted in longer steps.

Treat the two published variants as standalone likelihood-based proxies:

* **ASLEC-DROP:** compute student log-likelihood while excluding the first token of every reasoning step from the score.
* **ASLEC-CASL:** start from student log-likelihood and remove the estimated effect of the first-token ratio using the paper's regression-based adjustment.

Use the official implementation:

```text
github.com/wangbing1416/ASLEC
```

Map reasoning-step boundaries to Terminus-2 assistant/action turns and evaluate both variants.

Implementation (`compute_aslec`, official `output_drop_score` /
`output_causal_score` reimplemented and verified identical on real
trajectories): a step is one assistant turn and one token is skipped per step
(`skip_tokens=1`, the paper's "first token"; the authors' run script defaults
to 2). ASLEC-DROP is the mean log-probability over the step tokens except
the first of every step. ASLEC-CASL fits, over all trajectories of all
teachers, a linear regression of the mean log-probability on the first-token
ratio, steps divided by tokens, and subtracts the fitted effect of that
ratio, so it is GRAPE with the step-length trend removed.

A step starts at the first token of the analysis text, after the
`{ "analysis": "` template of the Terminus-2 JSON turn, i.e. the first token
the teacher chose; the template tokens are left out of every statistic. The
template is located as a token subsequence in the first 40 tokens of the
turn (the chat template may put an empty think block before it); a turn
without it falls back to the turn start. Starting at the turn's literal
first token, the brace, was tried first: that token is near-certain, not the
low-probability step opener the paper has in mind, and DROP and CASL then
gave +0.18 and -0.18 at n=1000 (8B), the same as GRAPE with Qwen3.5-Plus
first; those files are kept as `aslec_*__v1_turnfirst.jsonl`.

The CASL regression: one observation per trajectory over all
teachers; per trajectory M = mean log-probability over all step tokens,
M_first = mean over the first token of each step, M_non = mean over the
others, F = steps / tokens (the inverse mean step length). Least squares
M ~ beta1 M_non + beta2 M_first + gamma F + intercept, then score = M -
gamma F: the part of the mean log-probability that follows from step
length is removed.

---

## 7.5 RSR

Reference:

```text
Which Reasoning Trajectories Teach Students to Reason Better?
arXiv:2601.14249
```
Observation that effective trajectories typically balance learning signal strength and behavioral alignment by combining low absolute probability with relatively high-ranked tokens under the student model → i.e. student has high entropy (not so confident what it should do, then a low prob token can have high rank) → so kind of “I wasn't going to confidently say this, but among the things I might reasonably have said, this is near the top.

Propose RSR  defined as the ratio of a trajectory’s average tokenwise rank to its average negative log-likelihood. 

Reuse implementation in their git repo: https://github.com/UmeanNever/RankSurprisalRatio.
Also I believe their current repo has actually been extended to multi-round chat / agent data. The implementation scans the complete chat-formatted sequence and identifies assistant spans only as the tokens to score. So use their exact implementation. 

---

## 7.6 GRACE

Reference:

```text
In Good GRACEs:
Principled Teacher Selection for Knowledge Distillation
arXiv:2511.02833
```

Start from the student’s cross-entropy/NLL on each teacher trajectory and differentiate that loss w.r.t. the student parameters to obtain one gradient vector per trajectory. For each teacher, GRACE treats these vectors as a gradient distribution over that teacher’s data. It repeatedly splits the data by task/prompt into a large reference set and a held-out set (so trajectories from the same task stay together; one trajectory per task is also valid), uses the reference gradients to estimate the teacher’s typical gradient-direction distribution, and then measures whether held-out trajectories induce gradients that are both reasonably sized and lie in directions supported by that distribution. Lower GRACE = better: the teacher induces stable, generalizable student updates across tasks rather than large or outlier updates. The exact score is in GRACE/GRACE_computation.py (grace(...)); NLL → gradient extraction is in GRACE/gradient_computation.py.

Use official code where possible: https://github.com/abhishekpanigrahi1996/GRACE

Do not invent an unsupported per-trajectory approximation.

---

## 7.7 SCAS

Reference:

```text
The Strongest Teacher Is Not Always the Best Teacher:
Student-Centric Answer Selection
arXiv:2605.26872
```

Originally dynamically selects responses based on changing student learning cost during SFT.

Using its pre-update score as a static pre-SFT ranking is an **adaptation**.

If implemented, compute the published pre-update learning-cost score for each trajectory and document the aggregation.

Advantage to GRACE is that we do not need to compute the actual graidents of the student model under teacher response. 
---

## 7.8 LARK

Reference:

```text
LARK:
Learnability-Grounded Trajectory Selection for Efficient Reasoning Distillation
arXiv:2605.30651
```

Originally designed for trajectory-level reasoning-data selection.

Compute the published trajectory score and document aggregation to teacher level.

---

## 7.9 PerSyn

Reference:

```text
Find Your Optimal Teacher:
Personalized Data Synthesis via Router-Guided Multi-Teacher Distillation
arXiv:2510.10925
```

Conceptually relevant because it performs query-level teacher assignment.

However, it requires training a router and is therefore not an inexpensive zero-training proxy.

Acknowledge as related work / optional later baseline.

Do not implement initially.

---

# 8. Student specificity

The two Terminal-Lego student rankings differ only in:

```text
GLM-5 vs Qwen3.5-Plus
```

For every student-dependent proxy report:

```text
Qwen3-8B:
predicted GLM-5 vs Qwen3.5-Plus

Qwen3-32B:
predicted GLM-5 vs Qwen3.5-Plus

student-dependent difference captured? yes/no
```

Teacher-only proxies cannot capture this by construction.

Treat this analysis as exploratory because there is only one published teacher pair whose relation differs between the two students.

---

# 9. Proposed behavioral proxy: SCRF

**Student-Conditioned Recovery Fit (SCRF)** is a new experimental proxy proposed in this project, not an established published method.

**Hypothesis:** A useful teacher provides successful recovery demonstrations for the command-level error modes on which the target student tends to struggle.

Start from the formulation below, but treat it as a proxy to develop and validate rather than a fixed final metric. The agent may refine the definitions, normalization, recovery window, or aggregation based on inspectable intermediate evidence. Do **not** modify the proxy based on agreement with the known teacher ranking.

## 9.1 Student error profile

Use student trajectories from the same **200 matched Terminal-Lego tasks**.

Detect and classify command failures using the **Terminal-Bench 2.0 command-failure taxonomy** from Section 4.5 / Appendix E.2 of arXiv:2601.11868. Start from the published failure-detection and classification prompts in Appendices E.3–E.4.

For each error category (e), report:

```text
frequency across all student trajectories
frequency in failed student trajectories
frequency in successful student trajectories
```

Use these statistics to estimate:

[
q_S(e)
]

representing how relevant error (e) is to the target student's weaknesses. Errors concentrated in failed trajectories should generally receive more importance than errors the student frequently encounters but successfully recovers from.

Retain all underlying frequencies independently of the final definition of (q_S(e)).

## 9.2 Teacher error exposure

Apply the same command-error classifier to every teacher trajectory.

For teacher (T) and error category (e), count:

[
N_T(e)=\text{number of occurrences of error }e
]

and define the initial exposure measure as:

[
E_T(e)=
\frac{N_T(e)}
{\text{number of teacher commands}}.
]

Also retain raw counts and errors per trajectory. For Terminal-Lego, compute this over all available teacher trajectories; do not require successful-vs.-failed teacher statistics if only successful trajectories are available.

## 9.3 Teacher local recovery

Develop a **reusable automated recovery classifier** for detected command-level errors.

For each error (e), the pipeline should extract the error-producing command/output plus a short subsequent trajectory window and use a fixed structured LLM-judge rubric to determine whether the model **corrected or successfully recovered from that specific error**.

Start with:

```text
K = 3 subsequent assistant turns
```

including intervening environment observations. During development, inspect a small sample to verify that the rubric and window capture recovery reliably and adjust them if necessary. Once fixed, the same classifier must run automatically and unchanged on new teacher/student trajectory sets.

Define:

[
R_T(e)=
\frac{#\text{occurrences of }e\text{ classified as recovered}}
{#\text{occurrences of }e}.
]

The judge should receive anonymized trajectory snippets and must not see teacher identity or the published teacher ranking.

## 9.4 Initial SCRF score

Start from:

[
\boxed{
SCRF(T)=\sum_e q_S(e),E_T(e),R_T(e)
}
]

where:

```text
q_S(e) = importance of error e for the target student
E_T(e) = frequency/density of error e in teacher trajectories
R_T(e) = probability that the teacher locally recovers from error e
```

Thus, (E_T(e)R_T(e)) measures how densely the teacher data contains successful recovery demonstrations for error (e), while (q_S(e)) emphasizes errors relevant to the target student's weaknesses.

Always retain and report (q_S(e)), (E_T(e)), and (R_T(e)) separately.

Also report the following controls:

```text
command error rate
    = detected command-level errors / teacher commands

local recovery rate
    = recovered command-level errors / detected command-level errors

student–teacher command-error similarity
    = similarity between student and teacher distributions over error categories

unconditioned recovery density
    = Σ_e E_T(e) R_T(e)
      # same recovery signal as SCRF, but without student-specific weighting
```

These controls test whether student conditioning contributes information beyond general teacher error/recovery behavior.

The SCRF formulation may be refined if intermediate analysis reveals clear problems—for example overly coarse error categories, inadequate recovery windows, or problematic normalization—but never because a modification improves agreement with the known teacher ranking.


---

# 10. Teacher-level vs trajectory-level selection

Beyond asking which teacher has the best **average** proxy score, determine whether **teacher identity itself is actually the right granularity for data selection**.

For every proxy on the primary 200-task benchmark, retain the individual trajectory scores and report within each teacher:

```text
mean
median
standard deviation
variance
interquartile range
score distribution
```

Because the same task IDs exist for all teachers, also determine for every task which teacher trajectory receives the best proxy score.

For example:

```text
task_001 → DeepSeek
task_002 → GLM
task_003 → Claude
task_004 → DeepSeek
...
```

Report:

```text
fraction of tasks won by each teacher
```

High within-teacher variance or frequent changes in the task-level winning teacher would suggest that global teacher identity may be too coarse a selection unit.

This analysis is performed for **all proxies on the 200-task benchmark** and does not yet require constructing a full training dataset.


---

# 11. Proxy sample-efficiency / task-count scaling analysis

Run this **only after the main proxy benchmark on 200 matched tasks has completed**.

The purpose is to answer:

> How many matched tasks are actually needed before a proxy produces a stable teacher ranking?

Do not rerun the expensive proxy computation where unnecessary. Reuse the already stored **trajectory-level scores** from the 200-task benchmark.

Construct nested, seeded subsets:

```text
n ∈ {10, 25, 50, 100, 200}
```

If scores on a larger matched pool are already available, optionally extend to:

```text
500
```

The subsets must be nested:

```text
10 ⊂ 25 ⊂ 50 ⊂ 100 ⊂ 200
```

and use the same task IDs for every teacher and, where possible, every proxy.

For each `n`:

1. take the corresponding matched task subset;
2. aggregate each teacher's trajectory scores over those tasks;
3. recompute the teacher ranking;
4. compare it with the ranking obtained using all 200 tasks;
5. evaluate against the published SFT teacher ranking;
6. compute task-bootstrap uncertainty.

Report per proxy:

```text
teacher ranking at n=10
teacher ranking at n=25
teacher ranking at n=50
teacher ranking at n=100
teacher ranking at n=200

Kendall tau-b at each n
pairwise accuracy at each n
bootstrap uncertainty at each n
probability each teacher occupies each rank
```

Also report the **smallest task budget at which the predicted teacher ranking becomes reasonably stable**.

This is a **proxy sample-efficiency analysis**, not another teacher experiment: there is still exactly one trajectory per teacher per task, and only the number of matched tasks used to estimate the teacher-level average changes.

---


# 12. Construct candidate proxy-selected SFT datasets (STOP and wait for manial go of step 12)

This stage is **not primarily for evaluating the proxies again**. Its purpose is to use the most promising trajectory-level proxies from the 200-task benchmark to **construct candidate multi-teacher SFT datasets** for a later training experiment. 

After completing the primary 200-task proxy benchmark and all proxy implementations, stop and wait for manual review before proceeding to Stage 11.

The question is:

> Even if one teacher is best on average, can we build a better dataset by selecting the best teacher trajectory separately for each task?

After the primary proxy benchmark, select approximately **2–3 promising trajectory-level proxies** based on:

```text
teacher-ranking agreement
ranking stability / uncertainty
computation cost
trajectory-level heterogeneity
student specificity where relevant
conceptual relevance
```

Do not select methods solely because they happen to match the known teacher ranking best.

Apply only these selected proxies to the **full matched Terminal-Lego task pool**.

For every matched task, score all available teacher trajectories and retain the **best-ranked trajectory according to that proxy's score direction**:

```text
task_i

Claude trajectory     score
Qwen trajectory       score
GLM trajectory        score
DeepSeek trajectory   score

→ select proxy-preferred trajectory
```

This produces one candidate multi-teacher dataset per proxy, where the selected teacher may differ across tasks.

Examples:

```text
tor_selected
global_nll_selected
local_nll_selected
rsr_selected
scrf_selected
```

For each candidate dataset, report:

```text
number of tasks
fraction selected from each teacher
proxy-score distribution
comparison with using the globally best teacher for every task
```

Save:

```text
task_id
selected_teacher
selected_trajectory_id
all candidate teacher scores
selected proxy score
```

so each dataset can be reproduced later.

**Do not run SFT on these datasets in this stage.**

They are saved for a later controlled experiment comparing:

```text
globally best single-teacher dataset
vs.
proxy-selected teacher per task
vs.
random teacher per task
```

The later experiment tests whether **trajectory-level teacher selection** improves over choosing one teacher globally.