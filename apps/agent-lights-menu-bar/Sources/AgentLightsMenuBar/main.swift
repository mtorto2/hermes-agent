import AppKit
import AgentLightsCore
import Foundation

private final class StatusRowAction: NSObject {
    let status: SlotStatus

    init(status: SlotStatus) {
        self.status = status
    }
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
    private let slotsDirectory: URL
    private let agentsDirectory: URL

    override init() {
        let home = ProcessInfo.processInfo.environment["HERMES_HOME"]
            ?? NSHomeDirectory() + "/.hermes"
        let agentLightsDirectory = URL(fileURLWithPath: home)
            .appendingPathComponent("agent-lights")
        self.slotsDirectory = agentLightsDirectory
            .appendingPathComponent("slots")
        self.agentsDirectory = agentLightsDirectory
            .appendingPathComponent("agents")
        super.init()
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)
        configureMenu()
        statusItem.button?.toolTip = "Hermes Agent Lights"
        statusItem.menu = statusMenu
        render()
        timer = Timer.scheduledTimer(timeInterval: 0.75, target: self, selector: #selector(tick), userInfo: nil, repeats: true)
    }

    private func configureMenu() {
        statusMenu.delegate = self
        statusSummaryItem.isEnabled = false
        statusMenu.addItem(statusSummaryItem)
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

    func menuWillOpen(_ menu: NSMenu) {
        render()
    }

    @objc private func tick(_ timer: Timer) {
        render()
    }

    private func render() {
        let slotStatuses = (1...4).compactMap { loadRenderableStatus(directory: slotsDirectory, slot: $0) }
        let agentStatuses = (1...8).compactMap { loadRenderableStatus(directory: agentsDirectory, slot: $0) }
        let legacyAgentStatuses = slotStatuses.filter(\.isKanbanWorker)
        let hermesStatuses = slotStatuses.filter { !$0.isKanbanWorker }
        let allAgentStatuses = agentStatuses + legacyAgentStatuses

        let agentGroup = AgentRingGroup(statuses: allAgentStatuses)
        statusItem.button?.image = DotRenderer.image(for: hermesStatuses, agentGroup: agentGroup)
        removeAgentStatusItemIfPresent()
        let menuModel = SlotStatusMenuModel(hermesStatuses: hermesStatuses, agentStatuses: allAgentStatuses)
        statusItem.button?.toolTip = menuModel.tooltip
        statusSummaryItem.title = menuModel.summaryTitle
        replaceStatusRows(with: hermesStatuses + allAgentStatuses, rowTitles: menuModel.rowTitles)
        monitorController?.update(hermesStatuses: hermesStatuses, agentGroup: agentGroup)
    }

    private func removeAgentStatusItemIfPresent() {
        if let agentStatusItem {
            NSStatusBar.system.removeStatusItem(agentStatusItem)
            self.agentStatusItem = nil
        }
    }

    private func replaceStatusRows(with statuses: [SlotStatus], rowTitles: [String]) {
        for item in statusRowItems {
            statusMenu.removeItem(item)
        }
        statusRowItems = zip(statuses, rowTitles).map { status, title in
            let item = NSMenuItem(title: title, action: #selector(openStatusRow(_:)), keyEquivalent: "")
            item.target = self
            item.isEnabled = status.pid != nil || (status.isKanbanWorker && status.kanbanTaskId != nil)
            item.representedObject = StatusRowAction(status: status)
            return item
        }
        for (offset, item) in statusRowItems.enumerated() {
            statusMenu.insertItem(item, at: 1 + offset)
        }
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
        var error: NSDictionary?
        let result = NSAppleScript(source: script)?.executeAndReturnError(&error)
        if error != nil || result?.booleanValue != true {
            NSSound.beep()
        }
    }

    private func ttyForProcess(pid: Int) -> String? {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/bin/ps")
        process.arguments = ["-o", "tty=", "-p", String(pid)]
        let pipe = Pipe()
        process.standardOutput = pipe
        process.standardError = Pipe()
        do {
            try process.run()
            process.waitUntilExit()
        } catch {
            return nil
        }
        guard process.terminationStatus == 0 else { return nil }
        let data = pipe.fileHandleForReading.readDataToEndOfFile()
        let tty = String(data: data, encoding: .utf8)?.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let tty, !tty.isEmpty, tty != "??" else { return nil }
        return tty
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
        NSWorkspace.shared.activateFileViewerSelecting([slotsDirectory, agentsDirectory])
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
            pruneSlotFiles(directory: directory, slot: slot)
        }
        return nil
    }

    private func pruneSlotFiles(directory: URL, slot: Int) {
        let jsonURL = directory.appendingPathComponent("\(slot).json")
        let lockURL = directory.appendingPathComponent("\(slot).lock")
        try? FileManager.default.removeItem(at: jsonURL)
        try? FileManager.default.removeItem(at: lockURL)
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
    static func image(for statuses: [SlotStatus], agentGroup: AgentRingGroup = AgentRingGroup(statuses: [])) -> NSImage {
        let visibleStatuses = Array(statuses.prefix(4))
        let layout = StatusIndicatorLayout(hermesStatuses: visibleStatuses, agentGroup: agentGroup)
        let geometry = StatusIndicatorGeometry()
        let dotCount = max(layout.filledDotCount, layout.shouldRenderAgentRings ? 0 : 1)
        let dotWidth = dotCount > 0 ? CGFloat(8) + CGFloat(dotCount) * CGFloat(geometry.hermesDotSpacing) : 0
        let gapWidth: CGFloat = layout.shouldRenderAgentRings && dotCount > 0 ? 6 : 0
        let ringWidth = layout.shouldRenderAgentRings ? CGFloat(geometry.agentIndicatorWidth) : 0
        let width = max(CGFloat(18), dotWidth + gapWidth + ringWidth)
        let size = NSSize(width: width, height: 18)
        let image = NSImage(size: size)
        image.lockFocus()
        NSColor.clear.setFill()
        NSRect(origin: .zero, size: size).fill()

        for (index, status) in visibleStatuses.enumerated() {
            let x = CGFloat(4) + CGFloat(index) * CGFloat(geometry.hermesDotSpacing)
            let dotDiameter = CGFloat(geometry.hermesDotDiameter)
            let rect = NSRect(x: x, y: (18 - dotDiameter) / 2, width: dotDiameter, height: dotDiameter)
            let path = NSBezierPath(ovalIn: rect)
            color(for: status.state).setFill()
            path.fill()
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
