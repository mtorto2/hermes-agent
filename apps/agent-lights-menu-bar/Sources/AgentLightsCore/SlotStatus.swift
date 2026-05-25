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

    public static func missing(slot: Int) -> SlotStatus {
        SlotStatus(slot: slot, state: .missing, event: nil, updatedAt: nil, pid: nil, processStartedAt: nil)
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
            processStartedAt: payload.processStartedAt
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
            guard processIsRunning(pid) else { return false }
            guard let processStartedAt, !processStartedAt.isEmpty else { return true }
            return processStartedAtForPid(pid) == processStartedAt
        }

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

private struct SlotStatusPayload: Decodable {
    let slot: Int
    let event: String?
    let state: String?
    let updatedAt: String?
    let pid: Int?
    let processStartedAt: String?

    enum CodingKeys: String, CodingKey {
        case slot
        case event
        case state
        case updatedAt = "updated_at"
        case pid
        case processStartedAt = "process_started_at"
    }
}
