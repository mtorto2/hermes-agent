# Agent Lights Menu Bar

A small native macOS menu-bar item that shows one dot per running Hermes
session (CLI or TUI) and a grid for Kanban workers, colored by what each one
is doing: working, waiting for you, answer ready, error, or idle.

## Install

You need macOS 14 or later, Swift 6 (Xcode, or the Command Line Tools:
`xcode-select --install`), and Hermes with `hermes` on your `PATH`. No Apple
Developer account is involved: the app is built and signed on your Mac.

```bash
unzip agent-lights-src-*.zip
cd agent-lights
bash install.sh
```

`install.sh` builds the app (`swift build -c release`), runs `swift test`,
installs it as `~/Applications/AgentLightsMenuBar-v2.app`, copies the Hermes
plugin into `~/.hermes/plugins/agent-lights` and enables it, then opens the
app. Options:

| Option | Effect |
| --- | --- |
| `--profile NAME` | Also install the plugin into Hermes profile `NAME` (repeatable; plugins are per profile). The default profile is always included, whichever profile `hermes profile use` selected. |
| `--login-item` | Open the app at login (a LaunchAgent). Not needed for normal use: Hermes opens the app when a session starts. |
| `--no-plugin` | Install only the app. |
| `--no-test` | Skip `swift test`. |
| `--remove-legacy` | Move the pieces of an older Agent Lights install into a new `~/.hermes/agent-lights-legacy-backup-<time>-<id>/` (see "Replacing an older install"). |
| `--allow-core-producer` | Install even though Hermes itself carries an Agent Lights patch. Only for the Hermes fork Agent Lights came from. |
| `--sign-identity ID` | Sign with a certificate you have instead of ad-hoc. |
| `--yes` | Don't ask before quitting a running copy. |

Then, once:

1. Terminal → Settings → Profiles → (each profile you use) → Tab: turn on
   **TTY**, so dots follow your tab order (see "Dot order").
2. Allow **Hermes Agent Lights** in System Settings → Privacy & Security →
   Accessibility when macOS asks. Optional: without it, dots are ordered by
   when each tab was opened. Because the app is signed locally, macOS treats
   every rebuild as a new app: allow it again after each `install.sh`. The
   same goes for the Terminal automation prompt (used when you click a row).
3. Start a new Hermes session; sessions already running don't load the
   plugin. The dot appears with your first message.

To change the app or plugin, edit the source and re-run `./install.sh`. The
bundle id lives in `Resources/Info.plist`. To remove everything,
`./uninstall.sh` (`--profile NAME` for profiles). `--purge` also deletes the
status files, the app's preferences and its privacy grants; it is refused
(before anything is removed) while any Hermes session still owns a dot,
because clearing the pool under a running session lets two sessions share a
slot. Exit those sessions first.

### Replacing an older install

Earlier Agent Lights builds were installed by patching Hermes itself
(`agent/light_cues.py` plus edits to `cli.py`, `tui_gateway/server.py` and
`tools/kanban_tools.py`). That patch builds and opens its own
`apps/agent-lights-menu-bar/.build/AgentLightsMenuBar.app` inside the Hermes
checkout and writes status files under each profile's `agent-lights/` folder,
none of which the current app reads. Before building, `install.sh` runs
`scripts/legacy_agent_lights.py scan` (you can run it on its own too):

- **Set aside with `--remove-legacy`:** old Agent Lights apps (and aliases to
  them, and the apps they point to), login items that open one, plugins whose
  code is an Agent Lights producer, pre-v2 status folders, and anything
  already at the new app's or plugin's install path that isn't what is being
  installed. Without the flag, install stops and lists them. With it, install
  quits old copies, moves each item into a new
  `~/.hermes/agent-lights-legacy-backup-<time>-<id>/` (never reused, never
  overwritten, one `MANIFEST.txt` line per move), then checks that every item
  is gone, every old login item is unloaded and no old copy still runs.
  Nothing is deleted.
