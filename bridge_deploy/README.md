# Bridge mode deployment (ZIH)

Bridge mode = marin-community/harbor@main (native bridge/worker support) in a
SECOND venv (`venv_bridge`); the master venv keeps the local-mode fork
(franziweindel/harbor@franziska/hpc-fixes). Local mode is unaffected.

Build the bridge venv (once):
    uv venv --python 3.12 $WS_ROOT/teacher_ranking_proxy/venv_bridge
    uv pip install -p $WS_ROOT/teacher_ranking_proxy/venv_bridge/bin/python \
        -e '.[datagen,harbor-docker]'   # pyproject pins harbor -> marin@main

Run order: start_bridge_zih.sh -> sbatch zih_workers.sbatch ->
    generate_trajectories.py --runtime apptainer_bridge  (needs
    APPTAINER_BRIDGE_URL; --check-runtime reports bridge status).

STATUS (2026-08-25): rung 3 (single-node loopback, loopback_alpha.sbatch on
Alpha, 3 oracle tasks) PASSES with reward 1/1/1 on harbor-marin
franziska/zih-bridge. Bridge-side fixes live in harbor-marin (worker.py:
--fakeroot + directory overlay + apt fixup); driver-side in
generate_trajectories.py (marin CLI flags, top-level n_concurrent/retry,
no local-mode drift-guard patches in bridge mode).

Alpha (no /data/cat) uses the horse-only twin: hpc/dotenv/zih_alpha_franziska.env,
repo copy $TRP_SHARED/repo, harbor-marin on horse (editable in venv_bridge).

Rung 4 (cross-cluster) PASSES 2026-08-25: relay + workers on Julia
(julia_bridge_workers.sbatch; Julia has an empty queue, Barnard/Romeo are
days deep), driver on the Capella login node dialing julia:9910, oracle
reward 1/1/1 — after harbor-marin's sticky-routing fix (HPC_FIXES.md §4).
Alpha compute nodes also reach julia:9910 (oracle_alpha_remote.sbatch).

Rung 3b (agent through the bridge, one Alpha node, student_alpha_loopback.sbatch,
terminus-2 on vLLM Qwen3-8B, 2 tasks): runs end to end — task_06714 reward 1,
task_03245 reward 0 (model declared task_complete after one batch). The
exec-death risk (APPTAINER_FIXES.md F1) does NOT apply to worker-managed
instances: a tmux server started by one exec survives the next (probed on Julia and
Alpha: tmux_probe.py / tmux_probe_alpha.sbatch). No anchor needed.

Remaining before production use:
1. Scale: many concurrent tasks per worker node, SIF cache warm on horse
   for the full task set (bridge_deploy/sync_sif_cache.sh + real copies of
   the n200 trace_images, most horse entries are still symlinks into cat).
2. Worker hosting: Julia is verified; Barnard/Romeo untested (their queues
   were days deep on 2026-08-25) — same code path, only the sbatch header
   differs.

## Where things run

- **Driver (+ vLLM for agent runs)**: the GPU node (Alpha or Capella). Light
  apart from vLLM, which it launches itself.
- **Workers**: a CPU cluster (Julia, Barnard, Romeo) — that is where the
  containers and keystrokes execute; needs cores and node-local disk, no GPU.
- **Relay**: in the same job as the workers. It is a tiny HTTP process, and
  this is the one placement that works from every driver location, because
  the driver only ever dials OUT. **Capella is outbound-only** — nothing can
  connect *into* a Capella node, so the relay can never be on Capella; a
  Capella driver is fine. A relay on the driver's compute node also works when that
  cluster accepts inbound (tested: Julia workers reach an Alpha compute node)
  but has no advantage. A relay on a **login node does not work
  cross-cluster**: `login1.alpha:9912` was reachable from Alpha compute
  nodes only, not from Julia or Capella (tested 2026-08-26).
- **SIF cache**: `/data/horse` (visible from every cluster; set through the
  env file as `HARBOR_SIF_CACHE`). Never `/data/cat` — Capella only.

## One-command cross-cluster run

`cross_cluster.sh --workers julia|barnard|romeo --driver alpha|capella|capella-login
oracle|student <sample.json> [max_turns]` (from any ZIH login with passwordless
ssh to the cluster logins) submits the relay+workers job on the worker cluster
and the driver job on the GPU cluster in one go. Slurm cannot co-schedule
across clusters, so both are queued at once and the driver idles at start
(`wait_bridge_url.sh`) until the relay job has written its URL to
`runs/bridge_url.<stamp>` on horse; the driver cancels the relay job when it
finishes. `WORKERS_PER_NODE`, `N_CONCURRENT`, `RUN_ID`, `RELAY_TIME` pass
through. The run id defaults to the sample file's directory name.

## Placement matrix (2026-08-26, oracle on the 10 tasks with SIFs on horse, 10 workers/node)

| cell | relay | workers | driver | result |
|---|---|---|---|---|
| A | Alpha node | same node | same node (`matrix_A.sbatch`, 2 GPU / 12 CPU) | oracle 10/10; agent (Qwen3-8B, 30 turns) 2/10 |
| B | Alpha compute node | Julia | same Alpha node (`relay_oracle_alpha.sbatch`) | 10/10 |
| C | login1.alpha (`relay_login.sh`) | Julia | Alpha compute | fails: login node unreachable from other clusters |
| D | Barnard | Barnard | Capella login (`driver_login.sh`) | 10/10, started within minutes |
| E | Romeo | Romeo | Capella login | 10/10, started within minutes |

Samples and task dirs live in `runs/matrix-*/`. Old-base-image handling
(task_10016) is documented in harbor-marin `HPC_FIXES.md` §5–§9.
