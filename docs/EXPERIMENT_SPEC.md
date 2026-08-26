# Terminal-Lego Experiment Specification

## 1. Data sources

Primary paper:

```text
Terminal-Lego
arXiv:2606.03461
```

Released teacher trajectories:

```text
SWE-Lego/Terminal-Lego-Traj-8k
```

Runnable Harbor tasks:

```text
SWE-Lego/Terminal-Lego-15k
```

Candidate teachers:

```text
DeepSeek-V3.2
GLM-5
Qwen3.5-Plus
Claude Opus 4.6
```

Default target student:

```text
Qwen/Qwen3-8B
```

Optional second student — **future extension only, out of scope now**:

```text
Qwen/Qwen3-32B
```

All trajectory generation and proxy computation in this implementation targets
**Qwen/Qwen3-8B only**. Qwen3-32B stays in the registry labels for later use;
do not generate trajectories or compute proxies for it. Proxy scores are stored
per student (`proxy_scores/<student>/`, §15.4) so a second student can be added
later without collisions.

Published downstream teacher ranking for Qwen3-8B:

```text
DeepSeek-V3.2 > GLM-5 ≈ Qwen3.5-Plus > Claude Opus 4.6
```

Published downstream ranking for Qwen3-32B:

```text
DeepSeek-V3.2 > GLM-5 > Qwen3.5-Plus > Claude Opus 4.6
```

These rankings are evaluation labels only.

---

# 2. Build a canonical task manifest

`Terminal-Lego-Traj-8k` is the canonical source for the tasks in the four-teacher comparison.

Write a reproducible setup script:

```bash
python data/teacher_ranking_proxy/prepare_dataset.py --dataset terminal_lego
```

The script must:

1. download/load the released teacher trajectories for the requested teachers;
2. inspect their actual current schemas rather than assuming field names;
3. extract task IDs;
4. verify that the teacher releases contain the expected matched task set;
5. verify uniqueness/multiplicity of task IDs;
6. verify that each task can be resolved in `SWE-Lego/Terminal-Lego-15k`;
7. write a persistent manifest.

### Trajectory layout is unknown — discover it

`Terminal-Lego-Traj-8k` is a single HF repo. Whether the four teachers appear as
separate files/configs/splits, or as one table with a `teacher` column, is **not
yet known**. Do not assume: enumerate the repo's files and configs, print the
actual schema, and branch — load per-teacher files if that is the layout, or
group by the teacher field if it is a single table.

Report the layout found, the field names used for task ID and teacher, and the
distinct teacher values observed. If those values do not map cleanly onto the
four candidate teachers, **stop and report** rather than guessing a mapping.

Example:

```text
data/teacher_ranking_proxy/artifacts/task_manifest.jsonl
```

Each entry should contain enough information to retrieve:

```text
task_id
DeepSeek trajectory record
GLM-5 trajectory record
Qwen3.5-Plus trajectory record
Opus-4.6 trajectory record
corresponding Terminal-Lego-15k task
```

Use indices/identifiers rather than duplicating all trajectory content into the manifest.

### Matched-set policy

The manifest contains only tasks present for **all** requested teachers — a proxy
comparison is only meaningful on a common task set — but the intersection must
never be silent. Always report per teacher: record count, unique task IDs, IDs
missing relative to the union, and the final matched-set size.

* **Default four Terminal-Lego teachers:** the sets are expected to match
  exactly. If they do not, **stop and report.** `--allow-intersection` is
  required to proceed; without it a mismatch is a hard error.
* **User-supplied `--teachers`, or a future dataset:** intersecting is the
  defined behavior, but log the drop-out count and dropped IDs and record both in
  the manifest metadata.

Once validation passes, later scripts should use the manifest rather than repeating the full dataset audit every run.

Do not permanently create a second filtered copy of Terminal-Lego-15k solely to represent this subset.

---

# 3. Deterministic task sampling

There is no separate sampler script. `prepare_dataset.py`,
`generate_trajectories.py` and `compute_proxies.py` all accept `--n-tasks` and
`--seed` directly, so a run can be launched in one command:

```bash
python data/teacher_ranking_proxy/generate_trajectories.py \
    --dataset terminal_lego --model Qwen/Qwen3-8B \
    --n-tasks 10 --seed 42
```

Whichever script is given `--n-tasks/--seed` draws the sample and **writes
`runs/<run_id>/sampled_task_ids.json` before starting any agent.** A script given
`--sample-file` instead reads that file and does not sample.

