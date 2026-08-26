# §14 Milestone-1 Reproduction — ZIH Capella, Terminal-Lego, n=10, seed=42

Date: 2026-08-18. Host: ZIH Capella (Slurm job, 1× H100-SXM5 94 GB on node c128,
14 CPUs, 100 GB RAM cgroup). Runtime: **Apptainer 1.5.0 only** (no Docker).
Companion to the sequoia record in `MILESTONE_REPORT.md` — that file is the
sequoia outcome; this one is the cluster reproduction.

## 1. Outcome

Pipeline **completed end-to-end**: dataset audit + manifest byte-identical to
the committed sequoia manifest (8070 runnable tasks, sha `c6a2704f…`), runtime
check green, all 10 sampled trials (seed 42 — same task IDs as sequoia) ran to
terminal outcomes with the student served locally (vLLM 0.16.0, Qwen/Qwen3-8B,
bf16, TP=1, `mp` executor), and **21 trace rows exported**
(`--export-traces --export-verifier-metadata --export-episodes last`).
Nothing was uploaded anywhere; all artifacts live under
`/data/cat/ws/frwe188h-otagent/teacher_ranking_proxy/runs/terminal_lego-n10-s42/`
(job `harbor_jobs/student-Qwen__Qwen3-8B-apptainer-r6`, plus
`metadata/generation_metadata.json`).

| task | Capella reward | Capella termination | sequoia reward |
|---|---|---|---|
| task_00230 | 0.0 | task timeout (600 s) | 0.0 |
| task_01077 | 0.0 | verifier failure | 0.0 |
| task_01154 | 0.0 | verifier failure | 0.0 |
| task_01398 | 0.0 | verifier failure | 0.0 |
| task_02135 | 0.0 | verifier failure | **1.0** |
| task_02447 | 0.0 | task timeout (600 s) | 0.0 |
| task_02798 | 0.0 | verifier failure | **1.0** |
| task_06515 | 0.0 | verifier failure | 0.0 |
| task_07539 | 0.0 | verifier failure | **1.0** |
| task_07587 | 0.0 | verifier failure | 0.0 |

**0/10 reward=1 vs sequoia's 3/10.** Agent behavior was materially different,
not just unlucky sampling: e.g. task_02798 succeeded on sequoia in 2 episodes /
118 s, while here the agent ran 24 episodes with two context summarizations and
still failed. Terminus-2 samples stochastically, so run-to-run variance is
expected — but a 3→0 drop on the same tasks warrants suspicion of an
**environment-behavior difference (Docker vs Apptainer)**. That is precisely
what the §10 parity test would decide, and it could not run here (below).
Do not treat the Capella rewards as interchangeable with the sequoia ones
until parity is settled.

## 2. Parity status (§10): PASSED (cross-host smoke) + oracle validation PASSED

**2026-08-18 update: `--parity-check --apptainer-only` (baseline-independent
oracle validation, added for Docker-less hosts) PASSED — all 10 golden
solutions scored reward 1.0** under Apptainer with per-task Slurm-step
limits enforced (`oracle_all_reward_1=true`,
`runs/terminal_lego-n10-s42/parity/parity_report.json`). The environment —
containers, mounts, task files, verifiers, limits — is therefore proven
correct on Capella. Consequently the §1 student reward gap (0/10 vs 3/10)
cannot be a task-execution/verification defect; remaining suspects are
sampling stochasticity and serving-stack differences. **2026-08-18 later update: the cross-host comparison RAN and PASSED.**
Franziska imported the sequoia `parity-smoke` baseline; `--parity-check`
(with the new cached-half reuse, commit 4ca3010d) compared sequoia-Docker
against freshly-run Capella-Apptainer on task_06515: solve exit codes [0,0],
normalized oracle output equal, verifier rewards [1.0, 1.0], 16 artifact
files compared, 0 differing -> `parity_passed: true`
(`runs/parity-smoke/parity/parity_report.json`; sequoia's own halves
preserved as `oracle_apptainer.sequoia` / `parity_report.sequoia.json`).
Scope caveat: the smoke covers 1 task; the full n=10 docker baseline was
not part of the transferred tarball.

## 3. Per-task resource limits (skill §4 mandate)

Initial milestone trials ran WITHOUT per-task limits (no user D-Bus for
systemd-run; shim degraded — recorded per the skill mandate). **Fixed
2026-08-18: the shim now launches every instance in its own overlapped Slurm
job step** (`srun --mem/--cpus-per-task`, inherited SLURM launch env
stripped) — verified limcheck: `memory.max = 1073741824` for `--memory
1024M`, one pinned CPU. A task exceeding its budget is OOM-killed in its own
step (trial reward 0); job and other tasks unaffected — Docker-equivalent
semantics restored.

## 4. What broke and how it was fixed (in order encountered)

Repo commits (branch `merge-capella` = `main`):

1. `hpc/harbor_yaml/trace_docker_16concurrency_ctx32k.yaml` lost in the
   upstream merge → restored from pre-merge trp (`3555539a`).
2. **Ray cannot start on Capella**: the dashboard agent needs ~35–50 s to spawn
   off the Lustre venv; the raylet aborts after a fixed ~15 s wait for its port
   file (not tunable in Ray 2.57 via env or `--system-config`). TP=1 needs no
   Ray → `OTAGENT_VLLM_NO_RAY=1` skips the Ray head and serves vLLM with the
   `mp` executor (`2c19aa02`).
