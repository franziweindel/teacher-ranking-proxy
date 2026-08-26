# Making Apptainer trajectory generation work: all fixes, and how to adopt them

Audience: the OT-Agent team (and future us). This documents everything that was
needed to run **local Harbor trajectory generation under Apptainer** on an HPC
cluster (ZIH Capella: Slurm, Lustre, Apptainer 1.5, no Docker, no user
systemd) — what upstream has, what it lacks, which fix lives in which file,
and the exact adoption recipe. Validated end-to-end 2026-08-18: oracle
solutions 10/10 reward 1.0, cross-host Docker↔Apptainer parity passed, agent
sessions verified live (prompt visible, commands executing).

## 0. TL;DR for adoption

```toml
# pyproject / requirements — pin the fixed harbor fork:
harbor @ git+https://github.com/franziweindel/harbor.git@franziska/hpc-fixes
```

plus, from this repo:

* put `data/teacher_ranking_proxy/apptainer_patch/` **first in PATH** (the
  scripts here do it automatically via `ensure_big_disk_env()`);
* export `HARBOR_TMUX_ANCHOR=1` for apptainer runs
  (`generate_trajectories.py` does this automatically);
* if driving harbor via `hpc/harbor_utils.py` with this old-generation harbor
  CLI: `OTAGENT_HARBOR_NO_YES=1` (its `jobs start` predates `--yes`);
* on Lustre-backed clusters: copy the venv to node-local disk at job start
  **and rewrite console-script shebangs** (see §4.3 / the
  `teacher-proxy-cluster-setup` skill).

## 1. The environment facts that break naive Apptainer use

These are properties of Apptainer 1.5 + Slurm + Lustre, not bugs in our code —
every fix below traces back to one of them:

| # | fact | consequence |
|---|---|---|
| F1 | **Anything an `apptainer exec` spawns is killed when that exec exits** (verified with tmux servers and detached processes, with and without `--userns`) — Docker's `docker exec` does NOT do this | Harbor's agent loop (one exec starts a tmux server, dozens of later execs use it) talks to a dead terminal; agent keystrokes echo but never execute, rewards all 0, while one-shot oracle/verifier execs pass |
| F2 | **Unix domain sockets on Lustre hang clients forever** | tmux socket must never live on a Lustre bind (`/logs/agent`) |
| F3 | **No user systemd / D-Bus on compute nodes**; job cgroups are root-owned and not user-writable | Apptainer's own `--memory/--cpus` cannot work (nothing to delegate cgroups); `systemd-run --user` fails with "Failed to connect to bus" |
| F4 | **Slurm job steps get real root-created cgroups** (`srun --overlap --mem/--cpus-per-task` inside the job; steps must pass untyped `--gres=gpu:1` here) | the only per-task limit mechanism available — and it works (verified `memory.max` = request) |
| F5 | Nested `srun` inherits the parent step's `SLURM_*` launch env | new steps fail with "CPU binding outside of job step allocation" unless those vars are stripped |
| F6 | Lustre small-file I/O is ~65× slower than node-local disk | cold `import vllm` = 2m49s; Ray's fixed ~15s internal startup waits expire; slow shells; fix = node-local venv copy + shebang rewrite |
| F7 | fuse-overlayfs breaks apt's `_apt` sandbox; overlays on network FS break package installs | overlays must be node-local; apt needs a config shim inside each instance |

## 2. The Harbor fork — `github.com/franziweindel/harbor` @ `franziska/hpc-fixes`

Base: `laude-institute/harbor @ penfever/temp-override` (the last harbor
generation with a **local** ApptainerEnvironment). All fixes are ordinary
commits on the branch:

| commit | file | fix |
|---|---|---|
| `8eef8a9` | `src/harbor/environments/apptainer.py` | **Cached-SIF early return**: `start()` returned right after finding a cached SIF and never started the instance — every later exec failed "no instance found". Now falls through to instance start (and only builds when there is no cache hit). Latent upstream bug; only fires on apptainer + warm SIF cache + fresh trial. |
| `8eef8a9` | `src/harbor/agents/terminus_2/tmux_session.py` | **Socket off Lustre** (F2): tmux socket moved from the `/logs/agent` bind to container-local `/tmp` with a unique per-trial name. |
| `8eef8a9` | same | **Bounded, retried tmux bootstrap**: server start wrapped in `timeout` + health probe + retry on a fresh socket; `timeout` guards on follow-up tmux commands (previously a single wedge stalled the trial forever with no timeout). |
| `eb2810a` | same | **Shell-readiness gate**: after session creation, send `echo HARBOR_SHELL_READY_$((40+2))` and wait for the *evaluated* output line before anyone types; then clear. Prevents the agent's first screen being unexecuted keystroke echo on slow container shells. (This gate is also what surfaced F1 cleanly.) |
| `de8f642` | `src/harbor/environments/apptainer.py` + `tmux_session.py` | **Anchored tmux (the F1 fix, the big one)**: when `HARBOR_TMUX_ANCHOR=1`, the environment advertises a deterministic per-instance socket (`/tmp/harbor_tmux_<instance>.sock`, server started by the shim from a persistent exec — see §3) and `TmuxSession` *attaches* to that server (waiting up to 90s) instead of spawning its own doomed one. Docker/legacy hosts are untouched (flag off → old behavior). |

Isolation guarantee preserved throughout: one container, one anchor exec, one
tmux server, one socket **per task trial**; all torn down together.

## 3. Pipeline-side fixes in this repo

### 3.1 `data/teacher_ranking_proxy/apptainer_patch/apptainer` (the shim)

A wrapper that sits first in PATH and adapts Harbor's docker-shaped apptainer
calls. Pre-existing behavior (from the sequoia milestone): `--userns`
instances, disk-backed per-instance overlays on node-local `$TMPDIR` (F7),
apt sandbox fixup inside each instance (F7), `--pwd /workspace --userns`
injected on exec. Added on Capella:

* **Per-task limits via Slurm job steps** (F3/F4): each `instance start
  --memory/--cpus` now runs in its own overlapped job step whose cgroup
  enforces exactly the requested budget — a runaway task is OOM-killed alone
  (reward 0), job and siblings untouched. Falls back to the systemd user
  scope on lab hosts; warns loudly if neither exists.
* **SLURM env cleaning for nested steps** (F5) + per-instance step logs.
* **Anchored tmux server** (F1): the instance's step holds one persistent
  `apptainer exec` that installs/starts the tmux server on the deterministic
  socket and keeps it alive exactly as long as the instance.

### 3.2 `data/teacher_ranking_proxy/generate_trajectories.py`

* exports `HARBOR_TMUX_ANCHOR=1` for apptainer runs;
* `--parity-check --apptainer-only`: baseline-independent oracle validation
  (all golden solutions must score reward 1) for Docker-less hosts;
* parity no longer demands a runtime's *binary* when that half's oracle
  results are already cached (enables comparing an imported Docker baseline);
* applies `harbor_patches.py` at startup (below).

### 3.3 `data/teacher_ranking_proxy/harbor_patches.py`

Belt-and-braces: idempotently verifies/applies the §2 fixes to whatever
harbor is installed, with appliedness markers; refuses loudly if the harbor
version drifted. With the fork pinned it is a no-op check — it exists so a
rebuilt venv from a wrong pin cannot silently regress.

### 3.4 `hpc/harbor_utils.py`

`OTAGENT_HARBOR_NO_YES=1` omits `jobs start --yes` (this harbor generation's
CLI predates the flag; everything else in the command matched).

### 3.x Cold image builds vs the task's build budget

Each task's `task.toml` sets `[environment] build_timeout_sec` (120s for
Terminal-Lego). Harbor charges the WHOLE of `environment.start()` against it,
and on this cluster that includes building the image: Apptainer has no layer
cache, so every image is built from scratch (measured on an idle Capella node:
22s to import a base image, 81s for a full build with `apt` + `pip`). Run ten of
those concurrently and the budget is missed, and the trial dies with
`EnvironmentStartTimeoutError` before the agent ever starts.

