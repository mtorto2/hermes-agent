import Darwin
import Foundation

public enum SlotLifecycleState: String, Codable, Equatable {
    case missing
    case idle
    case working
    case humanIntervention = "human_intervention"
    case finalAnswer = "final_answer"
    case error
}

/// Agent Lights is a shared local surface across Hermes profiles, so it always
/// reads the primary user home's status directory rather than a profile-scoped
/// HERMES_HOME inherited from whichever producer launched it.
///
/// The `v2` pool belongs to the current ownership protocol (flock-serialized
/// claims, numeric start times, claim-time reaping). Pre-v2 producers write
/// `agent-lights/slots` and `agent-lights/agents`, which this app ignores, so
/// the two generations can never contend for one slot.
public enum AgentLightsPath {
    public static let poolVersion = "v2"

    public static func sharedDirectory(userHomeDirectory: String) -> URL {
        URL(fileURLWithPath: userHomeDirectory)
            .appendingPathComponent(".hermes")
            .appendingPathComponent("agent-lights")
            .appendingPathComponent(poolVersion)
    }
}

/// `app.json` in the pool tells Hermes's launcher a current (v2) app is
/// running. The launcher opens the app only when no Agent Lights app runs at
/// all; it never quits or replaces one.
public enum AppHeartbeat {
    public static let fileName = "app.json"

    public static func write(directory: URL, pid: Int, processStartTime: Double?) throws {
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        var payload: [String: Any] = ["pid": pid, "pool": AgentLightsPath.poolVersion]
        if let processStartTime {
            payload["process_start_time"] = processStartTime
        }
        let data = try JSONSerialization.data(withJSONObject: payload, options: [.sortedKeys])
        try data.write(to: directory.appendingPathComponent(fileName), options: .atomic)
    }

    /// Make sure the heartbeat names `pid`, rewriting it if it is missing,
    /// unreadable, or names another process. Returns whether it does now.
    @discardableResult
    public static func ensure(directory: URL, pid: Int, processStartTime: Double?) -> Bool {
        let url = directory.appendingPathComponent(fileName)
        if let data = try? Data(contentsOf: url),
           let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
           object["pid"] as? Int == pid,
           object["pool"] as? String == AgentLightsPath.poolVersion,
           startTimesMatch(object["process_start_time"] as? Double, processStartTime) {
            return true
        }
        do {
            try write(directory: directory, pid: pid, processStartTime: processStartTime)
            return true
        } catch {
            return false
        }
    }

    private static func startTimesMatch(_ stored: Double?, _ actual: Double?) -> Bool {
        guard let stored, let actual else { return stored == nil && actual == nil }
        return abs(stored - actual) <= SlotStatus.startTimeTolerance
    }

    /// Remove the heartbeat only if it still names `pid` (a newer app may own it).
    public static func removeIfOwned(directory: URL, pid: Int) {
        let url = directory.appendingPathComponent(fileName)
        guard let data = try? Data(contentsOf: url),
              let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              object["pid"] as? Int == pid else { return }
        try? FileManager.default.removeItem(at: url)
    }
}

/// Migration guard: the current app runs only from its own versioned bundle.
/// Pre-v2 launchers package the newest built binary (which after an upgrade is
/// this one) into `.build/AgentLightsMenuBar.app` and may rewrite that bundle
/// at any time without taking the v2 launch lock. A copy running from there,
/// or from anything aliased to it, must never become the v2 owner, so it exits
/// before taking the instance lock or writing the heartbeat. This checks where
/// the app runs, not build provenance. `AGENT_LIGHTS_ALLOW_ANY_BUNDLE=1`
/// disables it for development runs only; leave it unset in normal use.
public enum AppBundleIdentity {
    public static let bundleName = "AgentLightsMenuBar-v2.app"
    public static let legacyBundleName = "AgentLightsMenuBar.app"
    public static let allowAnyBundleEnvironmentKey = "AGENT_LIGHTS_ALLOW_ANY_BUNDLE"

    /// Why the app must not run from here, or nil if it may. Fails closed:
    /// anything that cannot be inspected is a rejection.
    public static func rejection(bundleURL: URL, executableURL: URL?, environment: [String: String]) -> String? {
        if environment[allowAnyBundleEnvironmentKey] == "1" {
            return nil
        }
        let launchedPath = bundleURL.standardizedFileURL.path
        guard (launchedPath as NSString).lastPathComponent == bundleName,
              let bundle = canonicalPath(launchedPath),
              (bundle as NSString).lastPathComponent == bundleName else {
            return "running from \(launchedPath), not a real \(bundleName)"
        }
        guard let executableURL, let executable = canonicalPath(executableURL.standardizedFileURL.path) else {
            return "cannot inspect the app executable"
        }
        guard executable.hasPrefix(bundle + "/") else {
            return "executable \(executable) is outside \(bundle)"
        }
        var info = stat()
        guard stat(executable, &info) == 0 else {
            return "cannot stat the app executable \(executable)"
        }
        // A hard-linked executable can be rewritten in place through another
        // path (pre-v2 launchers truncate and rewrite their executable).
        if info.st_nlink > 1 {
            return "executable \(executable) has \(info.st_nlink) hard links"
        }
        // The pre-v2 launcher writes <dir>/AgentLightsMenuBar.app/Contents/
        // MacOS/AgentLightsMenuBar. If that path reaches into this bundle
        // through any symlink (the bundle root, Contents, MacOS or the
        // executable itself), or names this very file, it could rewrite us.
        let relativeExecutable = String(executable.dropFirst(bundle.count + 1))
        for directory in Set([(launchedPath as NSString).deletingLastPathComponent, (bundle as NSString).deletingLastPathComponent]) {
            let legacyExecutable = ((directory as NSString).appendingPathComponent(legacyBundleName) as NSString)
                .appendingPathComponent(relativeExecutable)
            if let reached = canonicalPath(legacyExecutable), reached == bundle || reached.hasPrefix(bundle + "/") {
                return "\(legacyExecutable) resolves into this bundle (\(reached))"
            }
            var legacyInfo = stat()
            if stat(legacyExecutable, &legacyInfo) == 0, legacyInfo.st_dev == info.st_dev, legacyInfo.st_ino == info.st_ino {
                return "\(legacyExecutable) is this app's executable"
            }
        }
        return nil
    }

