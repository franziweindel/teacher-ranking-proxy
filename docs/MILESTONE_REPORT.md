# §14 First-Milestone Report — Terminal-Lego, n=10, seed=42

> **Host scope: this report is sequoia-specific.** Every result below (runtime
> checks, the Docker parity baseline, trace generation) was obtained on the lab
> host **sequoia** (4× L40, Docker + unprivileged Apptainer,
> `/mnt/hdd_pool_bigsur` storage). It is kept as the sequoia outcome record.
> The ZIH Capella reproduction (Slurm, H100, Apptainer-only,
> `/data/cat/ws/frwe188h-otagent` storage) is a separate run — its results do
> not overwrite this file; the sequoia Docker oracle baseline referenced here is
> the comparison reference for parity on Docker-less hosts.

Date: 2026-08-17. Scripts delivered: `prepare_dataset.py`, `generate_trajectories.py`
(plus `apptainer_patch/apptainer` and `.env.example`). `compute_proxies.py` /
`evaluate_ranking.py` are **not started**, per the hard stop.

## 1. Teacher trajectory schema and repo layout (§2)

`SWE-Lego/Terminal-Lego-Traj-8k` @ `02f33fb252b15308f309e7d10472bb781eeb1ecf` is
**one JSON file per teacher** (no configs/splits, no teacher column):

```text
terminal-lego-deepseek-v3-2-8k.json    → DeepSeek-V3.2
terminal-lego-glm-5-8k.json            → GLM-5
terminal-lego-qwen-3-5-plus-8k.json    → Qwen3.5-Plus
terminal-lego-opus-4-6-8k.json         → Claude Opus 4.6
```

Each file is a JSON list of records:

```json
{"conversations": [{"from": "human"|"gpt", "value": "..."}, ...],
 "metadata": {"oracle_passed_task": "task_NNNNN", "difficulty": "easy|medium|hard"}}
```

Task-ID field: `metadata.oracle_passed_task` (same in all four files). Teacher is
keyed by filename slug; all four map cleanly onto the candidate teachers.
Conversation roles are strictly alternating `human`/`gpt`.

## 2. Records and unique task IDs per teacher

| teacher | records | unique task IDs | duplicates |
|---|---|---|---|
| DeepSeek-V3.2 | 8,300 | 8,300 | 0 |
| GLM-5 | 8,300 | 8,300 | 0 |
| Qwen3.5-Plus | 8,300 | 8,300 | 0 |
| Claude Opus 4.6 | 8,318 | 8,318 | 0 |

## 3. Task-set matching — they do NOT match exactly

DeepSeek/GLM-5/Qwen3.5-Plus share an **identical** 8,300-task set. Opus has a
different 8,318-task set (182 tasks not covered by the trio; 164 trio tasks not
covered by Opus). Union 8,482, intersection 8,136. Additionally **66**
intersection tasks do not resolve to a `task_NNNNN/` directory in
`SWE-Lego/Terminal-Lego-15k` (5,733 of the union's 5,956 unresolvables are
outside the intersection anyway). Final matched runnable set: **8,070 tasks**
(95.1 % of the union). Per §2 this was surfaced as a hard stop; Franziska
approved `--allow-intersection` on 2026-08-17. All drop-out IDs are recorded in
the manifest header.

## 4. Manifest schema

`artifacts/task_manifest.jsonl` (committed; 8,071 lines, sha256 `c6a2704f…`):
line 1 is a header object (dataset, repo IDs + revisions, teachers,
teacher→file map, published rankings/scores, layout + audit numbers, all
missing/unresolvable IDs, `allow_intersection` flag); each further line is

```json
{"task_id": "task_NNNNN", "task_dir": "task_NNNNN",
 "teacher_record_index": {"DeepSeek-V3.2": i, "GLM-5": j, "Qwen3.5-Plus": k, "Claude Opus 4.6": l}}
```

— indices into each teacher's JSON array at the pinned revision, so records are
retrievable without duplicating trajectory content.

## 5. Sampled task IDs (pinned §3 sampler, n=10, seed=42, draw order)

```text
task_06515, task_01154, task_00230, task_07587, task_02798,
task_02447, task_02135, task_01398, task_07539, task_01077
```

Sample file: `runs/terminal_lego-n10-s42/sampled_task_ids.json` (with
`manifest_sha256` provenance; reuse + mismatch cross-check verified to fail
loudly on a differing seed).

## 6. Example teacher trajectory

DeepSeek-V3.2 on `task_06515` (difficulty "hard", 28 turns): system+task prompt
in the first `human` turn; `gpt` turns are JSON objects
(`{"analysis": ..., "plan": ..., "commands": [...], "task_complete": ...}`);
subsequent `human` turns carry `New Terminal Output:` with the terminal
observation (e.g. `root@43e37b4f1d88:/app# cd /app/task_file …`).

## 7. Example runnable task

