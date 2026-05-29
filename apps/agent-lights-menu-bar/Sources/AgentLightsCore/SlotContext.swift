import Foundation

public struct SlotContext: Equatable {
    public let slot: Int
    public let tabName: String?
    public let provider: String?
    public let modelName: String?
    public let sessionId: String?
    public let currentContext: String?
    public let chatSummary: String?
    public let updatedAt: String?

    public init(
        slot: Int,
        tabName: String? = nil,
        provider: String? = nil,
        modelName: String? = nil,
        sessionId: String? = nil,
        currentContext: String? = nil,
        chatSummary: String? = nil,
        updatedAt: String? = nil
    ) {
        self.slot = slot
        self.tabName = tabName
        self.provider = provider
        self.modelName = modelName
        self.sessionId = sessionId
        self.currentContext = currentContext
        self.chatSummary = chatSummary
        self.updatedAt = updatedAt
    }

    public static func fallback(for status: SlotStatus) -> SlotContext {
        SlotContext(slot: status.slot, modelName: status.modelName)
    }

    public var detailRows: [String] {
        var rows: [String] = []
        rows.append("Tab: \(slotLetter)" + (trimmed(tabName).map { " — \($0)" } ?? ""))
        if let providerModel = providerModelLabel {
            rows.append("Provider / Model: \(providerModel)")
        }
        if let sessionId = trimmed(sessionId) {
            rows.append("Session: \(sessionId)")
        }
        if let currentContext = trimmed(currentContext) {
            rows.append("Current Context: \(currentContext)")
        }
        if let chatSummary = trimmed(chatSummary) {
            rows.append("Chat Summary: \(chatSummary)")
        }
        if let updatedAt = trimmed(updatedAt) {
            rows.append("Updated: \(updatedAt)")
        }
        return rows
    }

    public static func decode(from data: Data) throws -> SlotContext {
        let payload = try JSONDecoder().decode(SlotContextPayload.self, from: data)
        return SlotContext(
            slot: payload.slot,
            tabName: payload.tabName,
            provider: payload.provider,
            modelName: payload.modelName,
            sessionId: payload.sessionId,
            currentContext: payload.currentContext,
            chatSummary: payload.chatSummary,
            updatedAt: payload.updatedAt
        )
    }

    private var slotLetter: String {
        guard (1...26).contains(slot), let scalar = UnicodeScalar(64 + slot) else { return String(slot) }
        return String(Character(scalar))
    }

    private var providerModelLabel: String? {
        let provider = trimmed(provider)
        let model = trimmed(modelName)
        switch (provider, model) {
        case let (provider?, model?): return "\(provider) / \(model)"
        case let (provider?, nil): return provider
        case let (nil, model?): return model
        default: return nil
        }
    }
}

private func trimmed(_ value: String?) -> String? {
    guard let value else { return nil }
    let trimmedValue = value.trimmingCharacters(in: .whitespacesAndNewlines)
    return trimmedValue.isEmpty ? nil : trimmedValue
}

private struct SlotContextPayload: Decodable {
    let slot: Int
    let tabName: String?
    let provider: String?
    let modelName: String?
    let sessionId: String?
    let currentContext: String?
    let chatSummary: String?
    let updatedAt: String?

    enum CodingKeys: String, CodingKey {
        case slot
        case tabName = "tab_name"
        case provider
        case modelName = "model_name"
        case sessionId = "session_id"
        case currentContext = "current_context"
        case chatSummary = "chat_summary"
        case updatedAt = "updated_at"
    }
}
