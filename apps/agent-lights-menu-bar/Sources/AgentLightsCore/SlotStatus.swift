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

public struct SlotStatus: Equatable {
    public static let recentWithoutLivePidWindow: TimeInterval = 120

    public let slot: Int
    public let state: SlotLifecycleState
    public let event: String?
    public let updatedAt: String?
    public let pid: Int?
    public let processStartedAt: String?
    public let source: String?
    public let modelName: String?
    public let kanbanBoard: String?
    public let kanbanTaskId: String?
    public let kanbanTaskTitle: String?
    public let profile: String?

    public var isKanbanWorker: Bool {
        source == "kanban_worker"
    }

    public var menuDetail: String {
        "\(slotLetter): \(displayModelName) - \(state.menuLabel)"
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
        SlotStatus(slot: slot, state: .missing, event: nil, updatedAt: nil, pid: nil, processStartedAt: nil, source: nil, modelName: nil, kanbanBoard: nil, kanbanTaskId: nil, kanbanTaskTitle: nil, profile: nil)
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
            source: payload.source,
            modelName: payload.modelName,
            kanbanBoard: payload.kanbanBoard,
            kanbanTaskId: payload.kanbanTaskId,
            kanbanTaskTitle: payload.kanbanTaskTitle,
            profile: payload.profile
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

    public func shouldRender(
        now: Date = Date(),
        fileModifiedAt: Date? = nil,
        processIsRunning: (Int) -> Bool = SlotStatus.processIsRunning,
        processStartedAtForPid: (Int) -> String? = SlotStatus.processStartedAt
    ) -> Bool {
        guard state != .missing else { return false }

        if let pid, pid > 0 {
            if processIsRunning(pid) {
                guard let processStartedAt, !processStartedAt.isEmpty else { return true }
                if processStartedAtForPid(pid) == processStartedAt {
                    return true
                }
            }
            return wasRecentlyModified(now: now, fileModifiedAt: fileModifiedAt)
        }

        return wasRecentlyModified(now: now, fileModifiedAt: fileModifiedAt)
    }

    public func shouldPrune(
        now: Date = Date(),
        fileModifiedAt: Date? = nil,
        processIsRunning: (Int) -> Bool = SlotStatus.processIsRunning,
        processStartedAtForPid: (Int) -> String? = SlotStatus.processStartedAt
    ) -> Bool {
        state != .missing && !shouldRender(
            now: now,
            fileModifiedAt: fileModifiedAt,
            processIsRunning: processIsRunning,
            processStartedAtForPid: processStartedAtForPid
        )
    }

    private func wasRecentlyModified(now: Date, fileModifiedAt: Date?) -> Bool {
        guard let fileModifiedAt else { return false }
        return now.timeIntervalSince(fileModifiedAt) <= Self.recentWithoutLivePidWindow
    }

    public static func processIsRunning(pid: Int) -> Bool {
        kill(pid_t(pid), 0) == 0 || errno == EPERM
    }

    public static func processStartedAt(pid: Int) -> String? {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/bin/ps")
        process.arguments = ["-o", "lstart=", "-p", String(pid)]
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
        let output = String(data: data, encoding: .utf8)?.trimmingCharacters(in: .whitespacesAndNewlines)
        return output?.isEmpty == false ? output : nil
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

    public init(hermesStatuses: [SlotStatus], agentStatuses: [SlotStatus], agentCount: Int? = nil) {
        let hermesRows = hermesStatuses.map(\.menuDetail)
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
    public let filledDotCount: Int
    public let agentRingCount: Int
    public let agentRingColumns: Int
    public let agentRingRows: Int
    public let agentRingGridPositions: [AgentRingGridPosition]

    public var shouldRenderAgentRings: Bool {
        agentRingCount > 0
    }

    public init(hermesStatuses: [SlotStatus], agentGroup: AgentRingGroup) {
        self.filledDotCount = min(hermesStatuses.count, 4)
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
        let activeHermes = Array(hermesStatuses.prefix(4))
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
    let source: String?
    let modelName: String?
    let kanbanBoard: String?
    let kanbanTaskId: String?
    let kanbanTaskTitle: String?
    let profile: String?

    enum CodingKeys: String, CodingKey {
        case slot
        case event
        case state
        case updatedAt = "updated_at"
        case pid
        case processStartedAt = "process_started_at"
        case source
        case modelName = "model_name"
        case kanbanBoard = "kanban_board"
        case kanbanTaskId = "kanban_task_id"
        case kanbanTaskTitle = "kanban_task_title"
        case profile
    }
}
