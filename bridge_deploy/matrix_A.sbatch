#!/bin/bash
#SBATCH --job-name=trp-matrix-A
#SBATCH --partition=alpha
#SBATCH --account=p_agents_finetuning
#SBATCH --nodes=1 --ntasks=1 --cpus-per-task=12
#SBATCH --gres=gpu:2
#SBATCH --mem=96G
#SBATCH --time=03:00:00
#SBATCH --output=/data/horse/ws/frwe188h-trp-shared/teacher_ranking_proxy/runs/logs/%x-%j.out
# Matrix cell A: relay + 10 workers + driver on ONE Alpha node (2 GPUs so we
# may have 12 CPUs), 10 tasks: oracle first, then the agent (terminus-2 on
# vLLM Qwen3-8B). Usage: sbatch matrix_A.sbatch <sample.json> [max_turns]
set -euo pipefail
REPO=/data/horse/ws/frwe188h-trp-shared/repo
source $REPO/hpc/dotenv/zih_alpha_franziska.env
BV=$HARBOR_BRIDGE_VENV; PORT=9910
PKG=$("$BV/bin/python" -c "import harbor.environments.apptainer as m, pathlib; print(pathlib.Path(m.__file__).parent)")
LOGS=$(dirname "${1:?sample file}")
"$BV/bin/python" "$PKG/server.py" --host 127.0.0.1 --port $PORT >"$LOGS/bridge.log" 2>&1 &
"$BV/bin/python" "$PKG/worker.py" --bridge-url "http://127.0.0.1:$PORT" \
    --sif-cache "$HARBOR_SIF_CACHE" --staging-base "${TMPDIR:-/tmp}/bridge_staging" \
    --num-workers "${WORKERS_PER_NODE:-10}" >"$LOGS/worker.log" 2>&1 &
trap 'kill $(jobs -p) 2>/dev/null || true' EXIT
export APPTAINER_BRIDGE_URL="http://127.0.0.1:$PORT"
for i in $(seq 30); do curl -sf "$APPTAINER_BRIDGE_URL/status" >/dev/null && break; sleep 2; done
echo "[A] status: $(curl -s $APPTAINER_BRIDGE_URL/status)"
cd $REPO
RUN=$(basename "$LOGS")
echo "[A] === oracle ==="
"$BV/bin/python" data/teacher_ranking_proxy/generate_trajectories.py \
    --parity-check --apptainer-only --runtime apptainer_bridge --run-id "$RUN" \
    --sample-file "$1" --n-concurrent "${N_CONCURRENT:-10}"
echo "[A] === agent ==="
"$BV/bin/python" data/teacher_ranking_proxy/generate_trajectories.py \
    --runtime apptainer_bridge --run-id "$RUN" --sample-file "$1" \
    --n-concurrent "${N_CONCURRENT:-10}" ${2:+--max-turns $2}