    /// `realpath` of the longest existing prefix of `path`, with the missing
    /// remainder appended. Unlike `URL.resolvingSymlinksInPath`, this never
    /// rewrites `/private/var` as `/var`, so equal storage compares equal.
    public static func canonicalPath(_ path: String) -> String? {
        var existing = path
        var missing: [String] = []
        while !existing.isEmpty, existing != "/" {
            if let resolved = realpath(existing, nil) {
                defer { free(resolved) }
                var result = String(cString: resolved)
                for component in missing.reversed() {
                    result = (result as NSString).appendingPathComponent(component)
                }
                return result
            }
            guard errno == ENOENT else { return nil }
            missing.append((existing as NSString).lastPathComponent)
            existing = (existing as NSString).deletingLastPathComponent
        }
        return nil
    }

}

/// One current (v2) app at a time. The app holds an exclusive `flock` on
/// `<pool>/app.lock` for its whole life; a second copy (opened by a racing
/// launcher or by hand) cannot get it and exits. The kernel releases the lock
/// when the holder dies, so a crash never blocks the next launch.
public enum AppInstanceLock {
    public static let fileName = "app.lock"

    public enum Outcome: Equatable {
        /// Keep this descriptor open for the process lifetime.
        case acquired(Int32)
        case heldByAnotherApp
        case failed(String)
    }

    public static func acquire(directory: URL) -> Outcome {
        do {
            try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        } catch {
            return .failed("cannot create \(directory.path): \(error.localizedDescription)")
        }
        let path = directory.appendingPathComponent(fileName).path
        let fd = open(path, O_RDWR | O_CREAT, 0o600)
        guard fd >= 0 else {
            return .failed("cannot open \(path): \(String(cString: strerror(errno)))")
        }
        guard flock(fd, LOCK_EX | LOCK_NB) == 0 else {
            let code = errno
            close(fd)
            return code == EWOULDBLOCK ? .heldByAnotherApp : .failed("cannot lock \(path): \(String(cString: strerror(code)))")
        }
        return .acquired(fd)
    }
}

public struct SlotStatus: Equatable {
    public static let recentWithoutLivePidWindow: TimeInterval = 120

    public let slot: Int
    public let state: SlotLifecycleState
    public let event: String?
    public let updatedAt: String?
    public let pid: Int?
    /// Legacy `ps -o lstart` text from pre-hardening producers. Its wording
    /// depends on the writer's locale and timezone, so it is decoded for
    /// display/debugging only and never used to judge liveness.
    public let processStartedAt: String?
    /// Owner process start time in epoch seconds (locale-independent).
    public let processStartTime: Double?
    public let source: String?
    public let modelName: String?
    public let kanbanBoard: String?
    public let kanbanTaskId: String?
    public let kanbanTaskTitle: String?
    public let profile: String?
    public let sessionId: String?

    public var isKanbanWorker: Bool {
        source == "kanban_worker"
    }

    public var menuDetail: String {
        menuDetail(displayName: nil)
    }

    public func menuDetail(displayName: String?) -> String {
        let label = trimmed(displayName) ?? displayModelName
        return "\(slotLetter): \(label) - \(state.menuLabel)"
    }

    public var terminalTabTitle: String {
        "Hermes \(slotLetter)"
    }

    public var agentMenuDetail: String {
        "Agent \(slot): \(displayModelName) - \(state.menuLabel)"
    }

    private var slotLetter: String {
        guard (1...26).contains(slot),
              let scalar = UnicodeScalar(64 + slot) else { return String(slot) }
        return String(Character(scalar))
    }

    private var displayModelName: String {
        guard let rawModel = trimmed(modelName) else { return "unknown" }
        let leaf = rawModel.split(separator: "/").last.map(String.init) ?? rawModel
        let lowerLeaf = leaf.lowercased()
        if lowerLeaf.hasPrefix("gpt-") {
            return "GPT-" + String(leaf.dropFirst(4))
        }
        if lowerLeaf.hasPrefix("claude-sonnet-") {
            return "Sonnet " + String(leaf.dropFirst("claude-sonnet-".count)).replacingOccurrences(of: "-", with: ".")
        }
        if lowerLeaf.hasPrefix("claude-opus-") {
            return "Opus " + String(leaf.dropFirst("claude-opus-".count)).replacingOccurrences(of: "-", with: ".")
        }
        if lowerLeaf.hasPrefix("claude-haiku-") {
            return "Haiku " + String(leaf.dropFirst("claude-haiku-".count)).replacingOccurrences(of: "-", with: ".")
        }
        let normalized = leaf
            .replacingOccurrences(of: "-", with: " ")
            .replacingOccurrences(of: "_", with: " ")
        return normalized.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? "unknown" : normalized
    }

