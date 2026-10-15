#!/usr/bin/env python3
"""Find, and optionally set aside, an older Agent Lights install.

Earlier Agent Lights builds were installed by patching Hermes itself: they
added ``agent/light_cues.py`` (plus edits to ``cli.py``, ``tui_gateway`` and
``tools/kanban_tools.py``) to the Hermes checkout, built
``apps/agent-lights-menu-bar/.build/AgentLightsMenuBar.app`` inside it (bundle
id ``com.savant.hermes-agent-lights``), and wrote status files under each
profile's ``agent-lights/`` folder. An agent may also have improvised other
pieces (a login item, a plugin, a copied app). Any of these can keep an old
menu-bar item running beside the new one.

    legacy_agent_lights.py scan   [options]   report; exit 0 none, 10 found, 11 blocking
    legacy_agent_lights.py remove [options]   set the removable ones aside

Options: --hermes-dir DIR (the Hermes checkout), --hermes-bin PATH (to
disable old plugins), --profile NAME (repeatable), --backup DIR (remove; default
~/.hermes/agent-lights-legacy-backup-<time>), --no-spotlight.

``remove`` never deletes: it moves each item into the backup folder and
writes MANIFEST.txt there with the original paths. It never edits Hermes
source. A Hermes core patch is reported as blocking; restore those files to
stock Hermes (see the README) and scan again. Standard library only.
"""

from __future__ import annotations

import argparse
import os
import plistlib
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

EXIT_NONE, EXIT_FOUND, EXIT_BLOCKING, EXIT_FAILED = 0, 10, 11, 12

NEW_BUNDLE_NAME = "AgentLightsMenuBar-v2.app"
NEW_PLUGIN_DIR = "agent-lights"
EXECUTABLE = "AgentLightsMenuBar"
OLD_BUNDLE_IDS = ("com.savant.hermes-agent-lights",)
# Text that marks a file as part of some Agent Lights install.
MARKERS = re.compile(r"AgentLightsMenuBar|agent-lights|agent_lights|light_cues|LightCue")


@dataclass
class Finding:
    kind: str  # app, process, launch_agent, plugin, pool, core
    path: Path
    detail: str
    removable: bool = True
    profile: str | None = None
    plugin_name: str | None = None


@dataclass
class Context:
    home: Path
    hermes_dir: Path | None
    profiles: list[str]
    spotlight: bool
    new_bundle_id: str | None
    new_dest: Path = field(init=False)

    def __post_init__(self) -> None:
        self.new_dest = self.home / "Applications" / NEW_BUNDLE_NAME

    def profile_home(self, name: str) -> Path:
        root = self.home / ".hermes"
        return root if name == "default" else root / "profiles" / name

    def all_profile_homes(self) -> list[tuple[str, Path]]:
        names = ["default"] + [p for p in self.profiles if p != "default"]
        profiles_dir = self.home / ".hermes" / "profiles"
        if profiles_dir.is_dir():
            names += sorted(p.name for p in profiles_dir.iterdir() if p.is_dir() and p.name not in names)
        return [(name, self.profile_home(name)) for name in names]


def _bundle_id(app: Path) -> str | None:
    try:
        with open(app / "Contents" / "Info.plist", "rb") as handle:
            return plistlib.load(handle).get("CFBundleIdentifier")
    except Exception:
        return None


def _is_agent_lights_bundle(app: Path) -> bool:
    return (app / "Contents" / "MacOS" / EXECUTABLE).exists() or _bundle_id(app) in OLD_BUNDLE_IDS


def _mentions_agent_lights(directory: Path, limit: int = 200) -> bool:
    for count, path in enumerate(p for p in directory.rglob("*") if p.is_file()):
        if count >= limit:
            break
        if path.suffix not in {".py", ".yaml", ".yml", ".json", ".toml", ".md", ".sh", ".txt"}:
            continue
        try:
            if MARKERS.search(path.read_text(encoding="utf-8", errors="ignore")):
                return True
        except OSError:
            continue
    return False


def _spotlight_apps() -> list[Path]:
    queries = [f"kMDItemCFBundleIdentifier == '{bundle_id}'" for bundle_id in OLD_BUNDLE_IDS]
    queries.append("kMDItemFSName == 'AgentLightsMenuBar*.app'")
    found: list[Path] = []
    for query in queries:
        try:
            out = subprocess.run(["mdfind", query], capture_output=True, text=True, timeout=20).stdout
        except Exception:
            continue
        found += [Path(line) for line in out.splitlines() if line.endswith(".app")]
    return found


