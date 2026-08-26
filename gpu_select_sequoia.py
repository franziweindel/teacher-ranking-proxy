"""GPU selection for the sequoia host — deliberately host-specific.

GPU 2 has fallen off the PCI bus: bare `nvidia-smi` errors, NVML per-device
queries on it fail, and it must never be used. Healthy devices are 0, 1 and 3;
0 and 3 are often busy with other users' jobs, so the preference order is
1 -> 3 -> 0. Callers should pair the chosen index with
CUDA_DEVICE_ORDER=PCI_BUS_ID so CUDA indices match these NVML indices.

On another machine, replace this module (or extend it with per-host logic);
nothing else in the pipeline encodes GPU topology.
"""
from __future__ import annotations

import socket
import subprocess

HOSTNAME = "sequoia"


def is_this_host() -> bool:
    """True when running on the sequoia host this module describes."""
    return socket.gethostname().split(".")[0] == HOSTNAME

HEALTHY_GPUS = [1, 3, 0]  # preference order; GPU 2 is dead — never use it


def query_gpus() -> list:
    """Usage stats for the healthy devices via `nvidia-smi -i 0,1,3`."""
    cmd = ["nvidia-smi", "-i", ",".join(str(g) for g in sorted(HEALTHY_GPUS)),
           "--query-gpu=index,memory.used,utilization.gpu",
           "--format=csv,noheader,nounits"]
    out = subprocess.run(cmd, capture_output=True, text=True)
    gpus = []
    if out.returncode == 0:
        for line in out.stdout.strip().splitlines():
            idx, mem, util = [x.strip() for x in line.split(",")]
            gpus.append({"index": int(idx), "memory_used_mib": int(mem),
                         "utilization_pct": int(util)})
    return gpus


def pick_free_gpu() -> int:
    """An idle healthy GPU, tried in preference order."""
    gpus = {g["index"]: g for g in query_gpus()}
    for idx in HEALTHY_GPUS:
        g = gpus.get(idx)
        if g and g["memory_used_mib"] < 1024 and g["utilization_pct"] < 5:
            return idx
    raise SystemExit(f"No idle GPU among {sorted(HEALTHY_GPUS)}: "
                     f"{list(gpus.values())}.")
