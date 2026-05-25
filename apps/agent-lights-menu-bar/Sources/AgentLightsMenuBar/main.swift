import AppKit
import AgentLightsCore
import Foundation

@MainActor
private final class AgentLightsApp: NSObject, NSApplicationDelegate, NSMenuDelegate {
    private let statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
    private let statusMenu = NSMenu()
    private let statusSummaryItem = NSMenuItem(title: "No active Hermes slots", action: nil, keyEquivalent: "")
    private var timer: Timer?
    private let slotsDirectory: URL

    override init() {
        let home = ProcessInfo.processInfo.environment["HERMES_HOME"]
            ?? NSHomeDirectory() + "/.hermes"
        self.slotsDirectory = URL(fileURLWithPath: home)
            .appendingPathComponent("agent-lights")
            .appendingPathComponent("slots")
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
        let statuses = (1...4).compactMap(loadRenderableStatus(slot:))
        statusItem.button?.image = DotRenderer.image(for: statuses)
        let summary = statusSummary(for: statuses)
        statusItem.button?.toolTip = summary
        statusSummaryItem.title = summary
    }

    private func statusSummary(for statuses: [SlotStatus]) -> String {
        guard !statuses.isEmpty else {
            return "Hermes Agent Lights: no active slots"
        }
        let slotSummaries = statuses.map { "\($0.slot): \($0.state.rawValue)" }.joined(separator: ", ")
        return "Hermes Agent Lights: " + slotSummaries
    }

    @objc private func refreshNow(_ sender: NSMenuItem) {
        render()
    }

    @objc private func openStatusFolder(_ sender: NSMenuItem) {
        try? FileManager.default.createDirectory(at: slotsDirectory, withIntermediateDirectories: true)
        NSWorkspace.shared.activateFileViewerSelecting([slotsDirectory])
    }

    @objc private func quit(_ sender: NSMenuItem) {
        NSApp.terminate(nil)
    }

    private func loadRenderableStatus(slot: Int) -> SlotStatus? {
        let url = slotsDirectory.appendingPathComponent("\(slot).json")
        guard let data = try? Data(contentsOf: url),
              let status = try? SlotStatus.decode(from: data),
              status.slot == slot else {
            return nil
        }

        let modifiedAt = try? url.resourceValues(forKeys: [.contentModificationDateKey]).contentModificationDate
        return status.shouldRender(fileModifiedAt: modifiedAt) ? status : nil
    }
}

private enum DotRenderer {
    static func image(for statuses: [SlotStatus]) -> NSImage {
        let visibleStatuses = Array(statuses.prefix(4))
        let dotCount = max(visibleStatuses.count, 1)
        let width = CGFloat(8 + dotCount * 14)
        let size = NSSize(width: width, height: 18)
        let image = NSImage(size: size)
        image.lockFocus()
        NSColor.clear.setFill()
        NSRect(origin: .zero, size: size).fill()

        for (index, status) in visibleStatuses.enumerated() {
            let x = CGFloat(4 + index * 14)
            let rect = NSRect(x: x, y: 5, width: 8, height: 8)
            let path = NSBezierPath(ovalIn: rect)
            color(for: status.state).setFill()
            path.fill()
        }

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