- **Backed up automatically:** a previous install of this plugin, when you
  re-run `install.sh` after changes.
- **Reported only:** a plugin that merely mentions Agent Lights (a README, a
  comment), an app you can't write to, and anything already inside a backup
  folder or the Trash (never touched).
- **A Hermes core patch (blocking):** this tool never edits Hermes source.
  Use `scripts/hermes_core_patch.py` in the Hermes checkout (`hermes
  --version` prints its "Install directory"). Its classification only
  decides what is *eligible* for automatic removal; every removal also needs
  your approval of the exact changes being discarded.
  1. `python3 scripts/hermes_core_patch.py inventory --hermes-dir <dir>`
     finds the stock baseline (merge-base with the branch's upstream, else
     `origin/main`; override with `--baseline`), lists committed, staged,
     unstaged and untracked changes, and classifies each path, judging the
     staged and unstaged layers separately: `agent-lights-file` (a new file
     at a known Agent Lights path), `agent-lights-only` (every added line in
     both layers is Agent Lights code or bare syntax), `mixed`, `review` (a
     new file elsewhere that mentions Agent Lights) or `unrelated`.
  2. `... backup --hermes-dir <dir>` saves everything (staged, unstaged and
     committed patches, untracked files, git refs, a fingerprint) into a new
     `~/.hermes/agent-lights-core-backup-<time>-<id>/`.
  3. `... review --hermes-dir <dir> <paths>` prints exactly what restoring
     those paths would discard and an `APPROVAL DIGEST`.
  4. `... restore --hermes-dir <dir> --backup <folder> --approved <digest>
     --yes <paths>` takes them back to the baseline in both the index and the
     working tree. It refuses `mixed`, `review` and `unrelated` paths, a
     digest that no longer matches, and a backup that isn't of the current
     state. Edit mixed files by hand, removing only the Agent Lights hunks.
  5. To undo: `... recover --backup <folder> --yes` puts the checkout back
     exactly as it was at backup time and verifies it.
  Then check `hermes` still starts, restart running Hermes sessions, and run
  `install.sh` again.

### How the plugin reports state

The plugin maps Hermes hooks to dot states (on Hermes without an Agent Lights
core patch; beside an older pre-v2 patch it still runs, since that patch
writes files the current app ignores): a turn starting is **working**; a
clarify question, command approval or sudo prompt is **waiting for you**
until answered; a finished turn is **answer ready**, a failed one **error**,
and an interrupted one **idle**. Kanban workers report blocked and completed
tasks. Hermes builds from before the `on_human_input_*` hooks (2026-10) fall
back to the clarify tool and approval hooks, so sudo prompts don't turn the
dot yellow there.

Waiting-for-you counts only for the session's own open turn: a subagent's
prompts, or a background subagent's after the turn ended, never change the
dot. A Kanban worker that blocks its task stays yellow (and one that
completes it, red) through the end of its turn and after it exits; only a
failed turn overrides that, with error.

Known limits: a session's dot appears on its first message, not when the tab
opens (no hook runs earlier); secret-entry prompts don't turn the dot yellow;
messaging gateways and the desktop app's backend are not shown (one process
serves many chats). Delegated subagents never change their parent's dot.

### Troubleshooting

- **No dot:** `hermes plugins list` should show `agent-lights` enabled, and
  the app should be running (`pgrep -x AgentLightsMenuBar`). Start a *new*
  session and send a message.
- **Dot hidden:** menu-bar managers like Ice may file the item under hidden
  items; reveal it once and pin "Hermes Agent Lights" visible.
- **Dot order says "needs TTY in tab titles" or "needs Accessibility":** see
  setup steps 1 and 2.
