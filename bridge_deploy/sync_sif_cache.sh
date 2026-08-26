#!/bin/bash
# Mirror the local-mode SIF cache (cat, Capella-only) to the shared one (horse,
# readable from the CPU clusters where bridge workers run).
#
# Builds only ever ADD files — every name is content-addressed by the
# Dockerfile hash — so a one-way rsync can never invalidate anything. --sparse
# keeps the deferred-build overlays from expanding to their full nominal size.
#
# Usage: source hpc/dotenv/zih_capella_franziska.env && sync_sif_cache.sh
set -euo pipefail
SRC="${HARBOR_SIF_CACHE:?source the dotenv first}"
DST="${TRP_BRIDGE_SIF_CACHE:?source the dotenv first}"
[ -d "$(dirname "$DST")" ] || { echo "$(dirname "$DST") not mounted on $(hostname) — run this from a node that sees horse" >&2; exit 1; }
mkdir -p "$DST"
rsync -a --sparse --ignore-existing "$SRC/" "$DST/"
echo "synced $(ls "$SRC" | wc -l) -> $DST ($(du -sh "$DST" | cut -f1))"
