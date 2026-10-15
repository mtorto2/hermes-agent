#!/bin/bash
# Build and install Agent Lights: the menu-bar app plus the Hermes plugin.
#
# Builds the app from this folder with Swift, installs it to
# ~/Applications/AgentLightsMenuBar-v2.app, copies the plugin into your Hermes
# home and enables it. Re-run after changing the source to rebuild and
# reinstall. See README.md ("Install") for details.

set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
APP_NAME="AgentLightsMenuBar-v2.app"
EXECUTABLE="AgentLightsMenuBar"
PLUGIN_NAME="agent-lights"
INFO_PLIST="$HERE/Resources/Info.plist"
DEST_DIR="$HOME/Applications"
DEST="$DEST_DIR/$APP_NAME"

run_tests=1
login_item=0
install_plugin=1
assume_yes=0
sign_identity="-"
remove_legacy=0
allow_core_producer=0
profiles=()

usage() {
  cat <<'EOF'
Usage: ./install.sh [options]

  --profile NAME       Also install the plugin into Hermes profile NAME
                       (repeatable). The default profile (~/.hermes) is
                       always included.
  --no-plugin          Install only the app.
  --no-test            Skip `swift test`.
  --login-item         Open the app at login (a LaunchAgent).
  --sign-identity ID   Sign with this certificate instead of ad-hoc ("-").
  --remove-legacy      Set aside pieces of an older Agent Lights install
                       (old apps, login items, plugins, status files) into
                       ~/.hermes/agent-lights-legacy-backup-<time>/. Without
                       it, install stops and lists them.
  --allow-core-producer
                       Install even though Hermes itself contains an Agent
                       Lights patch (only for the Hermes fork it came from).
  --yes                Don't ask before quitting a running Agent Lights app.
  -h, --help           Show this help.
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --profile) [ $# -ge 2 ] || { echo "--profile needs a name" >&2; exit 2; }; profiles+=("$2"); shift 2 ;;
    --no-plugin) install_plugin=0; shift ;;
    --no-test) run_tests=0; shift ;;
    --login-item) login_item=1; shift ;;
    --sign-identity) [ $# -ge 2 ] || { echo "--sign-identity needs a value" >&2; exit 2; }; sign_identity="$2"; shift 2 ;;
    --remove-legacy) remove_legacy=1; shift ;;
    --allow-core-producer) allow_core_producer=1; shift ;;
    --yes|-y) assume_yes=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

say() { printf '\n==> %s\n' "$*"; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }

confirm() {
  [ "$assume_yes" -eq 1 ] && return 0
  local reply
  read -r -p "$1 [y/N] " reply || return 1
  [[ "$reply" =~ ^[Yy] ]]
}

# -- preflight ---------------------------------------------------------------

[ "$(uname -s)" = "Darwin" ] || die "Agent Lights is a macOS menu-bar app."
macos_major="$(sw_vers -productVersion | cut -d. -f1)"
[ "$macos_major" -ge 14 ] || die "macOS 14 or later is required (found $(sw_vers -productVersion))."

if ! command -v swift >/dev/null 2>&1; then
  die "Swift is not installed. Run \`xcode-select --install\` (or install Xcode), then re-run."
fi
swift_major="$(swift --version 2>&1 | sed -nE 's/.*Swift version ([0-9]+).*/\1/p' | head -1)"
[ -n "$swift_major" ] && [ "$swift_major" -ge 6 ] || die "Swift 6 or later is required (found: $(swift --version 2>&1 | head -1)). Update Xcode or the Command Line Tools."

[ -f "$INFO_PLIST" ] || die "Missing $INFO_PLIST."
bundle_id="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$INFO_PLIST")"

hermes_bin="$(command -v hermes || true)"
if [ "$install_plugin" -eq 1 ] && [ -z "$hermes_bin" ]; then
  die "\`hermes\` is not on PATH. Install Hermes first, or pass --no-plugin to install only the app."
fi