## The sampling algorithm is pinned

Because more than one script can sample, the procedure is fixed here so that any
correct implementation yields byte-identical output. Do not vary it:

1. read the task IDs from `task_manifest.jsonl`;
2. deduplicate, then **sort ascending** — never rely on file or dataset order;
3. draw with `random.Random(seed).sample(sorted_ids, n_tasks)`;
4. preserve the draw order returned by `sample()`; do not re-sort afterwards;
5. fail if `n_tasks` exceeds the number of available tasks.

## The run identifier

`--run-id` defaults to a value **derived from what defines the task set**, not
from a timestamp:

```text
run_id = f"{dataset}-n{n_tasks}-s{seed}"      e.g.  terminal_lego-n10-s42
```

A timestamp default would be wrong here: every invocation would open a new run
directory, so the sample file would never be found, the cross-check below would
never fire, and `--resume` would never match anything.

Deriving it from `dataset/n_tasks/seed` and nothing else is deliberate. The
student model, runtime, agent and attempt count are **not** part of `run_id` —
they are subdirectories inside the run:

```text
runs/terminal_lego-n10-s42/
├── sampled_task_ids.json
├── traces/<student>/<runtime>/
├── proxy_scores/<student>/<proxy>.jsonl
└── metadata/
```

That is what makes Qwen3-8B and Qwen3-32B share one sampled task set, as §3
requires, instead of silently drawing their own.

Rules:

* an explicit `--run-id` always wins;
* with `--sample-file` and no `--run-id`, the run is the sample file's parent
  directory;
* `run_id` must be filesystem-safe — slashes in model names never appear in it;
* to force a fresh directory for the same configuration, pass an explicit
  `--run-id`; do not auto-suffix.

## Reuse and cross-check

`sampled_task_ids.json` records the IDs together with the provenance that
produced them:

```json
{"task_ids": [...],
 "n_tasks": 10, "seed": 42,
 "manifest_sha256": "...", "dataset": "terminal_lego"}
```

If a script is asked to sample for a `run_id` whose sample file already exists,
it **reuses that file** and errors out if the recorded `n_tasks`, `seed`, or
`manifest_sha256` disagree with what was requested. So the convenience of
`--n-tasks/--seed` never silently produces a second, different task set — a
mismatch is a loud failure, not a fresh sample.

Defaults:

```text
seed = 42
```

Requirements:

* same seed + same N + same manifest → exactly same task IDs, in any script;
* different seed → different sample where possible;
* preserve sample order;
* fail if `N` exceeds available tasks;
* write the selected IDs before running any agents.

All teacher analysis and student trajectories for a run must use these same task
IDs. Do not independently resample teacher and student tasks — the pinned
algorithm plus the cross-check above is what enforces this.

### Terminology

Two distinct artifacts; never use one word for both:

```text
task_manifest.jsonl     "the manifest"     — the full validated task set
sampled_task_ids.json   "the sample file"  — the N IDs drawn for one run
```

CLI flags referring to the second use `--sample-file`, never `--sample-manifest`.

Every script that runs on a task subset accepts **either** `--sample-file`
**or** `--n-tasks/--seed`. Passing both is an error.

---

# 4. Materialize runnable tasks only when needed

Teacher trajectories come from:

```text
SWE-Lego/Terminal-Lego-Traj-8k
```

Runnable environments come from:

```text
SWE-Lego/Terminal-Lego-15k
```

When Harbor needs to execute a sampled task, resolve/materialize only the selected task directories.

Avoid storing a permanent redundant 8K copy unless technically required.

Temporary/run-specific task directories are fine.

---

# 5. Reuse OpenThoughts-Agent trace generation

Before writing new infrastructure, inspect and reuse code from at least:

```text
data/local/run_tracegen.py
data/commons.py
hpc/local_runner_utils.py
hpc/arg_groups.py
```

OpenThoughts-Agent already provides reusable infrastructure for:

```text
Harbor task execution
Terminus-2
local vLLM/Ray serving
trace generation
trace export
Hugging Face upload
endpoint/model configuration
```

Prefer a small wrapper or extension around the existing infrastructure.

Do not copy large implementations into `teacher_ranking_proxy`.

The student trajectory script is `generate_trajectories.py`:

