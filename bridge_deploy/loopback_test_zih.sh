#!/bin/bash
# Bridge ladder rung 1: bridge + workers + driver all on ONE compute node.
# Proves the marin bridge/worker path end-to-end without any cross-cluster
# networking, so a failure here is the code, not the network.
#
# Run INSIDE an allocation (srun/salloc shell), after:
#   source hpc/dotenv/zih_capella_franziska.env
# Usage: loopback_test_zih.sh [port] [sample_file]
set -euo pipefail
PORT="${1:-9910}"
SAMPLE="${2:-}"
: "${WS_ROOT:?source hpc/dotenv/zih_capella_franziska.env first}"
BV="${HARBOR_BRIDGE_VENV:-$WS_ROOT/teacher_ranking_proxy/venv_bridge}"
SIF_CACHE="${SIF_CACHE:-$WS_ROOT/teacher_ranking_proxy/harbor_sif_cache}"
LOGS="${LOGS:-$WS_ROOT/teacher_ranking_proxy/runs/bridge-loopback}"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
mkdir -p "$LOGS" "$SIF_CACHE"

PKG=$("$BV/bin/python" -c "import harbor.environments.apptainer as m, pathlib; print(pathlib.Path(m.__file__).parent)")
echo "[loopback] harbor apptainer pkg: $PKG"

"$BV/bin/python" "$PKG/server.py" --host 127.0.0.1 --port "$PORT" \
    >"$LOGS/bridge.log" 2>&1 &
BRIDGE_PID=$!
"$BV/bin/python" "$PKG/worker.py" --bridge-url "http://127.0.0.1:$PORT" \
    --sif-cache "$SIF_CACHE" --staging-base "${TMPDIR:-/tmp}/bridge_staging" \
    --num-workers "${WORKERS_PER_NODE:-2}" >"$LOGS/worker.log" 2>&1 &
WORKER_PID=$!
trap 'kill $WORKER_PID $BRIDGE_PID 2>/dev/null || true' EXIT

export APPTAINER_BRIDGE_URL="http://127.0.0.1:$PORT"
for i in $(seq 30); do
    if curl -sf "$APPTAINER_BRIDGE_URL/status" >/dev/null; then break; fi
    sleep 2
done
echo "[loopback] status: $(curl -s "$APPTAINER_BRIDGE_URL/status")"

ARGS=(--parity-check --apptainer-only --runtime apptainer_bridge
      --run-id bridge-loopback)
[ -n "$SAMPLE" ] && ARGS+=(--sample-file "$SAMPLE")
"$BV/bin/python" "$REPO/data/teacher_ranking_proxy/generate_trajectories.py" "${ARGS[@]}"
echo "[loopback] logs in $LOGS (bridge.log, worker.log)"
