#!/bin/bash
#SBATCH --job-name=trp-proxies-gpu
#SBATCH --partition=capella
#SBATCH --nodes=1 --ntasks=1 --cpus-per-task=14
#SBATCH --gres=gpu:1
#SBATCH --mem=120G
#SBATCH --time=12:00:00
#SBATCH --output=/data/cat/ws/frwe188h-otagent/teacher_ranking_proxy/runs/logs/%x-%j.out
# Student-dependent proxies on the n=200 benchmark (PROXY_SPEC.md §7).
# Usage: sbatch proxies_student_gpu.sbatch [run_id] [extra compute_proxies args]
set -euo pipefail
RUN_ID=${1:-terminal_lego-n200-s42}; shift || true
REPO=/data/cat/ws/frwe188h-otagent/OpenThoughts-Agent-trp
source $REPO/hpc/dotenv/zih_capella_franziska.env
# Node-local venv copy (cluster skill §4: Lustre venvs are pathologically slow)
VT=${TMPDIR:-/tmp}/venv_trp_$SLURM_JOB_ID
if [ ! -x $VT/bin/python ]; then
  mkdir -p $VT
  tar --warning=no-file-changed -C $WS_ROOT/teacher_ranking_proxy/venv -cf - . | tar -C $VT -xf - || [ ${PIPESTATUS[0]} -le 1 ]
  sed -i "1s|^#!.*/teacher_ranking_proxy/venv/bin/python.*|#!$VT/bin/python|" $VT/bin/* || true
fi
export TEACHER_PROXY_VENV=$VT PATH=$VT/bin:$PATH
cd $REPO
SAMPLE=data/teacher_ranking_proxy/runs/$RUN_ID/sampled_task_ids.json
# grace LAST: it wraps the shared student in LoRA layers (numerically inert,
# but keep the plain-forward proxies on the unwrapped model).
$VT/bin/python data/teacher_ranking_proxy/compute_proxies.py --sample-file $SAMPLE \
  --proxy traj_length --proxy global_nll \
  --proxy local_nll_k1 --proxy local_nll_k2 --proxy local_nll_k4 --proxy local_nll_k8 \
  --proxy aslec_drop --proxy aslec_casl --proxy rsr --proxy scas --proxy grace "$@"
$VT/bin/python data/teacher_ranking_proxy/evaluate_ranking.py --run-id $RUN_ID