```bash
python data/teacher_ranking_proxy/generate_trajectories.py \
    --dataset terminal_lego \
    --n-tasks 10 --seed 42 \
    --model Qwen/Qwen3-8B \
    --runtime docker
```

or, to run on a sample drawn earlier,
`--sample-file runs/<run_id>/sampled_task_ids.json` in place of
`--n-tasks/--seed` (§3).

Default:

```text
student = Qwen/Qwen3-8B
agent = Terminus-2
attempts_per_task = 1
```

Do not use `Qwen/Qwen3-8B-Base` unless explicitly requested.

## Generation parameters are inherited, not invented

Serving/generation settings come from the existing datagen YAML
`hpc/datagen_yaml/qwen3_8b_vllm_serve_32k_1xH200.yaml`
(`max_model_len` 32768, `max_output_tokens` 8192, bfloat16, TP=1); sampling
parameters (temperature etc.) come from the Terminus-2 agent defaults in
Harbor. Do not introduce new temperature/top-p values. The YAML was written for
an H200 — adjust only `max_num_seqs`/`gpu_memory_utilization` if the GPU
requires it (on sequoia: L40 46 GB; on ZIH Capella: H100-SXM5 94 GB, usually no
adjustment needed). Record all resolved values in the §13 metadata.

---

# 6. Timeout and stopping behavior

Use each Terminal-Lego/Harbor task's existing configured timeout.

Do not introduce a fixed turn limit by default.

Optional:

```bash
--max-turns N
```

may be provided for explicit experiments only.

For every run record termination reason, distinguishing at least:

```text
success
verifier failure
task timeout
optional max-turn limit
model/API failure
environment failure
```

Do not silently replace task timeouts with a global arbitrary timeout.

---

# 7. Local and HPC container runtimes

## Local reference

Use:

```text
Docker
```

as the reference runtime because it already works with Harbor/OpenThoughts-Agent.

## Apptainer is the second backend

`generate_trajectories.py` accepts:

```bash
--runtime {docker,apptainer}
```

Docker is the local reference; **Apptainer is the portability target** and the
only alternative backend to implement. It is rootless and daemonless — the usual
HPC constraint — and is what ZIH provides.

Do not implement a Podman backend. It was considered as an intermediate step and
deliberately dropped: it is a third runtime to maintain that is not the actual
target, and Apptainer can be installed here without root anyway (see
`system_prompt.md`), so there is nothing to be gained by routing through it.

Singularity is Apptainer's predecessor and is largely CLI-compatible; treat it as
a fallback name for the same backend rather than a separate implementation, and
detect which is present (§9).

If a requested runtime is unavailable, fail with an error naming it — never fall
back silently.

## HPC targets

The pipeline will later be used on:

```text
TU Dresden / ZIH HPC
https://tu-dresden.de/zih/hochleistungsrechnen/hpc

LRZ MCML
https://doku.lrz.de/mcml-1899757881.html
```

The intended ZIH portability target is:

```text
Singularity / Apptainer
```

For LRZ, detect the supported runtime on an allocated compute node rather than assuming one.

Do not design the pipeline around Daytona.

---

# 8. Search before implementing a new runtime backend

Before implementing Apptainer/Singularity support:

1. inspect current Harbor runtime/environment APIs;
2. search Harbor GitHub;
3. search public GitHub for:

   * Harbor + Apptainer,
   * Harbor + Singularity;
4. inspect existing custom `BaseEnvironment` implementations;
5. reuse an existing backend if possible.

If no Apptainer backend exists, any existing non-Docker `BaseEnvironment` is
still worth reading as a structural reference for how Harbor expects a runtime to
be plugged in — read it, but implement Apptainer (§7).

Keep runtime-specific code separate from Terminal-Lego-specific code.

Implement the smallest extension necessary.

---

# 9. Runtime detection

The diagnostic is a dry-run mode of `generate_trajectories.py`, not a separate
script:

```bash
python data/teacher_ranking_proxy/generate_trajectories.py --check-runtime
```

It reports and exits without loading a model or running a task, so it is cheap to
run anywhere. It must report availability/version of:

```text
docker
apptainer
singularity
```

On HPC, this check must be runnable **inside an allocated compute node**, because software available on login nodes may differ.

Never silently substitute one runtime for another.

---

# 10. Docker ↔ HPC-runtime parity test

Before running LLM trajectories with an alternative backend, verify environment parity independently of the LLM.

