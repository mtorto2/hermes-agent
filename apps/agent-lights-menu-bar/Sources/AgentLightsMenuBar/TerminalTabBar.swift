import AgentLightsCore
import AppKit
import ApplicationServices

/// Reads Terminal's tab bar through Accessibility: the only API that exposes
/// tabs in on-screen order. Safe to call off the main thread.
enum TerminalTabBar {
    enum Outcome: Sendable {
        /// This app has no Accessibility permission.
        case untrusted
        /// The read failed or was incomplete; the reason is for diagnostics.
        case failed(String)
        /// Tab titles in on-screen order (see TabBarOrder.onScreenTabTitles).
        case snapshot([String])
    }

    /// Whole-read budget (monotonic), on top of the per-message timeout: a
    /// slow Terminal must not hold the snapshot (and so the dot order).
    static let readBudget: TimeInterval = 3

    static func read(terminalPid: pid_t) -> Outcome {
        guard AXIsProcessTrusted() else { return .untrusted }
        let deadline = ProcessInfo.processInfo.systemUptime + readBudget
        let app = AXUIElementCreateApplication(terminalPid)
        AXUIElementSetMessagingTimeout(app, 1.0)
        do {
            let windows: [AXUIElement] = try optionalAttribute(app, kAXWindowsAttribute, "windows") ?? []
            var reported: [TabBarOrder.WindowTabs] = []
            for window in windows {
                try checkBudget(deadline)
                // Position orders windows: it must be read, not guessed.
                let origin = try position(of: window)
                var tabTitles: [String]?
                if let tabGroup = try firstDescendant(of: window, role: kAXTabGroupRole, depth: 3, deadline: deadline) {
                    var titles: [String] = []
                    let children: [AXUIElement] = try optionalAttribute(tabGroup, kAXChildrenAttribute, "tab bar children") ?? []
                    for child in children {
                        try checkBudget(deadline)
                        let role: String? = try optionalAttribute(child, kAXRoleAttribute, "tab role")
                        guard role == kAXRadioButtonRole else { continue }
                        guard let title: String = try optionalAttribute(child, kAXTitleAttribute, "tab title") else {
                            throw ReadError(what: "tab title", code: .noValue)
                        }
                        titles.append(title)
                    }
                    tabTitles = titles
                }
                // A window title only matters for a window without a tab bar;
                // there its read must succeed (absence is fine).
                let windowTitle: String? = tabTitles == nil
                    ? try optionalAttribute(window, kAXTitleAttribute, "window title")
                    : nil
                reported.append(TabBarOrder.WindowTabs(
                    x: Double(origin.x),
                    y: Double(origin.y),
                    tabTitles: tabTitles,
                    windowTitle: windowTitle
                ))
            }
            try checkBudget(deadline)
            return .snapshot(TabBarOrder.onScreenTabTitles(reported))
        } catch let error as ReadError {
            return .failed(error.description)
        } catch {
            return .failed("\(error)")
        }
    }

    /// Show the system prompt that adds this app to Accessibility settings.
    @discardableResult
    static func requestPermission() -> Bool {
        let options = ["AXTrustedCheckOptionPrompt": true] as CFDictionary
        return AXIsProcessTrustedWithOptions(options)
    }

    static func openAccessibilitySettings() {
        if let url = URL(string: "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility") {
            NSWorkspace.shared.open(url)
        }
    }

    struct ReadError: Error, CustomStringConvertible {
        let what: String
        let code: AXError?
        var description: String {
            code.map { "\(what): AXError \($0.rawValue)" } ?? what
        }
    }

    private static func checkBudget(_ deadline: TimeInterval) throws {
        if ProcessInfo.processInfo.systemUptime > deadline {
            throw ReadError(what: "read exceeded \(Int(readBudget))s", code: nil)
        }
    }

    /// The attribute's value, or nil when the element genuinely does not have
    /// it (unsupported / no value). Any other result, including an element that
    /// vanished mid-read or a value of an unexpected type, is a read error:
    /// the snapshot is incomplete and must not replace a good one.
    private static func optionalAttribute<T>(_ element: AXUIElement, _ name: String, _ what: String) throws -> T? {
        var value: CFTypeRef?
        let code = AXUIElementCopyAttributeValue(element, name as CFString, &value)
        switch code {
        case .success:
            guard let typed = value as? T else { throw ReadError(what: "\(what) (unexpected type)", code: nil) }
            return typed
        case .attributeUnsupported, .noValue:
            return nil
        default:
            throw ReadError(what: what, code: code)
        }
    }

    private static func position(of element: AXUIElement) throws -> CGPoint {
        var value: CFTypeRef?
        let code = AXUIElementCopyAttributeValue(element, kAXPositionAttribute as CFString, &value)
        guard code == .success else { throw ReadError(what: "window position", code: code) }
        var point = CGPoint.zero
        guard let value, CFGetTypeID(value) == AXValueGetTypeID(),
              AXValueGetValue(value as! AXValue, .cgPoint, &point) else {
            throw ReadError(what: "window position (unexpected type)", code: nil)
        }
        return point
    }

    private static func firstDescendant(of element: AXUIElement, role: String, depth: Int, deadline: TimeInterval) throws -> AXUIElement? {
        guard depth > 0 else { return nil }
        try checkBudget(deadline)
        let children: [AXUIElement] = try optionalAttribute(element, kAXChildrenAttribute, "children") ?? []
        for child in children {
            let childRole: String? = try optionalAttribute(child, kAXRoleAttribute, "role")
            if childRole == role { return child }
        }
        for child in children {
            if let found = try firstDescendant(of: child, role: role, depth: depth - 1, deadline: deadline) {
                return found
            }
        }
        return nil
    }
}
