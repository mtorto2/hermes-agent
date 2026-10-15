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
disable old plugins), --profile NAME (repeatable), --backup DIR (remove: a
folder that must not exist yet; default a new, unique
~/.hermes/agent-lights-legacy-backup-<time>-<id>), --no-spotlight.

What is set aside automatically, and what is only reported:

- set aside: app bundles that are Agent Lights (its executable or the old
  bundle id), login items that launch one, plugins whose *code* is an Agent
  Lights producer, pre-v2 status folders, and anything already at the new
  app's or plugin's install path that is not identical to what is being
  installed (so nothing there is ever deleted unsaved);
- reported only: plugins that merely mention Agent Lights (a README, a
  comment), apps you can't write to, running old copies (install.sh quits
  them), and a Hermes core patch (blocking: this tool never edits Hermes
  source; see scripts/hermes_core_patch.py).

``remove`` never deletes. It moves each item into a backup folder it creates
exclusively, refuses to overwrite anything there, and appends one manifest
line per move as it happens. It then checks every item is gone, every old
login item is unloaded, and no old copy is still running. Anything already
inside an Agent Lights backup folder or the Trash is left alone. Standard
library only.

Test-only seams: AGENT_LIGHTS_SYSTEM_APPLICATIONS replaces /Applications,
and AGENT_LIGHTS_LEGACY_CONFINE refuses any move outside that directory.
"""

from __future__ import annotations

import argparse
import filecmp
import os
import plistlib
import re
import secrets
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

EXIT_NONE, EXIT_FOUND, EXIT_BLOCKING, EXIT_FAILED = 0, 10, 11, 12

HERE = Path(__file__).resolve().parent
NEW_BUNDLE_NAME = "AgentLightsMenuBar-v2.app"
NEW_PLUGIN_DIR = "agent-lights"
EXECUTABLE = "AgentLightsMenuBar"
OLD_BUNDLE_IDS = ("com.savant.hermes-agent-lights",)
BACKUP_PREFIXES = ("agent-lights-legacy-backup-", "agent-lights-core-backup-")
# Text that mentions Agent Lights somewhere (report-only for plugins).
MENTIONS = re.compile(r"AgentLightsMenuBar|agent-lights|agent_lights|light_cues|LightCue")
# Code that *is* an Agent Lights producer or launcher.
PRODUCER_CODE = re.compile(
    r"AgentLightsMenuBar"
    r"|(from|import)\s+agent\.light_cues"
    r"|SlotStatusFileBackend"
    r"|[\"']agent-lights[\"']\s*\)?\s*/\s*[\"'](slots|agents|v2)[\"']"
)


@dataclass
class Finding:
    kind: str  # app, process, launch_agent, plugin, pool, core
    path: Path
    detail: str
    removable: bool = True
    profile: str | None = None
    plugin_name: str | None = None
    label: str | None = None
    # A previous install of the current plugin: backed up and replaced
    # without needing --remove-legacy (re-running install.sh is the update path).
    auto: bool = False


@dataclass
class Context:
    home: Path
    hermes_dir: Path | None
    profiles: list[str]
    spotlight: bool
    new_bundle_id: str | None
    system_apps: Path = Path("/Applications")
    new_plugin_dir: Path | None = None
    confine: Path | None = None
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


def _archived(path: Path) -> bool:
    """Inside an Agent Lights backup folder or the Trash: not an active install."""
    return any(part.startswith(BACKUP_PREFIXES) or part == ".Trash" for part in path.parts)


def _code_files(directory: Path, limit: int = 300) -> list[Path]:
    files = [p for p in directory.rglob("*.py") if p.is_file() and "__pycache__" not in p.parts]
    return files[:limit]


def _text_files(directory: Path, limit: int = 300) -> list[Path]:
    suffixes = {".py", ".yaml", ".yml", ".json", ".toml", ".md", ".sh", ".txt"}
    return [p for p in directory.rglob("*") if p.is_file() and p.suffix in suffixes][:limit]


def _any_match(files: list[Path], pattern: re.Pattern) -> bool:
    for path in files:
        try:
            if pattern.search(path.read_text(encoding="utf-8", errors="ignore")):
                return True
        except OSError:
            continue
    return False


def _same_tree(a: Path, b: Path) -> bool:
    """Same files with the same contents, ignoring __pycache__."""
    cmp = filecmp.dircmp(a, b, ignore=["__pycache__", ".DS_Store"])

    def same(c: filecmp.dircmp) -> bool:
        if c.left_only or c.right_only or c.funny_files:
            return False
        _, mismatch, errors = filecmp.cmpfiles(c.left, c.right, c.common_files, shallow=False)
        if mismatch or errors:
            return False
        return all(same(sub) for sub in c.subdirs.values())

    return same(cmp)


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


def _old_processes(ctx: Context) -> list[tuple[str, str]]:
    try:
        out = subprocess.run(["ps", "-axo", "pid=,comm="], capture_output=True, text=True, timeout=10).stdout
    except Exception:
        return []
    result = []
    for line in out.splitlines():
        pid, _, command = line.strip().partition(" ")
        command = command.strip()
        if Path(command).name == EXECUTABLE and not command.startswith(str(ctx.new_dest) + "/"):
            result.append((pid, command))
    return result


def scan(ctx: Context) -> list[Finding]:
    findings: list[Finding] = []
    seen: set[str] = set()

    def add(finding: Finding) -> None:
        # Key on the path as found: an alias and the bundle it points to are
        # two separate things to set aside.
        key = f"{finding.kind}:{os.path.abspath(finding.path)}"
        if key not in seen:
            seen.add(key)
            findings.append(finding)

    # -- app bundles ----------------------------------------------------------
    candidates: list[Path] = []
    for apps_dir in (ctx.home / "Applications", ctx.system_apps):
        if apps_dir.is_dir():
            candidates += [p for p in apps_dir.iterdir() if p.suffix == ".app"]
    if ctx.hermes_dir:
        build = ctx.hermes_dir / "apps" / "agent-lights-menu-bar" / ".build"
        if build.is_dir():
            candidates += [p for p in build.iterdir() if p.suffix == ".app"]
    if ctx.spotlight:
        candidates += _spotlight_apps()
    # A running old copy tells us where its bundle is, wherever that is.
    for _, command in _old_processes(ctx):
        parts = Path(command).parts
        if len(parts) >= 4 and parts[-2:] == ("MacOS", EXECUTABLE) and parts[-3] == "Contents":
            candidates.append(Path(*parts[:-3]))
    queue = list(candidates)
    while queue:
        app = queue.pop(0)
        if _archived(app):
            continue
        if app.is_symlink():
            target = Path(os.path.realpath(app))
            if _is_agent_lights_bundle(app):
                add(Finding("app", app, f"alias to an Agent Lights app ({target})",
                            removable=os.access(app.parent, os.W_OK)))
                queue.append(target)
            continue
        if not app.is_dir() or not _is_agent_lights_bundle(app):
            continue
        if app == ctx.new_dest:
            # install.sh replaces this path; keep anything there that is not a
            # previous install of this same app.
            if ctx.new_bundle_id and _bundle_id(app) == ctx.new_bundle_id:
                continue
            add(Finding("app", app, f"another app at the new app's path (bundle id {_bundle_id(app) or 'unknown'})"))
            continue
        add(Finding("app", app, f"old Agent Lights app (bundle id {_bundle_id(app) or 'unknown'})",
                    removable=os.access(app.parent, os.W_OK)))

    # -- running copies that are not the new app ------------------------------
    for pid, command in _old_processes(ctx):
        add(Finding("process", Path(command), f"old Agent Lights app running (pid {pid})",
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
            if MENTIONS.search(text) or any(bundle_id in text for bundle_id in OLD_BUNDLE_IDS):
                add(Finding("launch_agent", plist, f"old login item {label or plist.name}", label=label or None))

    # -- per-profile plugins and status pools ---------------------------------
    for name, home in ctx.all_profile_homes():
        plugins = home / "plugins"
        if plugins.is_dir():
            for plugin in sorted(p for p in plugins.iterdir() if p.is_dir() or p.is_symlink()):
                plugin_name = plugin.name
                manifest = plugin / "plugin.yaml"
                if manifest.is_file():
                    match = re.search(r"^name:\s*['\"]?([^'\"\n]+)", manifest.read_text(errors="ignore"), re.M)
                    plugin_name = match.group(1).strip() if match else plugin_name
                if plugin.name == NEW_PLUGIN_DIR:
                    # install.sh replaces this directory: keep it unless it is
                    # exactly the plugin being installed.
                    real_dir = plugin.is_dir() and not plugin.is_symlink()
                    if ctx.new_plugin_dir and real_dir and _same_tree(plugin, ctx.new_plugin_dir):
                        continue
                    previous = real_dir and _any_match([plugin / "__init__.py"],
                                                       re.compile(r"class AgentLightsProducer\b"))
                    if previous:
                        add(Finding("plugin", plugin,
                                    f"previous install of this plugin in profile {name}; backed up before "
                                    "being replaced", profile=name, plugin_name=plugin_name, auto=True))
                    else:
                        add(Finding("plugin", plugin,
                                    f"another '{plugin.name}' plugin in profile {name} (not this one); "
                                    "backed up before being replaced", profile=name, plugin_name=plugin_name))
                    continue
                if plugin.is_symlink():
                    continue
                if _any_match(_code_files(plugin), PRODUCER_CODE):
                    add(Finding("plugin", plugin, f"old Agent Lights plugin '{plugin_name}' in profile {name}",
                                profile=name, plugin_name=plugin_name))
                elif _any_match(_text_files(plugin), MENTIONS):
                    add(Finding("plugin", plugin,
                                f"plugin '{plugin_name}' in profile {name} mentions Agent Lights but is not "
                                "recognizably part of it; review it yourself",
                                removable=False, profile=name, plugin_name=plugin_name))
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
        add(Finding("core", ctx.hermes_dir / "agent" / "light_cues.py",
                    "Hermes core contains an Agent Lights patch (agent/light_cues.py); use "
                    "scripts/hermes_core_patch.py to inventory, back up and remove it",
                    removable=False))
    return findings


def report(findings: list[Finding]) -> int:
    for f in findings:
        if f.auto:
            print(f"  [{f.kind}] {f.path}\n      {f.detail}")
    findings = [f for f in findings if not f.auto]
    if not findings:
        print("No older Agent Lights install found.")
        return EXIT_NONE
    actionable = [f for f in findings if f.removable or f.kind == "core"]
    print("Older Agent Lights pieces found:" if actionable else "Notes (nothing to set aside):")
    for f in findings:
        if f.removable:
            tag = "set aside by --remove-legacy"
        elif f.kind == "core":
            tag = "BLOCKING"
        elif f.kind == "process":
            tag = "install quits it"
        else:
            tag = "report only: handle it yourself"
        print(f"  [{f.kind}] {f.path}\n      {f.detail} ({tag})")
    if any(f.kind == "core" for f in findings):
        print("\nThe Hermes core patch must be removed before installing (see README, \"Replacing an\n"
              "older install\"). This tool never edits Hermes source.")
        return EXIT_BLOCKING
    # Report-only items and running copies (install quits them) don't block.
    return EXIT_FOUND if actionable else EXIT_NONE


def _new_backup_dir(home: Path) -> Path:
    parent = home / ".hermes"
    parent.mkdir(parents=True, exist_ok=True)
    for _ in range(20):
        candidate = parent / f"agent-lights-legacy-backup-{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(3)}"
        try:
            candidate.mkdir()
            return candidate
        except FileExistsError:
            continue
    raise RuntimeError("could not create a unique backup folder")


def _launchctl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["launchctl", *args], capture_output=True, text=True)


def _unload(label: str) -> str | None:
    """Unload a login item; None when it is not (or no longer) loaded."""
    target = f"gui/{os.getuid()}/{label}"
    if _launchctl("print", target).returncode != 0:
        return None  # not loaded
    _launchctl("bootout", target)
    for _ in range(20):
        if _launchctl("print", target).returncode != 0:
            return None
        time.sleep(0.1)
    return f"login item {label} is still loaded after launchctl bootout"


def remove(ctx: Context, findings: list[Finding], backup: Path | None, hermes_bin: str | None,
           *, auto_only: bool = False) -> int:
    movable = [f for f in findings if f.removable and (f.auto or not auto_only)]
    failed: list[str] = []
    if movable:
        if backup is None:
            backup = _new_backup_dir(ctx.home)
        else:
            try:
                backup.mkdir(parents=True)  # must be new: never mix with older backups
            except FileExistsError:
                print(f"error: backup folder {backup} already exists; choose a new one.", file=sys.stderr)
                return EXIT_FAILED
        print(f"Setting older Agent Lights pieces aside in {backup}")
        with open(backup / "MANIFEST.txt", "x", encoding="utf-8") as manifest:
            manifest.write(
                f"Older Agent Lights pieces set aside on {time.strftime('%Y-%m-%d %H:%M:%S')}.\n"
                "To restore one, move it back to its original path (left column).\n\n")
    confine = Path(os.path.realpath(ctx.confine)) if ctx.confine else None
    for index, finding in enumerate(movable, 1):
        source = Path(os.path.abspath(finding.path))
        if confine and not (str(Path(os.path.realpath(source.parent)) / source.name) + os.sep).startswith(
                str(confine) + os.sep):
            failed.append(f"{source}: outside {confine}; refused")
            continue
        if finding.kind == "launch_agent" and finding.label:
            problem = _unload(finding.label)
            if problem:
                failed.append(problem)
                continue
        if finding.kind == "plugin" and hermes_bin and finding.profile and finding.plugin_name:
            env = {k: v for k, v in os.environ.items() if k != "HERMES_HOME"}
            disabled = subprocess.run([hermes_bin, "-p", finding.profile, "plugins", "disable", finding.plugin_name],
                                      capture_output=True, text=True, env=env)
            if disabled.returncode != 0:
                print(f"  note: `hermes -p {finding.profile} plugins disable {finding.plugin_name}` failed "
                      f"({(disabled.stderr or disabled.stdout).strip()[-200:]}); moving its files anyway",
                      file=sys.stderr)
        target = backup / f"{index:02d}-{finding.kind}-{source.name}"
        if os.path.lexists(target):
            failed.append(f"{source}: {target} already exists; refused to overwrite")
            continue
        try:
            os.rename(source, target)  # same volume: atomic, never merges or overwrites a directory
        except OSError:
            try:
                shutil.move(str(source), str(target))
            except OSError as exc:
                failed.append(f"{source}: {exc}")
                continue
        with open(backup / "MANIFEST.txt", "a", encoding="utf-8") as manifest:
            manifest.write(f"{source}\t->\t{target.name}\t({finding.detail})\n")
            manifest.flush()
            os.fsync(manifest.fileno())
        print(f"  moved {source} -> {target}")

    # Verify: everything set aside is gone, nothing old is still running.
    for finding in movable:
        if os.path.lexists(finding.path) and not any(str(finding.path) in line for line in failed):
            failed.append(f"{finding.path}: still present after moving")
    for pid, command in _old_processes(ctx):
        failed.append(f"old Agent Lights app still running (pid {pid}: {command}); quit it and run again")
    for line in failed:
        print(f"  error: {line}", file=sys.stderr)
    if failed:
        return EXIT_FAILED  # takes precedence over everything else
    remaining = [f for f in findings if f not in movable and f.kind != "process"]
    for f in remaining:
        if f.kind != "core":
            print(f"  note: left in place: {f.path}: {f.detail}")
    if auto_only and any(f.removable and f.kind != "core" for f in remaining):
        return EXIT_FOUND
    if any(f.kind == "core" for f in remaining):
        return report([f for f in remaining if f.kind == "core"])
    return EXIT_NONE


def _default_new_bundle_id() -> str | None:
    try:
        with open(HERE.parent / "Resources" / "Info.plist", "rb") as handle:
            return plistlib.load(handle).get("CFBundleIdentifier")
    except Exception:
        return None


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["scan", "remove"])
    parser.add_argument("--hermes-dir")
    parser.add_argument("--hermes-bin")
    parser.add_argument("--profile", action="append", default=[])
    parser.add_argument("--backup")
    parser.add_argument("--new-bundle-id", default=_default_new_bundle_id())
    parser.add_argument("--new-plugin-dir", default=str(HERE.parent / "plugin" / NEW_PLUGIN_DIR))
    parser.add_argument("--no-spotlight", action="store_true")
    parser.add_argument("--auto-only", action="store_true",
                        help="remove: only back up what the install replaces, not older pieces")
    args = parser.parse_args(argv)
    confine = os.environ.get("AGENT_LIGHTS_LEGACY_CONFINE")
    ctx = Context(
        home=Path.home(),
        hermes_dir=Path(args.hermes_dir) if args.hermes_dir else None,
        profiles=args.profile,
        spotlight=not args.no_spotlight,
        new_bundle_id=args.new_bundle_id,
        system_apps=Path(os.environ.get("AGENT_LIGHTS_SYSTEM_APPLICATIONS", "/Applications")),
        new_plugin_dir=Path(args.new_plugin_dir) if Path(args.new_plugin_dir).is_dir() else None,
        confine=Path(confine) if confine else None,
    )
    findings = scan(ctx)
    if args.command == "scan":
        return report(findings)
    return remove(ctx, findings, Path(args.backup) if args.backup else None, args.hermes_bin,
                  auto_only=args.auto_only)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