- **App quits right after opening:** it only runs from a bundle named
  `AgentLightsMenuBar-v2.app` that is not an alias (see "Upgrading from pre-v2
  builds"); reinstall with `install.sh`.

## Contract

The app reads normal Hermes slot files from:

```text
~/.hermes/agent-lights/v2/slots/1.json
~/.hermes/agent-lights/v2/slots/2.json
~/.hermes/agent-lights/v2/slots/3.json
~/.hermes/agent-lights/v2/slots/4.json
~/.hermes/agent-lights/v2/slots/5.json
~/.hermes/agent-lights/v2/slots/6.json
```

Kanban worker agents use a separate capacity pool under:

```text
~/.hermes/agent-lights/v2/agents/1.json
~/.hermes/agent-lights/v2/agents/2.json
~/.hermes/agent-lights/v2/agents/3.json
~/.hermes/agent-lights/v2/agents/4.json
~/.hermes/agent-lights/v2/agents/5.json
~/.hermes/agent-lights/v2/agents/6.json
~/.hermes/agent-lights/v2/agents/7.json
~/.hermes/agent-lights/v2/agents/8.json
```

Agent Lights is a shared, cross-profile surface rooted at the primary user home:
the companion reads `~/.hermes/agent-lights/v2` even when a producer runs under a
profile-specific `HERMES_HOME`. Hermes auto-assigns the first available slot
within the appropriate pool, capped at 6 active normal instances and 8 active
Kanban workers. Claims are serialized by an exclusive `flock` on
`<pool>/.claim.lock` (bounded 2-second wait; on timeout the session skips
claiming and retries on a later lifecycle event), and each claim writes a
per-slot `.lock` and reaps the previous dead owner's `.json`. Setting
`HERMES_SLOT=1..6` *prefers* that normal-Hermes slot: it is claimed under the
same rules and never taken from a live session; when it is occupied,
auto-assigning sessions fall back to the first free slot. Kanban workers
auto-assign within their 1..8 pool. Missing or invalid values such as
`HERMES_SLOT=9` fall back to auto-assignment when capacity is available.

## Dot mapping

The menu-bar item renders valid, renderable normal Hermes session status files
in stable slot order 1 through 6. Each live idle session is a dim gray
**outline**—a clear “agent is open, but not working” cue—while a live active
session is a filled lifecycle-colored dot. Missing, stale, and dead-PID
slots are not capacity placeholders and do not add an extra circle. A normal
Hermes slot disappears as soon as its owner process is gone; only Kanban
workers and files without a `pid` keep a 120-second freshness grace.

| State | Dot |
| --- | --- |
| no slot / stale / dead normal Hermes PID | hidden |
| recent dead Kanban worker or no-PID file (≤120 seconds) | rendered from its last valid state |
| working | green filled |
| human_intervention | yellow filled |
| final_answer | red filled, steady |
| error | red, flashing (filled/outline each 0.75 s tick) in the menu-bar dots; steady red for Kanban circles and the floating monitor |
| idle live session | dim gray outline |

### Dot order

Slots are reused lowest-free-first, so slot numbers do not follow your tabs.
The dots are ordered instead:

1. **By Terminal's real tab bar** (windows left to right, then tabs left to
   right), when Agent Lights has Accessibility permission and each tab title
   includes its TTY. This follows tabs opened mid-row with ⌘T and tabs you
   drag. Set it up once:
   - Terminal → Settings → Profiles → (each profile you use) → Tab →
     turn on **TTY**. Titles are often identical ("Terminal"); the TTY is what
     identifies each tab's session.
   - System Settings → Privacy & Security → Accessibility → allow
     **Hermes Agent Lights** (the app asks once per build; local builds are
     re-signed each time, so the grant must be renewed after an update).
2. **Otherwise by when each session's tab was opened** (the oldest process on
   the session's terminal, so relaunching Hermes in a tab keeps its place),
   then by process start time for sessions without a terminal. This is right
   when new tabs open at the end; it does not follow mid-row inserts or drags.

