"""Apple Calendar tool backed by macOS EventKit via a small Swift bridge."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from hermes_constants import get_hermes_home
from tools.registry import registry


BRIDGE_SOURCE = r'''
import Foundation
import EventKit

func fail(_ message: String, _ code: Int32 = 1) -> Never {
    FileHandle.standardError.write((message + "\n").data(using: .utf8)!)
    exit(code)
}

func jsonPrint(_ object: Any) {
    do {
        let data = try JSONSerialization.data(withJSONObject: object, options: [.prettyPrinted, .sortedKeys])
        FileHandle.standardOutput.write(data)
        FileHandle.standardOutput.write("\n".data(using: .utf8)!)
    } catch {
        fail("JSON_ERROR: \(error)")
    }
}

func argValue(_ args: [String], _ name: String) -> String? {
    guard let i = args.firstIndex(of: name), i + 1 < args.count else { return nil }
    return args[i + 1]
}

func parseISO(_ s: String) -> Date? {
    let f = ISO8601DateFormatter()
    f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
    if let d = f.date(from: s) { return d }
    f.formatOptions = [.withInternetDateTime]
    return f.date(from: s)
}

let args = Array(CommandLine.arguments.dropFirst())
let command = args.first ?? "help"

if command == "help" || command == "--help" || command == "-h" {
    print("Usage: calendars | events [--days N] [--calendar NAME] | create ... | delete EVENT_IDENTIFIER")
    exit(0)
}

let store = EKEventStore()
let sem = DispatchSemaphore(value: 0)
var granted = false
var authError: Error?

if #available(macOS 14.0, *) {
    store.requestFullAccessToEvents { ok, error in
        granted = ok
        authError = error
        sem.signal()
    }
} else {
    store.requestAccess(to: .event) { ok, error in
        granted = ok
        authError = error
        sem.signal()
    }
}
sem.wait()

if !granted {
    fail("CALENDAR_ACCESS_DENIED: \(authError?.localizedDescription ?? "Calendar permission was not granted")")
}

let calendars = store.calendars(for: .event)
let outDate = ISO8601DateFormatter()
outDate.formatOptions = [.withInternetDateTime]

func selectedCalendar(_ args: [String]) -> EKCalendar? {
    if let name = argValue(args, "--calendar") {
        return calendars.first { $0.title.caseInsensitiveCompare(name) == .orderedSame }
    }
    return store.defaultCalendarForNewEvents ?? calendars.first { $0.allowsContentModifications }
}

switch command {
case "calendars":
    let rows = calendars.map { cal -> [String: Any] in
        [
            "title": cal.title,
            "calendarIdentifier": cal.calendarIdentifier,
            "allowsContentModifications": cal.allowsContentModifications,
            "source": cal.source.title,
            "type": cal.type.rawValue
        ]
    }
    jsonPrint(rows)

case "events":
    let days = Int(argValue(args, "--days") ?? "7") ?? 7
    let start = Date()
    guard let end = Calendar.current.date(byAdding: .day, value: days, to: start) else { fail("DATE_ERROR") }
    let cals: [EKCalendar]
    if let name = argValue(args, "--calendar") {
        guard let cal = calendars.first(where: { $0.title.caseInsensitiveCompare(name) == .orderedSame }) else {
            fail("CALENDAR_NOT_FOUND: \(name)")
        }
        cals = [cal]
    } else {
        cals = calendars
    }
    let predicate = store.predicateForEvents(withStart: start, end: end, calendars: cals)
    let events = store.events(matching: predicate).sorted { $0.startDate < $1.startDate }
    let rows = events.map { ev -> [String: Any] in
        [
            "eventIdentifier": ev.eventIdentifier ?? "",
            "calendar": ev.calendar.title,
            "title": ev.title ?? "",
            "start": outDate.string(from: ev.startDate),
            "end": outDate.string(from: ev.endDate),
            "isAllDay": ev.isAllDay,
            "location": ev.location ?? "",
            "notes": ev.notes ?? "",
            "url": ev.url?.absoluteString ?? ""
        ]
    }
    jsonPrint(rows)

case "create":
    guard let title = argValue(args, "--title") else { fail("MISSING_ARG: --title") }
    guard let startS = argValue(args, "--start"), let start = parseISO(startS) else { fail("MISSING_OR_BAD_ARG: --start ISO") }
    guard let endS = argValue(args, "--end"), let end = parseISO(endS) else { fail("MISSING_OR_BAD_ARG: --end ISO") }
    guard let cal = selectedCalendar(args) else { fail("NO_CALENDAR") }
    if !cal.allowsContentModifications { fail("CALENDAR_NOT_WRITABLE: \(cal.title)") }
    let ev = EKEvent(eventStore: store)
    ev.calendar = cal
    ev.title = title
    ev.startDate = start
    ev.endDate = end
    ev.location = argValue(args, "--location")
    ev.notes = argValue(args, "--notes")
    do {
        try store.save(ev, span: .thisEvent, commit: true)
        jsonPrint(["status": "created", "eventIdentifier": ev.eventIdentifier ?? "", "calendar": cal.title, "title": title, "start": outDate.string(from: start), "end": outDate.string(from: end), "location": ev.location ?? ""])
    } catch {
        fail("CREATE_FAILED: \(error.localizedDescription)")
    }

case "delete":
    guard args.count >= 2 else { fail("MISSING_ARG: event_identifier") }
    let eventID = args[1]
    guard let ev = store.event(withIdentifier: eventID) else { fail("EVENT_NOT_FOUND: \(eventID)") }
    let title = ev.title ?? ""
    let cal = ev.calendar.title
    let start = ev.startDate ?? Date.distantPast
    let end = ev.endDate ?? Date.distantPast
    do {
        try store.remove(ev, span: .thisEvent, commit: true)
        jsonPrint(["status": "deleted", "eventIdentifier": eventID, "calendar": cal, "title": title, "start": outDate.string(from: start), "end": outDate.string(from: end)])
    } catch {
        fail("DELETE_FAILED: \(error.localizedDescription)")
    }

default:
    fail("UNKNOWN_COMMAND: \(command)")
}
'''.lstrip()


APPLE_CALENDAR_SCHEMA = {
    "name": "apple_calendar",
    "description": (
        "Read and manage the user's native macOS/iCloud Apple Calendar via EventKit. "
        "Create and delete actions require explicit user approval and confirmed=true."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["list_calendars", "list_events", "create_event", "delete_event"],
                "description": "Calendar operation to perform.",
            },
            "calendar": {"type": "string", "description": "Calendar name, e.g. Home or Gigs."},
            "days": {"type": "integer", "description": "Number of days ahead for list_events.", "default": 7},
            "title": {"type": "string", "description": "Event title for create_event."},
            "start": {"type": "string", "description": "ISO-8601 event start with timezone offset."},
            "end": {"type": "string", "description": "ISO-8601 event end with timezone offset."},
            "location": {"type": "string", "description": "Event location for create_event."},
            "notes": {"type": "string", "description": "Event notes for create_event."},
            "event_identifier": {"type": "string", "description": "Event identifier for delete_event."},
            "confirmed": {
                "type": "boolean",
                "description": "Must be true only after the user confirms the exact create/delete action.",
                "default": False,
            },
        },
        "required": ["action"],
    },
}


def check_apple_calendar_requirements() -> bool:
    return sys.platform == "darwin" and Path("/usr/bin/swift").exists()


def _ensure_bridge_script() -> str:
    scripts_dir = Path(get_hermes_home()) / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    script_path = scripts_dir / "apple_calendar_bridge.swift"
    if not script_path.exists() or script_path.read_text(encoding="utf-8") != BRIDGE_SOURCE:
        script_path.write_text(BRIDGE_SOURCE, encoding="utf-8")
    return str(script_path)


def _json_error(message: str) -> str:
    return json.dumps({"success": False, "error": message})


def _run_bridge(args: list[str]) -> str:
    swift = shutil.which("swift") or "/usr/bin/swift"
    cmd = [swift, _ensure_bridge_script(), *args]
    try:
        proc = subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired:
        return _json_error("Apple Calendar operation timed out")
    except Exception as exc:  # pragma: no cover - defensive wrapper
        return _json_error(f"Apple Calendar operation failed: {exc}")
    if proc.returncode != 0:
        return _json_error((proc.stderr or proc.stdout or "Apple Calendar operation failed").strip())
    try:
        return json.dumps(json.loads(proc.stdout))
    except json.JSONDecodeError:
        return _json_error(f"Apple Calendar returned non-JSON output: {proc.stdout.strip()}")


def _append_optional(cmd: list[str], flag: str, value: str | None) -> None:
    if value:
        cmd.extend([flag, value])


def apple_calendar(
    action: str,
    calendar: str | None = None,
    days: int | None = None,
    title: str | None = None,
    start: str | None = None,
    end: str | None = None,
    location: str | None = None,
    notes: str | None = None,
    event_identifier: str | None = None,
    confirmed: bool = False,
    task_id: str | None = None,
) -> str:
    del task_id
    if action == "list_calendars":
        return _run_bridge(["calendars"])

    if action == "list_events":
        cmd = ["events", "--days", str(days or 7)]
        _append_optional(cmd, "--calendar", calendar)
        return _run_bridge(cmd)

    if action == "create_event":
        if not confirmed:
            return _json_error("create_event requires confirmed=true after explicit user approval")
        missing = [name for name, value in {"title": title, "start": start, "end": end}.items() if not value]
        if missing:
            return _json_error(f"create_event missing required field(s): {', '.join(missing)}")
        cmd = ["create"]
        _append_optional(cmd, "--calendar", calendar)
        cmd.extend(["--title", title or "", "--start", start or "", "--end", end or ""])
        _append_optional(cmd, "--location", location)
        _append_optional(cmd, "--notes", notes)
        return _run_bridge(cmd)

    if action == "delete_event":
        if not confirmed:
            return _json_error("delete_event requires confirmed=true after explicit user approval")
        if not event_identifier:
            return _json_error("delete_event missing required field: event_identifier")
        return _run_bridge(["delete", event_identifier])

    return _json_error(f"Unknown action: {action}")


def _handle_apple_calendar(args: dict[str, Any], **kwargs: Any) -> str:
    return apple_calendar(
        action=args.get("action", ""),
        calendar=args.get("calendar"),
        days=args.get("days"),
        title=args.get("title"),
        start=args.get("start"),
        end=args.get("end"),
        location=args.get("location"),
        notes=args.get("notes"),
        event_identifier=args.get("event_identifier"),
        confirmed=bool(args.get("confirmed", False)),
        task_id=kwargs.get("task_id"),
    )


registry.register(
    name="apple_calendar",
    toolset="apple_calendar",
    schema=APPLE_CALENDAR_SCHEMA,
    handler=_handle_apple_calendar,
    check_fn=check_apple_calendar_requirements,
    emoji="📅",
    max_result_size_chars=100_000,
)
