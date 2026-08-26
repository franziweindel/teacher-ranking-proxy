#!/bin/bash
# Make local-mode SIFs reusable by bridge workers.
#
# Local mode (fork harbor) names images  build_<task>-<sha256_64>.sif
# Bridge mode (marin harbor) looks for   build_<task>-<sha256_12>.sif
# Both hash the same Dockerfile bytes with sha256 (verified 2026-08-20), so the
# 12-char name is just a prefix of ours -> a symlink is a valid adapter and
# avoids rebuilding every task image for bridge runs.
#
# Usage: link_sifs_for_bridge.sh <src_trace_images_dir> [dest_sif_cache]
set -euo pipefail
SRC="${1:?usage: link_sifs_for_bridge.sh <trace_images_dir> [sif_cache]}"
DEST="${2:-${TRP_BRIDGE_SIF_CACHE:-${TRP_SHARED:?set TRP_SHARED}/harbor_sif_cache}}"
mkdir -p "$DEST"
n=0
for f in "$SRC"/build_*.sif; do
    [ -e "$f" ] || continue
    base=$(basename "$f" .sif)          # build_<task>-<64hash>
    task=${base%-*}                      # build_<task>
    hash=${base##*-}                     # <64hash>
    [ ${#hash} -eq 64 ] || continue      # already short-named: skip
    link="$DEST/${task}-${hash:0:12}.sif"
    [ -e "$link" ] || ln -s "$f" "$link"
    n=$((n+1))
done
echo "linked/verified $n SIFs into $DEST"