Sessions the tab bar cannot place keep fallback order after the placed ones.
Windows at the same position (stacked, full screen, other Spaces) are ordered
by their lowest TTY number, so focus changes never reorder them. A failed,
incomplete or slow tab-bar read (any Accessibility error other than an
attribute that does not exist, or more than 3 seconds) keeps the last good
order for at most 10 seconds, enforced each time the dots are drawn, before
falling back. The menu's "Dot order" line says which ordering
is in effect: tab bar, needs Accessibility, needs TTY in tab titles, tab bar
unreadable (with the reason), or sessions not matched to a Terminal tab.

Normal Hermes CLI/TUI instances render as horizontal dots in the primary
menu-bar item; an empty normal slot adds nothing. Kanban workers render as
compact filled-circle groups in that same visible item so Ice/menu-bar managers
cannot hide a second status item independently. The agent group appears only
while at least one worker is active: slots 1..4 render as the first 2×2 bank
and slots 5..8 as a second 2×2 bank; active worker circles use lifecycle colors;
unused worker capacity circles remain gray.

A slot renders when its status file is valid and either:

- `pid` is set and that process is still alive (its kernel start time matches
  `process_start_time`, so a recycled PID does not count), or
- it is a Kanban worker, or has no `pid`, and the file was modified in the last
  120 seconds.

Hermes often exits through paths that skip cleanup (closing the Terminal tab,
`os._exit` shutdown paths, `kill -9`), so the companion app is the authority on
dead slots: it prunes a dead owner's `.json` and `.lock` files. Pruning takes the
same `.claim.lock` `flock` Hermes holds while claiming (without waiting, so the
UI never blocks), then re-reads each file and deletes it only if the owner it
names *at that moment* is dead by PID and start time. Because Hermes reaps the
previous owner's `.json` inside its own claim, the only status file that can
exist after a claim is the live owner's, which the pruner keeps, so pruning
cannot remove a live session's files, including after PID reuse. Every producer
of the `v2` pool follows this protocol; pre-v2 producers use a separate pool
(see "Upgrading from pre-v2 builds").

`process_start_time` is epoch seconds from psutil (Python) and `sysctl`
(Swift). Both read the kernel's process start time and matched to the
microsecond in testing; comparisons allow a 1-second tolerance. Neither depends
on locale or timezone. Files
from older producers carry a `process_started_at` `ps lstart` string instead;
those are judged on PID liveness alone.

For normal Hermes Terminal tabs, the companion app treats Terminal labels as
user-owned. It does not write OSC title sequences or clear Terminal title
components. When Terminal reports a non-empty custom tab title from Inspector
(`⌘I`), Agent Lights can use that title in its status menu/tooltip; generated
status-like titles such as `✓ GPT-5.5 · matt` are ignored so model/state fallback
labels remain readable.

Clicking the menu bar item opens a vertical status menu. The first row uses this
compact count format:

```text
Hermes: 3 active  Agents: 6 active
```

Each active process then gets its own clickable row with compact model/state text,
for example:

```text
1: gpt 5.5 - answer ready
2: claude 4.7 - working
3: gpt 5.2 - needs intervention
Agent 1: gpt 5.5 - working
```

Model names are shortened for readability by dropping provider prefixes and common
Claude family suffixes. Status labels use human wording (`answer ready`, `working`,
`needs intervention`) instead of raw lifecycle enum names. Clicking a normal
Hermes row uses the slot producer PID to find its TTY and asks Terminal.app to
select/raise the matching tab/window. Clicking an agent row with `kanban_task_id`
opens Terminal.app and runs `hermes kanban [--board <board>] show <task_id>` so
the associated Kanban card is pulled up directly. If the process has exited, has
no TTY, lacks card metadata, or Terminal automation permission is denied, the app
beeps and leaves focus unchanged.

