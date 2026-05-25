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
          "process_started_at": "Mon May 25 05:00:00 2026",
          "source": "kanban_worker",
          "model_name": "openai-codex/gpt-5.5",
          "kanban_board": "default",
          "kanban_task_id": "t_1234abcd",
          "kanban_task_title": "Investigate menu rings",
          "profile": "matt-codex"
        }
        """.data(using: .utf8)!

        let status = try SlotStatus.decode(from: payload)

        XCTAssertEqual(status.slot, 2)
        XCTAssertEqual(status.state, .finalAnswer)
        XCTAssertEqual(status.pid, 123)
        XCTAssertEqual(status.processStartedAt, "Mon May 25 05:00:00 2026")
        XCTAssertEqual(status.source, "kanban_worker")
        XCTAssertEqual(status.modelName, "openai-codex/gpt-5.5")
        XCTAssertEqual(status.kanbanBoard, "default")
        XCTAssertEqual(status.kanbanTaskId, "t_1234abcd")
        XCTAssertEqual(status.kanbanTaskTitle, "Investigate menu rings")
        XCTAssertEqual(status.profile, "matt-codex")
        XCTAssertTrue(status.isKanbanWorker)
        XCTAssertEqual(
            status.menuDetail,
            "2: final_answer · ring · default/t_1234abcd · Investigate menu rings · matt-codex · openai-codex/gpt-5.5"
        )
    }

    func testNonKanbanSlotsAreNotWorkerRings() throws {
        let payload = """
        {
          "slot": 1,
          "state": "working",
          "source": "hermes",
          "model_name": "anthropic/claude-sonnet-4.6"
        }
        """.data(using: .utf8)!

        let status = try SlotStatus.decode(from: payload)

        XCTAssertFalse(status.isKanbanWorker)
        XCTAssertEqual(status.menuDetail, "1: working · dot · anthropic/claude-sonnet-4.6")
    }

    func testMenuModelProvidesOneRowPerRenderableSlot() throws {
        let first = try SlotStatus.decode(from: """
        {"slot":1,"state":"working","source":"hermes","model_name":"gpt-5.5"}
        """.data(using: .utf8)!)
        let second = try SlotStatus.decode(from: """
        {"slot":2,"state":"final_answer","source":"kanban_worker","kanban_board":"default","kanban_task_id":"t_abc","kanban_task_title":"Cleanup rings","profile":"matt-codex","model_name":"codex"}
        """.data(using: .utf8)!)

        let model = SlotStatusMenuModel(statuses: [first, second])

        XCTAssertEqual(model.summaryTitle, "Hermes Agent Lights: 2 active slots")
        XCTAssertEqual(model.rowTitles, [
            "1: working · dot · gpt-5.5",
            "2: final_answer · ring · default/t_abc · Cleanup rings · matt-codex · codex",
        ])
        XCTAssertEqual(
            model.tooltip,
            "Hermes Agent Lights:\n1: working · dot · gpt-5.5\n2: final_answer · ring · default/t_abc · Cleanup rings · matt-codex · codex"
        )
    }

    func testStaleDeadPidSlotsShouldBePruned() throws {
        let payload = """
        {
          "slot": 4,
          "state": "final_answer",
          "updated_at": "2026-05-25T12:00:00+00:00",
          "pid": 999,
          "source": "kanban_worker"
        }
        """.data(using: .utf8)!
        let status = try SlotStatus.decode(from: payload)
        let now = Date(timeIntervalSince1970: 1_000)

        XCTAssertTrue(status.shouldPrune(
            now: now,
            fileModifiedAt: now.addingTimeInterval(-300),
            processIsRunning: { _ in false }
        ))
        XCTAssertFalse(status.shouldPrune(
            now: now,
            fileModifiedAt: now.addingTimeInterval(-30),
            processIsRunning: { _ in false }
        ))
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

    func testRecentDeadPidAttentionSlotsRenderButStaleDeadPidSlotsDoNot() throws {
        let payload = """
        {
          "slot": 1,
          "state": "human_intervention",
          "updated_at": "2026-05-25T12:00:00+00:00",
          "pid": 999,
          "source": "kanban_worker"
        }
        """.data(using: .utf8)!

        let status = try SlotStatus.decode(from: payload)
        let now = Date(timeIntervalSince1970: 1_000)

        XCTAssertTrue(status.shouldRender(
            now: now,
            fileModifiedAt: now.addingTimeInterval(-30),
            processIsRunning: { _ in false }
        ))
        XCTAssertFalse(status.shouldRender(
            now: now,
            fileModifiedAt: now.addingTimeInterval(-300),
            processIsRunning: { _ in false }
        ))
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