    public static func missing(slot: Int) -> SlotStatus {
        SlotStatus(slot: slot, state: .missing, event: nil, updatedAt: nil, pid: nil, processStartedAt: nil, processStartTime: nil, source: nil, modelName: nil, kanbanBoard: nil, kanbanTaskId: nil, kanbanTaskTitle: nil, profile: nil, sessionId: nil)
    }

    public static func decode(from data: Data) throws -> SlotStatus {
        let payload = try JSONDecoder().decode(SlotStatusPayload.self, from: data)
        let state = SlotLifecycleState(rawValue: payload.state ?? payload.event ?? "") ?? .missing
        return SlotStatus(
            slot: payload.slot,
            state: state,
            event: payload.event,
            updatedAt: payload.updatedAt,
            pid: payload.pid,
            processStartedAt: payload.processStartedAt,
            processStartTime: payload.processStartTime,
            source: payload.source,
            modelName: payload.modelName,
            kanbanBoard: payload.kanbanBoard,
            kanbanTaskId: payload.kanbanTaskId,
            kanbanTaskTitle: payload.kanbanTaskTitle,
            profile: payload.profile,
            sessionId: payload.sessionId
        )
    }

    public static func orderedByTerminalTTY(
        _ statuses: [SlotStatus],
        ttyForPid: [Int: String],
        terminalTTYOrder: [String]
    ) -> [SlotStatus] {
        var ttyRank: [String: Int] = [:]
        for (index, tty) in terminalTTYOrder.enumerated() {
            guard let normalized = normalizedTTY(tty), ttyRank[normalized] == nil else { continue }
            ttyRank[normalized] = index
        }
        return statuses.sorted { left, right in
            let leftRank = left.pid.flatMap { ttyForPid[$0] }.flatMap { ttyRank[normalizedTTY($0) ?? ""] }
            let rightRank = right.pid.flatMap { ttyForPid[$0] }.flatMap { ttyRank[normalizedTTY($0) ?? ""] }
            switch (leftRank, rightRank) {
            case let (left?, right?) where left != right:
                return left < right
            case (_?, nil):
                return true
            case (nil, _?):
                return false
            default:
                return left.slot < right.slot
            }
        }
    }

    private static func normalizedTTY(_ tty: String) -> String? {
        let trimmedTTY = tty.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmedTTY.isEmpty, trimmedTTY != "??" else { return nil }
        if trimmedTTY.hasPrefix("/dev/") {
            return String(trimmedTTY.dropFirst(5))
        }
        return trimmedTTY
    }

    /// Two readings of one process's start time can differ by float rounding;
    /// a recycled PID cannot start within this window of the original.
    public static let startTimeTolerance: TimeInterval = 1.0

    public func shouldRender(
        now: Date = Date(),
        fileModifiedAt: Date? = nil,
        processIsRunning: (Int) -> Bool = SlotStatus.processIsRunning,
        processStartTimeForPid: (Int) -> Double? = ProcessInspector.startTime
    ) -> Bool {
        guard state != .missing else { return false }

        if let pid, pid > 0 {
            if Self.ownerIsAlive(
                pid: pid,
                processStartTime: processStartTime,
                processIsRunning: processIsRunning,
                processStartTimeForPid: processStartTimeForPid
            ) {
                return true
            }
            // The owner is gone. Normal Hermes sessions often die without
            // cleanup (terminal close, os._exit paths, kill -9), so hide them
            // at once instead of leaving a ghost dot. Kanban workers exit by
            // design after finishing; keep their last state briefly visible.
            return isKanbanWorker && wasRecentlyModified(now: now, fileModifiedAt: fileModifiedAt)
        }

        return wasRecentlyModified(now: now, fileModifiedAt: fileModifiedAt)
    }

    public func shouldPrune(
        now: Date = Date(),
        fileModifiedAt: Date? = nil,
        processIsRunning: (Int) -> Bool = SlotStatus.processIsRunning,
        processStartTimeForPid: (Int) -> Double? = ProcessInspector.startTime
    ) -> Bool {
        state != .missing && !shouldRender(
            now: now,
            fileModifiedAt: fileModifiedAt,
            processIsRunning: processIsRunning,
            processStartTimeForPid: processStartTimeForPid
        )
    }

    /// Whether `pid` still names the process that wrote a file stamped with
    /// `processStartTime` (nil for legacy files: judged on PID liveness alone).
    public static func ownerIsAlive(
        pid: Int,
        processStartTime: Double?,
        processIsRunning: (Int) -> Bool = SlotStatus.processIsRunning,
        processStartTimeForPid: (Int) -> Double? = ProcessInspector.startTime
    ) -> Bool {
        guard processIsRunning(pid) else { return false }
        // Legacy payloads have no numeric start time: trust PID liveness.
        guard let processStartTime else { return true }
        guard let actual = processStartTimeForPid(pid) else { return false }
        return abs(actual - processStartTime) <= Self.startTimeTolerance
    }

