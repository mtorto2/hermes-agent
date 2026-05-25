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
          "pid": 123,
          "process_started_at": "Mon May 25 05:00:00 2026"
        }
        """.data(using: .utf8)!

        let status = try SlotStatus.decode(from: payload)

        XCTAssertEqual(status.slot, 2)
        XCTAssertEqual(status.state, .finalAnswer)
        XCTAssertEqual(status.pid, 123)
        XCTAssertEqual(status.processStartedAt, "Mon May 25 05:00:00 2026")
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

    func testMissingOrDeadPidSlotsDoNotRender() throws {
        let payload = """
        {
          "slot": 1,
          "state": "working",
          "updated_at": "2026-05-25T12:00:00+00:00",
          "pid": 999
        }
        """.data(using: .utf8)!

        let status = try SlotStatus.decode(from: payload)

        XCTAssertFalse(status.shouldRender(processIsRunning: { _ in false }))
        XCTAssertFalse(SlotStatus.missing(slot: 2).shouldRender(processIsRunning: { _ in true }))
    }

    func testLivePidSlotsRenderEvenWhenOld() throws {
        let payload = """
        {
          "slot": 1,
          "state": "final_answer",
          "updated_at": "2026-05-25T12:00:00+00:00",
          "pid": 999,
          "process_started_at": "Mon May 25 05:00:00 2026"
        }
        """.data(using: .utf8)!

        let status = try SlotStatus.decode(from: payload)
        let oldModifiedAt = Date(timeIntervalSince1970: 0)

        XCTAssertTrue(status.shouldRender(
            now: Date(),
            fileModifiedAt: oldModifiedAt,
            processIsRunning: { _ in true },
            processStartedAtForPid: { _ in "Mon May 25 05:00:00 2026" }
        ))
        XCTAssertFalse(status.shouldRender(
            now: Date(),
            fileModifiedAt: oldModifiedAt,
            processIsRunning: { _ in true },
            processStartedAtForPid: { _ in "Mon May 25 06:00:00 2026" }
        ))
    }

    func testRecentNoPidSlotsRenderButStaleNoPidSlotsDoNot() throws {
        let payload = """
        {
          "slot": 1,
          "state": "working",
          "updated_at": "2026-05-25T12:00:00+00:00"
        }
        """.data(using: .utf8)!

        let status = try SlotStatus.decode(from: payload)
        let now = Date(timeIntervalSince1970: 1_000)

        XCTAssertTrue(status.shouldRender(now: now, fileModifiedAt: now.addingTimeInterval(-30)))
        XCTAssertFalse(status.shouldRender(now: now, fileModifiedAt: now.addingTimeInterval(-300)))
    }
}
