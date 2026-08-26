#!/bin/bash
# Matrix cell C: relay on a LOGIN node (no Slurm). Starts server.py detached,
# publishes the URL to BRIDGE_URL_FILE, prints the pid. Login nodes kill
# processes exceeding 600 s CPU time; the idle relay stays far below that.
# Usage: relay_login.sh <url_file> [port]
set -euo pipefail
URLF=${1:?url file}; PORT=${2:-9912}
source /data/horse/ws/frwe188h-trp-shared/repo/hpc/dotenv/zih_alpha_franziska.env
BV=$HARBOR_BRIDGE_VENV
PKG=$("$BV/bin/python" -c "import harbor.environments.apptainer as m, pathlib; print(pathlib.Path(m.__file__).parent)")
setsid nohup "$BV/bin/python" "$PKG/server.py" --host 0.0.0.0 --port $PORT >"$URLF.relay.log" 2>&1 &
echo $! > "$URLF.pid"
for i in $(seq 15); do curl -sf "http://127.0.0.1:$PORT/status" >/dev/null && break; sleep 2; done
FQDN=$(hostname -f); case $FQDN in *.*) ;; *) FQDN=$FQDN.hpc.tu-dresden.de ;; esac
echo "http://$FQDN:$PORT" > "$URLF"
echo "relay $(cat $URLF) pid $(cat $URLF.pid) on $(hostname)"
