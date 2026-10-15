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
profiles=()

usage() {
  cat <<'EOF'
Usage: ./install.sh [options]

  --profile NAME       Also install the plugin into Hermes profile NAME
                       (repeatable). The default Hermes home is always used.
  --no-plugin          Install only the app.
  --no-test            Skip `swift test`.
  --login-item         Open the app at login (a LaunchAgent).
  --sign-identity ID   Sign with this certificate instead of ad-hoc ("-").
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

hermes_root="${HERMES_HOME:-$HOME/.hermes}"
hermes_bin="$(command -v hermes || true)"
if [ "$install_plugin" -eq 1 ] && [ -z "$hermes_bin" ]; then
  die "\`hermes\` is not on PATH. Install Hermes first, or pass --no-plugin to install only the app."
fi

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

# The app refuses to run from an aliased bundle (see README, "Upgrading from
# pre-v2 builds"); refuse to install into one rather than install a dead app.
mkdir -p "$DEST_DIR"
[ -L "$DEST" ] && die "$DEST is a symlink; remove it and re-run."
legacy_exe="$DEST_DIR/AgentLightsMenuBar.app/Contents/MacOS/$EXECUTABLE"
if [ -e "$legacy_exe" ] && [ -e "$DEST" ]; then
  reached="$(realpath "$legacy_exe")"
  real_dest="$(realpath "$DEST")"
  case "$reached" in
    "$real_dest"|"$real_dest"/*) die "$legacy_exe resolves into $DEST; remove the old AgentLightsMenuBar.app alias and re-run." ;;
  esac
fi

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

say "Installing $DEST"
# Replace the whole bundle: never write through an existing one.
rm -rf "$DEST"
ditto "$stage/$APP_NAME" "$DEST"

# -- install the plugin ------------------------------------------------------

install_plugin_into() {
  local home="$1"
  [ -d "$home" ] || die "Hermes home $home does not exist."
  mkdir -p "$home/plugins"
  rm -rf "$home/plugins/$PLUGIN_NAME"
  cp -R "$HERE/plugin/$PLUGIN_NAME" "$home/plugins/$PLUGIN_NAME"
  find "$home/plugins/$PLUGIN_NAME" -name __pycache__ -type d -prune -exec rm -rf {} +
  HERMES_HOME="$home" "$hermes_bin" plugins enable "$PLUGIN_NAME"
}

if [ "$install_plugin" -eq 1 ]; then
  say "Installing the Hermes plugin into $hermes_root"
  install_plugin_into "$hermes_root"
  for profile in ${profiles[@]+"${profiles[@]}"}; do
    say "Installing the Hermes plugin into profile $profile"
    install_plugin_into "$HOME/.hermes/profiles/$profile"
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