    private func wasRecentlyModified(now: Date, fileModifiedAt: Date?) -> Bool {
        guard let fileModifiedAt else { return false }
        return now.timeIntervalSince(fileModifiedAt) <= Self.recentWithoutLivePidWindow
    }

    public static func processIsRunning(pid: Int) -> Bool {
        kill(pid_t(pid), 0) == 0 || errno == EPERM
    }
}

/// Process facts read straight from the kernel. Unlike `ps`, this spawns no
/// subprocess (so it is cheap on the main thread) and its answers do not depend
/// on locale or timezone.
public enum ProcessInspector {
    private static func kinfo(pid: Int) -> kinfo_proc? {
        var info = kinfo_proc()
        var size = MemoryLayout<kinfo_proc>.stride
        var mib: [Int32] = [CTL_KERN, KERN_PROC, KERN_PROC_PID, Int32(pid)]
        guard sysctl(&mib, u_int(mib.count), &info, &size, nil, 0) == 0, size > 0 else { return nil }
        return info
    }

    /// Process start time in epoch seconds, or nil if no such process.
    public static func startTime(pid: Int) -> Double? {
        guard pid > 0, let info = kinfo(pid: pid) else { return nil }
        let start = info.kp_proc.p_un.__p_starttime
        return Double(start.tv_sec) + Double(start.tv_usec) / 1_000_000
    }

    /// Controlling terminal name such as `ttys003`, or nil if none.
    public static func tty(pid: Int) -> String? {
        guard pid > 0, let info = kinfo(pid: pid) else { return nil }
        let device = info.kp_eproc.e_tdev
        guard device != -1, let name = devname(device, S_IFCHR) else { return nil }
        let tty = String(cString: name)
        return tty.isEmpty || tty == "??" ? nil : tty
    }

    /// Parent process id, or nil if no such process.
    public static func parentPid(pid: Int) -> Int? {
        guard pid > 0, let info = kinfo(pid: pid) else { return nil }
        let parent = Int(info.kp_eproc.e_ppid)
        return parent > 0 ? parent : nil
    }

    /// When the terminal tab hosting `pid` was opened: the start time of the
    /// oldest ancestor on the same controlling terminal (the tab's login
    /// shell). Nil when the process has no terminal. Unlike the process's own
    /// start time, this stays put when Hermes is relaunched in the same tab or
    /// restarts a backend process.
    public static func terminalSessionStartTime(
        pid: Int,
        ttyOf: (Int) -> String? = ProcessInspector.tty,
        parentOf: (Int) -> Int? = ProcessInspector.parentPid,
        startTimeOf: (Int) -> Double? = ProcessInspector.startTime
    ) -> Double? {
        guard let tty = ttyOf(pid) else { return nil }
        var oldest = pid
        var seen: Set<Int> = [pid]
        while let parent = parentOf(oldest), !seen.contains(parent), ttyOf(parent) == tty {
            seen.insert(parent)
            oldest = parent
        }
        return startTimeOf(oldest)
    }
}

/// Removes a dead owner's slot files without racing a new claimant.
///
/// Hermes claims slots while holding an exclusive `flock` on
/// `<directory>/.claim.lock`, and reaps the previous owner's status file in the
/// same critical section. Pruning takes the same lock (without waiting, so the
/// UI never blocks) and then re-reads each file and judges the owner it names
/// *now*, by PID and start time. A decision made from an earlier snapshot can
/// therefore never delete a file a new or PID-recycled owner has written since.
public enum SlotFilePruner {
    @discardableResult
    public static func prune(
        directory: URL,
        slot: Int,
        now: Date = Date(),
        processIsRunning: (Int) -> Bool = SlotStatus.processIsRunning,
        processStartTimeForPid: (Int) -> Double? = ProcessInspector.startTime
    ) -> Bool {
        let claimLock = directory.appendingPathComponent(".claim.lock").path
        let fd = open(claimLock, O_RDWR | O_CREAT, 0o600)
        guard fd >= 0 else { return false }
        defer { close(fd) }
        guard flock(fd, LOCK_EX | LOCK_NB) == 0 else { return false }
        defer { flock(fd, LOCK_UN) }

        var removed = false
        let jsonURL = directory.appendingPathComponent("\(slot).json")
        if let data = try? Data(contentsOf: jsonURL),
           let status = try? SlotStatus.decode(from: data),
           status.slot == slot {
            let modifiedAt = try? jsonURL.resourceValues(forKeys: [.contentModificationDateKey]).contentModificationDate
            if status.shouldPrune(
                now: now,
                fileModifiedAt: modifiedAt,
                processIsRunning: processIsRunning,
                processStartTimeForPid: processStartTimeForPid
            ), (try? FileManager.default.removeItem(at: jsonURL)) != nil {
                removed = true
            }
        }

        let lockURL = directory.appendingPathComponent("\(slot).lock")
        if let data = try? Data(contentsOf: lockURL),
           let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
           let pid = object["pid"] as? Int, pid > 0,
           !SlotStatus.ownerIsAlive(
               pid: pid,
               processStartTime: object["process_start_time"] as? Double,
               processIsRunning: processIsRunning,
               processStartTimeForPid: processStartTimeForPid
           ),
           (try? FileManager.default.removeItem(at: lockURL)) != nil {
            removed = true
        }
        return removed
    }
}