def scan(ctx: Context) -> list[Finding]:
    findings: list[Finding] = []
    seen: set[str] = set()

    def add(finding: Finding) -> None:
        key = f"{finding.kind}:{os.path.realpath(finding.path)}"
        if key not in seen:
            seen.add(key)
            findings.append(finding)

    # -- old app bundles ----------------------------------------------------
    candidates: list[Path] = []
    for apps_dir in (ctx.home / "Applications", Path("/Applications")):
        if apps_dir.is_dir():
            candidates += [p for p in apps_dir.iterdir() if p.suffix == ".app"]
    if ctx.hermes_dir:
        build = ctx.hermes_dir / "apps" / "agent-lights-menu-bar" / ".build"
        if build.is_dir():
            candidates += [p for p in build.iterdir() if p.suffix == ".app"]
    if ctx.spotlight:
        candidates += _spotlight_apps()
    for app in candidates:
        if not app.is_dir() or app == ctx.new_dest:
            continue
        if _is_agent_lights_bundle(app):
            writable = os.access(app.parent, os.W_OK)
            add(Finding("app", app, f"old Agent Lights app (bundle id {_bundle_id(app) or 'unknown'})",
                        removable=writable))

    # -- running copies that are not the new app ------------------------------
    try:
        out = subprocess.run(["ps", "-axo", "pid=,comm="], capture_output=True, text=True, timeout=10).stdout
    except Exception:
        out = ""
    for line in out.splitlines():
        pid, _, command = line.strip().partition(" ")
        command = command.strip()
        if Path(command).name == EXECUTABLE and not command.startswith(str(ctx.new_dest) + "/"):
            add(Finding("process", Path(command), f"old Agent Lights app running (pid {pid}); install quits it",
                        removable=False))

    # -- login items ----------------------------------------------------------
    agents_dir = ctx.home / "Library" / "LaunchAgents"
    if agents_dir.is_dir():
        for plist in sorted(agents_dir.glob("*.plist")):
            try:
                with open(plist, "rb") as handle:
                    data = plistlib.load(handle)
            except Exception:
                continue
            label = str(data.get("Label", ""))
            if ctx.new_bundle_id and label == ctx.new_bundle_id:
                continue
            text = " ".join(map(str, [label, data.get("Program", ""), *data.get("ProgramArguments", [])]))
            if MARKERS.search(text) or any(bundle_id in text for bundle_id in OLD_BUNDLE_IDS):
                add(Finding("launch_agent", plist, f"old login item {label or plist.name}"))

    # -- per-profile plugins and status pools ---------------------------------
    for name, home in ctx.all_profile_homes():
        plugins = home / "plugins"
        if plugins.is_dir():
            for plugin in sorted(p for p in plugins.iterdir() if p.is_dir()):
                if plugin.name == NEW_PLUGIN_DIR or not _mentions_agent_lights(plugin):
                    continue
                plugin_name = plugin.name
                manifest = plugin / "plugin.yaml"
                if manifest.is_file():
                    match = re.search(r"^name:\s*['\"]?([^'\"\n]+)", manifest.read_text(errors="ignore"), re.M)
                    plugin_name = match.group(1).strip() if match else plugin_name
                add(Finding("plugin", plugin, f"old Agent Lights plugin '{plugin_name}' in profile {name}",
                            profile=name, plugin_name=plugin_name))
        lights = home / "agent-lights"
        if lights.is_dir():
            # The new shared pool is ~/.hermes/agent-lights/v2; everything else
            # there (and any profile-level agent-lights folder) is pre-v2.
            for entry in sorted(lights.iterdir()):
                if (name == "default" and entry.name == "v2") or entry.name == ".DS_Store":
                    continue
                add(Finding("pool", entry, f"old status files (profile {name})"))

    # -- a Hermes core patch ---------------------------------------------------
    if ctx.hermes_dir and (ctx.hermes_dir / "agent" / "light_cues.py").exists():
        detail = "Hermes core contains an Agent Lights patch (agent/light_cues.py)"
        changed = _patched_files(ctx.hermes_dir)
        if changed:
            detail += "; changed files mentioning it: " + ", ".join(changed)
        add(Finding("core", ctx.hermes_dir / "agent" / "light_cues.py", detail, removable=False))
    return findings