Like `--check-runtime` (§9), the parity test is a mode of
`generate_trajectories.py`, not a fifth script:

```bash
python data/teacher_ranking_proxy/generate_trajectories.py --parity-check \
    --n-tasks 10 --seed 42
```

It loads no student model and makes no LLM calls. Rerunning this exact command
is the regression test referred to at the end of this section; its comparison
report goes under `runs/<run_id>/parity/`.

**On sequoia (original host):** only Docker was installed, but a second runtime
could be added — see the runtime notes in `system_prompt.md`. In order of
preference:

1. install **Apptainer** unprivileged (no root needed, and it is the ZIH target)
   and run the full comparison — this is the intended path;
2. run the Docker half alone and persist it as the **reference baseline** for a
   later runtime — useful work, but it does not satisfy the criterion;
3. defer to the allocated HPC compute node.

**On ZIH Capella (current cluster):** the situation is inverted — Apptainer
1.5.0 is system-wide, Docker does not exist and cannot be installed. The full
Docker↔Apptainer comparison therefore requires the **sequoia Docker oracle
baseline** (`runs/<run_id>/parity/oracle_docker/`, which lives on sequoia's
disk, not in git) to be copied into the cluster run directory; only the
Apptainer half runs locally. Until that baseline is transferred, the parity
criterion on Capella is *not yet satisfied* — run trace generation anyway but
record the skipped parity and its reason in the run metadata.

Never fabricate the result, compare Docker against itself, or drop the test from
the milestone report. If only one half was possible, mark the parity criterion
*not yet satisfied* and say so explicitly.

Use:

```text
10 tasks
seed = 42
```

sampled using the normal task sampler.

For each task:

1. initialize the task using Docker;
2. initialize the same task using the Apptainer backend;
3. execute the **same deterministic command sequence** in both;
4. compare observable results.

Prefer reference/oracle solution commands where they provide a deterministic executable sequence.

For each command compare:

```text
command
exit code
normalized stdout
normalized stderr
```

Also compare relevant final state:

```text
created/modified files
file hashes or contents
working directory where relevant
verifier reward
pass/fail result
```

The key requirement is:

> same command in the same benchmark state should produce semantically equivalent output/state under both runtimes.

Do not require byte-for-byte equality for irrelevant nondeterministic output such as:

```text
timestamps
temporary paths
process IDs
random runtime-generated IDs
```

If normalization is required, document exactly what is normalized.

A candidate backend passes only when all 10 tasks produce equivalent task semantics and verifier outcomes.

If parity fails, investigate:

```text
mounts/binds
permissions
UID/GID
working directory
environment variables
networking
filesystem behavior
entrypoint
process behavior
GPU exposure
verifier execution
```

Then rerun the **same 10-task test**.

Preserve this as a regression test.

---

# 11. Student trajectory output

For each student rollout preserve the complete Harbor trace.

At minimum retain:

```text
task_id
instruction
assistant/model turns
terminal commands
terminal observations
reward/verifier result
success/failure
termination reason
runtime
student model
agent
attempt
configuration metadata
```

Do not throw away the raw Harbor representation even if a normalized representation is created later.

Store outputs under:

```text
data/teacher_ranking_proxy/runs/<run_id>/
```

where `runs/` is a **symlink to the big storage** — on sequoia
`/mnt/hdd_pool_bigsur/userdata/franziska/teacher_ranking_proxy/runs/`, on ZIH
Capella `$WS_ROOT/teacher_ranking_proxy/runs/` (i.e.
`/data/cat/ws/frwe188h-otagent/...`); the scripts create the correct link from
`WS_ROOT`. Trajectory dumps are large and must not consume `/`
(sequoia) or `/home` (cluster, 50 GB quota). See the filesystem layout in
`system_prompt.md`.

---

# 12. Hugging Face upload

Student trajectories should optionally be uploadable to a configurable Hugging Face dataset repo.

For example:

```text
<HF_USERNAME>/terminal-lego-qwen3-8b-student-traces
```

Prefer reusing OpenThoughts-Agent/Harbor's existing trace-upload functionality.

Credentials must come from:

```text
HF_TOKEN
```

via environment variables or an ignored `.env`.

Provide:

```text
.env.example
```

containing:

```text
HF_TOKEN=
HF_USERNAME=
HF_STUDENT_TRACES_REPO=
```

Ensure:

```text
.env
```

is ignored by git.

Never:

