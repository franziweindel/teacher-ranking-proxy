#!/bin/bash
#SBATCH --job-name=trp-bridge-remote-oracle
#SBATCH --partition=alpha
#SBATCH --account=p_agents_finetuning
#SBATCH --nodes=1 --ntasks=1 --cpus-per-task=6
#SBATCH --gres=gpu:1
#SBATCH --mem=32G
#SBATCH --time=01:00:00
#SBATCH --output=/data/horse/ws/frwe188h-trp-shared/teacher_ranking_proxy/runs/logs/%x-%j.out
# Rung 4 driver half: oracle through a REMOTE bridge (workers on another
# cluster, see julia_bridge_workers.sbatch). Only the network differs from the
# passing loopback. Usage:
#   APPTAINER_BRIDGE_URL=http://julia.hpc.tu-dresden.de:9910 sbatch oracle_alpha_remote.sbatch <sample.json>
# or BRIDGE_URL_FILE=<file> (written by julia_bridge_workers.sbatch; see cross_cluster.sh)
set -euo pipefail
ls -d /data/horse/ws >/dev/null || { echo "missing /data/horse on $(hostname)"; exit 3; }
REPO=/data/horse/ws/frwe188h-trp-shared/repo
source $REPO/data/teacher_ranking_proxy/bridge_deploy/wait_bridge_url.sh
source $REPO/hpc/dotenv/zih_alpha_franziska.env
export APPTAINER_BRIDGE_URL
cd $REPO
$HARBOR_BRIDGE_VENV/bin/python data/teacher_ranking_proxy/generate_trajectories.py \
    --parity-check --apptainer-only --runtime apptainer_bridge \
    --run-id "${RUN_ID:-bridge-remote}" --sample-file "${1:?sample file}" --n-concurrent "${N_CONCURRENT:-10}"
