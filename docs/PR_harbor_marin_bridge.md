
TL;DR: to run marin's Apptainer bridge on ZIH, instances now start with
`--fakeroot` (tasks need root at task time; §1–3), the relay stores the
node name instead of guessing it from the worker id (§4), old Debian images
whose build steps cannot run rootless are imported and their `RUN` steps
baked into an overlay (§5), and four worker bugs found on the way are fixed
(§6–9: `/usr/bin/bash`, dropped `COPY`, truncated multi-line `RUN`,
unwritable `$HOME`).

## 1. Some tasks need root inside the running container

The worker used `--fakeroot` only for `apptainer build`, i.e. for the
Dockerfile's `RUN` steps while the SIF is produced. That assumes every
privileged command (`apt-get install`, writing under `/root`) happens at
build time. Some tasks, e.g. in Terminal-Lego, also run such commands at
task time, in the verifier or the golden solution, because under Docker the
task runs as root anyway — in Apptainer it does not, and those commands
fail (`dpkg: requested operation requires superuser privilege`), so the
task scores 0 without any visible error.

Fix: also pass `--fakeroot` to `apptainer instance start`, so the task runs
as (fake) root like under Docker. This is the default
(`BRIDGE_USE_FAKEROOT=auto`). If the fakeroot start itself fails (it does
for images on old Debian whose glibc cannot load Apptainer's fakeroot
helper), the worker retries the start as before, so such tasks still run
but are not root at task time — and will score 0 if they need root.
`BRIDGE_USE_FAKEROOT=1`/`0` forces one or the other.

## 2. Fakeroot and the ext3 overlay do not mix

Each instance gets a writable layer on top of the read-only SIF. The worker
used an ext3 image file for that (`overlay.img`, made with `apptainer
overlay create`). With `--fakeroot` the start failed on ZIH (`setup of
overlay upper dir failed … /upper is not writable: permission denied`) and
`auto` mode silently fell back to the non-root start of §1. Cause, verified
on a Julia node: `overlay create` records our real uid as the owner of the
directories inside the image (it even prints "Creating overlay image for
use without fakeroot"). Inside the fakeroot namespace the only mapping is
our uid ↔ 0, so that raw number is nobody's and not writable. Ownership of
host files, by contrast, is translated by the namespace: a directory we own
on the host appears as root-owned inside.

Fix: with `--fakeroot`, the worker creates a plain directory on node-local
disk right before the start and uses it as the writable layer instead of
the image file. This works because a host directory's owner is translated
by the user namespace (ours → root inside), whereas the owner recorded
inside an image file is not. Without fakeroot the image file is kept as
before, so other clusters are unaffected.

## 3. apt inside a single-uid user namespace

Without a `/etc/subuid` entry, `--fakeroot` maps only our own uid to 0;
every other uid is unmapped. apt normally drops privileges to the `_apt`
user (uid 100) for downloads and keeps its download directories owned by it
with mode 700, which namespace-root cannot write. The worker's post-start
script therefore writes an apt config that runs apt as root and points its
archive and list directories at fresh root-owned ones on the overlay. Only
applied when the instance actually started with fakeroot.

Verified necessary in isolation: with §1 and §2 in place but this config
left out, apt downloads the packages and then fails on every one with
`Could not open file /var/cache/apt/archives/partial/….deb Permission
denied`.

## 4. Jobs vanish when the worker host name contains a dash

The relay routes jobs to workers: the start of a task can go to any node,
every later job for that task must go to the same node (one `worker.py`
per node polls the relay under its host name and feeds its worker threads
`host-0 … host-N`). The relay derived the node name from the id it was
given by cutting off the last `-…` part — a leftover from when each thread
polled as `host-N`. That is harmless for `jwc07n056` (JSC) or `i8035` (ZIH
Alpha), but ZIH Julia's host name is `julia.hpc.tu-dresden.de`, which
itself contains a dash: follow-up jobs were queued under `julia.hpc.tu`,
which no node polls, and timed out.

Fix: store the node name when the start is handed out and route by it.

## 5. Images whose build steps can't run rootless

One task, `task_10016` from the Terminal-Lego dataset
(`SWE-Lego/Terminal-Lego-15k`). Its Dockerfile is:

```dockerfile
FROM mcr.microsoft.com/dotnet/sdk:6.0
WORKDIR /app
RUN apt-get update && apt-get install -y curl git vim && rm -rf /var/lib/apt/lists/*
```

That base image is built on old Debian (bullseye).

Apptainer converts a Dockerfile into a `.def` file and runs the `RUN` lines in
a `%post` section. With no root on the cluster it falls back to fakeroot, which
preloads a small library into every command so that `apt-get` believes it is
root when it does `chown`/`chmod`. That preload has to link against the glibc
inside the image; against bullseye's glibc it fails to load and the build dies
(`SIF build failed`). The same images cannot start an instance with
`--fakeroot` either, so §1's `auto` mode falls back to a plain start; the
worker now logs that as a WARNING and reports `fakeroot: false` in the start
result, which the driver logs as a warning naming the task — a 0 there is
not the agent's doing.

Fix (the same as in the local-mode fork): import the base image with
`apptainer build … docker://<base>` (no `%post`, no fakeroot) and run the
Dockerfile's own steps separately, once, inside a new user namespace created
with `unshare -r`, where our uid is mapped to 0 — so `apt-get` and `dpkg` see
a real root and install normally, without any fakeroot shim that would have
to load against the image's old libs. What they install is not written back
into the SIF, which stays immutable: it goes into a separate overlay file
next to it (`<sif>.overlay.img`, steps recorded in `<sif>.deferred.json`,
same names as the fork so the caches are interchangeable). At instance start
that overlay is mounted read-only under the writable layer, so every trial
starts from an image that already has everything installed.

Two apt details inside that namespace, found when the worker baked the
overlay itself: Docker images ship an apt hook (`docker-clean`) that `rm`s a
directory owned by the unmapped `_apt` uid and makes apt exit 100 after a
successful install — the hook is removed first; and packages that chown
files to another uid (`libutempter0`, pulled in by tmux) cannot be unpacked
at all, so the tmux/asciinema install the worker adds for the terminus agent
runs after the Dockerfile's steps and is non-fatal.

## 6. `/usr/bin/bash` is hard-coded

Every command ran as `apptainer exec … instance://hb_env_… /usr/bin/bash -lc "X"`.
Older Debian/Ubuntu images only have `/bin/bash`, so the exec failed before
`X` ran. Changed to `/bin/bash`, which exists on
every image. 

## 7. `COPY` dropped from builds

The Dockerfile → Apptainer conversion ignored `COPY`, which has to be
mapped to a `%files` section, so images built by the worker lacked their
task files (`COPY ./task_file /app/task_file`). Fix: emit `%files` lines so
Apptainer copies them too. For the old images of §5 (no `.def` build) the
files are copied at start into the writable workdir instead.

## 8. Multi-line `RUN` truncated

`RUN` lines were read one physical line at a time, so
`RUN apt-get install -y \` + continuation lines lost the package list.
Fix: join backslash-continued lines before parsing.

## 9. `$HOME` unwritable when the instance is not root

When the `--fakeroot` start fails and the worker retries without it, the
task runs as the normal user, but `$HOME` is still `/root`, which that user
cannot write. Not an issue upstream, whose images have everything
preinstalled; Terminal-Lego tasks sometimes install things at task time
(`uv`, `pip --user`), which then failed. Fix: on such a start, mount a
per-instance host folder at `/root` (pre-filled with the image's `/root`),
so `$HOME` stays `/root` but is writable.