```text
hard-code a token
commit a token
print a token
write a token into run metadata
```

## Uploading is opt-in and never automatic

Build the capability; do not exercise it. `--upload-hf` defaults to **off**, and
must only ever run when Franziska asks for that specific upload — not because a
milestone finished, not because the repo name is configured, not as a convenient
default. The same applies to `git push` and to any other write to a remote; see
the publishing rule in `system_prompt.md`.

Uploads are effectively irreversible: a pushed dataset can be cached or indexed
after deletion, and both the teacher trajectories and the student traces here are
unreleased research data. If an upload looks useful, propose it and stop.

When Franziska does request an upload, the target must be a **private** repo
under her own HF account (`HF_USERNAME`) that she has explicitly named — never
a public repo, never an org repo. Smoke-testing the upload path follows the
same rule: only on her explicit request, only against that private repo. Git
remotes have no such exception: never `git push` anything, anywhere.

---

# 13. Reproducibility metadata

Record at least:

```text
OpenThoughts-Agent git commit
Terminal-Lego dataset revision
teacher trajectory dataset revision
student model ID
student model revision if available
Harbor version
vLLM version
container backend
seed
run_id
sampled task IDs
attempts/task
timeout configuration
generation parameters
GPU device IDs actually used
resolved HF_HOME / HF_DATASETS_CACHE / run output paths
```

---

# 14. First milestone

Do not immediately implement the full proxy benchmark.

First make this entire pipeline work for:

```text
n_tasks = 10
seed = 42
student = Qwen/Qwen3-8B
agent = Terminus-2
attempts = 1
```

The milestone consists of:

### Data validation

Confirm:

```text
number of records per teacher file
number of unique task IDs
whether all four task-ID sets match
whether all corresponding runnable tasks exist
```

### Runtime validation

Run the 10-task Docker parity baseline.

If implementing an alternative HPC backend now, execute the same deterministic commands in both runtimes and compare results.

### Student rollout validation

Generate the 10 Qwen3-8B trajectories.

Confirm:

```text
correct task instruction
correct environment
full terminal interaction saved
verifier result saved
timeout respected
run resumable
```

### Report

After completing the milestone, stop and report:

1. actual teacher trajectory schema, and which repo layout was found (§2);
2. number of records and unique task IDs per teacher;
3. whether teacher task sets match exactly, plus any drop-out counts;
4. manifest schema;
5. 10 sampled task IDs;
6. one example teacher trajectory;
7. one example runnable task;
8. Docker/runtime parity results — including, if the comparison was blocked by
   the missing second runtime (§10), an explicit statement that the parity
   criterion is **not yet satisfied**;
9. exact student trace-generation command;
10. success/failure status for all 10 student trajectories, with termination
    reasons from the §6 taxonomy;
11. where outputs are stored — confirm the `runs/` symlink resolves to the big
    disk and that `HF_HOME`/`HF_DATASETS_CACHE` did not write to `/home`;
12. GPU devices actually used, given that GPU 2 is dead and 0/3 are often busy;
13. any unresolved issues.

**Hard stop.** Once the 10 student trajectories generate successfully and the
§10 parity question is resolved — the Docker↔Apptainer test passes, or the
report explicitly states why it is *not yet satisfied* — deliver the report
above and stop completely. Do not start `compute_proxies.py`, `evaluate_ranking.py`, or
anything in `PROXY_SPEC.md`: the proxy stage will be reviewed and re-specified
with Franziska first — including decisions deliberately left open there, such
as which LLM judge SCRF uses and its API budget.

---

# 15. Deliverables

**Four Python files**, one per pipeline stage:

```text
data/teacher_ranking_proxy/
├── prepare_dataset.py        cheap, network   → task_manifest.jsonl
├── generate_trajectories.py  expensive, GPU   → student traces
├── compute_proxies.py        varies by proxy  → per-task proxy scores
└── evaluate_ranking.py       free, seconds    → rankings + metrics
```

The split follows cost, which is why it matters. `evaluate_ranking.py` must stay
a **pure function of files on disk** — student NLL over thousands of trajectories
costs GPU-hours, while metric definitions get revised constantly, so the two must
never share a script.

`compute_proxies.py` spans a wide cost range: the §2 teacher-only baselines are
CPU text analysis, the `PROXY_SPEC.md` §3 likelihood proxies run the student
model over every teacher trajectory (GRACE also needs backward passes), and SCRF
is mostly CPU plus LLM-judge calls. Per-proxy caching therefore matters — a cheap
proxy must not wait on an expensive one.

