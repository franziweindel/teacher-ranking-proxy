#!/bin/bash
#SBATCH --job-name=trp-bridge-student
#SBATCH --partition=alpha
#SBATCH --account=p_agents_finetuning
#SBATCH --nodes=1 --ntasks=1 --cpus-per-task=6
#SBATCH --gres=gpu:1
#SBATCH --mem=64G
#SBATCH --time=02:00:00
#SBATCH --output=/data/horse/ws/frwe188h-trp-shared/teacher_ranking_proxy/runs/logs/%x-%j.out
# Rung 3b: an AGENT (terminus-2 on vLLM Qwen3-8B) solving tasks through the
# bridge, everything on ONE Alpha node — exercises the tmux session and the
# one-exec-per-action path that the oracle loopback does not touch.
# Usage: sbatch student_alpha_loopback.sbatch <sample.json> [max_turns]
set -euo pipefail
REPO=/data/horse/ws/frwe188h-trp-shared/repo
source $REPO/hpc/dotenv/zih_alpha_franziska.env
BV=$HARBOR_BRIDGE_VENV
PORT=9910
PKG=$("$BV/bin/python" -c "import harbor.environments.apptainer as m, pathlib; print(pathlib.Path(m.__file__).parent)")
LOGS=$(dirname "${1:?sample file}")
"$BV/bin/python" "$PKG/server.py" --host 127.0.0.1 --port $PORT >"$LOGS/bridge.log" 2>&1 &
"$BV/bin/python" "$PKG/worker.py" --bridge-url "http://127.0.0.1:$PORT" \
    --sif-cache "$HARBOR_SIF_CACHE" --staging-base "${TMPDIR:-/tmp}/bridge_staging" \
    --num-workers 2 >"$LOGS/worker.log" 2>&1 &
trap 'kill $(jobs -p) 2>/dev/null || true' EXIT
export APPTAINER_BRIDGE_URL="http://127.0.0.1:$PORT"
for i in $(seq 30); do curl -sf "$APPTAINER_BRIDGE_URL/status" >/dev/null && break; sleep 2; done
echo "[student] status: $(curl -s $APPTAINER_BRIDGE_URL/status)"
cd $REPO
exec "$BV/bin/python" data/teacher_ranking_proxy/generate_trajectories.py \
    --runtime apptainer_bridge --run-id bridge-student --sample-file "$1" \
    --n-concurrent 2 ${2:+--max-turns $2}