# Agent Lights and its plugin follow Hermes's profile layout under ~/.hermes;
# each target is a profile name, passed to hermes with -p so the config it
# edits is always the home the files go into (HERMES_HOME pointing at the
# root would still follow a sticky `hermes profile use` selection).
profile_home() {
  if [ "$1" = "default" ]; then echo "$HOME/.hermes"; else echo "$HOME/.hermes/profiles/$1"; fi
}
targets=(default)
for profile in ${profiles[@]+"${profiles[@]}"}; do
  [ "$profile" = "default" ] || targets+=("$profile")
done
if [ "$install_plugin" -eq 1 ]; then
  for target in "${targets[@]}"; do
    [ -d "$(profile_home "$target")" ] || die "Hermes profile '$target' not found at $(profile_home "$target")."
  done
fi

# python3 for path checks: Swift already requires Xcode or the Command Line
# Tools, which provide it.
python_bin="$(command -v python3 || true)"
[ -n "$python_bin" ] || die "python3 is required (it comes with the Command Line Tools)."

# -- destination checks (before building) -----------------------------------

# The app refuses to run from an aliased bundle (see README, "Upgrading from
# pre-v2 builds"); refuse to install into one rather than install a dead app.
# A legacy AgentLightsMenuBar.app alias may dangle until v2 exists, so resolve
# where it *would* point (non-strict realpath follows dangling links too).
[ -L "$DEST" ] && die "$DEST is a symlink; remove it and re-run."
legacy_problem="$("$python_bin" -I - "$DEST_DIR" "$APP_NAME" "$EXECUTABLE" <<'PYCODE'
import os, sys
dest_dir, app_name, exe = sys.argv[1:4]
dest = os.path.realpath(os.path.join(dest_dir, app_name))
legacy = os.path.join(dest_dir, "AgentLightsMenuBar.app")
for probe in (legacy, os.path.join(legacy, "Contents", "MacOS", exe)):
    reached = os.path.realpath(probe)
    if reached == dest or reached.startswith(dest + os.sep):
        print(f"{probe} resolves into {dest}")
        break
PYCODE
)"
[ -z "$legacy_problem" ] || die "$legacy_problem; remove the old AgentLightsMenuBar.app alias and re-run."

# -- an older Agent Lights install ---------------------------------------------

hermes_dir=""
if [ -n "$hermes_bin" ]; then
  hermes_dir="$("$hermes_bin" --version 2>/dev/null | sed -n 's/^Install directory: //p' | head -1)"
fi
legacy_args=(--new-bundle-id "$bundle_id")
[ -n "$hermes_dir" ] && legacy_args+=(--hermes-dir "$hermes_dir")
[ -n "$hermes_bin" ] && legacy_args+=(--hermes-bin "$hermes_bin")
for profile in ${profiles[@]+"${profiles[@]}"}; do legacy_args+=(--profile "$profile"); done
legacy_tool="$HERE/scripts/legacy_agent_lights.py"

say "Checking for an older Agent Lights install"
set +e
"$python_bin" -I "$legacy_tool" scan "${legacy_args[@]}"
legacy_status=$?
set -e
case "$legacy_status" in
  0) ;;
  10)
    [ "$remove_legacy" -eq 1 ] || die "Older Agent Lights pieces are installed (above). Re-run with --remove-legacy to move them into a backup folder (nothing is deleted)."
    ;;
  11)
    if [ "$allow_core_producer" -eq 1 ]; then
      echo "Continuing with the Hermes core patch in place (--allow-core-producer)."
      [ "$remove_legacy" -eq 1 ] || echo "Other pieces listed above stay in place unless you also pass --remove-legacy."
    else
      die "Hermes itself contains an older Agent Lights patch (above). Restore those files to stock Hermes first; see README, \"Replacing an older install\"."
    fi
    ;;
  *) die "The older-install check failed (exit $legacy_status)." ;;
esac

# -- build -------------------------------------------------------------------

say "Building the app (swift build -c release)"
(cd "$HERE" && swift build -c release)
bin_dir="$(cd "$HERE" && swift build -c release --show-bin-path)"
[ -x "$bin_dir/$EXECUTABLE" ] || die "Build finished but $bin_dir/$EXECUTABLE is missing."

if [ "$run_tests" -eq 1 ]; then
  say "Running tests (swift test)"
  (cd "$HERE" && swift test)
fi

