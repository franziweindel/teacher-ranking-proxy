#!/bin/bash
#SBATCH --job-name=trp-proxies-judge
#SBATCH --partition=capella
#SBATCH --nodes=1 --ntasks=1 --cpus-per-task=14
#SBATCH --gres=gpu:1
#SBATCH --mem=120G
#SBATCH --time=06:00:00
#SBATCH --output=/data/cat/ws/frwe188h-otagent/teacher_ranking_proxy/runs/logs/%x-%j.out
# LLM-judge proxies (cmd_error §6.3, scrf §9) with a locally served judge
# (judge.py launches vLLM on the allocation GPU; cannot share it with the
# likelihood job). Usage: sbatch proxies_judge.sbatch [judge_model] [tag] [run_id]
#   e.g. sbatch proxies_judge.sbatch openai/gpt-oss-120b @gpt-oss-120b
set -euo pipefail
JUDGE=${1:-openai/gpt-oss-120b}; TAG=${2:-@gpt-oss-120b}; RUN_ID=${3:-terminal_lego-n200-s42}
REPO=/data/cat/ws/frwe188h-otagent/OpenThoughts-Agent-trp
source $REPO/hpc/dotenv/zih_capella_franziska.env
VT=${TMPDIR:-/tmp}/venv_trp_$SLURM_JOB_ID
if [ ! -x $VT/bin/python ]; then
  mkdir -p $VT
  tar --warning=no-file-changed -C $WS_ROOT/teacher_ranking_proxy/venv -cf - . | tar -C $VT -xf - || [ ${PIPESTATUS[0]} -le 1 ]
  sed -i "1s|^#!.*/teacher_ranking_proxy/venv/bin/python.*|#!$VT/bin/python|" $VT/bin/* || true
fi
export TEACHER_PROXY_VENV=$VT PATH=$VT/bin:$PATH
export TRP_JUDGE_MODEL=$JUDGE
# Fetch the judge weights from the compute node (login-node downloads stall
# on large shards); then run offline.
unset HF_HUB_DISABLE_XET; $VT/bin/hf download "$JUDGE" > $TMPDIR/hf_download_$SLURM_JOB_ID.log 2>&1
export HF_HUB_OFFLINE=1
cd $REPO
SAMPLE=data/teacher_ranking_proxy/runs/$RUN_ID/sampled_task_ids.json
$VT/bin/python data/teacher_ranking_proxy/compute_proxies.py --sample-file $SAMPLE \
  --proxy cmd_error --proxy scrf --student-runs $RUN_ID --judge-tag "$TAG"
$VT/bin/python data/teacher_ranking_proxy/evaluate_ranking.py --run-id $RUN_ID
