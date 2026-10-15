#!/bin/bash
# Write a source tarball of Agent Lights (app, plugin, install scripts) to
# share: only files committed under apps/agent-lights-menu-bar, at HEAD.
#
#   scripts/package-source.sh [output_dir]   # default: current directory

set -euo pipefail

HERE="$(cd "$(dirname "$0")/.." && pwd)"
repo="$(git -C "$HERE" rev-parse --show-toplevel)"
subdir="${HERE#"$repo"/}"
out_dir="${1:-$PWD}"
stamp="$(date +%Y%m%d)"
commit="$(git -C "$repo" rev-parse --short HEAD)"
name="agent-lights-src-$stamp-$commit"

if [ -n "$(git -C "$repo" status --porcelain -- "$subdir")" ]; then
  echo "warning: uncommitted changes under $subdir are NOT included." >&2
fi

mkdir -p "$out_dir"
git -C "$repo" archive --format=tar.gz --prefix="agent-lights/" \
  -o "$out_dir/$name.tar.gz" "HEAD:$subdir"
(cd "$out_dir" && shasum -a 256 "$name.tar.gz" > "$name.tar.gz.sha256")

echo "$out_dir/$name.tar.gz"
echo "Unpack with: tar -xzf $name.tar.gz && cd agent-lights && ./install.sh"
