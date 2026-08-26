import base64, os, sys, time
from pathlib import Path
import asyncio
from harbor.environments.apptainer.apptainer import _http_post, _poll_job, dockerfile_hash_truncated
B = os.environ["APPTAINER_BRIDGE_URL"]
env_dir = Path(sys.argv[1])
files = {str(p.relative_to(env_dir)): base64.b64encode(p.read_bytes()).decode() for p in env_dir.rglob("*") if p.is_file()}
def poll(job_id, t=180):
    try:
        return {"state": "done", "result": asyncio.run(_poll_job(B, job_id, timeout_sec=t))}
    except Exception as e:
        return {"state": "error", "result": {"stderr": str(e)}}
r = _http_post(f"{B}/env/create", {"task_name": "task_06714", "dockerfile_hash": dockerfile_hash_truncated(env_dir/"Dockerfile"), "sif_path": "", "base_image": "", "environment_dir": str(env_dir), "force_build": False, "task_env_config": {"memory_mb": 1024, "cpus": 1, "allow_internet": True}, "files_b64": files})
env_id, job = r["env_id"], r["job_id"]; print("create:", poll(job).get("state"))
def ex(cmd, t=60):
    r = _http_post(f"{B}/env/exec", {"env_id": env_id, "command": cmd, "timeout_sec": t}); res = poll(r["job_id"]).get("result") or {}
    print(f"$ {cmd}\n  rc={res.get('return_code')} out={res.get('stdout','').strip()[:300]!r} err={res.get('stderr','').strip()[:200]!r}")
ex("tmux -V; tmux new-session -d -s probe 'bash --login'; sleep 1; tmux ls")
ex("sleep 2; tmux ls || echo NO_SERVER_AFTER_EXEC_EXIT")
ex("tmux send-keys -t probe 'echo ALIVE > /tmp/probe.txt' Enter; sleep 2; cat /tmp/probe.txt || echo NO_FILE")
ex("setsid nohup tmux new-session -d -s probe2 'bash --login' >/dev/null 2>&1 < /dev/null; sleep 1; tmux ls")
ex("sleep 2; tmux ls || echo NO_SERVER_2")
_http_post(f"{B}/env/stop", {"env_id": env_id})
