#!/bin/bash
# Write a source zip of Agent Lights (app, plugin, install scripts) to share:
# only files committed under apps/agent-lights-menu-bar, at HEAD.
#
#   scripts/package-source.sh [output_dir]   # default: current directory

set -euo pipefail

HERE="$(cd "$(dirname "$0")/.." && pwd)"
repo="$(git -C "$HERE" rev-parse --show-toplevel)"
subdir="${HERE#"$repo"/}"
# Absolute: git -C below resolves a relative -o against the repo root.
out_dir="${1:-$PWD}"
mkdir -p "$out_dir"
out_dir="$(cd "$out_dir" && pwd)"
stamp="$(date +%Y%m%d)"
commit="$(git -C "$repo" rev-parse --short HEAD)"
name="agent-lights-src-$stamp-$commit"

if [ -n "$(git -C "$repo" status --porcelain -- "$subdir")" ]; then
  echo "warning: uncommitted changes under $subdir are NOT included." >&2
fi

git -C "$repo" archive --format=zip --prefix="agent-lights/" \
  -o "$out_dir/$name.zip" "HEAD:$subdir"
(cd "$out_dir" && shasum -a 256 "$name.zip" > "$name.zip.sha256")

echo "$out_dir/$name.zip"
echo "Unpack with: unzip $name.zip && cd agent-lights && bash install.sh"
