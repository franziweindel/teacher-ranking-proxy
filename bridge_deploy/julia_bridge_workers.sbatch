#!/bin/bash
#SBATCH --job-name=trp-bridge-julia
#SBATCH --partition=julia
#SBATCH --account=p_agents_finetuning
#SBATCH --nodes=1 --ntasks=1 --cpus-per-task=16
#SBATCH --mem=96G
#SBATCH --time=04:00:00
#SBATCH --output=/data/horse/ws/frwe188h-trp-shared/teacher_ranking_proxy/runs/logs/%x-%j.out
# Bridge ladder rung 4 (cross-cluster): bridge SERVER + WORKERS on Julia (the
# SMP cluster, reachable from every ZIH cluster; near-empty queue), drivers on
# the GPU clusters dial OUT to it. Submit from julia.hpc.tu-dresden.de.
#   export APPTAINER_BRIDGE_URL=http://julia.hpc.tu-dresden.de:9910
# If BRIDGE_URL_FILE is set (cross_cluster.sh does this) the URL is written
# there once the relay answers, so a driver job on another cluster can wait
# for it instead of hard-coding the host.
set -euo pipefail
source /data/horse/ws/frwe188h-trp-shared/repo/hpc/dotenv/zih_alpha_franziska.env
# Pick a free port: Julia is one big SMP node, so two relay jobs can share a
# host and a fixed port collides ("Address already in use").
PORT=${PORT:-$(python3 -c 'import socket; s=socket.socket(); s.bind(("",0)); print(s.getsockname()[1])')}
HARBOR_SIF_CACHE=${SIF_CACHE_OVERRIDE:-$HARBOR_SIF_CACHE}   # tests may point at a scratch cache
BV=$HARBOR_BRIDGE_VENV
PKG=$("$BV/bin/python" -c "import harbor.environments.apptainer as m, pathlib; print(pathlib.Path(m.__file__).parent)")
STAGING=${TMPDIR:-/tmp}/apptainer_bridge_staging_$SLURM_JOB_ID
mkdir -p "$STAGING"
echo "bridge on $(hostname -f):$PORT  sif_cache=$HARBOR_SIF_CACHE staging=$STAGING pkg=$PKG"
"$BV/bin/python" "$PKG/server.py" --host 0.0.0.0 --port "$PORT" &
for i in $(seq 30); do curl -sf "http://127.0.0.1:$PORT/status" >/dev/null && break; sleep 2; done
if [ -n "${BRIDGE_URL_FILE:-}" ]; then
    FQDN=$(hostname -f); case $FQDN in *.*) ;; *) FQDN=$FQDN.hpc.tu-dresden.de ;; esac
    echo "http://$FQDN:$PORT" > "$BRIDGE_URL_FILE.tmp" && mv "$BRIDGE_URL_FILE.tmp" "$BRIDGE_URL_FILE"
    trap 'rm -f "$BRIDGE_URL_FILE"' EXIT
fi
exec "$BV/bin/python" "$PKG/worker.py" --bridge-url "http://127.0.0.1:$PORT" \
    --sif-cache "$HARBOR_SIF_CACHE" --staging-base "$STAGING" \
    --num-workers "${WORKERS_PER_NODE:-4}"
