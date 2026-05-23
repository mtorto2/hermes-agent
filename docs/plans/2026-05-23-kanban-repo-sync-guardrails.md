# Kanban Repo Sync Guardrails

Date: 2026-05-23

## Incident Summary

A real project checkout was assigned to a Kanban task as a `scratch` workspace.
When the task completed, scratch cleanup removed that checkout. The fix now in
this branch keeps inherited board default workdirs as durable `dir` workspaces
and refuses scratch cleanup outside the Hermes Kanban workspaces root.

That fix addresses deletion safety, but coordinated repo work also needs sync
safety before an agent starts a task.

## Required Kanban Preflight

Before a Kanban worker starts repo-backed work, it should run a repo preflight
against the selected workspace:

1. Resolve the workspace path and workspace kind.
2. Refuse `scratch` cleanup semantics for a real project checkout.
3. Run `git fetch --prune --no-tags` with a bounded timeout.
4. Compare the current branch against its upstream.
5. If the repo is behind upstream, pause the task and report that the repo must
   be synced before work begins.
6. If the repo has local changes, pause unless the task explicitly owns those
   changes or the operator has approved adopting them.
7. Record the preflight result in the task log/comment trail.

For high-value shared repos such as `juror-research`, `behind > 0` should be a
hard pause condition before implementation work. The worker should not attempt a
merge, rebase, or pull unless the task explicitly says to sync the repo and the
operator has approved the intended strategy.

## Visibility Surfaces

The `/repos` command and SwiftBar menu item are operator visibility surfaces.
They help Matt notice dirty repos, broken Git state, or behind branches across
the DEV folder, but they do not replace Kanban preflight enforcement.

The recommended setup is:

- SwiftBar display refresh: 1 minute.
- Remote ref refresh: stale fetch every 5 minutes.
- Manual refresh: Force fetch from the SwiftBar dropdown.
- Kanban task start: immediate fetch/preflight regardless of menu bar state.

## Dropbox Boundary

Dropbox is acceptable for durable project checkouts when it is intentionally
part of the operator workflow and provides recovery value. Disposable Kanban
scratch workspaces, temporary clones, and cleanup-owned directories should stay
under Hermes' Kanban workspaces root, not under Dropbox-synced project folders.