The status menu also includes **Show Floating Monitor**. This opens a Sticky
Notes-style monitor window near the upper-right of the screen: translucent
off-white, rounded, resizable, always-on-top (`.floating` panel level), and
closable with a simple `×` control. The window shows the same live status feed at
a larger scale: up to six normal Hermes TUI sessions render as large filled circles
with no outline, and Kanban worker capacity renders as a large filled 4×2 grid
(two 2×2 banks) with gray placeholders. The monitor keeps the same left-to-right ordering as the menu-bar
item: each Hermes TUI circle occupies one visual unit, and the entire Kanban 4×2
grid occupies one matching unit to its right. The circle layout recalculates from
the current window size, so resizing the panel scales and repositions the
indicators instead of leaving a fixed-size icon. **Monitor More Transparent** and
**Monitor Less Transparent** menu items adjust the panel opacity in persisted
steps.

## Upgrading from pre-v2 builds

Everything lives under the versioned pool `~/.hermes/agent-lights/v2/`
(`slots/`, `agents/`, `context/`, and the app's `app.json` heartbeat). Hermes
processes started before the update keep running their old code, which writes
the legacy `~/.hermes/agent-lights/slots` and `agents` pools without the
claim lock or numeric start times. Current producers and the current app
never read or write those pools, so old and new code cannot contend for a slot
and no coordinated stop is needed. A pre-v2 session just shows no dot until it
is restarted.

The app bundle is versioned too. Current launchers package and open
`.build/AgentLightsMenuBar-v2.app`; pre-v2 launchers only ever write
`.build/AgentLightsMenuBar.app`. A pre-v2 launcher may still copy the newly
built binary into that legacy bundle and open it, so the current app checks
where it runs from (a migration guard, not build-provenance verification). It
exits before taking the instance lock or writing the heartbeat unless its
bundle is named `AgentLightsMenuBar-v2.app` both as launched and with symlinks
resolved, no sibling `AgentLightsMenuBar.app` is an alias of it, and its
executable lives inside it with no other hard links. The launcher likewise
refuses to package into an aliased bundle and installs the executable by
rename, never writing through a hard link. The current app therefore only runs
from a bundle old launchers cannot write. `AGENT_LIGHTS_ALLOW_ANY_BUNDLE=1`
disables the guard for development runs outside the bundle; it must be unset
in normal use.

The current app keeps `v2/app.json` (its PID, start time and pool) up to
date, rewriting it every few seconds if it is missing or stale. Hermes's
launcher treats a current app as running only while that heartbeat is live.
Otherwise, under a launch lock shared by all current Hermes processes, it
rechecks, and opens the app **only if no Agent Lights app is running at all**,
then waits (still holding the lock, up to 3 seconds) until an app process is
visible so launchers queued behind it do not open another. That wait is about
process visibility, not proof the v2 app is ready, and it does not cancel a
slow LaunchServices request after the timeout; the instance lock below is what
guarantees a single current renderer, and step 3 of the upgrade is the
completion check. It never terminates an app and
never rewrites the bundle of a running one; each reason for not launching is
logged distinctly.

At most one current app runs at a time: the app holds an exclusive `flock` on
`v2/app.lock` for its lifetime, and a second copy (for example a late
LaunchServices start, or one opened by hand) exits immediately, logging
whether the lock was held by another app or could not be taken at all. A
pre-v2 app can still run alongside the current one if an old launcher opens
it; it only renders the legacy pool. Quit it from its menu.

To upgrade:

1. Merge, then run `swift build` in `apps/agent-lights-menu-bar`.
2. Quit the running Agent Lights app once (menu > Quit Hermes Agent Lights, or
   `pkill -x AgentLightsMenuBar`). This is the only manual step: the launcher
   will not open the current app while any Agent Lights app is running, and it
   will not quit one for you.
3. Start a Hermes session. Its launcher refreshes
   `.build/AgentLightsMenuBar-v2.app` from the newest built binary and opens
   it. Confirm with `cat ~/.hermes/agent-lights/v2/app.json`: it should name
   the PID shown by `pgrep -fl AgentLightsMenuBar-v2.app`. If no current app
   appears (for example a pre-v2 launcher opened the legacy bundle first; a
   current binary there exits by design), quit any Agent Lights app and start
   another session. If a pre-v2 app is also running, quit that one.
4. Restart other sessions and the gateways
   (`launchctl kickstart -k gui/$(id -u)/ai.hermes.gateway` and the per-profile
   labels) whenever convenient to bring their dots back. No coordinated stop
   is needed for slot safety.

The legacy `agent-lights/slots` and `agents` directories can be deleted once no
pre-v2 Hermes process is left. Pool isolation assumes those are ordinary
directories, not symlinks into `v2`.

## Development

Two producers write the slot files, following the same protocol:

- **This Hermes fork's core** (`agent/light_cues.py`, called from `cli.py`,
  `tui_gateway/server.py` and `tools/kanban_tools.py`). On this fork the
  plugin stays inactive so a session never claims two slots.
