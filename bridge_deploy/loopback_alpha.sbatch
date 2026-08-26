#!/bin/bash
#SBATCH --job-name=trp-bridge-loopback
#SBATCH --partition=alpha
#SBATCH --account=p_agents_finetuning
#SBATCH --nodes=1 --ntasks=1 --cpus-per-task=6
#SBATCH --gres=gpu:1
#SBATCH --mem=48G
#SBATCH --time=01:30:00
#SBATCH --output=/data/horse/ws/frwe188h-trp-shared/teacher_ranking_proxy/runs/logs/%x-%j.out
# Bridge ladder rung 3 on ALPHA (horse-only twin of loopback_zih.sbatch):
# marin-main bridge server + workers + driver oracle on ONE node.
# Alpha submit rules: GPU required, <=6 CPUs per GPU. Submit from
# login1.alpha.hpc.tu-dresden.de (alpha is a separate Slurm cluster).
# Usage: sbatch loopback_alpha.sbatch [sample_file]
set -euo pipefail
REPO=/data/horse/ws/frwe188h-trp-shared/repo
source $REPO/hpc/dotenv/zih_alpha_franziska.env
ls -d /data/horse/ws || { echo "missing mount on $(hostname)"; exit 3; }
export WORKERS_PER_NODE=${WORKERS_PER_NODE:-2}
export SIF_CACHE=$HARBOR_SIF_CACHE
cd $REPO
exec bash data/teacher_ranking_proxy/bridge_deploy/loopback_test_zih.sh 9910 "${1:-}"
