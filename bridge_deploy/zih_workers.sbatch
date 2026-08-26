#!/bin/bash
#SBATCH --job-name=trp-bridge-workers
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=14
#SBATCH --gres=gpu:1
#SBATCH --mem=100G
#SBATCH --time=12:00:00
#SBATCH --partition=capella
#SBATCH --output=%x_%j.out

# Apptainer Bridge WORKERS on ZIH Capella (bridge mode = marin-main harbor in
# venv_bridge; see docs/APPTAINER_FIXES.md §4.2 and bridge_deploy/README.md).
#
# Capella submit quirks (.agents/ops/zih/ops.md): UNTYPED --gres=gpu:1 is
# required by the cli_filter even for CPU-bound workers; <=14 CPUs per GPU.
# Staging (overlays, per-instance /tmp binds) MUST be node-local: the default
# below uses $TMPDIR.
#
# --- Barnard (CPU cluster) variant: UNVERIFIED --------------------------------
#   #SBATCH --partition=barnard  (drop --gres; different cli_filter — untested)
# Prerequisite: verify Barnard nodes reach the bridge host over TCP. Workers
# only poll OUTWARD to the bridge, which helps across clusters.
# ------------------------------------------------------------------------------
#
# Usage:
#   source hpc/dotenv/zih_capella_franziska.env
#   BRIDGE_URL=http://<bridge-host>:9910 sbatch data/teacher_ranking_proxy/bridge_deploy/zih_workers.sbatch

set -e
BRIDGE_URL="${BRIDGE_URL:?set BRIDGE_URL=http://<bridge-host>:9910}"
WS_ROOT="${WS_ROOT:?source hpc/dotenv/zih_capella_franziska.env first}"
BV="${HARBOR_BRIDGE_VENV:-$WS_ROOT/teacher_ranking_proxy/venv_bridge}"
# Workers may run on a cluster that cannot see cat, so their cache must live on
# a filesystem every cluster mounts (horse) — NOT the local-mode HARBOR_SIF_CACHE:
SIF_CACHE="${SIF_CACHE:-${TRP_BRIDGE_SIF_CACHE:-$TRP_SHARED/harbor_sif_cache}}"
STAGING_BASE="${STAGING_BASE:-${TMPDIR:-/tmp}/apptainer_bridge_staging}"
WORKERS_PER_NODE="${WORKERS_PER_NODE:-12}"

# worker.py ships inside the marin-main harbor package in venv_bridge:
WORKER_SCRIPT=$($BV/bin/python -c "import harbor.environments.apptainer as m, pathlib; print(pathlib.Path(m.__file__).parent / 'worker.py')")

mkdir -p "$STAGING_BASE" "$SIF_CACHE"
echo "bridge=$BRIDGE_URL sif_cache=$SIF_CACHE staging=$STAGING_BASE workers=$WORKERS_PER_NODE script=$WORKER_SCRIPT"
exec "$BV/bin/python" "$WORKER_SCRIPT" \
    --bridge-url "$BRIDGE_URL" \
    --sif-cache "$SIF_CACHE" \
    --staging-base "$STAGING_BASE" \
    --num-workers "$WORKERS_PER_NODE"
