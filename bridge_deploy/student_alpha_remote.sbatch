#!/bin/bash
#SBATCH --job-name=trp-bridge-remote-student
#SBATCH --partition=alpha
#SBATCH --account=p_agents_finetuning
#SBATCH --nodes=1 --ntasks=1 --cpus-per-task=6
#SBATCH --gres=gpu:1
#SBATCH --mem=64G
#SBATCH --time=02:00:00
#SBATCH --output=/data/horse/ws/frwe188h-trp-shared/teacher_ranking_proxy/runs/logs/%x-%j.out
# Agent (terminus-2 on vLLM Qwen3-8B) on this Alpha node, task containers on
# a REMOTE bridge (julia_bridge_workers.sbatch). Usage:
#   APPTAINER_BRIDGE_URL=... sbatch student_alpha_remote.sbatch <sample.json> [max_turns]
# or via cross_cluster.sh, which sets BRIDGE_URL_FILE instead.
set -euo pipefail
ls -d /data/horse/ws >/dev/null || { echo "missing /data/horse on $(hostname)"; exit 3; }
REPO=/data/horse/ws/frwe188h-trp-shared/repo
source $REPO/hpc/dotenv/zih_alpha_franziska.env
source $REPO/data/teacher_ranking_proxy/bridge_deploy/wait_bridge_url.sh
cd $REPO
$HARBOR_BRIDGE_VENV/bin/python data/teacher_ranking_proxy/generate_trajectories.py \
    --runtime apptainer_bridge --run-id "${RUN_ID:-bridge-student-remote}" --sample-file "${1:?sample file}" \
    --n-concurrent "${N_CONCURRENT:-2}" ${2:+--max-turns $2}
