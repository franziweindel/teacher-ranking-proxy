#!/bin/bash
# Matrix cells D/E: oracle driver on a LOGIN node (e.g. Capella login, which
# can only dial out) against a relay published in BRIDGE_URL_FILE. Waits up
# to BRIDGE_URL_WAIT_SEC (default 48 h, deep queues) for the relay job.
# Usage: setsid nohup driver_login.sh <url_file> <sample.json> > log 2>&1 &
set -euo pipefail
export BRIDGE_URL_FILE=${1:?url file}; SAMPLE=${2:?sample}
export BRIDGE_URL_WAIT_SEC=${BRIDGE_URL_WAIT_SEC:-172800}
REPO=/data/horse/ws/frwe188h-trp-shared/repo
source $REPO/hpc/dotenv/zih_alpha_franziska.env
source $REPO/data/teacher_ranking_proxy/bridge_deploy/wait_bridge_url.sh
cd $REPO
$HARBOR_BRIDGE_VENV/bin/python data/teacher_ranking_proxy/generate_trajectories.py \
    --parity-check --apptainer-only --runtime apptainer_bridge \
    --run-id "$(basename "$(dirname "$SAMPLE")")" --sample-file "$SAMPLE" --n-concurrent "${N_CONCURRENT:-10}"
