#!/bin/bash
#SBATCH --job-name=trp-bridge-loopback
#SBATCH --partition=capella
#SBATCH --nodes=1 --ntasks=1 --cpus-per-task=14
#SBATCH --gres=gpu:1
#SBATCH --mem=64G
#SBATCH --time=01:30:00
#SBATCH --output=/data/cat/ws/frwe188h-otagent/teacher_ranking_proxy/runs/logs/%x-%j.out
# Bridge ladder rung 3: run bridge_deploy/loopback_test_zih.sh (marin-main
# bridge server + workers + driver oracle, all on ONE Capella node).
# Usage: sbatch loopback_zih.sbatch [sample_file]
set -euo pipefail
REPO=/data/cat/ws/frwe188h-otagent/OpenThoughts-Agent-trp
source $REPO/hpc/dotenv/zih_capella_franziska.env
ls -d /data/cat/ws /data/horse/ws || { echo "missing mount on $(hostname)"; exit 3; }
export WORKERS_PER_NODE=${WORKERS_PER_NODE:-2}
export SIF_CACHE=$HARBOR_SIF_CACHE
cd $REPO
exec bash data/teacher_ranking_proxy/bridge_deploy/loopback_test_zih.sh 9910 "${1:-}"
