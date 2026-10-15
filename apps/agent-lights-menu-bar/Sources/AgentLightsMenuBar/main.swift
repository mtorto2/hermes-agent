import AppKit
import AgentLightsCore
import Foundation
import TerminalScriptingBridge

private final class StatusRowAction: NSObject {
    let status: SlotStatus

    init(status: SlotStatus) {
        self.status = status
    }
}

private func trimmed(_ value: String?) -> String? {
    guard let value else { return nil }
    let trimmedValue = value.trimmingCharacters(in: .whitespacesAndNewlines)
    return trimmedValue.isEmpty ? nil : trimmedValue
}

@MainActor
private final class AgentLightsApp: NSObject, NSApplicationDelegate, NSMenuDelegate {
    private let statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
    private let statusMenu = NSMenu()
    private let statusSummaryItem = NSMenuItem(title: "No active Hermes slots", action: nil, keyEquivalent: "")
    private var statusRowItems: [NSMenuItem] = []
    private var agentStatusItem: NSStatusItem?
    private var timer: Timer?
    private var monitorController: FloatingMonitorController?
    private let monitorOpacityDefaultsKey = "HermesFloatingMonitorOpacity"
    private let agentLightsDirectory: URL
    private let slotsDirectory: URL
    private let agentsDirectory: URL
    private let contextDirectory: URL
    private let subprocessTimeout: TimeInterval = 2.0
    private let terminalSnapshotMaxAge: TimeInterval = 2.0
    /// Terminal tab titles keyed by TTY, refreshed off the main thread: the
    /// Apple Event round-trip to Terminal can take up to a second.
    private var terminalTitlesByTTY: [String: String] = [:]
    private var terminalSnapshotLoadedAt: ContinuousClock.Instant?
    private var terminalSnapshotInFlight = false
    /// Terminal tab titles in on-screen order, read via Accessibility, with
    /// the last good read kept through brief failures.
    private var tabBarCache = TabBarCache()
    private var tabBarTrusted = false
    private let dotOrderItem = NSMenuItem(title: "", action: nil, keyEquivalent: "")
    private let tabOrderPromptDefaultsKey = "TabOrderPermissionPromptedForBuild"
    private var errorFlashOn = true
    private var tickCount = 0
    private var heartbeatFailureLogged = false
    private var instanceLockFD: Int32?

    override init() {
        let agentLightsDirectory = AgentLightsPath.sharedDirectory(
            userHomeDirectory: NSHomeDirectory()
        )
        self.agentLightsDirectory = agentLightsDirectory
        self.slotsDirectory = agentLightsDirectory
            .appendingPathComponent("slots")
        self.agentsDirectory = agentLightsDirectory
            .appendingPathComponent("agents")
        self.contextDirectory = agentLightsDirectory
            .appendingPathComponent("context")
        super.init()
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)
        if let rejection = AppBundleIdentity.rejection(
            bundleURL: Bundle.main.bundleURL,
            executableURL: Bundle.main.executableURL,
            environment: ProcessInfo.processInfo.environment
        ) {
            exitEarly("\(rejection); exiting so the versioned app can start.")
            return
        }
        switch AppInstanceLock.acquire(directory: agentLightsDirectory) {
        case .acquired(let lockFD):
            instanceLockFD = lockFD
        case .heldByAnotherApp:
            exitEarly("another current app is already running; exiting.")
            return
        case .failed(let reason):
            exitEarly("cannot take the instance lock (\(reason)); exiting.")
            return
        }
        refreshHeartbeat()
        promptForTabOrderPermissionOncePerBuild()
        configureMenu()
        // A stable name lets macOS (and bar managers such as Ice) remember
        // where the item sits across relaunches.
        statusItem.autosaveName = "HermesAgentLights"
        statusItem.button?.toolTip = "Hermes Agent Lights"
        statusItem.menu = statusMenu
        render()
        let timer = Timer(timeInterval: 0.75, target: self, selector: #selector(tick), userInfo: nil, repeats: true)
        // .common keeps the dots updating while the menu is open.
        RunLoop.main.add(timer, forMode: .common)
        self.timer = timer
    }