/// Left-to-right order of the normal Hermes dots.
///
/// Slots are reused lowest-free-first, so slot order drifts away from the
/// Terminal tab order as sessions come and go. Terminal's own window/tab order
/// follows focus, which made the dots jump. Ordering by when each session's
/// tab was opened (falling back to the process start time without a terminal)
/// matches tabs opened left to right and never moves with focus. Ties and
/// sessions with no time sort by slot.
public enum DotOrder {
    public static func ordered(
        _ statuses: [SlotStatus],
        sessionStartTime: (SlotStatus) -> Double? = DotOrder.defaultSessionStartTime,
        processStartTime: (SlotStatus) -> Double? = { $0.processStartTime }
    ) -> [SlotStatus] {
        // Earlier first; a missing time sorts after any known one.
        func compare(_ left: Double?, _ right: Double?) -> Bool? {
            switch (left, right) {
            case let (l?, r?) where l != r: return l < r
            case (_?, nil): return true
            case (nil, _?): return false
            default: return nil
            }
        }
        let keyed = statuses.map { (status: $0, session: sessionStartTime($0), process: processStartTime($0)) }
        return keyed.sorted { left, right in
            compare(left.session, right.session)
                ?? compare(left.process, right.process)
                ?? (left.status.slot < right.status.slot)
        }.map(\.status)
    }

    /// When the session's Terminal tab was opened, or the process start time
    /// for a session without a terminal.
    public static func defaultSessionStartTime(_ status: SlotStatus) -> Double? {
        if let pid = status.pid, let tabOpened = ProcessInspector.terminalSessionStartTime(pid: pid) {
            return tabOpened
        }
        return status.processStartTime
    }
}

/// Last good Terminal tab bar read. A failed or incomplete Accessibility read
/// must not swap the dots into fallback order for a moment, so a failure keeps
/// the last good titles until they are `staleAfter` seconds old, then reports
/// the tab bar as unavailable.
public struct TabBarCache: Equatable {
    public static let staleAfter: TimeInterval = 10

    public private(set) var titles: [String] = []
    public private(set) var lastGoodAt: Date?
    public private(set) var lastFailure: String?

    public init() {}

    /// True when reads are failing and no recent good read remains.
    public var isUnavailable: Bool {
        lastFailure != nil && lastGoodAt == nil
    }

    public mutating func recordSuccess(_ titles: [String], at now: Date) {
        self.titles = titles
        lastGoodAt = now
        lastFailure = nil
    }

    public mutating func recordFailure(_ reason: String, at now: Date) {
        lastFailure = reason
        if let lastGoodAt, now.timeIntervalSince(lastGoodAt) <= Self.staleAfter {
            return
        }
        titles = []
        lastGoodAt = nil
    }

    /// Enforce the age limit when the titles are used, so a read that is
    /// slow or stuck (not just one that fails) cannot keep stale order alive.
    public mutating func expire(at now: Date) {
        guard let lastGoodAt, now.timeIntervalSince(lastGoodAt) > Self.staleAfter else { return }
        titles = []
        self.lastGoodAt = nil
        lastFailure = lastFailure ?? "no tab bar read in \(Int(Self.staleAfter))s"
    }

    /// Permission revoked or Terminal gone: nothing to keep.
    public mutating func reset() {
        self = TabBarCache()
    }
}

/// Dot order from Terminal's real tab bar.
///
/// Terminal's tab titles are the only place the on-screen tab order is
/// exposed (through Accessibility), and titles are often identical
/// ("Terminal"). With the profile's Tab title set to include "TTY", each title
/// carries its tab's `ttysNNN`, which matches a session's controlling terminal
/// exactly. Sessions whose terminal is not found in the tab bar keep their
/// fallback (tab-opened) order after the matched ones.
public enum TabBarOrder {
    public enum Source: Equatable {
        /// Every session was placed by its tab's position.
        case tabBar
        /// Some or all sessions fell back to tab-opened order, and why.
        case tabOpened(reason: String)
    }

