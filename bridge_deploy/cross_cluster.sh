#!/bin/bash
# Submit a cross-cluster bridge run with ONE command.
#   cross_cluster.sh --workers julia|barnard|romeo --driver alpha|capella|capella-login \
#                    oracle|student <sample.json> [max_turns]
# Placement (see README "Where things run"): relay + workers go together on the
# worker cluster (one sbatch), the driver (+ vLLM for student) on the GPU
# cluster and dials OUT to the relay — works from Capella too, which cannot
# host the relay (outbound-only). Both jobs are queued at once; the driver
# idles at start until the relay job has published its URL to a file on
# /data/horse, and cancels the relay job when it finishes. Run from any ZIH
# login with passwordless ssh to the cluster logins.
set -euo pipefail
WORKERS=julia; DRIVER=alpha
while [ $# -gt 0 ]; do case $1 in
  --workers) WORKERS=$2; shift 2;; --driver) DRIVER=$2; shift 2;; *) break;; esac; done
MODE=${1:?oracle|student}; SAMPLE=${2:?sample.json}; MAXT=${3:-}
REPO=/data/horse/ws/frwe188h-trp-shared/repo
BD=$REPO/data/teacher_ranking_proxy/bridge_deploy
RUNS=/data/horse/ws/frwe188h-trp-shared/teacher_ranking_proxy/runs
mkdir -p "$RUNS/logs"
case $WORKERS in
  julia)   WLOGIN=julia.hpc.tu-dresden.de;         WPART=julia ;;
  barnard) WLOGIN=login1.barnard.hpc.tu-dresden.de; WPART=barnard ;;
  romeo)   WLOGIN=login1.romeo.hpc.tu-dresden.de;   WPART=romeo ;;
  *) echo "--workers must be julia|barnard|romeo (capella cannot host the relay)"; exit 2 ;;
esac
case $MODE in
  oracle)  DSCRIPT=oracle_alpha_remote.sbatch ;;
  student) DSCRIPT=student_alpha_remote.sbatch ;;
  *) echo "mode must be oracle or student"; exit 2 ;;
esac
RUN_ID=${RUN_ID:-$(basename "$(dirname "$SAMPLE")")}
STAMP=$(date +%Y%m%d-%H%M%S)
URLF=$RUNS/bridge_url.$STAMP
SSH() { ssh -o BatchMode=yes "$@" 2>&1 | grep -v "bashrc\|keysign\|Certificate\|sign using"; }
RJOB=$(SSH $WLOGIN "BRIDGE_URL_FILE=$URLF WORKERS_PER_NODE=${WORKERS_PER_NODE:-10} SIF_CACHE_OVERRIDE=${SIF_CACHE_OVERRIDE:-} \
    sbatch --parsable -p $WPART -J trp-relay-$WORKERS -t ${RELAY_TIME:-04:00:00} $BD/julia_bridge_workers.sbatch")
echo "relay+workers on $WORKERS: job $RJOB  (url file $URLF)"
DENV="BRIDGE_URL_FILE=$URLF BRIDGE_RELAY_JOB=$RJOB BRIDGE_RELAY_LOGIN=$WLOGIN RUN_ID=$RUN_ID N_CONCURRENT=${N_CONCURRENT:-10}"
case $DRIVER in
  alpha)
    DJOB=$(SSH login1.alpha.hpc.tu-dresden.de "$DENV sbatch --parsable -J trp-$MODE-$DRIVER $BD/$DSCRIPT $SAMPLE $MAXT")
    echo "$MODE driver on alpha: job $DJOB" ;;
  capella)
    DJOB=$(env $DENV sbatch --parsable -p capella -J trp-$MODE-$DRIVER --gres=gpu:1 -c 14 \
        --exclude=c115,c118,c46 $BD/$DSCRIPT $SAMPLE $MAXT)
    echo "$MODE driver on capella: job $DJOB" ;;
  capella-login)
    [ $MODE = oracle ] || { echo "capella-login supports oracle only (no GPU)"; exit 2; }
    env $DENV setsid nohup $BD/driver_login.sh $URLF $SAMPLE > $RUNS/logs/driver-$RUN_ID.out 2>&1 &
    echo "oracle driver on $(hostname) (login), log $RUNS/logs/driver-$RUN_ID.out" ;;
  *) echo "--driver must be alpha|capella|capella-login"; exit 2 ;;
esac
echo "cancel relay: ssh $WLOGIN scancel $RJOB"