    private func configureMenu() {
        statusMenu.delegate = self
        statusSummaryItem.isEnabled = false
        statusMenu.addItem(statusSummaryItem)
        dotOrderItem.target = self
        statusMenu.addItem(dotOrderItem)
        statusMenu.addItem(NSMenuItem.separator())

        let refreshItem = NSMenuItem(title: "Refresh", action: #selector(refreshNow), keyEquivalent: "r")
        refreshItem.target = self
        statusMenu.addItem(refreshItem)

        let monitorItem = NSMenuItem(title: "Show Floating Monitor", action: #selector(showFloatingMonitor), keyEquivalent: "m")
        monitorItem.target = self
        statusMenu.addItem(monitorItem)

        let moreTransparentItem = NSMenuItem(title: "Monitor More Transparent", action: #selector(makeMonitorMoreTransparent), keyEquivalent: "[")
        moreTransparentItem.target = self
        statusMenu.addItem(moreTransparentItem)

        let lessTransparentItem = NSMenuItem(title: "Monitor Less Transparent", action: #selector(makeMonitorLessTransparent), keyEquivalent: "]")
        lessTransparentItem.target = self
        statusMenu.addItem(lessTransparentItem)

        let openFolderItem = NSMenuItem(title: "Open Status Folder", action: #selector(openStatusFolder), keyEquivalent: "")
        openFolderItem.target = self
        statusMenu.addItem(openFolderItem)

        statusMenu.addItem(NSMenuItem.separator())

        let quitItem = NSMenuItem(title: "Quit Hermes Agent Lights", action: #selector(quit), keyEquivalent: "q")
        quitItem.target = self
        statusMenu.addItem(quitItem)
    }

    private func exitEarly(_ reason: String) {
        NSLog("Hermes Agent Lights: %@", reason)
        NSStatusBar.system.removeStatusItem(statusItem)
        NSApp.terminate(nil)
    }

    func applicationWillTerminate(_ notification: Notification) {
        AppHeartbeat.removeIfOwned(directory: agentLightsDirectory, pid: Int(getpid()))
    }

    func menuWillOpen(_ menu: NSMenu) {
        render()
    }

    @objc private func tick(_ timer: Timer) {
        errorFlashOn.toggle()
        tickCount += 1
        if tickCount % 8 == 0 {
            refreshHeartbeat()
        }
        render()
    }

    /// Keep `app.json` naming this process: Hermes's launcher only treats a
    /// current app as running while it does. Retried every few seconds, so a
    /// transient write failure heals; a persistent one is logged once.
    private func refreshHeartbeat() {
        let pid = Int(getpid())
        let ok = AppHeartbeat.ensure(
            directory: agentLightsDirectory,
            pid: pid,
            processStartTime: ProcessInspector.startTime(pid: pid)
        )
        if ok {
            heartbeatFailureLogged = false
        } else if !heartbeatFailureLogged {
            heartbeatFailureLogged = true
            NSLog("Hermes Agent Lights: cannot write heartbeat in %@", agentLightsDirectory.path)
        }
    }

    private func render() {
        let slotStatuses = (1...6).compactMap { loadRenderableStatus(directory: slotsDirectory, slot: $0) }
        let agentStatuses = (1...8).compactMap { loadRenderableStatus(directory: agentsDirectory, slot: $0) }
        let legacyAgentStatuses = slotStatuses.filter(\.isKanbanWorker)
        let hermesStatuses = slotStatuses.filter { !$0.isKanbanWorker }
        let allAgentStatuses = agentStatuses + legacyAgentStatuses
        // Order dots by Terminal's real tab bar when it can be read and its
        // titles carry each tab's TTY (TabBarOrder); otherwise by when each
        // session's tab was opened (DotOrder), which never jumps on focus
        // changes. Slot letters (A-F) still label the menu rows.
        refreshTerminalSnapshotIfStale()
        tabBarCache.expire(at: .now)
        let tabBarResult = TabBarOrder.ordered(
            fallbackOrdered: DotOrder.ordered(hermesStatuses),
            tabTitles: tabBarCache.titles,
            ttyOf: { $0.pid.flatMap { ProcessInspector.tty(pid: $0) } }
        )
        let orderedHermesStatuses = tabBarResult.statuses
        updateDotOrderItem(matched: tabBarResult.matchedCount, total: hermesStatuses.count)
        // Agent Lights must not write Terminal tab titles. Terminal Inspector titles
        // are user-owned and should persist across Hermes prompts; Agent Lights only
        // reads manual titles for menu labels and click-to-focus context.

        let agentGroup = AgentRingGroup(statuses: allAgentStatuses)
        statusItem.button?.image = DotRenderer.image(for: orderedHermesStatuses, agentGroup: agentGroup, flashOn: errorFlashOn)
        removeAgentStatusItemIfPresent()
        let terminalDisplayNames = terminalDisplayNamesBySlot(for: orderedHermesStatuses)
        let menuModel = SlotStatusMenuModel(
            hermesStatuses: orderedHermesStatuses,
            agentStatuses: allAgentStatuses,
            hermesDisplayNames: terminalDisplayNames
        )
        statusItem.button?.toolTip = menuModel.tooltip
        statusSummaryItem.title = menuModel.summaryTitle
        let slotContexts = loadSlotContexts(for: orderedHermesStatuses)
        replaceStatusRows(with: orderedHermesStatuses + allAgentStatuses, rowTitles: menuModel.rowTitles, contexts: slotContexts)
        monitorController?.update(hermesStatuses: orderedHermesStatuses, agentGroup: agentGroup)
    }

    private func terminalProcessIdentifier() -> pid_t? {
        NSRunningApplication.runningApplications(withBundleIdentifier: "com.apple.Terminal").first?.processIdentifier
    }

    private func terminalDisplayNamesBySlot(for statuses: [SlotStatus]) -> [Int: String] {
        refreshTerminalSnapshotIfStale()
        let titlesByTTY = terminalTitlesByTTY
        guard !titlesByTTY.isEmpty else { return [:] }
        var result: [Int: String] = [:]
        for status in statuses {
            guard let pid = status.pid,
                  let tty = ttyForProcess(pid: pid),
                  let normalized = normalizedTTY(tty),
                  let title = titlesByTTY[normalized] else { continue }
            result[status.slot] = title
        }
        return result
    }

    /// Kick off a background Terminal snapshot when the cached one is stale.
    /// Results land on the next render tick; the main thread never waits.
    private func refreshTerminalSnapshotIfStale() {
        if terminalSnapshotInFlight { return }
        if let loadedAt = terminalSnapshotLoadedAt, ContinuousClock.now - loadedAt < .seconds(terminalSnapshotMaxAge) { return }
        guard let processIdentifier = terminalProcessIdentifier() else {
            terminalTitlesByTTY = [:]
            tabBarCache.reset()
            tabBarTrusted = AXIsProcessTrusted()
            terminalSnapshotLoadedAt = .now
            return
        }
        terminalSnapshotInFlight = true
        Task.detached(priority: .utility) {
            // PID-targeted Apple events fail harmlessly if Terminal exits; unlike a
            // bundle-targeted `tell application "Terminal"`, they cannot relaunch it.
            let output = AgentLightsTerminalTabSnapshot(processIdentifier) as String? ?? ""
            let tabBar = TerminalTabBar.read(terminalPid: processIdentifier)
            await MainActor.run {
                self.terminalTitlesByTTY = self.parseTerminalTitles(output)
                switch tabBar {
                case .untrusted:
                    self.tabBarTrusted = false
                    self.tabBarCache.reset()
                case .failed(let reason):
                    self.tabBarTrusted = true
                    self.tabBarCache.recordFailure(reason, at: .now)
                case .snapshot(let titles):
                    self.tabBarTrusted = true
                    self.tabBarCache.recordSuccess(titles, at: .now)
                }
                self.terminalSnapshotLoadedAt = .now
                self.terminalSnapshotInFlight = false
            }
        }
    }

    private func parseTerminalTitles(_ output: String) -> [String: String] {
        var titlesByTTY: [String: String] = [:]
        for line in output.split(whereSeparator: \.isNewline).map(String.init) {
            let parts = line.split(separator: "\t", maxSplits: 3, omittingEmptySubsequences: false)
            guard parts.count == 4,
                  let tty = normalizedTTY(String(parts[0])) else { continue }
            let displaysCustom = String(parts[1]).trimmingCharacters(in: .whitespacesAndNewlines).lowercased() == "true"
            let tabTitle = trimmed(String(parts[2]))
            let windowTitle = trimmed(String(parts[3]))
            if let tabTitle, shouldUseTerminalDisplayName(tabTitle, displaysCustom: displaysCustom) {
                titlesByTTY[tty] = tabTitle
            } else if let windowTitle, shouldUseTerminalDisplayName(windowTitle, displaysCustom: false) {
                titlesByTTY[tty] = windowTitle
            }
        }
        return titlesByTTY
    }

    private func shouldUseTerminalDisplayName(_ title: String, displaysCustom: Bool) -> Bool {
        if displaysCustom {
            return true
        }
        let trimmedTitle = title.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmedTitle.isEmpty else { return false }
        let lowerTitle = trimmedTitle.lowercased()
        if lowerTitle == "terminal" || lowerTitle == "login" || lowerTitle == "bash" || lowerTitle == "zsh" {
            return false
        }
        if trimmedTitle.range(of: #"^((✓|⏳|⚠|V)\s+.*(GPT|Claude|Sonnet|Opus|Haiku).*|Hermes\s+[A-Z])$"#, options: .regularExpression) != nil {
            return false
        }
        return true
    }

    private func normalizedTTY(_ tty: String) -> String? {
        let trimmedTTY = tty.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmedTTY.isEmpty, trimmedTTY != "??" else { return nil }
        if trimmedTTY.hasPrefix("/dev/") {
            return String(trimmedTTY.dropFirst(5))
        }
        return trimmedTTY
    }

    private func removeAgentStatusItemIfPresent() {
        if let agentStatusItem {
            NSStatusBar.system.removeStatusItem(agentStatusItem)
            self.agentStatusItem = nil
        }
    }

    private func replaceStatusRows(with statuses: [SlotStatus], rowTitles: [String], contexts: [Int: SlotContext]) {
        for item in statusRowItems {
            statusMenu.removeItem(item)
        }
        statusRowItems = zip(statuses, rowTitles).map { status, title in
            let item = NSMenuItem(title: title, action: #selector(openStatusRow(_:)), keyEquivalent: "")
            item.target = self
            item.isEnabled = status.pid != nil || (status.isKanbanWorker && status.kanbanTaskId != nil)
            item.representedObject = StatusRowAction(status: status)
            if !status.isKanbanWorker, let context = contexts[status.slot] {
                item.submenu = contextMenu(for: status, context: context)
            }
            return item
        }
        for (offset, item) in statusRowItems.enumerated() {
            statusMenu.insertItem(item, at: 1 + offset)
        }
    }

    private func contextMenu(for status: SlotStatus, context: SlotContext) -> NSMenu {
        let menu = NSMenu()
        for row in context.detailRows {
            let item = NSMenuItem(title: row, action: nil, keyEquivalent: "")
            item.isEnabled = false
            menu.addItem(item)
        }
        menu.addItem(NSMenuItem.separator())
        let focusItem = NSMenuItem(title: "Focus Tab", action: #selector(openStatusRow(_:)), keyEquivalent: "")
        focusItem.target = self
        focusItem.isEnabled = status.pid != nil
        focusItem.representedObject = StatusRowAction(status: status)
        menu.addItem(focusItem)
        return menu
    }

    private func loadSlotContexts(for statuses: [SlotStatus]) -> [Int: SlotContext] {
        var contexts: [Int: SlotContext] = [:]
        for status in statuses {
            let url = contextDirectory.appendingPathComponent("\(status.slot).json")
            if let data = try? Data(contentsOf: url),
               let context = try? SlotContext.decode(from: data),
               context.slot == status.slot {
                contexts[status.slot] = context
            } else {
                contexts[status.slot] = SlotContext.fallback(for: status)
            }
        }
        return contexts
    }


    @objc private func openStatusRow(_ sender: NSMenuItem) {
        guard let action = sender.representedObject as? StatusRowAction else {
            NSSound.beep()
            return
        }
        if action.status.isKanbanWorker,
           let script = KanbanCardOpenScript.script(taskId: action.status.kanbanTaskId, board: action.status.kanbanBoard) {
            executeAppleScript(script)
            return
        }
        focusTerminal(pid: action.status.pid)
    }

    private func focusTerminal(pid: Int?) {
        guard let pid,
              let tty = ttyForProcess(pid: pid),
              let script = TerminalFocusScript.script(forTTY: tty) else {
            NSSound.beep()
            return
        }
        executeAppleScript(script)
    }

    private func executeAppleScript(_ script: String) {
        if !Self.executeAppleScriptReturnsBool(script, timeout: subprocessTimeout) {
            NSSound.beep()
        }
    }

    nonisolated private static func executeAppleScriptReturnsBool(_ script: String, timeout: TimeInterval) -> Bool {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/osascript")
        process.arguments = ["-e", script]
        let output = Pipe()
        process.standardOutput = output
        process.standardError = Pipe()
        do {
            try process.run()
        } catch {
            return false
        }
        guard waitForProcess(process, timeout: timeout), process.terminationStatus == 0 else { return false }
        let data = output.fileHandleForReading.readDataToEndOfFile()
        let text = String(data: data, encoding: .utf8)?.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        return text == "true"
    }

    private func ttyForProcess(pid: Int) -> String? {
        ProcessInspector.tty(pid: pid)
    }

    nonisolated private static func waitForProcess(_ process: Process, timeout: TimeInterval) -> Bool {
        let deadline = Date().addingTimeInterval(timeout)
        while process.isRunning && Date() < deadline {
            Thread.sleep(forTimeInterval: 0.02)
        }
        if process.isRunning {
            process.terminate()
            process.waitUntilExit()
            return false
        }
        return true
    }

    private func updateDotOrderItem(matched: Int, total: Int) {
        let titlesHaveTTYs = tabBarCache.titles.contains { !TabBarOrder.ttys(inTitle: $0).isEmpty }
        if !tabBarTrusted {
            setDotOrderItem("tab opened \u{2014} Allow Accessibility to follow tabs\u{2026}", action: #selector(requestTabOrderPermission))
        } else if tabBarCache.isUnavailable {
            setDotOrderItem("tab opened \u{2014} can\u{2019}t read Terminal\u{2019}s tab bar (\(tabBarCache.lastFailure ?? "unknown"))", action: nil)
        } else if matched < total && !titlesHaveTTYs {
            setDotOrderItem("tab opened \u{2014} add \u{201C}TTY\u{201D} to Terminal tab titles\u{2026}", action: #selector(showTabTitleHelp))
        } else if matched < total {
            let count = total - matched
            setDotOrderItem("Terminal tab bar; \(count) session\(count == 1 ? "" : "s") not matched to a Terminal tab", action: nil)
        } else {
            setDotOrderItem("Terminal tab bar", action: nil)
        }
    }

    private func setDotOrderItem(_ detail: String, action: Selector?) {
        dotOrderItem.title = "Dot order: \(detail)"
        dotOrderItem.action = action
        dotOrderItem.isEnabled = action != nil
    }

    /// Ask once per build: macOS ties the Accessibility grant to the app's
    /// signature, and local builds are re-signed every time.
    private func promptForTabOrderPermissionOncePerBuild() {
        let executable = Bundle.main.executableURL
        let modified = (try? executable?.resourceValues(forKeys: [.contentModificationDateKey]))?.contentModificationDate
        let build = AccessibilityPrompt.buildKey(executablePath: executable?.path, modifiedAt: modified)
        guard AccessibilityPrompt.shouldPrompt(
            trusted: AXIsProcessTrusted(),
            buildKey: build,
            lastPromptedBuildKey: UserDefaults.standard.string(forKey: tabOrderPromptDefaultsKey)
        ) else { return }
        UserDefaults.standard.set(build, forKey: tabOrderPromptDefaultsKey)
        TerminalTabBar.requestPermission()
    }

    @objc private func requestTabOrderPermission(_ sender: NSMenuItem) {
        if !TerminalTabBar.requestPermission() {
            TerminalTabBar.openAccessibilitySettings()
        }
        terminalSnapshotLoadedAt = nil
    }

    @objc private func showTabTitleHelp(_ sender: NSMenuItem) {
        let alert = NSAlert()
        alert.messageText = "Show each tab's TTY in Terminal tab titles"
        alert.informativeText = "Agent Lights matches Terminal tabs to Hermes sessions by the terminal device name in the tab title.\n\nIn Terminal: Settings \u{2192} Profiles \u{2192} (each profile you use) \u{2192} Tab \u{2192} turn on \u{201C}TTY\u{201D}.\n\nUntil then, dots follow the order tabs were opened."
        alert.addButton(withTitle: "OK")
        NSApp.activate(ignoringOtherApps: true)
        alert.runModal()
    }

    @objc private func refreshNow(_ sender: NSMenuItem) {
        render()
    }

    @objc private func showFloatingMonitor(_ sender: NSMenuItem) {
        if monitorController == nil {
            monitorController = FloatingMonitorController(opacity: monitorOpacity)
        }
        monitorController?.setOpacity(monitorOpacity)
        monitorController?.show()
        render()
    }

    @objc private func makeMonitorMoreTransparent(_ sender: NSMenuItem) {
        setMonitorOpacity(FloatingMonitorOpacity.moreTransparent(from: monitorOpacity))
    }

    @objc private func makeMonitorLessTransparent(_ sender: NSMenuItem) {
        setMonitorOpacity(FloatingMonitorOpacity.lessTransparent(from: monitorOpacity))
    }

    private var monitorOpacity: Double {
        let saved = UserDefaults.standard.object(forKey: monitorOpacityDefaultsKey) as? Double
        return FloatingMonitorOpacity.clamped(saved ?? FloatingMonitorWindowSpec.default.opacity)
    }

    private func setMonitorOpacity(_ opacity: Double) {
        let resolved = FloatingMonitorOpacity.clamped(opacity)
        UserDefaults.standard.set(resolved, forKey: monitorOpacityDefaultsKey)
        if monitorController == nil {
            monitorController = FloatingMonitorController(opacity: resolved)
        }
        monitorController?.setOpacity(resolved)
        monitorController?.show()
        render()
    }

    @objc private func openStatusFolder(_ sender: NSMenuItem) {
        try? FileManager.default.createDirectory(at: slotsDirectory, withIntermediateDirectories: true)
        try? FileManager.default.createDirectory(at: agentsDirectory, withIntermediateDirectories: true)
        try? FileManager.default.createDirectory(at: contextDirectory, withIntermediateDirectories: true)
        NSWorkspace.shared.activateFileViewerSelecting([slotsDirectory, agentsDirectory, contextDirectory])
    }

    @objc private func quit(_ sender: NSMenuItem) {
        NSApp.terminate(nil)
    }

    private func loadRenderableStatus(directory: URL, slot: Int) -> SlotStatus? {
        let url = directory.appendingPathComponent("\(slot).json")
        guard let data = try? Data(contentsOf: url),
              let status = try? SlotStatus.decode(from: data),
              status.slot == slot else {
            return nil
        }

        let modifiedAt = try? url.resourceValues(forKeys: [.contentModificationDateKey]).contentModificationDate
        if status.shouldRender(fileModifiedAt: modifiedAt) {
            return status
        }
        if status.shouldPrune(fileModifiedAt: modifiedAt) {
            SlotFilePruner.prune(directory: directory, slot: slot)
        }
        return nil
    }
}

@MainActor
private final class FloatingMonitorController: NSObject, NSWindowDelegate {
    private let spec = FloatingMonitorWindowSpec.default
    private let monitorView = FloatingMonitorView(frame: .zero)
    private var window: NSPanel?
    private var opacity: Double

    init(opacity: Double = FloatingMonitorWindowSpec.default.opacity) {
        self.opacity = FloatingMonitorOpacity.clamped(opacity)
        super.init()
    }

    func show() {
        let panel = window ?? makeWindow()
        if window == nil {
            window = panel
        }
        panel.orderFrontRegardless()
        NSApp.activate(ignoringOtherApps: false)
    }

    func update(hermesStatuses: [SlotStatus], agentGroup: AgentRingGroup) {
        monitorView.hermesStatuses = hermesStatuses
        monitorView.agentGroup = agentGroup
        monitorView.needsDisplay = true
        monitorView.displayIfNeeded()
    }

    func setOpacity(_ opacity: Double) {
        self.opacity = FloatingMonitorOpacity.clamped(opacity)
        window?.alphaValue = CGFloat(self.opacity)
    }

    func windowWillClose(_ notification: Notification) {
        window = nil
    }

    private func makeWindow() -> NSPanel {
        let frame = defaultFrame()
        let panel = NSPanel(
            contentRect: frame,
            styleMask: [.titled, .closable, .resizable, .fullSizeContentView, .nonactivatingPanel],
            backing: .buffered,
            defer: false
        )
        panel.delegate = self
        panel.title = "Hermes Monitor"
        panel.titleVisibility = .hidden
        panel.titlebarAppearsTransparent = true
        panel.isFloatingPanel = true
        panel.level = .floating
        panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
        panel.isMovableByWindowBackground = true
        panel.isOpaque = false
        panel.backgroundColor = .clear
        panel.alphaValue = CGFloat(opacity)
        panel.hasShadow = true
        panel.minSize = NSSize(width: 250, height: 115)
        panel.standardWindowButton(.closeButton)?.isHidden = true
        panel.standardWindowButton(.miniaturizeButton)?.isHidden = true
        panel.standardWindowButton(.zoomButton)?.isHidden = true

        let container = NSView(frame: NSRect(origin: .zero, size: frame.size))
        container.wantsLayer = true
        container.layer?.backgroundColor = NSColor(
            calibratedRed: CGFloat(spec.backgroundRed),
            green: CGFloat(spec.backgroundGreen),
            blue: CGFloat(spec.backgroundBlue),
            alpha: CGFloat(spec.backgroundAlpha)
        ).cgColor
        container.layer?.cornerRadius = 13
        container.layer?.masksToBounds = true
        container.autoresizingMask = [.width, .height]

        monitorView.frame = container.bounds
        monitorView.autoresizingMask = [.width, .height]
        container.addSubview(monitorView)

        let closeButton = NSButton(frame: NSRect(x: frame.width - 34, y: frame.height - 32, width: 24, height: 24))
        closeButton.title = "×"
        closeButton.bezelStyle = .regularSquare
        closeButton.isBordered = false
        closeButton.font = NSFont.systemFont(ofSize: 20, weight: .semibold)
        closeButton.contentTintColor = NSColor.black.withAlphaComponent(0.72)
        closeButton.target = self
        closeButton.action = #selector(closeWindow)
        closeButton.autoresizingMask = [.minXMargin, .minYMargin]
        container.addSubview(closeButton)

        panel.contentView = container
        return panel
    }

    @objc private func closeWindow() {
        window?.close()
    }

    private func defaultFrame() -> NSRect {
        let width = CGFloat(spec.defaultWidth)
        let height = CGFloat(spec.defaultHeight)
        let screenFrame = NSScreen.main?.visibleFrame ?? NSRect(x: 0, y: 0, width: 1440, height: 900)
        let margin: CGFloat = 24
        return NSRect(
            x: screenFrame.maxX - width - margin,
            y: screenFrame.maxY - height - margin,
            width: width,
            height: height
        )
    }
}

private final class FloatingMonitorView: NSView {
    var hermesStatuses: [SlotStatus] = []
    var agentGroup: AgentRingGroup = AgentRingGroup(statuses: [])

    override var isFlipped: Bool { false }

    override func draw(_ dirtyRect: NSRect) {
        super.draw(dirtyRect)
        let layout = FloatingMonitorLayout(
            size: FloatingMonitorSize(width: Double(bounds.width), height: Double(bounds.height)),
            hermesStatuses: hermesStatuses,
            agentGroup: agentGroup
        )
        if layout.items.isEmpty {
            drawEmptyState()
            return
        }
        for item in layout.items {
            drawCircle(item)
        }
    }

    private func drawCircle(_ item: FloatingMonitorItem) {
        let rect = NSRect(x: item.x, y: item.y, width: item.diameter, height: item.diameter)
        let path = NSBezierPath(ovalIn: rect)
        let color = item.isPlaceholder ? NSColor.black.withAlphaComponent(0.22) : monitorColor(for: item.state)
        color.setFill()
        path.fill()
    }

    private func drawEmptyState() {
        let text = "No active Hermes agents"
        let attributes: [NSAttributedString.Key: Any] = [
            .font: NSFont.systemFont(ofSize: 22, weight: .semibold),
            .foregroundColor: NSColor.black.withAlphaComponent(0.55),
        ]
        let size = text.size(withAttributes: attributes)
        text.draw(at: NSPoint(x: (bounds.width - size.width) / 2, y: (bounds.height - size.height) / 2), withAttributes: attributes)
    }

    private func monitorColor(for state: SlotLifecycleState) -> NSColor {
        switch state {
        case .working:
            return .systemGreen
        case .humanIntervention:
            return .systemYellow
        case .finalAnswer, .error:
            return .systemRed
        case .idle:
            return .systemGray
        case .missing:
            return NSColor.black.withAlphaComponent(0.22)
        }
    }
}

private enum DotRenderer {
    static func image(for statuses: [SlotStatus], agentGroup: AgentRingGroup = AgentRingGroup(statuses: []), flashOn: Bool = true) -> NSImage {
        let visibleStatuses = Array(statuses.prefix(6))
        let layout = StatusIndicatorLayout(hermesStatuses: visibleStatuses, agentGroup: agentGroup, flashOn: flashOn)
        let normalSlotIndicators = layout.normalSlotIndicators
        let geometry = StatusIndicatorGeometry()
        let dotCount = normalSlotIndicators.count
        let dotWidth = dotCount > 0 ? CGFloat(8) + CGFloat(dotCount) * CGFloat(geometry.hermesDotSpacing) : 0
        let gapWidth: CGFloat = layout.shouldRenderAgentRings && dotCount > 0 ? 6 : 0
        let ringWidth = layout.shouldRenderAgentRings ? CGFloat(geometry.agentIndicatorWidth) : 0
        let width = max(CGFloat(18), dotWidth + gapWidth + ringWidth)
        let size = NSSize(width: width, height: 18)
        let image = NSImage(size: size)
        image.lockFocus()
        NSColor.clear.setFill()
        NSRect(origin: .zero, size: size).fill()

        for (index, indicator) in normalSlotIndicators.enumerated() {
            let x = CGFloat(4) + CGFloat(index) * CGFloat(geometry.hermesDotSpacing)
            let dotDiameter = CGFloat(geometry.hermesDotDiameter)
            let rect = NSRect(x: x, y: (18 - dotDiameter) / 2, width: dotDiameter, height: dotDiameter)
            let path = NSBezierPath(ovalIn: rect)
            switch indicator.style {
            case .filled:
                color(for: indicator.state).setFill()
                path.fill()
            case .outline:
                let outlineColor = indicator.state == .error ? color(for: .error) : NSColor.secondaryLabelColor.withAlphaComponent(0.72)
                outlineColor.setStroke()
                path.lineWidth = 1.4
                path.stroke()
            }
        }

        if layout.shouldRenderAgentRings {
            drawAgentRings(for: agentGroup, xOffset: dotWidth + gapWidth)
        }

        image.unlockFocus()
        image.isTemplate = false
        return image
    }

    private static func drawAgentRings(for group: AgentRingGroup, xOffset: CGFloat) {
        let layout = StatusIndicatorLayout(hermesStatuses: [], agentGroup: group)
        let geometry = StatusIndicatorGeometry()
        let diameter = CGFloat(geometry.agentCircleDiameter)
        let columnSpacing = CGFloat(geometry.agentCircleColumnSpacing)
        let rowSpacing = CGFloat(geometry.agentCircleRowSpacing)
        let bottomY = (18 - (diameter + rowSpacing)) / 2
        let topY = bottomY + rowSpacing
        for (index, ring) in group.rings.enumerated() {
            let position = layout.agentRingGridPositions[index]
            let x = xOffset + CGFloat(3) + CGFloat(position.column) * columnSpacing
            let y = position.row == 0 ? topY : bottomY
            let rect = NSRect(x: x, y: y, width: diameter, height: diameter)
            let path = NSBezierPath(ovalIn: rect)
            let fillColor = ring.isPlaceholder ? NSColor.secondaryLabelColor : color(for: ring.state)
            fillColor.setFill()
            path.fill()
        }
    }

    static func agentImage(for group: AgentRingGroup) -> NSImage {
        let width = CGFloat(StatusIndicatorGeometry().agentIndicatorWidth)
        let size = NSSize(width: width, height: 18)
        let image = NSImage(size: size)
        image.lockFocus()
        NSColor.clear.setFill()
        NSRect(origin: .zero, size: size).fill()

        drawAgentRings(for: group, xOffset: 0)

        image.unlockFocus()
        image.isTemplate = false
        return image
    }

    private static func color(for state: SlotLifecycleState) -> NSColor {
        switch state {
        case .working:
            return NSColor.systemGreen
        case .humanIntervention:
            return NSColor.systemYellow
        case .finalAnswer:
            return NSColor.systemRed
        case .error:
            return NSColor.systemRed
        case .idle:
            return NSColor.systemGray
        case .missing:
            return NSColor.tertiaryLabelColor
        }
    }
}

let app = NSApplication.shared
private let delegate = AgentLightsApp()
app.delegate = delegate
app.run()