    public static func ttys(inTitle title: String) -> [String] {
        guard let regex = try? NSRegularExpression(pattern: #"\bttys[0-9]+\b"#) else { return [] }
        let range = NSRange(title.startIndex..., in: title)
        return regex.matches(in: title, range: range).compactMap { match in
            Range(match.range, in: title).map { String(title[$0]) }
        }
    }

    /// One Terminal window as Accessibility reports it.
    public struct WindowTabs: Equatable {
        public let x: Double
        public let y: Double
        /// Tab titles in tab-bar order, or nil when the window shows no tab bar.
        public let tabTitles: [String]?
        public let windowTitle: String?

        public init(x: Double, y: Double, tabTitles: [String]?, windowTitle: String?) {
            self.x = x
            self.y = y
            self.tabTitles = tabTitles
            self.windowTitle = windowTitle
        }
    }

    /// Flatten windows into on-screen tab order: windows left to right, then
    /// top to bottom, tabs left to right. Windows at the same position (stacked,
    /// full screen, other Spaces) are ordered by their lowest TTY number, which
    /// never changes, rather than by Accessibility's reporting order, which
    /// follows focus.
    ///
    /// Each tab of a tabbed window is also reported as its own window (the
    /// unselected ones hidden, at the same position, with no tab bar). Such a
    /// window is only a real single tab if its terminal is not already in some
    /// tab bar, so tab bars are placed first and duplicates are dropped.
    public static func onScreenTabTitles(_ windows: [WindowTabs]) -> [String] {
        func identity(_ window: WindowTabs) -> (tty: Int, title: String) {
            let titles = window.tabTitles ?? window.windowTitle.map { [$0] } ?? []
            let numbers = titles.flatMap(ttys(inTitle:)).compactMap { Int($0.dropFirst("ttys".count)) }
            return (numbers.min() ?? Int.max, titles.joined(separator: "\u{1F}"))
        }
        let ordered = windows.enumerated().sorted { left, right in
            if left.element.x != right.element.x { return left.element.x < right.element.x }
            if left.element.y != right.element.y { return left.element.y < right.element.y }
            let l = identity(left.element), r = identity(right.element)
            if l.tty != r.tty { return l.tty < r.tty }
            if l.title != r.title { return l.title < r.title }
            return left.offset < right.offset
        }.map(\.element)
        let inTabBars = Set(ordered.flatMap { ($0.tabTitles ?? []).flatMap(ttys(inTitle:)) })
        var titles: [String] = []
        for window in ordered {
            if let tabTitles = window.tabTitles {
                titles.append(contentsOf: tabTitles)
            } else if let title = window.windowTitle {
                let found = ttys(inTitle: title)
                if found.isEmpty || !found.allSatisfy(inTabBars.contains) {
                    titles.append(title)
                }
            }
        }
        return titles
    }

    /// `tabTitles` are Terminal tab titles in on-screen order (windows left to
    /// right, tabs left to right). `fallbackOrdered` is the tab-opened order.
    public static func ordered(
        fallbackOrdered: [SlotStatus],
        tabTitles: [String],
        ttyOf: (SlotStatus) -> String?
    ) -> (statuses: [SlotStatus], matchedCount: Int) {
        var rank: [String: Int] = [:]
        for (index, title) in tabTitles.enumerated() {
            let found = ttys(inTitle: title)
            // A title naming several terminals cannot place one tab.
            guard found.count == 1, rank[found[0]] == nil else { continue }
            rank[found[0]] = index
        }
        var matched: [(rank: Int, status: SlotStatus)] = []
        var unmatched: [SlotStatus] = []
        for status in fallbackOrdered {
            if let tty = ttyOf(status).map(normalizedTTY), let position = rank[tty] {
                matched.append((position, status))
            } else {
                unmatched.append(status)
            }
        }
        let placed = matched.sorted { $0.rank < $1.rank }.map(\.status)
        return (placed + unmatched, matched.count)
    }

    private static func normalizedTTY(_ tty: String) -> String {
        tty.hasPrefix("/dev/") ? String(tty.dropFirst(5)) : tty
    }
}

public struct SlotStatusMenuModel: Equatable {
    public let summaryTitle: String
    public let rowTitles: [String]
    public let tooltip: String

    public init(statuses: [SlotStatus], agentCount: Int? = nil) {
        let hermesStatuses = statuses.filter { !$0.isKanbanWorker }
        let agentStatuses = statuses.filter(\.isKanbanWorker)
        self.init(hermesStatuses: hermesStatuses, agentStatuses: agentStatuses, agentCount: agentCount)
    }

    public init(hermesStatuses: [SlotStatus], agentStatuses: [SlotStatus], agentCount: Int? = nil, hermesDisplayNames: [Int: String] = [:]) {
        let hermesRows = hermesStatuses.map { status in
            status.menuDetail(displayName: hermesDisplayNames[status.slot])
        }
        let agentRows = agentStatuses.map(\.agentMenuDetail)
        let resolvedAgentCount = agentCount ?? agentStatuses.count
        self.rowTitles = hermesRows + agentRows
        self.summaryTitle = "Hermes: \(hermesStatuses.count) active  Agents: \(resolvedAgentCount) active"
        self.tooltip = rowTitles.isEmpty ? self.summaryTitle : self.summaryTitle + "\n" + rowTitles.joined(separator: "\n")
    }
}

public struct AgentRing: Equatable {
    public let state: SlotLifecycleState
    public let isPlaceholder: Bool

    public init(state: SlotLifecycleState, isPlaceholder: Bool) {
        self.state = state
        self.isPlaceholder = isPlaceholder
    }
}

public struct AgentRingGroup: Equatable {
    public let capacity: Int
    public let rings: [AgentRing]

    public var shouldRender: Bool {
        rings.contains { !$0.isPlaceholder }
    }

    public init(statuses: [SlotStatus], capacity: Int = 8) {
        self.capacity = capacity
        let visible = Array(statuses.sorted { $0.slot < $1.slot }.prefix(capacity))
        var rings = visible.map { AgentRing(state: $0.state, isPlaceholder: false) }
        while rings.count < capacity {
            rings.append(AgentRing(state: .missing, isPlaceholder: true))
        }
        self.rings = rings
    }
}

public struct AgentRingGridPosition: Equatable {
    public let column: Int
    public let row: Int

    public init(column: Int, row: Int) {
        self.column = column
        self.row = row
    }
}

public enum AgentIndicatorStyle: Equatable {
    case filledCircle
}

public enum NormalSlotIndicatorStyle: Equatable {
    case filled
    case outline
}

public struct NormalSlotIndicator: Equatable {
    public let slot: Int
    public let state: SlotLifecycleState
    public let style: NormalSlotIndicatorStyle

