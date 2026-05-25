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
        XCTAssertEqual(status.menuDetail, "2: gpt 5.5 - answer ready")
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
        XCTAssertEqual(status.menuDetail, "1: claude 4.6 - working")
    }

    func testMenuModelProvidesOneRowPerRenderableSlot() throws {
        let first = try SlotStatus.decode(from: """
        {"slot":1,"state":"final_answer","source":"hermes","model_name":"openai/gpt-5.5"}
        """.data(using: .utf8)!)
        let second = try SlotStatus.decode(from: """
        {"slot":2,"state":"working","source":"hermes","model_name":"anthropic/claude-4.7"}
        """.data(using: .utf8)!)
        let worker = try SlotStatus.decode(from: """
        {"slot":1,"state":"working","source":"kanban_worker","model_name":"openai-codex/gpt-5.5"}
        """.data(using: .utf8)!)

        let model = SlotStatusMenuModel(hermesStatuses: [first, second], agentStatuses: [worker])

        XCTAssertEqual(model.summaryTitle, "Hermes: 2 active  Agents: 1 active")
        XCTAssertEqual(model.rowTitles, [
            "1: gpt 5.5 - answer ready",
            "2: claude 4.7 - working",
            "Agent 1: gpt 5.5 - working",
        ])
        XCTAssertEqual(
            model.tooltip,
            "Hermes: 2 active  Agents: 1 active\n1: gpt 5.5 - answer ready\n2: claude 4.7 - working\nAgent 1: gpt 5.5 - working"
        )
    }

    func testAgentRingGroupShowsFourCapacitySlotsWhenAnyAgentIsRunning() throws {
        let first = try SlotStatus.decode(from: """
        {"slot":1,"state":"working","source":"kanban_worker","model_name":"openai-codex/gpt-5.5"}
        """.data(using: .utf8)!)
        let second = try SlotStatus.decode(from: """
        {"slot":2,"state":"human_intervention","source":"kanban_worker","model_name":"anthropic/claude-sonnet-4.6"}
        """.data(using: .utf8)!)

        let group = AgentRingGroup(statuses: [first, second])

        XCTAssertTrue(group.shouldRender)
        XCTAssertEqual(group.capacity, 4)
        XCTAssertEqual(group.rings.map(\.state), [.working, .humanIntervention, .missing, .missing])
        XCTAssertEqual(group.rings.map(\.isPlaceholder), [false, false, true, true])
    }

    func testAgentRingGroupHiddenWhenNoAgentWorkersAreRunning() {
        let group = AgentRingGroup(statuses: [])

        XCTAssertFalse(group.shouldRender)
        XCTAssertEqual(group.rings.count, 4)
        XCTAssertTrue(group.rings.allSatisfy(\.isPlaceholder))
    }

    func testStatusIndicatorLayoutKeepsAgentCapacityInPrimaryVisibleItem() throws {
        let hermes = try SlotStatus.decode(from: """
        {"slot":1,"state":"final_answer","source":"hermes","model_name":"openai/gpt-5.5"}
        """.data(using: .utf8)!)
        let agent = try SlotStatus.decode(from: """
        {"slot":1,"state":"working","source":"kanban_worker","model_name":"openai-codex/gpt-5.5"}
        """.data(using: .utf8)!)

        let layout = StatusIndicatorLayout(
            hermesStatuses: [hermes],
            agentGroup: AgentRingGroup(statuses: [agent])
        )

        XCTAssertEqual(layout.filledDotCount, 1)
        XCTAssertEqual(layout.agentRingCount, 4)
        XCTAssertTrue(layout.shouldRenderAgentRings)
        XCTAssertEqual(layout.agentRingColumns, 2)
        XCTAssertEqual(layout.agentRingRows, 2)
        XCTAssertEqual(layout.agentRingGridPositions, [
            AgentRingGridPosition(column: 0, row: 0),
            AgentRingGridPosition(column: 1, row: 0),
            AgentRingGridPosition(column: 0, row: 1),
            AgentRingGridPosition(column: 1, row: 1),
        ])
    }

    func testStatusIndicatorGeometryKeepsRingsSeparatedAndDotsLegible() throws {
        let geometry = StatusIndicatorGeometry()

        XCTAssertEqual(geometry.hermesDotDiameter, 9.0, accuracy: 0.01)
        XCTAssertEqual(geometry.agentIndicatorStyle, .filledCircle)
        XCTAssertGreaterThan(geometry.agentCircleDiameter, 6.0)
        XCTAssertLessThanOrEqual(geometry.agentCircleDiameter, 6.8)
        XCTAssertGreaterThanOrEqual(geometry.agentCircleColumnSpacing - geometry.agentCircleDiameter, 1.0)
        XCTAssertGreaterThanOrEqual(geometry.agentCircleRowSpacing - geometry.agentCircleDiameter, 1.0)
    }

    func testFloatingMonitorWindowDefaultsMatchStickyNotesReference() {
        let spec = FloatingMonitorWindowSpec.default

        XCTAssertEqual(spec.defaultWidth, 500.0, accuracy: 0.01)
        XCTAssertEqual(spec.defaultHeight, 230.0, accuracy: 0.01)
        XCTAssertEqual(spec.opacity, 0.72, accuracy: 0.01)
        XCTAssertEqual(spec.backgroundRed, 0.97, accuracy: 0.01)
        XCTAssertEqual(spec.backgroundGreen, 0.96, accuracy: 0.01)
        XCTAssertEqual(spec.backgroundBlue, 0.93, accuracy: 0.01)
        XCTAssertEqual(spec.backgroundAlpha, 0.58, accuracy: 0.01)
        XCTAssertFalse(spec.drawsCircleOutlines)
        XCTAssertTrue(spec.isFloating)
        XCTAssertTrue(spec.isResizable)
        XCTAssertTrue(spec.isTranslucent)
    }

    func testFloatingMonitorOpacityAdjustmentClamps() {
        XCTAssertEqual(FloatingMonitorOpacity.moreTransparent(from: 0.72), 0.64, accuracy: 0.01)
        XCTAssertEqual(FloatingMonitorOpacity.lessTransparent(from: 0.72), 0.80, accuracy: 0.01)
        XCTAssertEqual(FloatingMonitorOpacity.moreTransparent(from: 0.35), 0.35, accuracy: 0.01)
        XCTAssertEqual(FloatingMonitorOpacity.lessTransparent(from: 0.95), 0.95, accuracy: 0.01)
    }

    func testFloatingMonitorLayoutScalesCirclesWithWindowSize() throws {
        let hermes = try SlotStatus.decode(from: """
        {"slot":1,"state":"working","source":"hermes","model_name":"openai/gpt-5.5"}
        """.data(using: .utf8)!)
        let agent = try SlotStatus.decode(from: """
        {"slot":1,"state":"human_intervention","source":"kanban_worker","model_name":"openai-codex/gpt-5.5"}
        """.data(using: .utf8)!)

        let large = FloatingMonitorLayout(
            size: FloatingMonitorSize(width: 500, height: 230),
            hermesStatuses: [hermes],
            agentGroup: AgentRingGroup(statuses: [agent])
        )
        let small = FloatingMonitorLayout(
            size: FloatingMonitorSize(width: 250, height: 115),
            hermesStatuses: [hermes],
            agentGroup: AgentRingGroup(statuses: [agent])
        )

        XCTAssertEqual(large.items.count, 5)
        XCTAssertGreaterThan(large.items[0].diameter, small.items[0].diameter)
        XCTAssertGreaterThan(large.items[0].diameter, 90)
        XCTAssertEqual(large.items.filter(\.isAgent).count, 4)
        XCTAssertEqual(large.items.filter { $0.state == .humanIntervention }.count, 1)
        XCTAssertEqual(large.items.filter { $0.isAgent && $0.isPlaceholder }.count, 3)

        let hermesCircle = try XCTUnwrap(large.items.first { !$0.isAgent })
        let agentCircles = large.items.filter(\.isAgent)
        let agentMinX = agentCircles.map(\.x).min()!
        let agentMaxX = agentCircles.map { $0.x + $0.diameter }.max()!
        let agentMinY = agentCircles.map(\.y).min()!
        let agentMaxY = agentCircles.map { $0.y + $0.diameter }.max()!
        let agentFootprintWidth = agentMaxX - agentMinX
        let agentFootprintHeight = agentMaxY - agentMinY
        let agentCenterY = (agentMinY + agentMaxY) / 2
        let hermesCenterY = hermesCircle.y + hermesCircle.diameter / 2

        XCTAssertEqual(hermesCircle.diameter, agentFootprintWidth, accuracy: 0.5)
        XCTAssertEqual(hermesCircle.diameter, agentFootprintHeight, accuracy: 0.5)
        XCTAssertEqual(hermesCenterY, agentCenterY, accuracy: 0.5)
        XCTAssertLessThan(hermesCircle.x + hermesCircle.diameter, agentMinX)

        for i in 0..<agentCircles.count {
            for j in (i + 1)..<agentCircles.count {
                let first = agentCircles[i]
                let second = agentCircles[j]
                let dx = (first.x + first.diameter / 2) - (second.x + second.diameter / 2)
                let dy = (first.y + first.diameter / 2) - (second.y + second.diameter / 2)
                let distance = (dx * dx + dy * dy).squareRoot()
                XCTAssertGreaterThanOrEqual(distance, first.diameter * 1.08)
            }
        }
    }

    func testKanbanCardOpenScriptShowsCardInTerminal() {
        let script = KanbanCardOpenScript.script(taskId: "t_1234abcd", board: "voice-clipboard")!

        XCTAssertTrue(script.contains("tell application \"Terminal\""))
        XCTAssertTrue(script.contains("kanban --board voice-clipboard show t_1234abcd"))
        XCTAssertTrue(script.contains("activate"))
    }

    func testKanbanCardOpenScriptRejectsUnsafeTaskOrBoardNames() {
        XCTAssertNil(KanbanCardOpenScript.script(taskId: "../../bad", board: "default"))
        XCTAssertNil(KanbanCardOpenScript.script(taskId: "t_1234abcd; rm -rf ~", board: "default"))
        XCTAssertNil(KanbanCardOpenScript.script(taskId: "t_1234abcd", board: "bad board; rm"))
    }

    func testTerminalFocusScriptTargetsTerminalTabByTty() {
        let script = TerminalFocusScript.script(forTTY: "ttys002")!

        XCTAssertTrue(script.contains("/dev/ttys002"))
        XCTAssertTrue(script.contains("set selected tab of w to t"))
        XCTAssertTrue(script.contains("set index of w to 1"))
        XCTAssertTrue(script.contains("activate"))
    }

    func testTerminalFocusScriptRejectsUnsafeTtyNames() {
        XCTAssertNil(TerminalFocusScript.script(forTTY: "??"))
        XCTAssertNil(TerminalFocusScript.script(forTTY: "ttys002\" & do shell script \"bad"))
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
