#!/bin/bash
#SBATCH --job-name=trp-bridge-tmuxprobe
#SBATCH --partition=alpha
#SBATCH --account=p_agents_finetuning
#SBATCH --nodes=1 --ntasks=1 --cpus-per-task=6
#SBATCH --gres=gpu:1
#SBATCH --mem=16G
#SBATCH --time=00:20:00
#SBATCH --output=/data/horse/ws/frwe188h-trp-shared/teacher_ranking_proxy/runs/logs/%x-%j.out
# Does a tmux server started by one bridge exec survive the exec's exit?
# (local mode's F1 exec-death). Node-local bridge + 1 worker + probe script.
set -euo pipefail
source /data/horse/ws/frwe188h-trp-shared/repo/hpc/dotenv/zih_alpha_franziska.env
BV=$HARBOR_BRIDGE_VENV; PKG=$("$BV/bin/python" -c "import harbor.environments.apptainer as m, pathlib; print(pathlib.Path(m.__file__).parent)")
"$BV/bin/python" "$PKG/server.py" --host 127.0.0.1 --port 9910 >/dev/null 2>&1 &
"$BV/bin/python" "$PKG/worker.py" --bridge-url http://127.0.0.1:9910 --sif-cache "$HARBOR_SIF_CACHE" --staging-base "${TMPDIR:-/tmp}/bridge_staging" --num-workers 1 >"$TRP_SHARED/teacher_ranking_proxy/runs/logs/tmuxprobe-worker-$SLURM_JOB_ID.log" 2>&1 &
trap 'kill $(jobs -p) 2>/dev/null || true' EXIT
for i in $(seq 30); do curl -sf http://127.0.0.1:9910/status >/dev/null && break; sleep 2; done
export APPTAINER_BRIDGE_URL=http://127.0.0.1:9910
"$BV/bin/python" -u "$TRP_SHARED/teacher_ranking_proxy/runs/bridge-remote/tmux_probe.py" "$TRP_SHARED/teacher_ranking_proxy/runs/bridge-remote/tasks/task_06714/environment" 2>&1 | grep -v '^\s*File\|^\s*\^'