    /// `flashOn` is the current blink phase. Errors alternate between filled
    /// and outline so they read differently from a steady red "answer ready".
    public init(status: SlotStatus, flashOn: Bool = true) {
        self.slot = status.slot
        self.state = status.state
        switch status.state {
        case .idle:
            self.style = .outline
        case .error:
            self.style = flashOn ? .filled : .outline
        default:
            self.style = .filled
        }
    }
}

public struct StatusIndicatorGeometry: Equatable {
    public let hermesDotDiameter: Double
    public let hermesDotSpacing: Double
    public let agentIndicatorStyle: AgentIndicatorStyle
    public let agentCircleDiameter: Double
    public let agentCircleColumnSpacing: Double
    public let agentCircleRowSpacing: Double
    public let agentIndicatorWidth: Double

    public init(
        hermesDotDiameter: Double = 9.0,
        hermesDotSpacing: Double = 14.0,
        agentIndicatorStyle: AgentIndicatorStyle = .filledCircle,
        agentCircleDiameter: Double = 6.8,
        agentCircleColumnSpacing: Double = 8.2,
        agentCircleRowSpacing: Double = 8.2,
        agentIndicatorWidth: Double = 39.0
    ) {
        self.hermesDotDiameter = hermesDotDiameter
        self.hermesDotSpacing = hermesDotSpacing
        self.agentIndicatorStyle = agentIndicatorStyle
        self.agentCircleDiameter = agentCircleDiameter
        self.agentCircleColumnSpacing = agentCircleColumnSpacing
        self.agentCircleRowSpacing = agentCircleRowSpacing
        self.agentIndicatorWidth = agentIndicatorWidth
    }
}

public struct StatusIndicatorLayout: Equatable {
    public let normalSlotIndicators: [NormalSlotIndicator]
    public let filledDotCount: Int
    public let agentRingCount: Int
    public let agentRingColumns: Int
    public let agentRingRows: Int
    public let agentRingGridPositions: [AgentRingGridPosition]

    public var shouldRenderAgentRings: Bool {
        agentRingCount > 0
    }

    public init(hermesStatuses: [SlotStatus], agentGroup: AgentRingGroup, flashOn: Bool = true) {
        self.normalSlotIndicators = hermesStatuses
            .filter { (1...6).contains($0.slot) }
            .map { NormalSlotIndicator(status: $0, flashOn: flashOn) }
        self.filledDotCount = normalSlotIndicators.filter { $0.style == .filled }.count
        self.agentRingCount = agentGroup.shouldRender ? agentGroup.capacity : 0
        let columns = max(2, ((agentGroup.capacity + 3) / 4) * 2)
        self.agentRingColumns = columns
        self.agentRingRows = 2
        self.agentRingGridPositions = (0..<agentGroup.capacity).map { index in
            let bank = index / 4
            let indexInBank = index % 4
            return AgentRingGridPosition(column: bank * 2 + indexInBank % 2, row: indexInBank / 2)
        }
    }
}

public struct FloatingMonitorWindowSpec: Equatable, Sendable {
    public let defaultWidth: Double
    public let defaultHeight: Double
    public let opacity: Double
    public let backgroundRed: Double
    public let backgroundGreen: Double
    public let backgroundBlue: Double
    public let backgroundAlpha: Double
    public let drawsCircleOutlines: Bool
    public let isFloating: Bool
    public let isResizable: Bool
    public let isTranslucent: Bool

    public static let `default` = FloatingMonitorWindowSpec(
        defaultWidth: 500,
        defaultHeight: 230,
        opacity: 0.72,
        backgroundRed: 0.97,
        backgroundGreen: 0.96,
        backgroundBlue: 0.93,
        backgroundAlpha: 0.58,
        drawsCircleOutlines: false,
        isFloating: true,
        isResizable: true,
        isTranslucent: true
    )
}

public enum FloatingMonitorOpacity {
    public static let minimum = 0.35
    public static let maximum = 0.95
    public static let step = 0.08

    public static func clamped(_ value: Double) -> Double {
        min(maximum, max(minimum, value))
    }

    public static func moreTransparent(from value: Double) -> Double {
        clamped(value - step)
    }

    public static func lessTransparent(from value: Double) -> Double {
        clamped(value + step)
    }
}

public struct FloatingMonitorSize: Equatable {
    public let width: Double
    public let height: Double

    public init(width: Double, height: Double) {
        self.width = width
        self.height = height
    }
}

public struct FloatingMonitorItem: Equatable {
    public let state: SlotLifecycleState
    public let isAgent: Bool
    public let isPlaceholder: Bool
    public let x: Double
    public let y: Double
    public let diameter: Double
}

public struct FloatingMonitorLayout: Equatable {
    public let items: [FloatingMonitorItem]

