#!/bin/bash
# Start the Apptainer bridge SERVER on ZIH (stdlib-only HTTP relay; near-zero
# CPU). Prefer a small CPU-bearing Slurm job; a login-node tmux works for
# short experiments (the 600s CPU-time kill rule rarely triggers on an idle
# relay, but is a risk for long campaigns).
# Usage: ./start_bridge_zih.sh [port]   then:
#   export APPTAINER_BRIDGE_URL=http://$(hostname):PORT
set -e
PORT="${1:-9910}"
WS_ROOT="${WS_ROOT:?source hpc/dotenv/zih_capella_franziska.env first}"
BV="${HARBOR_BRIDGE_VENV:-$WS_ROOT/teacher_ranking_proxy/venv_bridge}"
SERVER=$($BV/bin/python -c "import harbor.environments.apptainer as m, pathlib; print(pathlib.Path(m.__file__).parent / 'server.py')")
echo "bridge on $(hostname):$PORT -> export APPTAINER_BRIDGE_URL=http://$(hostname):$PORT"
exec "$BV/bin/python" "$SERVER" --host 0.0.0.0 --port "$PORT"
