#!/bin/bash
#SBATCH --job-name=trp-bridge-workers-only
#SBATCH --partition=julia
#SBATCH --account=p_agents_finetuning
#SBATCH --nodes=1 --ntasks=1 --cpus-per-task=16
#SBATCH --mem=96G
#SBATCH --time=03:00:00
#SBATCH --output=/data/horse/ws/frwe188h-trp-shared/teacher_ranking_proxy/runs/logs/%x-%j.out
# WORKERS ONLY: dial out to a relay that lives elsewhere (an Alpha compute
# node, a login node, ...). The relay URL comes from APPTAINER_BRIDGE_URL or
# from BRIDGE_URL_FILE (written by whoever starts the relay). Override the
# partition with `sbatch -p barnard ...`.
set -euo pipefail
source /data/horse/ws/frwe188h-trp-shared/repo/hpc/dotenv/zih_alpha_franziska.env
if [ -z "${APPTAINER_BRIDGE_URL:-}" ]; then
    : "${BRIDGE_URL_FILE:?set APPTAINER_BRIDGE_URL or BRIDGE_URL_FILE}"
    deadline=$(( $(date +%s) + ${BRIDGE_URL_WAIT_SEC:-3600} ))
    until [ -s "$BRIDGE_URL_FILE" ]; do
        [ "$(date +%s)" -lt "$deadline" ] || { echo "no relay URL in $BRIDGE_URL_FILE"; exit 1; }
        sleep 15
    done
    APPTAINER_BRIDGE_URL=$(cat "$BRIDGE_URL_FILE")
fi
BV=$HARBOR_BRIDGE_VENV
PKG=$("$BV/bin/python" -c "import harbor.environments.apptainer as m, pathlib; print(pathlib.Path(m.__file__).parent)")
STAGING=${TMPDIR:-/tmp}/apptainer_bridge_staging_$SLURM_JOB_ID
mkdir -p "$STAGING"
echo "workers on $(hostname -f) -> $APPTAINER_BRIDGE_URL : $(curl -sf --max-time 10 $APPTAINER_BRIDGE_URL/status || echo UNREACHABLE)"
exec "$BV/bin/python" "$PKG/worker.py" --bridge-url "$APPTAINER_BRIDGE_URL" \
    --sif-cache "$HARBOR_SIF_CACHE" --staging-base "$STAGING" \
    --num-workers "${WORKERS_PER_NODE:-4}"