def _patched_files(hermes_dir: Path) -> list[str]:
    try:
        out = subprocess.run(["git", "-C", str(hermes_dir), "status", "--porcelain", "--untracked-files=all"],
                             capture_output=True, text=True, timeout=30).stdout
    except Exception:
        return []
    files = []
    for line in out.splitlines():
        rel = line[3:].split(" -> ")[-1].strip().strip('"')
        path = hermes_dir / rel
        if path.is_file():
            try:
                if MARKERS.search(path.read_text(encoding="utf-8", errors="ignore")):
                    files.append(rel)
            except OSError:
                pass
    return files


def report(findings: list[Finding]) -> int:
    if not findings:
        print("No older Agent Lights install found.")
        return EXIT_NONE
    print("Older Agent Lights pieces found:")
    for f in findings:
        tag = "set aside by --remove-legacy" if f.removable else ("BLOCKING" if f.kind == "core" else "manual")
        print(f"  [{f.kind}] {f.path}\n      {f.detail} ({tag})")
    if any(f.kind == "core" for f in findings):
        print("\nThe Hermes core patch must be undone before installing: restore those files to stock\n"
              "Hermes (git), then restart Hermes sessions. This tool never edits Hermes source.")
        return EXIT_BLOCKING
    return EXIT_FOUND


def remove(ctx: Context, findings: list[Finding], backup: Path, hermes_bin: str | None) -> int:
    movable = [f for f in findings if f.removable]
    failed: list[str] = []
    if movable:
        backup.mkdir(parents=True, exist_ok=True)
    manifest_lines = []
    uid = os.getuid()
    for index, finding in enumerate(movable, 1):
        if finding.kind == "launch_agent":
            label = None
            try:
                with open(finding.path, "rb") as handle:
                    label = plistlib.load(handle).get("Label")
            except Exception:
                pass
            if label:
                subprocess.run(["launchctl", "bootout", f"gui/{uid}/{label}"], capture_output=True)
        if finding.kind == "plugin" and hermes_bin and finding.profile and finding.plugin_name:
            env = {k: v for k, v in os.environ.items() if k != "HERMES_HOME"}
            subprocess.run([hermes_bin, "-p", finding.profile, "plugins", "disable", finding.plugin_name],
                           capture_output=True, env=env)
        target = backup / f"{index:02d}-{finding.kind}-{finding.path.name}"
        try:
            shutil.move(str(finding.path), str(target))
        except OSError as exc:
            failed.append(f"{finding.path}: {exc}")
            continue
        manifest_lines.append(f"{finding.path}\t->\t{target.name}\t({finding.detail})")
        print(f"  moved {finding.path} -> {target}")
    if manifest_lines:
        (backup / "MANIFEST.txt").write_text(
            "Older Agent Lights pieces set aside by install.sh on "
            + time.strftime("%Y-%m-%d %H:%M:%S") + ".\n"
            "To restore one, move it back to the original path (left column).\n\n"
            + "\n".join(manifest_lines) + "\n"
        )
    for line in failed:
        print(f"  could not move {line}", file=sys.stderr)
    remaining = [f for f in findings if not f.removable and f.kind != "process"]
    if any(f.kind == "core" for f in remaining):
        return report([f for f in remaining if f.kind == "core"])
    if failed:
        return EXIT_FAILED
    for f in remaining:
        print(f"  note: {f.path}: {f.detail} (not writable; remove it yourself)")
    return EXIT_NONE


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["scan", "remove"])
    parser.add_argument("--hermes-dir")
    parser.add_argument("--hermes-bin")
    parser.add_argument("--profile", action="append", default=[])
    parser.add_argument("--backup")
    parser.add_argument("--new-bundle-id")
    parser.add_argument("--no-spotlight", action="store_true")
    args = parser.parse_args(argv)
    home = Path.home()
    ctx = Context(
        home=home,
        hermes_dir=Path(args.hermes_dir) if args.hermes_dir else None,
        profiles=args.profile,
        spotlight=not args.no_spotlight,
        new_bundle_id=args.new_bundle_id,
    )
    findings = scan(ctx)
    if args.command == "scan":
        return report(findings)
    if not findings:
        return EXIT_NONE
    backup = Path(args.backup) if args.backup else (
        home / ".hermes" / f"agent-lights-legacy-backup-{time.strftime('%Y%m%d-%H%M%S')}")
    print(f"Setting older Agent Lights pieces aside in {backup}")
    return remove(ctx, findings, backup, args.hermes_bin)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