This is a first-encounter problem only: once a task's SIF is in the cache,
environment start is ~1s and the budget is irrelevant. Harbor keeps four
independent budgets (verifier, agent setup, agent, environment build —
`trial/trial.py`), so build time never eats the agent's clock; a slow build can
only fail its own step, it cannot shorten or bias what the agent is measured on.

The task's budget is therefore never relaxed by default
(`environment_build_timeout_multiplier` = 1): quietly rewriting what a task asks
for is exactly what §6 forbids. Instead, warm a new task set once with
`TRP_BUILD_TIMEOUT_MULTIPLIER=8`, then run normally — verified 11/11 at
multiplier 1 on a warm cache, including the deferred-overlay task. A run whose
images are not cached warns before it starts rather than timing out mid-build.

## 4. How a run executes, and the two Apptainer architectures

### 4.1 Anatomy of one generation job (local mode)

One Slurm allocation (1 GPU + 14 CPUs on Capella): vLLM serves the student
with **continuous batching** (rolling pool up to `max_num_seqs=32`; each
agent request joins/leaves the in-flight batch immediately — no fixed
batches); `--n-concurrent` (CLI on `generate_trajectories.py`, default 10)
tasks run in parallel, each in its own Apptainer instance whose CPU/memory
come **from that task's own `task.toml [environment]`** (Terminal-Lego tasks
all declare `cpus=1, memory="1G"`, which is why 10 × 1 CPU + vLLM + driver
fits Capella's 14-CPUs-per-GPU cap). Inference and task/verifier execution
naturally overlap. Note the asymmetry: the GPU could batch ~32 concurrent
agents; the node's CPUs cap us at ~10 — scaling past that needs remote task
execution (below), more GPUs (`--gres=gpu:2` → 28 CPUs) or `--exclusive`.

### 4.2 Upstream (marin-community/harbor@main): bridge/worker — a different, complementary architecture

Upstream DOES have working Apptainer support, but as **standing
infrastructure**: you deploy a long-lived HTTP "bridge" service plus
persistent worker processes on HPC compute nodes (sbatch scripts for
JUWELS/JURECA/JUSUF ship with the module); the Harbor driver never runs
`apptainer` itself — it POSTs start/exec/stop to the bridge
(`APPTAINER_BRIDGE_URL`). Their persistent workers solve the same F1 problem
(exec-spawned processes dying) that our persistent anchor exec solves — but
at the cost of deploying and operating services per cluster. **You do NOT
need the bridge on ZIH** — local mode replaces it at single-node scale.
Where the bridge becomes interesting: cross-cluster scale-out, e.g. workers
on Barnard CPU nodes with vLLM on Capella (tasks over the network, like the
repo's standard Daytona datagen path) → 30+ concurrent tasks per GPU.
Prerequisite to verify first: Barnard→Capella node TCP reachability.

### 4.3 What exists nowhere upstream

The local mode itself, and every piece of it: the shim (overlays, apt fixup,
Slurm-step per-task limits, anchored tmux server), the harbor local-mode
fixes (§2 — new relative to BOTH laude-institute and marin-community
harbor), `--parity-check --apptainer-only` and cached-half parity, and the
auto-patcher. Local mode is also the only way to run Docker↔Apptainer parity
comparisons on one host.

## 5. Validation summary (what "works" means)

* `--check-runtime`: apptainer detected, GPU visible under Slurm.
* `--parity-check --apptainer-only`: **10/10 oracle tasks reward 1.0** with
  per-task limits enforced (limcheck: `memory.max` = request, 1 pinned CPU).
* Cross-host parity (imported sequoia Docker baseline vs Capella Apptainer,
  task_06515): exit codes equal, normalized output equal, rewards [1.0, 1.0],
  0/16 artifact files differing → `parity_passed: true`.
* Agent loop (post anchored-tmux): episode-0 terminal shows a live
  `Apptainer>` prompt, commands demonstrably execute, pane recordings grow.

Full narrative and per-fix forensics: `MILESTONE_REPORT_CAPELLA.md`.