Three things that might look like separate modules are folded in:

```text
dataset registry   → dict at the top of prepare_dataset.py (§15.1)
task sampling      → --n-tasks/--seed on each script that needs it (§3)
runtime diagnostic → generate_trajectories.py --check-runtime (§9)
parity test        → generate_trajectories.py --parity-check (§10)
```

## Preferred, not mandatory

Keep the file count minimal — these four are the target, and the four-stage split
itself is the requirement. Beyond that, these are defaults; deviate where
something is clearly better and note the reason in the report:

* **Prefer folding helpers in** over adding a fifth file, so the pipeline stays
  readable end to end. Add one when it genuinely earns its place, not for
  tidiness.
* **Prefer files on disk over cross-imports.** Sharing through the manifest,
  sample file and score files keeps each script independently runnable and
  resumable. The cost is that the ~5-line sampling routine is written more than
  once; that is tolerable because §3 pins the algorithm and the sample file
  carries a provenance cross-check, so drift fails loudly instead of silently
  producing two task sets. A small shared import is fine if genuinely cleaner.
* **Prefer these formats:** `jsonl` for the manifest and score files, `json` for
  the sample file and metadata. If Parquet or another format fits better at
  scale, switch — keep one format per artifact type and record it in the run
  metadata.

---

## 15.1 The dataset registry — inside `prepare_dataset.py`

A module-level dict at the top of `prepare_dataset.py`, making the pipeline
dataset-agnostic so OpenThoughts-Agent can be added later without rewriting the
scripts:

```python
DATASETS = {
    "terminal_lego": DatasetSpec(
        traj_repo="SWE-Lego/Terminal-Lego-Traj-8k",
        task_repo="SWE-Lego/Terminal-Lego-15k",
        default_teachers=[
            "DeepSeek-V3.2", "GLM-5", "Qwen3.5-Plus", "Claude Opus 4.6",
        ],
        published_rankings={
            "Qwen/Qwen3-8B":  [["DeepSeek-V3.2"], ["GLM-5", "Qwen3.5-Plus"],
                               ["Claude Opus 4.6"]],
            "Qwen/Qwen3-32B": [["DeepSeek-V3.2"], ["GLM-5"],
                               ["Qwen3.5-Plus"], ["Claude Opus 4.6"]],
        },
        published_scores=None,   # fill in from the paper if available
    ),
    # "ot_agent": DatasetSpec(...)   # added later, no script changes required
}
```

* HF repo IDs are hard-coded **here and only here** — no repo ID appears in any
  other file.
* Rankings are lists of tie-groups, so `GLM-5 ≈ Qwen3.5-Plus` is representable
  rather than silently forced into a strict order (`PROXY_SPEC.md` §1.1).
* `published_scores` holds the downstream benchmark numbers when known; they
  enable regret and NDCG (`PROXY_SPEC.md` §1.2). Leave `None` if unknown — do not
  invent numbers.
* Per-dataset schema quirks (field names, how teachers are keyed) live in a
  per-dataset handler within this file, not in `if dataset ==` branches
  scattered across the other three scripts.
* Registry entries are experiment definitions: do not edit them to make a run
  succeed.

**`prepare_dataset.py` copies the resolved entry into the manifest header** —
teachers, published rankings, published scores, repo IDs and revisions. The other
three scripts read it from there. That is why they need no import of this file
and no registry of their own; in particular `evaluate_ranking.py` gets its
ground-truth labels from the manifest, not from a hard-coded copy.

---

## 15.2 `prepare_dataset.py`

```bash
python data/teacher_ranking_proxy/prepare_dataset.py \
    --dataset terminal_lego \
    [--teachers DeepSeek-V3.2 GLM-5 Qwen3.5-Plus "Claude Opus 4.6"] \
    [--n-tasks N --seed 42] [--run-id ID] \
    [--allow-intersection] \
    [--out artifacts/task_manifest.jsonl]
```

`--teachers` defaults to the registry's `default_teachers`. Behavior is §2.

With `--n-tasks/--seed` it additionally writes
`runs/<run_id>/sampled_task_ids.json` per §3 — useful for inspecting the sample
before spending GPU time. Without them it only builds the manifest.

