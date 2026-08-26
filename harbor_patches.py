"""Idempotent local patches for the pinned harbor package.

The teacher-ranking-proxy pipeline pins
``harbor @ git+https://github.com/laude-institute/harbor.git@penfever/temp-override``
which carries two bugs that only surface on HPC (see
docs/MILESTONE_REPORT_CAPELLA.md §4). Until the fixes live in a maintained
fork (e.g. github.com/franziweindel/harbor), this module applies them to the
*installed* package at pipeline startup — the same auto-apply idea as
``apptainer_patch/`` (a PATH shim), but for Python sources.

Called from generate_trajectories.py before any harbor job starts. Safe to run
any number of times: each patch has a marker string; present -> skipped.
If harbor's code has drifted so an expected snippet is missing AND the marker
is absent, we STOP loudly rather than run with known-broken behavior.
"""

from __future__ import annotations

import shutil
from pathlib import Path

# Each patch: (relative file, marker-of-appliedness, old snippet, new snippet)
PATCHES: list[tuple[str, str, str, str]] = [
    (
        "environments/apptainer.py",
        "BUGFIX (local patch): do NOT return here",
        """                        if sif_copy and Path(sif_copy).exists():
                            self.logger.info(f"[apptainer] reusing cached SIF {sif_copy}")
                            self._sif_path = Path(sif_copy)
                            return
                except Exception as e:
                    self.logger.warning(f"[apptainer] cache lookup failed, rebuilding: {e}")
            # Convert Dockerfile to Singularity definition and build
            self._sif_path = await self._build_from_dockerfile(force=force_build)""",
        """                        if sif_copy and Path(sif_copy).exists():
                            self.logger.info(f"[apptainer] reusing cached SIF {sif_copy}")
                            # BUGFIX (local patch): do NOT return here — the
                            # instance start below must still run. The early
                            # return made cached-SIF trials skip instance
                            # startup entirely, so every exec then failed with
                            # "no instance found".
                            self._sif_path = Path(sif_copy)
                except Exception as e:
                    self.logger.warning(f"[apptainer] cache lookup failed, rebuilding: {e}")
            # Convert Dockerfile to Singularity definition and build
            if self._sif_path is None:
                self._sif_path = await self._build_from_dockerfile(force=force_build)""",
    ),
    (
        "agents/terminus_2/tmux_session.py",
        "LOCAL PATCH: the socket must NOT live on the /logs/agent bind",
        """        # Use a trial-specific socket path to prevent session collision on shared filesystems
        # The socket is placed in the agent logs directory which is unique per trial
        self._socket_path = self._logging_path.parent / "tmux.sock\"""",
        """        # Use a trial-specific socket path to prevent session collision.
        # LOCAL PATCH: the socket must NOT live on the /logs/agent bind mount —
        # on HPC that resolves to Lustre, where Unix domain sockets hang every
        # tmux client forever. Place it on container-local /tmp instead, with a
        # unique name (host /tmp may be shared across instances).
        import uuid as _uuid
        self._socket_path = Path("/tmp") / f"harbor_tmux_{_uuid.uuid4().hex[:12]}.sock\"""",
    ),
    (
        "agents/terminus_2/tmux_session.py",
        "retrying on a fresh socket",
        '''        history_limit = 10_000_000
        tmux = self._get_tmux_cmd()
        # Use script -qc PTY wrapper (same as _tmux_start_session) to ensure
        # tmux works in environments that require a PTY (e.g. Docker without -it)
        dummy_result = await self.environment.exec(
            command=f'script -qc "{tmux} -S {self._socket_path} new-session -d -s _harbor_dummy" /dev/null'
        )
        if dummy_result.return_code != 0:
            self._logger.warning(
                "Failed to create dummy tmux session for history-limit: %s",
                (dummy_result.stderr or "").strip(),
            )''',
        '''        history_limit = 10_000_000
        tmux = self._get_tmux_cmd()
        # LOCAL PATCH: bound each attempt — the first tmux server start can
        # hang forever under concurrent-instance startup load (observed on
        # ZIH Capella: the new-session client blocks in do_wait and every
        # later client queues behind it, stalling the trial with no timeout).
        # Bound with `timeout`, and on failure kill the wedged bootstrap,
        # switch to a fresh socket, and retry.
        import uuid as _uuid
        dummy_ok = False
        for _attempt in range(3):
            dummy_result = await self.environment.exec(
                command=(
                    f'timeout 60 script -qc "{tmux} -S {self._socket_path} '
                    f'new-session -d -s _harbor_dummy" /dev/null'
                )
            )
            probe = await self.environment.exec(
                command=(
                    f"timeout 15 {tmux} -S {self._socket_path} list-sessions "
                    f">/dev/null 2>&1 && echo TMUX_UP"
                )
            )
            if "TMUX_UP" in (probe.stdout or ""):
                dummy_ok = True
                break
            self._logger.warning(
                "tmux server bootstrap attempt %d failed (rc=%s); retrying on a fresh socket",
                _attempt + 1,
                dummy_result.return_code,
            )
            await self.environment.exec(
                command=(
                    f'pkill -f -- "-S {self._socket_path}" 2>/dev/null; '
                    f"rm -f {self._socket_path}; true"
                )
            )
            self._socket_path = Path("/tmp") / f"harbor_tmux_{_uuid.uuid4().hex[:12]}.sock"
        if not dummy_ok:
            self._logger.warning(
                "Failed to create dummy tmux session after retries; proceeding anyway",
            )''',
    ),
    (
        "agents/terminus_2/tmux_session.py",
        "timeout 30 {tmux} -S {self._socket_path} set-option",
        """        command = (
            f"{tmux} -S {self._socket_path} set-option -g history-limit {history_limit}"
        )""",
        """        command = (
            f"timeout 30 {tmux} -S {self._socket_path} set-option -g history-limit {history_limit}"
        )""",
    ),
    (
        "agents/terminus_2/tmux_session.py",
        "timeout 30 {tmux} -S {self._socket_path} kill-session",
        """        await self.environment.exec(
            command=f"{tmux} -S {self._socket_path} kill-session -t _harbor_dummy"
        )""",
        """        await self.environment.exec(
            command=f"timeout 30 {tmux} -S {self._socket_path} kill-session -t _harbor_dummy"
        )""",
    ),
]


def apply_harbor_patches(verbose: bool = True) -> int:
    """Patch the harbor package importable from THIS interpreter. Returns the
    number of patches newly applied. Raises SystemExit when harbor has drifted
    (snippet absent and marker absent)."""
    import harbor  # noqa: import resolves the active venv's copy
    pkg_root = Path(harbor.__file__).resolve().parent
    applied = 0
    touched: set[Path] = set()
    for rel, marker, old, new in PATCHES:
        f = pkg_root / rel
        text = f.read_text()
        if marker in text:
            continue  # already applied
        if old not in text:
            raise SystemExit(
                f"[harbor_patches] {f} does not contain the expected snippet "
                f"for patch {marker!r} — harbor version drifted; refusing to "
                "run with a known-broken harbor. Update PATCHES or the pin.")
        f.write_text(text.replace(old, new, 1))
        touched.add(f)
        applied += 1
        if verbose:
            print(f"[harbor_patches] applied: {rel}: {marker}")
    for f in touched:  # stale bytecode would shadow the patch
        pycache = f.parent / "__pycache__"
        if pycache.exists():
            shutil.rmtree(pycache, ignore_errors=True)
    if verbose and not applied:
        print(f"[harbor_patches] all {len(PATCHES)} patches already present "
              f"({pkg_root})")
    return applied


if __name__ == "__main__":
    apply_harbor_patches()
