#!/bin/bash
# Write a source tarball of Agent Lights (app, plugin, install scripts) to
# share: only files committed under apps/agent-lights-menu-bar, at HEAD.
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

git -C "$repo" archive --format=tar.gz --prefix="agent-lights/" \
  -o "$out_dir/$name.tar.gz" "HEAD:$subdir"
(cd "$out_dir" && shasum -a 256 "$name.tar.gz" > "$name.tar.gz.sha256")

echo "$out_dir/$name.tar.gz"
echo "Unpack with: tar -xzf $name.tar.gz && cd agent-lights && ./install.sh"