3. Old harbor CLI predates `jobs start --yes` → gated behind
   `OTAGENT_HARBOR_NO_YES=1`.
4. Apptainer shim: probe the user systemd bus before using the
   `systemd-run --scope` wrapper; degrade to unlimited instances otherwise
   (previously every `instance start` died with "Failed to connect to bus").

Environment facts driving the above: **Python spawns off the Lustre venv are
pathologically slow** (`import vllm`: 2 m 49 s cold, ~3 s CPU). Fix: copy the
venv to node-local disk at job start (`tar` stream, ~1 min) and run everything
via `TEACHER_PROXY_VENV=$TMPDIR/venv_trp` → import drops to 2.5 s. This is now
the standard Capella launch pattern (see the updated
`teacher-proxy-cluster-setup` skill).

Venv-local patches (in `site-packages`, both the Lustre venv and the node
copy — NOT in git; re-apply after any venv rebuild):

5. `harbor/environments/apptainer.py` (pin
   `laude-institute/harbor@penfever/temp-override`, reinstalled over the merged
   repo's marin-community pin whose apptainer env is bridge-based): the
   cached-SIF branch of `start()` **returned early and never started the
   instance** — every exec then failed with "no instance found". Fixed to fall
   through to instance start.
6. `harbor/agents/terminus_2/tmux_session.py`: (a) tmux socket moved off the
   `/logs/agent` bind (Lustre — Unix sockets hang every tmux client) onto
   container-local `/tmp` with a unique name; (b) the first tmux server start
   can wedge in `do_wait` under concurrent-instance startup load with **no
   timeout**, stalling trials forever — bootstrap now bounded with `timeout`
   and retried on a fresh socket (observed: retries fire and succeed).
7. `ray/_private/node.py`: `raylet_start_wait_time_s` 30→300 (moot once Ray was
   bypassed; harmless).

## 5. Reproduction command (Capella)

```bash
# inside a Slurm allocation (see skill for full setup):
VT=${TMPDIR:-/tmp}/venv_trp   # node-local venv copy
WS_ROOT=/data/cat/ws/frwe188h-otagent \
TEACHER_PROXY_VENV=$VT OTAGENT_VLLM_NO_RAY=1 VLLM_SKIP_RAY_PROBE=1 \
OTAGENT_HARBOR_NO_YES=1 \
$VT/bin/python data/teacher_ranking_proxy/generate_trajectories.py \
    --dataset terminal_lego --model Qwen/Qwen3-8B --runtime apptainer \
    --n-tasks 10 --seed 42 --resume
```

Wall-clock for the final clean attempt: ~50 min (server bringup ~5 min, trials
~35 min incl. tmux-bootstrap retries, verifiers + export ~10 min).

## 6. Full-stack validation run (2026-08-18, run `terminal_lego-n10-s42-rayv`)

Fresh run with every fix active simultaneously: **default Ray serving**
(head + vLLM-on-Ray — first fully Ray-backed run on Capella), harbor from
`franziweindel/harbor@franziska/hpc-fixes`, per-task Slurm-step limits, tmux
socket/bootstrap fixes, node-local venv with shebang rewrite. Outcome:
**rc=0, 10/10 trials terminal (8 verifier failures, 2 task timeouts), zero
infrastructure exceptions, 14 trace rows exported.** The tmux bootstrap
retry fired 30 times and recovered every time (0 trials lost).

Rewards again 0/10 vs sequoia's 3/10 — twice in a row, so the gap is
systematic, and §2's oracle result exonerates task execution/verification.
Leading hypothesis: sequoia's student traces ran under **Docker** while these
run under Apptainer, and the agent's interactive environment differs subtly
(fakeroot identity, apt-via-shim, prompt/tty details) in ways that may cost a
weak 8B student its marginal successes (e.g. task_02798: sequoia success in
2 episodes; here 24 episodes with 2 context summarizations, then failure).
Deciding this needs the sequoia Docker baseline (§2) or a per-episode trace
comparison — flagged as follow-up, not blocking the proxy stage.

## 7. Anchored tmux: the root cause of the 0/10 student runs (2026-08-18)

On this Apptainer 1.5 + Slurm setup, **any process spawned by `apptainer exec`
dies when that exec exits** (verified with tmux servers and detached
processes, with and without `--userns`). Harbor's agent loop assumes Docker
semantics (server created by one exec, used by dozens); consequently every
pre-fix student trial ran against a dead terminal — keystrokes only echoed by
the pty, never executed — which is the actual cause of §1/§6's 0/10 rewards.
One-shot execs (oracle, verifier, installs) were never affected, which is why
those all passed.

**Fix (fork `de8f642`, trp `3d84ff8d`):** the shim's per-instance Slurm step
holds one persistent exec that starts a per-instance tmux server on
`/tmp/harbor_tmux_<instance>.sock`; `ApptainerEnvironment` advertises the
socket and `TmuxSession` attaches to it (gated on `HARBOR_TMUX_ANCHOR=1`,
exported by generate_trajectories for apptainer runs; docker hosts
unaffected). Validated: live `Apptainer>` prompt in episode-0, commands
executing, pane recordings growing. Per-task isolation unchanged — one
container/anchor/server per task, torn down with the trial. All student
traces generated BEFORE this fix (both n=10 runs, the partial n=200) are
dead-terminal data and must not feed the proxy stage.
