import XCTest
@testable import AgentLightsCore

final class SlotStatusTests: XCTestCase {
    func testParsesKnownLifecycleStates() throws {
        let payload = """
        {
          "slot": 2,
          "event": "final_answer",
          "state": "final_answer",
          "updated_at": "2026-05-25T12:00:00+00:00",
          "pid": 123
        }
        """.data(using: .utf8)!

        let status = try SlotStatus.decode(from: payload)

        XCTAssertEqual(status.slot, 2)
        XCTAssertEqual(status.state, .finalAnswer)
        XCTAssertEqual(status.pid, 123)
    }

    func testMissingSlotFileDefaultsToMissingState() {
        let status = SlotStatus.missing(slot: 4)

        XCTAssertEqual(status.slot, 4)
        XCTAssertEqual(status.state, .missing)
    }

    func testInvalidStateFallsBackToMissing() throws {
        let payload = """
        {
          "slot": 1,
          "state": "surprise",
          "updated_at": "2026-05-25T12:00:00+00:00",
          "pid": 123
        }
        """.data(using: .utf8)!

        let status = try SlotStatus.decode(from: payload)

        XCTAssertEqual(status.state, .missing)
    }
}