    public init(size: FloatingMonitorSize, hermesStatuses: [SlotStatus], agentGroup: AgentRingGroup) {
        let activeHermes = Array(hermesStatuses.prefix(6))
        let includeAgents = agentGroup.shouldRender
        let agentUnitCount = includeAgents ? 1 : 0
        let unitCount = activeHermes.count + agentUnitCount
        guard unitCount > 0 else {
            self.items = []
            return
        }

        let availableWidth = max(80, size.width - 56)
        let availableHeight = max(80, size.height - 62)
        let unitGapRatio = 0.24
        let totalGapUnits = Double(max(0, unitCount - 1)) * unitGapRatio
        let unitSize = max(18, min(availableHeight * 0.72, availableWidth / (Double(unitCount) + totalGapUnits)))
        let unitGap = unitSize * unitGapRatio
        let totalWidth = Double(unitCount) * unitSize + Double(max(0, unitCount - 1)) * unitGap
        var cursorX = (size.width - totalWidth) / 2
        let centerY = size.height / 2
        var built: [FloatingMonitorItem] = []

        for status in activeHermes {
            built.append(FloatingMonitorItem(
                state: status.state,
                isAgent: false,
                isPlaceholder: false,
                x: cursorX,
                y: centerY - unitSize / 2,
                diameter: unitSize
            ))
            cursorX += unitSize + unitGap
        }

        if includeAgents {
            let agentGapRatio = 0.18
            let columns = max(2, (agentGroup.capacity + 1) / 2)
            let agentDiameter = unitSize / (Double(columns) + Double(max(0, columns - 1)) * agentGapRatio)
            let agentSpacing = agentDiameter * (1 + agentGapRatio)
            let footprintHeight = agentDiameter * 2 + agentDiameter * agentGapRatio
            let startX = cursorX
            let startY = centerY + footprintHeight / 2 - agentDiameter
            for (index, ring) in agentGroup.rings.enumerated() {
                let column = Double(index % columns)
                let row = Double(index / columns)
                built.append(FloatingMonitorItem(
                    state: ring.state,
                    isAgent: true,
                    isPlaceholder: ring.isPlaceholder,
                    x: startX + column * agentSpacing,
                    y: startY - row * agentSpacing,
                    diameter: agentDiameter
                ))
            }
        }

        self.items = built
    }
}

private extension SlotLifecycleState {
    var menuLabel: String {
        switch self {
        case .finalAnswer:
            return "answer ready"
        case .humanIntervention:
            return "needs intervention"
        case .working:
            return "working"
        case .error:
            return "error"
        case .idle:
            return "idle"
        case .missing:
            return "missing"
        }
    }
}

public enum TerminalFocusScript {
    public static func script(forTTY tty: String) -> String? {
        let trimmedTTY = tty.trimmingCharacters(in: .whitespacesAndNewlines)
        guard trimmedTTY.range(of: #"^ttys[0-9]+$"#, options: .regularExpression) != nil else {
            return nil
        }
        let terminalTTY = "/dev/\(trimmedTTY)"
        return """
        tell application "Terminal"
            activate
            repeat with w in windows
                repeat with t in tabs of w
                    if tty of t is "\(terminalTTY)" then
                        set selected tab of w to t
                        set index of w to 1
                        return true
                    end if
                end repeat
            end repeat
        end tell
        return false
        """
    }
}

public enum KanbanCardOpenScript {
    public static func script(taskId: String?, board: String?) -> String? {
        guard let rawTaskId = trimmed(taskId),
              rawTaskId.range(of: #"^t_[A-Za-z0-9_-]+$"#, options: .regularExpression) != nil else {
            return nil
        }
        let normalizedBoard = trimmed(board)
        if let normalizedBoard,
           normalizedBoard.range(of: #"^[A-Za-z0-9_.-]+$"#, options: .regularExpression) == nil {
            return nil
        }
        let boardArgs = normalizedBoard.map { " --board \($0)" } ?? ""
        let shellCommand = """
        if command -v hermes >/dev/null 2>&1; then HERMES_BIN=hermes; elif [ -x \"$HOME/.hermes/hermes-agent/venv/bin/hermes\" ]; then HERMES_BIN=\"$HOME/.hermes/hermes-agent/venv/bin/hermes\"; else echo 'hermes CLI not found'; exit 127; fi; clear; echo 'Hermes Kanban: \(rawTaskId)'; echo; \"$HERMES_BIN\" kanban\(boardArgs) show \(rawTaskId); echo; echo 'Press any key to close...'; read -n 1 -s
        """
        return """
        tell application "Terminal"
            activate
            do script \"\(appleScriptEscaped(shellCommand))\"
        end tell
        return true
        """
    }

    private static func appleScriptEscaped(_ value: String) -> String {
        value
            .replacingOccurrences(of: "\\", with: "\\\\")
            .replacingOccurrences(of: "\"", with: "\\\"")
            .replacingOccurrences(of: "\n", with: "; ")
    }
}

private func trimmed(_ value: String?) -> String? {
    guard let value else { return nil }
    let trimmedValue = value.trimmingCharacters(in: .whitespacesAndNewlines)
    return trimmedValue.isEmpty ? nil : trimmedValue
}

private struct SlotStatusPayload: Decodable {
    let slot: Int
    let event: String?
    let state: String?
    let updatedAt: String?
    let pid: Int?
    let processStartedAt: String?
    let processStartTime: Double?
    let source: String?
    let modelName: String?
    let kanbanBoard: String?
    let kanbanTaskId: String?
    let kanbanTaskTitle: String?
    let profile: String?
    let sessionId: String?

    enum CodingKeys: String, CodingKey {
        case slot
        case event
        case state
        case updatedAt = "updated_at"
        case pid
        case processStartedAt = "process_started_at"
        case processStartTime = "process_start_time"
        case source
        case modelName = "model_name"
        case kanbanBoard = "kanban_board"
        case kanbanTaskId = "kanban_task_id"
        case kanbanTaskTitle = "kanban_task_title"
        case profile
        case sessionId = "session_id"
    }
}