Report to stdout and into manifest metadata: detected layout and field names,
records and unique task IDs per teacher, whether the sets match, matched-set size
with any drop-outs, tasks unresolvable in the runnable repo, and the commit SHAs
of both HF repos.

Idempotent. Also ensures the `runs/` symlink and the HF cache variables point at
the big disk.

---

## 15.3 `generate_trajectories.py`

```bash
python data/teacher_ranking_proxy/generate_trajectories.py \
    --dataset terminal_lego \
    --model Qwen/Qwen3-8B \
    --runtime {docker,apptainer} \
    (--n-tasks N --seed 42 | --sample-file PATH) \
    [--attempts 1] [--agent terminus-2] [--max-turns N] \
    [--run-id ID] [--resume] [--upload-hf]

python data/teacher_ranking_proxy/generate_trajectories.py --check-runtime
python data/teacher_ranking_proxy/generate_trajectories.py --parity-check \
    --n-tasks 10 --seed 42
```

* A **thin wrapper** over `data/local/run_tracegen.py` and the existing Harbor /
  vLLM / trace-export infrastructure — do not reimplement or copy it (§5).
* `--parity-check` runs the §10 Docker↔Apptainer comparison without loading a
  model or calling an LLM.
* GPU selection: use a **free** GPU among devices **0, 1, 3**, checked with
  `nvidia-smi -i 0,1,3` — bare `nvidia-smi` errors out because GPU 2 has fallen
  off the PCI bus. Never use GPU 2; prefer an idle device over one already
  running a job (0 and 3 are often busy). Record the device actually used (§13).
* With `--n-tasks/--seed`, samples per the pinned §3 algorithm and writes the
  sample file **before** any agent starts; reuses and cross-checks an existing
  sample file for the same `run_id`.
* `--check-runtime` reports runtime availability and exits without loading a
  model or running a task (§9).
* `--resume` skips completed task/attempt pairs, tracked by an on-disk marker
  rather than inferred from partial output.
* Records a §6 termination reason per rollout; respects per-task timeouts.
* Fails loudly if the requested runtime is unavailable — never falls back.
* Writes §13 metadata to `runs/<run_id>/` on the big disk (§11).
* `--upload-hf` defaults to off and is never passed unprompted (§12).

---

## 15.4 `compute_proxies.py`

```bash
python data/teacher_ranking_proxy/compute_proxies.py \
    --dataset terminal_lego \
    --student Qwen/Qwen3-8B \
    --proxy {teacher_bench,traj_length,cmd_error,tor,global_nll,local_nll,
             rsr,scrf,car,grace,scas,lark} \
    (--n-tasks N --seed 42 | --sample-file PATH) \
    [--run-id ID] [--resume]
```

`--proxy` is repeatable; each proxy writes its own file.

Critical requirement: **write per-teacher, per-task scores**, not only aggregates
— task-level granularity is what makes the bootstrap in `PROXY_SPEC.md` §1.3
possible at all.

```text
runs/<run_id>/proxy_scores/<student>/<proxy>.jsonl
    {"proxy", "teacher", "task_id", "score", <components...>, "meta"}
```

* Persist separable components alongside the combined score (CAR's quality vs.
  compatibility, SCRF's `q_S`/`E_T`/`R_T`).
* Cache and resume per (proxy, teacher, task), so adding one proxy does not
  recompute the others.
* Record wall-clock and GPU-seconds per proxy — cost is a reported result.
* Student-dependent proxies use the **pre-SFT** student; never a fine-tuned
  checkpoint.
* Proxies needing student trajectories (SCRF) read them from the
  `generate_trajectories.py` run directory, and fail clearly if absent.

---

## 15.5 `evaluate_ranking.py`

```bash
python data/teacher_ranking_proxy/evaluate_ranking.py \
    --dataset terminal_lego \
    --run-id ID \
    [--students Qwen/Qwen3-8B] \
    [--proxies ...] [--bootstrap 10000] [--seed 42] \
    [--out runs/<run_id>/ranking_report.{json,md}]
```

Pure function of the score files — no GPU, no network, no model loading.

Reads `proxy_scores/<student>/*.jsonl`, aggregates to teacher-level scores,
produces
predicted orderings, and computes every metric in `PROXY_SPEC.md` §1 against the
`published_rankings` carried in the manifest header (§15.1) — never against a
hard-coded copy of the labels. Emits JSON plus a Markdown table, including the
null baselines and the student-specificity result.