say "Assembling and signing $APP_NAME"
stage="$(mktemp -d)"
trap 'rm -rf "$stage"' EXIT
mkdir -p "$stage/$APP_NAME/Contents/MacOS"
cp "$bin_dir/$EXECUTABLE" "$stage/$APP_NAME/Contents/MacOS/$EXECUTABLE"
cp "$INFO_PLIST" "$stage/$APP_NAME/Contents/Info.plist"
codesign --force --sign "$sign_identity" --timestamp=none "$stage/$APP_NAME"
codesign --verify --strict "$stage/$APP_NAME"

# -- install the app ---------------------------------------------------------

mkdir -p "$DEST_DIR"

if pgrep -x "$EXECUTABLE" >/dev/null 2>&1; then
  if confirm "Agent Lights is running. Quit it so the new build can be installed?"; then
    pkill -TERM -x "$EXECUTABLE" || true
    for _ in $(seq 1 50); do
      pgrep -x "$EXECUTABLE" >/dev/null 2>&1 || break
      sleep 0.1
    done
    pgrep -x "$EXECUTABLE" >/dev/null 2>&1 && die "Agent Lights did not quit; quit it from its menu and re-run."
  else
    die "Quit Agent Lights (menu > Quit Hermes Agent Lights) and re-run."
  fi
fi

if [ "$legacy_status" -ne 0 ] && [ "$remove_legacy" -eq 1 ]; then
  say "Setting aside the older Agent Lights install"
  set +e
  "$python_bin" -I "$legacy_tool" remove "${legacy_args[@]}"
  remove_status=$?
  set -e
  case "$remove_status" in
    0) ;;
    11) [ "$allow_core_producer" -eq 1 ] || die "The Hermes core patch is still present." ;;
    *) die "Could not set aside every older piece (above); nothing new was installed." ;;
  esac
fi

say "Installing $DEST"
# Replace the whole bundle: never write through an existing one.
rm -rf "$DEST"
ditto "$stage/$APP_NAME" "$DEST"

# -- install the plugin ------------------------------------------------------

install_plugin_into() {
  local profile="$1" home
  home="$(profile_home "$profile")"
  mkdir -p "$home/plugins"
  rm -rf "$home/plugins/$PLUGIN_NAME"
  cp -R "$HERE/plugin/$PLUGIN_NAME" "$home/plugins/$PLUGIN_NAME"
  find "$home/plugins/$PLUGIN_NAME" -name __pycache__ -type d -prune -exec rm -rf {} +
  env -u HERMES_HOME "$hermes_bin" -p "$profile" plugins enable "$PLUGIN_NAME"
}

if [ "$install_plugin" -eq 1 ]; then
  for target in "${targets[@]}"; do
    say "Installing the Hermes plugin into profile $target ($(profile_home "$target"))"
    install_plugin_into "$target"
  done
fi

# -- login item --------------------------------------------------------------

agent_plist="$HOME/Library/LaunchAgents/$bundle_id.plist"
if [ "$login_item" -eq 1 ]; then
  say "Adding a login item ($agent_plist)"
  mkdir -p "$HOME/Library/LaunchAgents"
  cat >"$agent_plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$bundle_id</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/open</string>
    <string>-g</string>
    <string>$DEST</string>
  </array>
  <key>RunAtLoad</key>
  <true/>
</dict>
</plist>
EOF
  launchctl bootout "gui/$(id -u)/$bundle_id" >/dev/null 2>&1 || true
  launchctl bootstrap "gui/$(id -u)" "$agent_plist"
fi

say "Opening Agent Lights"
open -g "$DEST"

cat <<EOF

Done. Agent Lights is installed at $DEST.

One-time setup:
  1. Terminal > Settings > Profiles > (each profile you use) > Tab:
     turn on "TTY", so each tab's dot can follow the tab bar.
  2. When macOS asks, allow "Hermes Agent Lights" under
     System Settings > Privacy & Security > Accessibility (optional; without
     it, dots are ordered by when each tab was opened). Locally built apps
     need this again after every rebuild.
  3. Start a new Hermes session (existing ones won't load the plugin).
     A dot appears on your first message.

To uninstall: $HERE/uninstall.sh
EOF
