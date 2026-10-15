#!/bin/bash
# Remove Agent Lights: the app, its login item, and the Hermes plugin.
# --purge also removes its status files, preferences and privacy grants.

set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
APP_NAME="AgentLightsMenuBar-v2.app"
EXECUTABLE="AgentLightsMenuBar"
PLUGIN_NAME="agent-lights"
DEST="$HOME/Applications/$APP_NAME"

purge=0
assume_yes=0
profiles=()

usage() {
  cat <<'EOF'
Usage: ./uninstall.sh [options]

  --profile NAME   Also remove the plugin from Hermes profile NAME (repeatable).
  --purge          Also delete ~/.hermes/agent-lights/v2, the app's
                   preferences, and its Accessibility/Automation grants.
  --yes            Don't ask for confirmation.
  -h, --help       Show this help.
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --profile) [ $# -ge 2 ] || { echo "--profile needs a name" >&2; exit 2; }; profiles+=("$2"); shift 2 ;;
    --purge) purge=1; shift ;;
    --yes|-y) assume_yes=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

say() { printf '==> %s\n' "$*"; }
confirm() {
  [ "$assume_yes" -eq 1 ] && return 0
  local reply
  read -r -p "$1 [y/N] " reply || return 1
  [[ "$reply" =~ ^[Yy] ]]
}

# Prefer the installed app's id; fall back to the one in this folder.
bundle_id=""
for plist in "$DEST/Contents/Info.plist" "$HERE/Resources/Info.plist"; do
  if [ -f "$plist" ]; then
    bundle_id="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$plist" 2>/dev/null || true)"
    [ -n "$bundle_id" ] && break
  fi
done

hermes_root="${HERMES_HOME:-$HOME/.hermes}"
if [ "$purge" -eq 1 ]; then
  confirm "Remove Agent Lights and all its data?" || exit 1
else
  confirm "Remove Agent Lights?" || exit 1
fi

if pgrep -x "$EXECUTABLE" >/dev/null 2>&1; then
  say "Quitting Agent Lights"
  pkill -TERM -x "$EXECUTABLE" || true
  for _ in $(seq 1 50); do
    pgrep -x "$EXECUTABLE" >/dev/null 2>&1 || break
    sleep 0.1
  done
fi

if [ -n "$bundle_id" ]; then
  agent_plist="$HOME/Library/LaunchAgents/$bundle_id.plist"
  launchctl bootout "gui/$(id -u)/$bundle_id" >/dev/null 2>&1 || true
  if [ -f "$agent_plist" ]; then
    say "Removing login item $agent_plist"
    rm -f "$agent_plist"
  fi
fi

if [ -L "$DEST" ]; then
  say "Removing symlink $DEST (not its target)"
  rm -f "$DEST"
elif [ -e "$DEST" ]; then
  say "Removing $DEST"
  rm -rf "$DEST"
fi

remove_plugin_from() {
  local home="$1"
  [ -d "$home/plugins/$PLUGIN_NAME" ] || return 0
  say "Removing the Hermes plugin from $home"
  if command -v hermes >/dev/null 2>&1; then
    HERMES_HOME="$home" hermes plugins disable "$PLUGIN_NAME" >/dev/null 2>&1 || true
  fi
  rm -rf "$home/plugins/$PLUGIN_NAME"
}

remove_plugin_from "$hermes_root"
for profile in ${profiles[@]+"${profiles[@]}"}; do
  remove_plugin_from "$HOME/.hermes/profiles/$profile"
done

if [ "$purge" -eq 1 ]; then
  # Running Hermes sessions recreate their status file on their next event.
  say "Removing $HOME/.hermes/agent-lights/v2"
  rm -rf "$HOME/.hermes/agent-lights/v2"
  if [ -n "$bundle_id" ]; then
    say "Removing preferences and privacy grants for $bundle_id"
    defaults delete "$bundle_id" >/dev/null 2>&1 || true
    tccutil reset Accessibility "$bundle_id" >/dev/null 2>&1 || true
    tccutil reset AppleEvents "$bundle_id" >/dev/null 2>&1 || true
  fi
fi

say "Agent Lights removed. Restart Hermes sessions to unload the plugin."