- **The plugin** in `plugin/agent-lights/` for stock Hermes. Its tests are
  `tests/plugins/test_agent_lights_plugin.py` in the fork (including a mixed
  core + plugin claim race over one pool). `scripts/plugin_e2e.py` runs one
  real agent turn on any Hermes checkout against a local fake model and
  checks the slot file goes working → waiting → working → answer ready.

`scripts/package-source.sh` writes the zip to hand out: committed files
under this folder only.

Hermes will best-effort launch the companion app when a CLI/TUI instance claims
an Agent Lights slot. Both producers open an app installed by `install.sh`
(`~/Applications/AgentLightsMenuBar-v2.app`) as-is. Without one, the fork's
launcher uses the built Swift debug binary if present and packages it into
`.build/AgentLightsMenuBar-v2.app`, ad-hoc signs that local bundle (with
`Resources/Info.plist`), then opens it in the background; re-run `install.sh`
instead if you have an installed copy, or the installed one keeps winning. If the Swift binary has not been built
yet, run:

```bash
cd apps/agent-lights-menu-bar
swift build
```

Then start a fresh Hermes instance; it should create/update its slot file and
open the companion app if it is not already running.

If Ice or another menu bar manager is installed, a freshly packaged app bundle
may be treated as a new menu bar item and placed in the hidden section. The app
can be running correctly while the dots are hidden by Ice; reveal the hidden menu
bar items once and pin "Hermes Agent Lights" visible for human QA. The Kanban
agent circles are intentionally drawn inside the primary Hermes item rather than
a second status item, so pinning that one item should reveal both Hermes dots and
agent circles.

```bash
cd apps/agent-lights-menu-bar
swift test
swift build
swift run AgentLightsMenuBar
```

For local smoke testing without a full Hermes turn:

```bash
mkdir -p ~/.hermes/agent-lights/v2/slots
cat > ~/.hermes/agent-lights/v2/slots/1.json <<'JSON'
{"slot":1,"state":"working","event":"working","updated_at":"manual","pid":0}
JSON
cat > ~/.hermes/agent-lights/v2/slots/2.json <<'JSON'
{"slot":2,"state":"final_answer","event":"final_answer","updated_at":"manual","pid":0}
JSON
mkdir -p ~/.hermes/agent-lights/v2/agents
cat > ~/.hermes/agent-lights/v2/agents/1.json <<'JSON'
{"slot":1,"state":"working","event":"working","updated_at":"manual","pid":0,"source":"kanban_worker"}
JSON
```

The menu should show two temporary filled dots plus the 8-circle agent group
(two 2×2 banks) in the same menu-bar item, with one active colored circle and
seven gray placeholders, then hide them after the status files become stale unless a live
`pid` is present.

To smoke test the once-per-build Accessibility prompt (the decision itself is
unit tested as `AccessibilityPrompt`):

1. `tccutil reset Accessibility <CFBundleIdentifier>` and quit the app.
2. Launch the app: macOS shows the Accessibility prompt once.
3. Quit and relaunch without granting: no prompt.
4. Rebuild and relaunch: one prompt again (each local build is a new
   signature, so a previous grant no longer applies).
