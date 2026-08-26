# Sourced by the Alpha driver sbatch scripts. Resolves APPTAINER_BRIDGE_URL:
# either it is already set, or BRIDGE_URL_FILE names a file that the relay job
# (julia_bridge_workers.sbatch) writes once it is up. Waits up to
# BRIDGE_URL_WAIT_SEC (default 3600) so the driver can be queued at the same
# time as the relay job and simply idles until the relay exists.
if [ -z "${APPTAINER_BRIDGE_URL:-}" ]; then
    : "${BRIDGE_URL_FILE:?set APPTAINER_BRIDGE_URL or BRIDGE_URL_FILE}"
    deadline=$(( $(date +%s) + ${BRIDGE_URL_WAIT_SEC:-3600} ))
    until [ -s "$BRIDGE_URL_FILE" ]; do
        [ "$(date +%s)" -lt "$deadline" ] || { echo "[bridge] no relay URL in $BRIDGE_URL_FILE after ${BRIDGE_URL_WAIT_SEC:-3600}s"; exit 1; }
        sleep 15
    done
    APPTAINER_BRIDGE_URL=$(cat "$BRIDGE_URL_FILE")
fi
export APPTAINER_BRIDGE_URL
# Give the relay a moment (up to 2 min) if it was published but is not answering yet.
for i in $(seq 24); do curl -sf --max-time 5 "$APPTAINER_BRIDGE_URL/status" >/dev/null && break; sleep 5; done
echo "driver on $(hostname) -> $APPTAINER_BRIDGE_URL : $(curl -sf --max-time 10 $APPTAINER_BRIDGE_URL/status || echo UNREACHABLE)"
# Optionally tear the relay job down when this driver finishes (callers must
# NOT `exec` the driver, or this EXIT trap is lost).
BRIDGE_RELAY_JOB=${BRIDGE_RELAY_JOB:-${BRIDGE_JULIA_JOB:-}}
BRIDGE_RELAY_LOGIN=${BRIDGE_RELAY_LOGIN:-julia.hpc.tu-dresden.de}
if [ -n "$BRIDGE_RELAY_JOB" ]; then
    trap 'ssh -o BatchMode=yes -o ConnectTimeout=10 "$BRIDGE_RELAY_LOGIN" scancel "$BRIDGE_RELAY_JOB" || echo "[bridge] scancel $BRIDGE_RELAY_JOB on $BRIDGE_RELAY_LOGIN failed; do it by hand"' EXIT
fi