`task_00230` in Terminal-Lego-15k: `task.toml` ([metadata], `[verifier]
timeout_sec=300`, `[agent] timeout_sec=600`, `[environment] cpus=1 memory="1G"
storage="5G"`), `environment/Dockerfile` (FROM ubuntu:22.04, COPY ./task_file),
`instruction.md`, `solution/solve.sh`, `tests/test.sh` (+pytest files). The 10
sampled tasks use bases `ubuntu:22.04` (6) and `python:3.13-slim-bookworm` (4).

## 8. Docker ↔ Apptainer parity (§10) — PASSED, option 1

Apptainer 1.5.3 is installed **system-wide** (`/usr/bin/apptainer`, official
PPA package plus `uidmap`, installed by Franziska with root on 2026-08-17;
setuid `newuidmap` gives full `--fakeroot`). Both halves run the **oracle
solution** (`chmod +x /solution/solve.sh && /solution/solve.sh`) per task
through Harbor's own DockerEnvironment/ApptainerEnvironment, then the real
verifier (`tests/test.sh`), with `artifacts: ["/app/task_file"]` downloaded for
file-state comparison.

**Result: all 10 tasks equivalent** — equal solve exit codes, equal normalized
oracle output, equal verifier rewards (10× reward 1.0 on both), no exceptions,
and identical artifact trees (content-hash comparison). Report:
`runs/terminal_lego-n10-s42/parity/parity_report.json`. Rerunning the same
command is the regression test; it was rerun from scratch (fresh SIF builds)
after the switch to the system install and passed again 10/10.

