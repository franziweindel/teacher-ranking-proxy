#!/bin/bash
#SBATCH --job-name=trp-matrix-B
#SBATCH --partition=alpha
#SBATCH --account=p_agents_finetuning
#SBATCH --nodes=1 --ntasks=1 --cpus-per-task=6
#SBATCH --gres=gpu:1
#SBATCH --mem=32G
#SBATCH --time=02:00:00
#SBATCH --output=/data/horse/ws/frwe188h-trp-shared/teacher_ranking_proxy/runs/logs/%x-%j.out
# Matrix cell B: relay AND driver on this Alpha compute node, workers
# elsewhere (workers_only.sbatch) dialing IN to this node. Publishes the
# relay URL to BRIDGE_URL_FILE. Usage:
#   BRIDGE_URL_FILE=<file> sbatch relay_oracle_alpha.sbatch <sample.json>
set -euo pipefail
: "${BRIDGE_URL_FILE:?}"
REPO=/data/horse/ws/frwe188h-trp-shared/repo
source $REPO/hpc/dotenv/zih_alpha_franziska.env
BV=$HARBOR_BRIDGE_VENV; PORT=${PORT:-9910}
PKG=$("$BV/bin/python" -c "import harbor.environments.apptainer as m, pathlib; print(pathlib.Path(m.__file__).parent)")
LOGS=$(dirname "${1:?sample file}")
"$BV/bin/python" "$PKG/server.py" --host 0.0.0.0 --port $PORT >"$LOGS/bridge.log" 2>&1 &
trap 'kill $(jobs -p) 2>/dev/null || true; rm -f "$BRIDGE_URL_FILE"' EXIT
for i in $(seq 30); do curl -sf "http://127.0.0.1:$PORT/status" >/dev/null && break; sleep 2; done
FQDN=$(hostname -f); case $FQDN in *.*) ;; *) FQDN=$FQDN.hpc.tu-dresden.de ;; esac
echo "http://$FQDN:$PORT" > "$BRIDGE_URL_FILE.tmp" && mv "$BRIDGE_URL_FILE.tmp" "$BRIDGE_URL_FILE"
export APPTAINER_BRIDGE_URL="http://127.0.0.1:$PORT"
echo "[B] relay published: $(cat $BRIDGE_URL_FILE); waiting for workers"
deadline=$(( $(date +%s) + ${WORKERS_WAIT_SEC:-3600} ))
until curl -s "$APPTAINER_BRIDGE_URL/status" | grep -q '"workers_alive": true'; do
    [ "$(date +%s)" -lt "$deadline" ] || { echo "[B] no workers connected"; exit 1; }; sleep 15; done
echo "[B] status: $(curl -s $APPTAINER_BRIDGE_URL/status)"
cd $REPO
"$BV/bin/python" data/teacher_ranking_proxy/generate_trajectories.py \
    --parity-check --apptainer-only --runtime apptainer_bridge --run-id "$(basename "$LOGS")" \
    --sample-file "$1" --n-concurrent "${N_CONCURRENT:-10}"
