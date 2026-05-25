import AppKit
import AgentLightsCore
import Foundation

@MainActor
private final class AgentLightsApp: NSObject, NSApplicationDelegate {
    private let statusItem = NSStatusBar.system.statusItem(withLength: 78)
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
        statusItem.button?.toolTip = "Hermes Agent Lights"
        render()
        timer = Timer.scheduledTimer(timeInterval: 0.75, target: self, selector: #selector(tick), userInfo: nil, repeats: true)
    }

    @objc private func tick(_ timer: Timer) {
        render()
    }

    private func render() {
        let statuses = (1...4).map(loadStatus(slot:))
        statusItem.button?.image = DotRenderer.image(for: statuses)
    }

    private func loadStatus(slot: Int) -> SlotStatus {
        let url = slotsDirectory.appendingPathComponent("\(slot).json")
        guard let data = try? Data(contentsOf: url),
              let status = try? SlotStatus.decode(from: data),
              status.slot == slot else {
            return .missing(slot: slot)
        }
        return status
    }
}

private enum DotRenderer {
    static func image(for statuses: [SlotStatus]) -> NSImage {
        let size = NSSize(width: 72, height: 18)
        let image = NSImage(size: size)
        image.lockFocus()
        NSColor.clear.setFill()
        NSRect(origin: .zero, size: size).fill()

        for (index, status) in statuses.prefix(4).enumerated() {
            let x = CGFloat(7 + index * 17)
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
