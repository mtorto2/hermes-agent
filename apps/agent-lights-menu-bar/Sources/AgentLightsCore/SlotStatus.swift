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
    public let slot: Int
    public let state: SlotLifecycleState
    public let event: String?
    public let updatedAt: String?
    public let pid: Int?

    public static func missing(slot: Int) -> SlotStatus {
        SlotStatus(slot: slot, state: .missing, event: nil, updatedAt: nil, pid: nil)
    }

    public static func decode(from data: Data) throws -> SlotStatus {
        let payload = try JSONDecoder().decode(SlotStatusPayload.self, from: data)
        let state = SlotLifecycleState(rawValue: payload.state ?? payload.event ?? "") ?? .missing
        return SlotStatus(
            slot: payload.slot,
            state: state,
            event: payload.event,
            updatedAt: payload.updatedAt,
            pid: payload.pid
        )
    }
}

private struct SlotStatusPayload: Decodable {
    let slot: Int
    let event: String?
    let state: String?
    let updatedAt: String?
    let pid: Int?

    enum CodingKeys: String, CodingKey {
        case slot
        case event
        case state
        case updatedAt = "updated_at"
        case pid
    }
}
