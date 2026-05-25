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
        var parts = ["\(slot): \(state.rawValue)", isKanbanWorker ? "ring" : "dot"]
        if let taskLocator = nonEmptyTaskLocator {
            parts.append(taskLocator)
        }
        appendIfPresent(kanbanTaskTitle, to: &parts)
        appendIfPresent(profile, to: &parts)
        appendIfPresent(modelName, to: &parts)
        return parts.joined(separator: " · ")
    }

    private var nonEmptyTaskLocator: String? {
        guard let taskId = trimmed(kanbanTaskId) else { return nil }
        if let board = trimmed(kanbanBoard) {
            return "\(board)/\(taskId)"
        }
        return taskId
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

    public init(statuses: [SlotStatus]) {
        let rowTitles = statuses.map(\.menuDetail)
        self.rowTitles = rowTitles
        if rowTitles.isEmpty {
            self.summaryTitle = "Hermes Agent Lights: no active slots"
            self.tooltip = self.summaryTitle
        } else {
            self.summaryTitle = "Hermes Agent Lights: \(rowTitles.count) active slot\(rowTitles.count == 1 ? "" : "s")"
            self.tooltip = "Hermes Agent Lights:\n" + rowTitles.joined(separator: "\n")
        }
    }
}

private func appendIfPresent(_ value: String?, to parts: inout [String]) {
    if let trimmedValue = trimmed(value) {
        parts.append(trimmedValue)
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