A small compatibility patch remains (`apptainer_patch/apptainer`, ~100 lines;
no Harbor sources modified) covering Harbor↔Apptainer-1.5 mismatches:
`instance start` accepts no `--pwd` flag (dropped; the workdir is injected on
`exec --userns` instead), instances are forced onto the user-namespace flow
(mixing the setuid instance flow with userns execs breaks image-file
ownership), each instance gets a disk-backed `--overlay` as Docker's writable
layer (a tmpfs layer is RAM-backed: size-capped and charged against the memory
limit, which broke package-installing verifiers), and Harbor's
`--memory/--cpus` flags are translated into a `systemd-run --user --scope`
wrapper — **`task.toml` cpu/memory limits are now enforced under both
runtimes** (verified live: each Harbor instance runs in a
`harbor-apptainer-<trial>.scope` with the task's `memory.max`/`cpu.max`).
This relies on host setup done with root on 2026-08-17: `apptainer` +
`uidmap` packages, and systemd user-session cgroup delegation
(`/etc/systemd/system/user@.service.d/delegate.conf` with
`Delegate=cpu cpuset io memory pids`). The right long-term fix is a small
upstream Harbor patch, which would make this file deletable.

*(Historical note: the milestone was first passed using an unprivileged
big-disk Apptainer install with additional single-uid-namespace workarounds —
apt-dir redirect, `TAR_OPTIONS=--no-same-owner`, `APPTAINER_IGNORE_SUBUID`.
Those became unnecessary with the system install and were removed; the
big-disk install was deleted after the regression rerun passed.)*

Normalizations applied before text comparison (documented in the report JSON):
hex ids ≥12 chars, container hostnames, ISO + ctime timestamps, /tmp paths,
pids, rates/durations, apt progress/ordering lines, pip "Downloading" vs
"Using cached" wording, and `ls -l` metadata (block totals, mode strings —
umask differs docker-root vs userns —, link counts, directory sizes, mtimes).
File sizes and contents still compared (text artifacts content-hashed after
normalization; `__pycache__/*.pyc` excluded — bytecode embeds source mtimes).

## 9. Exact student trace-generation command

```bash
python data/teacher_ranking_proxy/generate_trajectories.py \
    --dataset terminal_lego --model Qwen/Qwen3-8B --runtime docker \
    --n-tasks 10 --seed 42 --resume
```

which wraps (verbatim from the run):

```bash
<venv>/bin/python data/local/run_tracegen.py \
  --harbor_config runs/terminal_lego-n10-s42/traces/Qwen__Qwen3-8B/docker/harbor_job.yaml \
  --tasks_input_path runs/terminal_lego-n10-s42/tasks \
  --datagen_config hpc/datagen_yaml/qwen3_8b_vllm_serve_32k_1xH200.yaml \
  --harbor_env docker --model Qwen/Qwen3-8B --agent terminus-2 \
  --job_name student-Qwen__Qwen3-8B-docker --n_concurrent 10 --n_attempts 1 \
  --gpus 1 --experiments_dir .../experiments
```

Serving inherited from the datagen YAML (max_model_len 32768, max_output 8192,
bf16, TP=1, max_num_seqs 32, gpu_memory_utilization 0.9 — no L40 adjustment was
needed); sampling parameters are the Terminus-2 defaults. Verifier enabled;
agent `override_timeout_sec` and environment resource overrides nulled so
`task.toml` values apply (§6).

## 10. Student rollout results (Qwen3-8B, Terminus-2, 1 attempt, §6 taxonomy)

| task | reward | termination reason | episodes | agent time |
|---|---|---|---|---|
| task_06515 | 0.0 | verifier failure | 2 | 316 s |
| task_01154 | 0.0 | verifier failure | 2 | 126 s |
| task_00230 | 0.0 | verifier failure | 5 | 345 s |
| task_07587 | 0.0 | verifier failure | 3 | 132 s |
| task_02798 | 1.0 | success | 2 | 118 s |
| task_02447 | 0.0 | task timeout (600 s) | 7 | 600 s |
| task_02135 | 1.0 | success | 3 | 122 s |
| task_01398 | 0.0 | verifier failure | 2 | 179 s |
| task_07539 | 1.0 | success | 2 | 104 s |
| task_01077 | 0.0 | verifier failure | 2 | 87 s |

3/10 reward 1. Each trial preserves the raw Harbor trace: `result.json`
(verifier reward, timings, agent info), `agent/trajectory.json` (schema'd
steps), per-episode `prompt.txt`/`response.txt`/`debug.json`, tmux pane file,
`verifier/{reward.txt,ctrf.json,test-stdout.txt}`, `config.json`, `trial.log`.
Task instruction verified present in episode-0 prompt; per-task timeouts
respected (timeout task terminated at exactly its 600 s `task.toml` limit).
Resumability verified: rerunning the command reports
"all 10 trials already complete; nothing to do".

Honesty note on attempt provenance: two early infrastructure defects (4 tasks
whose empty `environment/task_file/` dir the HF upload drops — restored by the
pipeline now — and an over-eager resume that deleted two timed-out trials)
forced rerun batches (`…-r2`, `…-r3` job dirs). The canonical per-task result
is the earliest infrastructure-clean trial; superseded/extra trials remain on
disk. Four tasks also have an accidental second attempt in `-r2` (kept, not
counted) — task_07539 flips 1.0→0.0 between attempts, a reminder that
Terminus-2 sampling is stochastic.

## 11. Storage locations

`data/teacher_ranking_proxy/runs` → symlink →
`/mnt/hdd_pool_bigsur/userdata/franziska/teacher_ranking_proxy/runs` (verified;
run dir 710 MB, NFS big disk, 3.2 TB free). `HF_HOME`/`HF_DATASETS_CACHE`
resolved to `/mnt/hdd_pool_bigsur/userdata/franziska/hf_cache` (27 GB: teacher
files ~840 MB, task files, Qwen3-8B 16 GB). Nothing from this pipeline wrote to
`/home` (`~/.cache/huggingface` contains no Lego/Qwen artifacts; today's writes
there are from an unrelated process). Venv (~13 GB incl. caches) also on the
big disk. `~/.apptainer` symlinked to the big disk.

## 12. GPU devices used

GPU **1** (NVIDIA L40) for all vLLM serving, with `CUDA_VISIBLE_DEVICES=1` and
`CUDA_DEVICE_ORDER=PCI_BUS_ID`. GPU 2 is off the PCI bus and never touched;
GPU 0 was busy (~32 GB used) and GPU 3 idle throughout. Selection is automatic
(idle among 0/1/3, preference 1→3→0) and recorded in metadata.

## 13. Unresolved issues / notes

1. **Task-set mismatch is permanent** (8,070 matched runnable tasks, §3 above);
   every later stage inherits it via the manifest.
2. **Terminal-Lego-15k is incomplete relative to the trajectories**: 66 matched
   tasks unresolvable; 4 of our 10 sampled tasks are missing their (empty)
   `environment/task_file/` dir upstream — the pipeline restores such dirs and
   logs it. Worth reporting upstream.
3. **Resource limits under Apptainer**: enforced via the systemd-run scope
   wrapper in `apptainer_patch` (requires user-session cgroup delegation,
   configured on this host). On ZIH, rerun `--check-runtime`/`--parity-check`
   on an allocated compute node; Slurm additionally enforces job-level limits
   there regardless.
4. **NVML is broken host-wide** (dead GPU 2): vLLM crashes at import without the
   `sitecustomize.py` meta-path patch installed in the venv (documented there).
5. `harbor jobs start --auto-resume` cannot resume our jobs (fresh vLLM
   served-model id each run ⇒ "different config"); resume therefore runs a new
   job restricted via `-t <task>` filters and merges trial results across job
   dirs.
6. vLLM 0.11.2 warm-up + model load takes ~4–5 min per generation invocation on
   the L40 (weights are cached).
7. Nothing was committed or pushed; no HF uploads were made (`--upload-hf`
   exists, defaults off, requires `HF_STUDENT_TRACES_REPO`). The stray
   `" .env"` file (leading space — was not covered by `.gitignore`'s `.env`
   rule and contained API keys) was renamed to `.env`, which is ignored.
