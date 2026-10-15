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
        XCTAssertEqual(status.menuDetail, "B: GPT-5.5 - answer ready")
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
        XCTAssertEqual(status.menuDetail, "A: Sonnet 4.6 - working")
    }

    func testMenuModelProvidesOneRowPerRenderableSlot() throws {
        let first = try SlotStatus.decode(from: """
        {"slot":1,"state":"final_answer","source":"hermes","model_name":"openai/gpt-5.5"}
        """.data(using: .utf8)!)
        let second = try SlotStatus.decode(from: """
        {"slot":2,"state":"working","source":"hermes","model_name":"anthropic/claude-opus-4.8"}
        """.data(using: .utf8)!)
        let worker = try SlotStatus.decode(from: """
        {"slot":1,"state":"working","source":"kanban_worker","model_name":"openai-codex/gpt-5.5"}
        """.data(using: .utf8)!)

        let model = SlotStatusMenuModel(hermesStatuses: [first, second], agentStatuses: [worker])

        XCTAssertEqual(model.summaryTitle, "Hermes: 2 active  Agents: 1 active")
        XCTAssertEqual(model.rowTitles, [
            "A: GPT-5.5 - answer ready",
            "B: Opus 4.8 - working",
            "Agent 1: GPT-5.5 - working",
        ])
        XCTAssertEqual(
            model.tooltip,
            "Hermes: 2 active  Agents: 1 active\nA: GPT-5.5 - answer ready\nB: Opus 4.8 - working\nAgent 1: GPT-5.5 - working"
        )
    }

    func testMenuModelUsesManualTerminalDisplayNamesWhenAvailable() throws {
        let first = try SlotStatus.decode(from: """
        {"slot":1,"state":"idle","source":"hermes","model_name":"openai/gpt-5.5"}
        """.data(using: .utf8)!)
        let second = try SlotStatus.decode(from: """
        {"slot":2,"state":"working","source":"hermes","model_name":"anthropic/claude-opus-4.8"}
        """.data(using: .utf8)!)

        let model = SlotStatusMenuModel(
            hermesStatuses: [first, second],
            agentStatuses: [],
            hermesDisplayNames: [2: "Matt · hermes-agent"]
        )

        XCTAssertEqual(model.rowTitles, [
            "A: GPT-5.5 - idle",
            "B: Matt · hermes-agent - working",
        ])
        XCTAssertEqual(
            model.tooltip,
            "Hermes: 2 active  Agents: 0 active\nA: GPT-5.5 - idle\nB: Matt · hermes-agent - working"
        )
    }

    func testHermesStatusesCanBeOrderedByTerminalTabTtyInsteadOfSlotNumber() throws {
        let firstSlot = try SlotStatus.decode(from: """
        {"slot":1,"state":"final_answer","pid":101,"source":"hermes","model_name":"openai/gpt-5.5"}
        """.data(using: .utf8)!)
        let secondSlot = try SlotStatus.decode(from: """
        {"slot":2,"state":"working","pid":202,"source":"hermes","model_name":"anthropic/claude-4.7"}
        """.data(using: .utf8)!)
        let thirdSlot = try SlotStatus.decode(from: """
        {"slot":3,"state":"idle","pid":303,"source":"hermes","model_name":"openai/gpt-5.5"}
        """.data(using: .utf8)!)

        let ordered = SlotStatus.orderedByTerminalTTY(
            [firstSlot, secondSlot, thirdSlot],
            ttyForPid: [101: "ttys003", 202: "ttys001", 303: "ttys002"],
            terminalTTYOrder: ["/dev/ttys001", "/dev/ttys002", "/dev/ttys003"]
        )

        XCTAssertEqual(ordered.map(\.slot), [2, 3, 1])
    }

    func testTerminalTtyOrderingFallsBackToSlotOrderForUnknownTabs() throws {
        let firstSlot = try SlotStatus.decode(from: """
        {"slot":1,"state":"final_answer","pid":101,"source":"hermes","model_name":"openai/gpt-5.5"}
        """.data(using: .utf8)!)
        let secondSlot = try SlotStatus.decode(from: """
        {"slot":2,"state":"working","pid":202,"source":"hermes","model_name":"anthropic/claude-4.7"}
        """.data(using: .utf8)!)

        let ordered = SlotStatus.orderedByTerminalTTY(
            [secondSlot, firstSlot],
            ttyForPid: [202: "ttys009"],
            terminalTTYOrder: ["/dev/ttys001"]
        )

        XCTAssertEqual(ordered.map(\.slot), [1, 2])
    }

    func testTerminalTabTitleUsesSingleSlotLetterAtEndForReadableMacOSTabs() throws {
        let status = try SlotStatus.decode(from: """
        {"slot":2,"state":"working","pid":202,"source":"hermes","model_name":"openai/gpt-5.5"}
        """.data(using: .utf8)!)

        XCTAssertEqual(status.terminalTabTitle, "Hermes B")
    }

    func testSlotContextProvidesExpandableMenuRows() throws {
        let context = try SlotContext.decode(from: """
        {
          "slot": 2,
          "tab_name": "Build lights",
          "provider": "openai-codex",
          "model_name": "gpt-5.5",
          "session_id": "20260529_123456_abcd",
          "current_context": "Implement Agent Lights per-tab context submenu.",
          "chat_summary": "Tests are red; wiring Swift menu next.",
          "updated_at": "2026-05-29T12:34:56+00:00"
        }
        """.data(using: .utf8)!)

        XCTAssertEqual(context.slot, 2)
        XCTAssertEqual(context.detailRows, [
            "Tab: B — Build lights",
            "Provider / Model: openai-codex / gpt-5.5",
            "Session: 20260529_123456_abcd",
            "Current Context: Implement Agent Lights per-tab context submenu.",
            "Chat Summary: Tests are red; wiring Swift menu next.",
            "Updated: 2026-05-29T12:34:56+00:00",
        ])
    }

    func testSlotContextFallbackStillCreatesSubmenuRowsWithoutSidecar() throws {
        let status = try SlotStatus.decode(from: """
        {"slot":1,"state":"working","source":"hermes","model_name":"openai/gpt-5.5"}
        """.data(using: .utf8)!)

        let context = SlotContext.fallback(for: status)

        XCTAssertEqual(context.detailRows, [
            "Tab: A",
            "Provider / Model: openai/gpt-5.5",
        ])
    }

    func testAgentRingGroupShowsEightCapacitySlotsWhenAnyAgentIsRunning() throws {
        let first = try SlotStatus.decode(from: """
        {"slot":1,"state":"working","source":"kanban_worker","model_name":"openai-codex/gpt-5.5"}
        """.data(using: .utf8)!)
        let second = try SlotStatus.decode(from: """
        {"slot":2,"state":"human_intervention","source":"kanban_worker","model_name":"anthropic/claude-sonnet-4.6"}
        """.data(using: .utf8)!)
        let sixth = try SlotStatus.decode(from: """
        {"slot":6,"state":"error","source":"kanban_worker","model_name":"openai/gpt-5.5"}
        """.data(using: .utf8)!)

        let group = AgentRingGroup(statuses: [sixth, first, second])

        XCTAssertTrue(group.shouldRender)
        XCTAssertEqual(group.capacity, 8)
        XCTAssertEqual(group.rings.map(\.state), [.working, .humanIntervention, .error, .missing, .missing, .missing, .missing, .missing])
        XCTAssertEqual(group.rings.map(\.isPlaceholder), [false, false, false, true, true, true, true, true])
    }

    func testAgentRingGroupHiddenWhenNoAgentWorkersAreRunning() {
        let group = AgentRingGroup(statuses: [])

        XCTAssertFalse(group.shouldRender)
        XCTAssertEqual(group.rings.count, 8)
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
        XCTAssertEqual(layout.agentRingCount, 8)
        XCTAssertTrue(layout.shouldRenderAgentRings)
        XCTAssertEqual(layout.agentRingColumns, 4)
        XCTAssertEqual(layout.agentRingRows, 2)
        XCTAssertEqual(layout.agentRingGridPositions, [
            AgentRingGridPosition(column: 0, row: 0),
            AgentRingGridPosition(column: 1, row: 0),
            AgentRingGridPosition(column: 0, row: 1),
            AgentRingGridPosition(column: 1, row: 1),
            AgentRingGridPosition(column: 2, row: 0),
            AgentRingGridPosition(column: 3, row: 0),
            AgentRingGridPosition(column: 2, row: 1),
            AgentRingGridPosition(column: 3, row: 1),
        ])
    }

    func testStatusIndicatorLayoutSupportsSixNormalHermesSlotsAndExcludesOutOfRangeSlots() throws {
        let statuses = try (1...7).map { slot in
            try SlotStatus.decode(
                from: "{\"slot\":\(slot),\"state\":\"working\",\"source\":\"hermes\"}".data(using: .utf8)!
            )
        }

        let layout = StatusIndicatorLayout(
            hermesStatuses: statuses,
            agentGroup: AgentRingGroup(statuses: [])
        )

        XCTAssertEqual(layout.normalSlotIndicators.map(\.slot), [1, 2, 3, 4, 5, 6])
        XCTAssertEqual(layout.filledDotCount, 6)
    }

    func testStatusIndicatorLayoutKeepsTheGivenDotOrder() throws {
        let statuses = try [3, 1, 2].map { slot in
            try SlotStatus.decode(from: "{\"slot\":\(slot),\"state\":\"working\",\"source\":\"hermes\"}".data(using: .utf8)!)
        }
        let layout = StatusIndicatorLayout(hermesStatuses: statuses, agentGroup: AgentRingGroup(statuses: []))

        XCTAssertEqual(layout.normalSlotIndicators.map(\.slot), [3, 1, 2])
    }

    func testDotsFollowTabOpeningOrderNotSlotNumbers() throws {
        // The reported case: Hermes (tab 1, opened first) got slot 2 because
        // slot 1 was busy; Tate (tab 2) later took the freed slot 1.
        let hermes = try SlotStatus.decode(from: #"{"slot":2,"state":"idle","source":"hermes","pid":200,"profile":"default"}"#.data(using: .utf8)!)
        let tate = try SlotStatus.decode(from: #"{"slot":1,"state":"idle","source":"hermes","pid":100,"profile":"business"}"#.data(using: .utf8)!)
        let tabOpened: [Int: Double] = [200: 1_000, 100: 2_000]

        let ordered = DotOrder.ordered([tate, hermes], sessionStartTime: { $0.pid.flatMap { tabOpened[$0] } })

        XCTAssertEqual(ordered.map(\.profile), ["default", "business"])
    }

    func testDotOrderPutsSessionsWithoutATimeLastAndBreaksTiesBySlot() throws {
        let a = try SlotStatus.decode(from: #"{"slot":3,"state":"idle","pid":1}"#.data(using: .utf8)!)
        let b = try SlotStatus.decode(from: #"{"slot":1,"state":"idle","pid":2}"#.data(using: .utf8)!)
        let c = try SlotStatus.decode(from: #"{"slot":2,"state":"idle","pid":3}"#.data(using: .utf8)!)
        let times: [Int: Double] = [1: 50, 2: 50]

        let ordered = DotOrder.ordered([a, b, c], sessionStartTime: { $0.pid.flatMap { times[$0] } })

        XCTAssertEqual(ordered.map(\.slot), [1, 3, 2])
    }

    private func hermesStatus(slot: Int, pid: Int, profile: String) throws -> SlotStatus {
        try SlotStatus.decode(from: "{\"slot\":\(slot),\"state\":\"idle\",\"source\":\"hermes\",\"pid\":\(pid),\"profile\":\"\(profile)\"}".data(using: .utf8)!)
    }

    func testTabBarOrderFollowsOnScreenTabsIncludingMidRowInsertions() throws {
        // Tabs on screen: Hermes | Aurelius (opened last, via cmd-T from tab 1) | Tate
        let hermes = try hermesStatus(slot: 2, pid: 1, profile: "default")
        let tate = try hermesStatus(slot: 1, pid: 2, profile: "business")
        let aurelius = try hermesStatus(slot: 3, pid: 3, profile: "personal")
        let tty: [Int: String] = [1: "ttys001", 2: "ttys000", 3: "ttys004"]
        let titles = ["Terminal — ttys001", "Terminal — ttys004", "Terminal — ttys000"]

        let result = TabBarOrder.ordered(
            fallbackOrdered: [hermes, tate, aurelius],
            tabTitles: titles,
            ttyOf: { $0.pid.flatMap { tty[$0] } }
        )

        XCTAssertEqual(result.statuses.map(\.profile), ["default", "personal", "business"])
        XCTAssertEqual(result.matchedCount, 3)
    }

    func testTabBarOrderKeepsUnmatchedSessionsAfterMatchedOnesInFallbackOrder() throws {
        let a = try hermesStatus(slot: 1, pid: 1, profile: "a")
        let b = try hermesStatus(slot: 2, pid: 2, profile: "b")
        let c = try hermesStatus(slot: 3, pid: 3, profile: "c")
        let tty: [Int: String] = [1: "/dev/ttys007", 2: "ttys008"]
        // Identical titles without a TTY (the default Terminal setting) cannot
        // place anything; a title naming two terminals is ignored too.
        let result = TabBarOrder.ordered(
            fallbackOrdered: [a, b, c],
            tabTitles: ["Terminal", "Terminal", "Terminal — ttys008", "ttys007 ttys009"],
            ttyOf: { $0.pid.flatMap { tty[$0] } }
        )

        XCTAssertEqual(result.statuses.map(\.profile), ["b", "a", "c"])
        XCTAssertEqual(result.matchedCount, 1)
        XCTAssertEqual(TabBarOrder.ttys(inTitle: "matt — -zsh — ttys012 — 80×24"), ["ttys012"])
        XCTAssertEqual(TabBarOrder.ttys(inTitle: "nottys1 Terminal"), [])
    }

    func testHiddenPerTabWindowsDoNotJumpAheadOfTheRealTabBar() {
        // Live Terminal: the selected tab's window carries the tab bar; the
        // other tab is also reported as a bar-less window at the same spot,
        // and may be listed first.
        let windows = [
            TabBarOrder.WindowTabs(x: 0, y: 33, tabTitles: nil, windowTitle: "Terminal \u{2014} ttys001"),
            TabBarOrder.WindowTabs(x: 0, y: 33, tabTitles: ["ttys000", "ttys001"], windowTitle: "Terminal \u{2014} ttys000"),
        ]

        XCTAssertEqual(TabBarOrder.onScreenTabTitles(windows), ["ttys000", "ttys001"])
    }

    func testSeparateWindowsOrderLeftToRightAndKeepRealSingleTabWindows() {
        let windows = [
            TabBarOrder.WindowTabs(x: 800, y: 0, tabTitles: ["ttys005", "ttys006"], windowTitle: nil),
            TabBarOrder.WindowTabs(x: 0, y: 0, tabTitles: nil, windowTitle: "Terminal \u{2014} ttys009"),
            TabBarOrder.WindowTabs(x: 0, y: 500, tabTitles: ["ttys002"], windowTitle: nil),
        ]

        XCTAssertEqual(TabBarOrder.onScreenTabTitles(windows), ["Terminal \u{2014} ttys009", "ttys002", "ttys005", "ttys006"])
    }

    func testSamePositionWindowsKeepAStableOrderWhateverTheReportingOrder() {
        let a = TabBarOrder.WindowTabs(x: 0, y: 33, tabTitles: ["ttys004", "ttys002"], windowTitle: nil)
        let b = TabBarOrder.WindowTabs(x: 0, y: 33, tabTitles: ["ttys001"], windowTitle: nil)

        let forward = TabBarOrder.onScreenTabTitles([a, b])
        let reversed = TabBarOrder.onScreenTabTitles([b, a])

        XCTAssertEqual(forward, reversed)
        XCTAssertEqual(forward, ["ttys001", "ttys004", "ttys002"])
    }

    func testTabBarCacheRidesOutBriefFailuresThenReportsUnavailable() {
        var cache = TabBarCache()
        let start = Date(timeIntervalSince1970: 1_000)
        cache.recordSuccess(["ttys000", "ttys001"], at: start)

        cache.recordFailure("AXWindows read failed", at: start.addingTimeInterval(2))
        XCTAssertEqual(cache.titles, ["ttys000", "ttys001"])
        XCTAssertFalse(cache.isUnavailable)

        cache.recordFailure("AXWindows read failed", at: start.addingTimeInterval(TabBarCache.staleAfter + 1))
        XCTAssertEqual(cache.titles, [])
        XCTAssertTrue(cache.isUnavailable)

        cache.recordSuccess(["ttys001"], at: start.addingTimeInterval(30))
        XCTAssertEqual(cache.titles, ["ttys001"])
        XCTAssertFalse(cache.isUnavailable)
        XCTAssertNil(cache.lastFailure)
    }

    func testTabBarCacheExpiresOnUseWhenReadsStall() {
        var cache = TabBarCache()
        let start = Date(timeIntervalSince1970: 1_000)
        cache.recordSuccess(["ttys000"], at: start)

        cache.expire(at: start.addingTimeInterval(TabBarCache.staleAfter))
        XCTAssertEqual(cache.titles, ["ttys000"])

        // No failure was reported: the read is simply stuck.
        cache.expire(at: start.addingTimeInterval(TabBarCache.staleAfter + 0.5))
        XCTAssertEqual(cache.titles, [])
        XCTAssertTrue(cache.isUnavailable)
        XCTAssertNotNil(cache.lastFailure)
    }

    func testFallbackBreaksTabOpenedTiesByProcessStartTimeBeforeSlot() throws {
        let older = try hermesStatus(slot: 3, pid: 1, profile: "older")
        let newer = try hermesStatus(slot: 1, pid: 2, profile: "newer")
        let process: [Int: Double] = [1: 10, 2: 20]

        let ordered = DotOrder.ordered(
            [newer, older],
            sessionStartTime: { _ in 5 },
            processStartTime: { $0.pid.flatMap { process[$0] } }
        )

        XCTAssertEqual(ordered.map(\.profile), ["older", "newer"])
    }

    func testTerminalSessionStartTimeIsTheTabsOldestAncestorOnTheSameTTY() {
        // Terminal(1) -> login(10, ttys000) -> zsh(11) -> hermes(12) -> node(13) -> gateway python(14)
        let tty: [Int: String] = [10: "ttys000", 11: "ttys000", 12: "ttys000", 13: "ttys000", 14: "ttys000"]
        let parent: [Int: Int] = [14: 13, 13: 12, 12: 11, 11: 10, 10: 1]
        let start: [Int: Double] = [10: 100, 11: 101, 12: 500, 13: 501, 14: 900]

        let tabOpened = ProcessInspector.terminalSessionStartTime(
            pid: 14, ttyOf: { tty[$0] }, parentOf: { parent[$0] }, startTimeOf: { start[$0] }
        )

        XCTAssertEqual(tabOpened, 100)
        XCTAssertNil(ProcessInspector.terminalSessionStartTime(pid: 1, ttyOf: { tty[$0] }, parentOf: { parent[$0] }, startTimeOf: { start[$0] }))
        // A parent cycle cannot loop forever.
        XCTAssertEqual(ProcessInspector.terminalSessionStartTime(
            pid: 11, ttyOf: { _ in "ttys000" }, parentOf: { $0 == 11 ? 10 : 11 }, startTimeOf: { start[$0] }
        ), 100)
    }

    func testFloatingMonitorLayoutSupportsSixNormalHermesSlots() throws {
        let statuses = try (1...6).map { slot in
            try SlotStatus.decode(
                from: "{\"slot\":\(slot),\"state\":\"working\",\"source\":\"hermes\"}".data(using: .utf8)!
            )
        }

        let layout = FloatingMonitorLayout(
            size: FloatingMonitorSize(width: 500, height: 230),
            hermesStatuses: statuses,
            agentGroup: AgentRingGroup(statuses: [])
        )

        XCTAssertEqual(layout.items.filter { !$0.isAgent }.count, 6)
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

        XCTAssertEqual(large.items.count, 9)
        XCTAssertGreaterThan(large.items[0].diameter, small.items[0].diameter)
        XCTAssertGreaterThan(large.items[0].diameter, 90)
        XCTAssertEqual(large.items.filter(\.isAgent).count, 8)
        XCTAssertEqual(large.items.filter { $0.state == .humanIntervention }.count, 1)
        XCTAssertEqual(large.items.filter { $0.isAgent && $0.isPlaceholder }.count, 7)

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

        XCTAssertLessThan(agentFootprintHeight, hermesCircle.diameter * 0.55)
        XCTAssertEqual(hermesCenterY, agentCenterY, accuracy: 0.5)
        XCTAssertLessThan(hermesCircle.x + hermesCircle.diameter, agentMinX)
        XCTAssertEqual(agentFootprintWidth, hermesCircle.diameter, accuracy: 0.5)

        let sortedAgentCentersX = agentCircles.map { $0.x + $0.diameter / 2 }.sorted()
        let uniqueColumns = sortedAgentCentersX.reduce(into: [Double]()) { columns, center in
            if columns.last.map({ abs($0 - center) > 0.5 }) ?? true {
                columns.append(center)
            }
        }
        XCTAssertEqual(uniqueColumns.count, 4)
        for gap in zip(uniqueColumns, uniqueColumns.dropFirst()).map({ $1 - $0 }) {
            XCTAssertLessThan(gap, agentCircles[0].diameter * 1.35)
            XCTAssertGreaterThan(gap, agentCircles[0].diameter * 1.05)
        }

        for i in 0..<agentCircles.count {
            for j in (i + 1)..<agentCircles.count {
                let first = agentCircles[i]
                let second = agentCircles[j]
                let dx = (first.x + first.diameter / 2) - (second.x + second.diameter / 2)
                let dy = (first.y + first.diameter / 2) - (second.y + second.diameter / 2)
                let distance = (dx * dx + dy * dy).squareRoot()
                XCTAssertGreaterThanOrEqual(distance, first.diameter * 1.05)
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
          "process_start_time": 1779688800.25
        }
        """.data(using: .utf8)!

        let status = try SlotStatus.decode(from: payload)
        let oldModifiedAt = Date(timeIntervalSince1970: 0)

        XCTAssertEqual(status.processStartTime, 1779688800.25)
        XCTAssertTrue(status.shouldRender(
            now: Date(),
            fileModifiedAt: oldModifiedAt,
            processIsRunning: { _ in true },
            processStartTimeForPid: { _ in 1779688800.7 }
        ))
        // Same PID now names a different, later process.
        XCTAssertFalse(status.shouldRender(
            now: Date(),
            fileModifiedAt: oldModifiedAt,
            processIsRunning: { _ in true },
            processStartTimeForPid: { _ in 1779692400.0 }
        ))
    }

    func testDeadNormalHermesSlotHidesImmediatelyWithoutGrace() throws {
        // Closing a Terminal tab kills Hermes through paths that skip cleanup,
        // leaving a freshly written file behind. It must not linger as a ghost.
        let status = try SlotStatus.decode(from: """
        {"slot":2,"state":"idle","source":"hermes","pid":48734,"process_start_time":1000.0}
        """.data(using: .utf8)!)
        let now = Date(timeIntervalSince1970: 5_000)

        XCTAssertFalse(status.shouldRender(
            now: now,
            fileModifiedAt: now.addingTimeInterval(-1),
            processIsRunning: { _ in false }
        ))
        XCTAssertTrue(status.shouldPrune(
            now: now,
            fileModifiedAt: now.addingTimeInterval(-1),
            processIsRunning: { _ in false }
        ))
    }

    func testLegacyLstartPayloadIsJudgedOnPidLivenessAlone() throws {
        // Pre-upgrade producers wrote locale-dependent `ps lstart` text. A
        // reader in another locale must not mistake a live session for dead.
        let status = try SlotStatus.decode(from: """
        {"slot":1,"state":"working","source":"hermes","pid":51652,"process_started_at":"Mi. 14 Okt. 16:07:19 2026"}
        """.data(using: .utf8)!)

        XCTAssertNil(status.processStartTime)
        XCTAssertTrue(status.shouldRender(
            fileModifiedAt: Date(timeIntervalSince1970: 0),
            processIsRunning: { _ in true },
            processStartTimeForPid: { _ in 1.0 }
        ))
    }

    func testProcessInspectorReadsRealProcessStartTimeAndHandlesMissingPids() throws {
        let child = Process()
        child.executableURL = URL(fileURLWithPath: "/bin/sleep")
        child.arguments = ["5"]
        let launchedAround = Date().timeIntervalSince1970
        try child.run()
        defer { child.terminate() }

        let start = try XCTUnwrap(ProcessInspector.startTime(pid: Int(child.processIdentifier)))
        XCTAssertEqual(start, launchedAround, accuracy: 2.0)
        XCTAssertNotNil(ProcessInspector.startTime(pid: Int(getpid())))
        XCTAssertNil(ProcessInspector.startTime(pid: 0))
        XCTAssertNil(ProcessInspector.startTime(pid: 99_999_999))
        XCTAssertNil(ProcessInspector.tty(pid: 99_999_999))
        // A child inherits our controlling terminal: none under CI or an agent
        // shell, the Terminal tab's TTY when run interactively.
        XCTAssertEqual(ProcessInspector.tty(pid: Int(child.processIdentifier)), ProcessInspector.tty(pid: Int(getpid())))
    }

    private func makeSlotDirectory() throws -> URL {
        let url = FileManager.default.temporaryDirectory.appendingPathComponent("agent-lights-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
        addTeardownBlock { try? FileManager.default.removeItem(at: url) }
        return url
    }

    private func write(_ json: String, _ name: String, in directory: URL) throws {
        try json.write(to: directory.appendingPathComponent(name), atomically: true, encoding: .utf8)
    }

    private func exists(_ name: String, in directory: URL) -> Bool {
        FileManager.default.fileExists(atPath: directory.appendingPathComponent(name).path)
    }

    func testSlotFilePrunerRemovesDeadOwnerButKeepsNewClaimantsLock() throws {
        let directory = try makeSlotDirectory()
        try write(#"{"slot":1,"state":"idle","source":"hermes","pid":111,"process_start_time":100.0}"#, "1.json", in: directory)
        // A new session already re-claimed the lock: it must survive.
        try write(#"{"pid":222,"process_start_time":200.0}"#, "1.lock", in: directory)

        let removed = SlotFilePruner.prune(
            directory: directory, slot: 1,
            processIsRunning: { $0 == 222 },
            processStartTimeForPid: { $0 == 222 ? 200.0 : nil }
        )

        XCTAssertTrue(removed)
        XCTAssertFalse(exists("1.json", in: directory))
        XCTAssertTrue(exists("1.lock", in: directory))
    }

    func testSlotFilePrunerKeepsFilesOfALiveProcessThatRecycledTheDeadPid() throws {
        // The renderer saw the old owner (pid 111, started at 100) dead. Since
        // then a new process got pid 111, claimed the slot and emitted. Its
        // files name the same PID but a different start time: they must stay.
        let directory = try makeSlotDirectory()
        try write(#"{"slot":1,"state":"working","source":"hermes","pid":111,"process_start_time":900.0}"#, "1.json", in: directory)
        try write(#"{"pid":111,"process_start_time":900.0}"#, "1.lock", in: directory)

        let removed = SlotFilePruner.prune(
            directory: directory, slot: 1,
            processIsRunning: { $0 == 111 },
            processStartTimeForPid: { $0 == 111 ? 900.0 : nil }
        )

        XCTAssertFalse(removed)
        XCTAssertTrue(exists("1.json", in: directory))
        XCTAssertTrue(exists("1.lock", in: directory))
    }

    func testSlotFilePrunerKeepsAStatusFileReplacedByTheLiveOwner() throws {
        // The renderer read a dead owner's file, but by the time the pruner
        // holds the lock the live claimant has replaced it with its own.
        let directory = try makeSlotDirectory()
        try write(#"{"slot":1,"state":"idle","source":"hermes","pid":222,"process_start_time":200.0}"#, "1.json", in: directory)
        try write(#"{"pid":222,"process_start_time":200.0}"#, "1.lock", in: directory)

        XCTAssertFalse(SlotFilePruner.prune(
            directory: directory, slot: 1,
            processIsRunning: { $0 == 222 },
            processStartTimeForPid: { $0 == 222 ? 200.0 : nil }
        ))
        XCTAssertTrue(exists("1.json", in: directory))
        XCTAssertTrue(exists("1.lock", in: directory))
    }

    func testSlotFilePrunerBacksOffWhileAClaimIsInProgress() throws {
        let directory = try makeSlotDirectory()
        try write(#"{"slot":1,"state":"idle","source":"hermes","pid":111,"process_start_time":100.0}"#, "1.json", in: directory)
        // Simulate Hermes holding the claim mutex (flock is per open file
        // description, so a second descriptor in this process conflicts).
        let fd = open(directory.appendingPathComponent(".claim.lock").path, O_RDWR | O_CREAT, 0o600)
        XCTAssertGreaterThanOrEqual(fd, 0)
        XCTAssertEqual(flock(fd, LOCK_EX), 0)

        XCTAssertFalse(SlotFilePruner.prune(directory: directory, slot: 1, processIsRunning: { _ in false }))
        XCTAssertTrue(exists("1.json", in: directory))

        flock(fd, LOCK_UN)
        close(fd)
        XCTAssertTrue(SlotFilePruner.prune(directory: directory, slot: 1, processIsRunning: { _ in false }))
    }

    func testErrorDotsFlashWhileAnswerReadyStaysSteady() throws {
        let error = try SlotStatus.decode(from: #"{"slot":1,"state":"error","source":"hermes"}"#.data(using: .utf8)!)
        let answer = try SlotStatus.decode(from: #"{"slot":2,"state":"final_answer","source":"hermes"}"#.data(using: .utf8)!)

        XCTAssertEqual(NormalSlotIndicator(status: error, flashOn: true).style, .filled)
        XCTAssertEqual(NormalSlotIndicator(status: error, flashOn: false).style, .outline)
        XCTAssertEqual(NormalSlotIndicator(status: answer, flashOn: true).style, .filled)
        XCTAssertEqual(NormalSlotIndicator(status: answer, flashOn: false).style, .filled)

        let off = StatusIndicatorLayout(hermesStatuses: [error, answer], agentGroup: AgentRingGroup(statuses: []), flashOn: false)
        XCTAssertEqual(off.normalSlotIndicators.map(\.style), [.outline, .filled])
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

    func testStatusIndicatorLayoutRendersOnlyLiveNormalSlotsAndUsesOutlineForIdle() throws {
        let working = try SlotStatus.decode(from: """
        {"slot":2,"state":"working","source":"hermes","model_name":"openai/gpt-5.5"}
        """.data(using: .utf8)!)
        let idle = try SlotStatus.decode(from: """
        {"slot":6,"state":"idle","source":"hermes","model_name":"anthropic/claude-opus-4.8"}
        """.data(using: .utf8)!)

        let layout = StatusIndicatorLayout(
            hermesStatuses: [working, idle],
            agentGroup: AgentRingGroup(statuses: [])
        )

        XCTAssertEqual(layout.normalSlotIndicators.map(\.slot), [2, 6])
        XCTAssertEqual(layout.normalSlotIndicators.map(\.style), [.filled, .outline])
        XCTAssertEqual(layout.normalSlotIndicators.map(\.state), [.working, .idle])
        XCTAssertEqual(layout.filledDotCount, 1)
    }

    func testAgentLightsSharedDirectoryIgnoresProfileScopedHermesHome() {
        let directory = AgentLightsPath.sharedDirectory(userHomeDirectory: "/Users/matt")

        XCTAssertEqual(directory.path, "/Users/matt/.hermes/agent-lights/v2")
    }

    private func acquiredFD(_ outcome: AppInstanceLock.Outcome) throws -> Int32 {
        guard case .acquired(let fd) = outcome else {
            XCTFail("expected the lock, got \(outcome)")
            throw CocoaError(.featureUnsupported)
        }
        return fd
    }

    func testAppInstanceLockAdmitsOneAppAtATimeAndFreesOnRelease() throws {
        let directory = try makeSlotDirectory()
        let first = try acquiredFD(AppInstanceLock.acquire(directory: directory))
        // flock is per open file description: a second open in this process
        // contends exactly like a second app would.
        XCTAssertEqual(AppInstanceLock.acquire(directory: directory), .heldByAnotherApp)
        close(first)
        close(try acquiredFD(AppInstanceLock.acquire(directory: directory)))
    }

    func testAppInstanceLockReportsFailureSeparatelyFromContention() {
        guard case .failed(let reason) = AppInstanceLock.acquire(directory: URL(fileURLWithPath: "/dev/null/nope")) else {
            return XCTFail("an unusable directory must be reported as a failure, not contention")
        }
        XCTAssertTrue(reason.contains("/dev/null/nope"))
    }

    private func makeBundle(_ name: String, in directory: URL) throws -> (bundle: URL, executable: URL) {
        let bundle = directory.appendingPathComponent(name)
        let macOS = bundle.appendingPathComponent("Contents/MacOS")
        try FileManager.default.createDirectory(at: macOS, withIntermediateDirectories: true)
        let executable = macOS.appendingPathComponent("AgentLightsMenuBar")
        try Data("bin".utf8).write(to: executable)
        return (bundle, executable)
    }

    func testOrdinaryVersionedBundleIsAccepted() throws {
        let directory = try makeSlotDirectory()
        let v2 = try makeBundle(AppBundleIdentity.bundleName, in: directory)
        _ = try makeBundle(AppBundleIdentity.legacyBundleName, in: directory)

        XCTAssertNil(AppBundleIdentity.rejection(bundleURL: v2.bundle, executableURL: v2.executable, environment: [:]))
    }

    func testVersionedNameSymlinkedToTheLegacyBundleIsRejected() throws {
        let directory = try makeSlotDirectory()
        let legacy = try makeBundle(AppBundleIdentity.legacyBundleName, in: directory)
        let alias = directory.appendingPathComponent(AppBundleIdentity.bundleName)
        try FileManager.default.createSymbolicLink(at: alias, withDestinationURL: legacy.bundle)

        let reason = AppBundleIdentity.rejection(
            bundleURL: alias,
            executableURL: alias.appendingPathComponent("Contents/MacOS/AgentLightsMenuBar"),
            environment: [:]
        )
        XCTAssertNotNil(reason)
    }

    func testLegacyNameSymlinkedToTheVersionedBundleIsRejected() throws {
        let directory = try makeSlotDirectory()
        let v2 = try makeBundle(AppBundleIdentity.bundleName, in: directory)
        try FileManager.default.createSymbolicLink(
            at: directory.appendingPathComponent(AppBundleIdentity.legacyBundleName),
            withDestinationURL: v2.bundle
        )

        let reason = try XCTUnwrap(AppBundleIdentity.rejection(bundleURL: v2.bundle, executableURL: v2.executable, environment: [:]))
        XCTAssertTrue(reason.contains("resolves into this bundle"), reason)
    }

    func testLegacyBundleInternalSymlinksIntoTheVersionedBundleAreRejected() throws {
        // Bundle roots stay distinct, but the path a pre-v2 launcher writes
        // reaches the v2 executable through a symlink deeper in the tree.
        for linkLevel in ["Contents", "Contents/MacOS", "Contents/MacOS/AgentLightsMenuBar"] {
            let directory = try makeSlotDirectory()
            let v2 = try makeBundle(AppBundleIdentity.bundleName, in: directory)
            let legacyLink = directory.appendingPathComponent("\(AppBundleIdentity.legacyBundleName)/\(linkLevel)")
            try FileManager.default.createDirectory(at: legacyLink.deletingLastPathComponent(), withIntermediateDirectories: true)
            try FileManager.default.createSymbolicLink(
                at: legacyLink,
                withDestinationURL: v2.bundle.appendingPathComponent(linkLevel)
            )

            let reason = AppBundleIdentity.rejection(bundleURL: v2.bundle, executableURL: v2.executable, environment: [:])
            XCTAssertNotNil(reason, "legacy \(linkLevel) symlink must be rejected")
            XCTAssertTrue(reason?.contains("resolves into this bundle") == true, reason ?? "")
        }
    }

    func testRejectionUsesCanonicalPathsUnderPrivateVar() throws {
        // FileManager.temporaryDirectory lives under /var -> /private/var;
        // the reverse whole-bundle alias must be caught regardless.
        let directory = try makeSlotDirectory()
        let v2 = try makeBundle(AppBundleIdentity.bundleName, in: directory)
        try FileManager.default.createSymbolicLink(
            at: directory.appendingPathComponent(AppBundleIdentity.legacyBundleName),
            withDestinationURL: URL(fileURLWithPath: AppBundleIdentity.canonicalPath(v2.bundle.path)!)
        )
        let viaVar = URL(fileURLWithPath: v2.bundle.path.replacingOccurrences(of: "/private/var/", with: "/var/"))

        XCTAssertNotNil(AppBundleIdentity.rejection(bundleURL: viaVar, executableURL: viaVar.appendingPathComponent("Contents/MacOS/AgentLightsMenuBar"), environment: [:]))
        XCTAssertNotNil(AppBundleIdentity.rejection(bundleURL: v2.bundle, executableURL: v2.executable, environment: [:]))
    }

    func testRejectionFailsClosedWithoutAnInspectableExecutable() throws {
        let directory = try makeSlotDirectory()
        let v2 = try makeBundle(AppBundleIdentity.bundleName, in: directory)

        XCTAssertNotNil(AppBundleIdentity.rejection(bundleURL: v2.bundle, executableURL: nil, environment: [:]))
        try FileManager.default.removeItem(at: v2.executable)
        XCTAssertNotNil(AppBundleIdentity.rejection(bundleURL: v2.bundle, executableURL: v2.executable, environment: [:]))
    }

    func testHardLinkedExecutableIsRejected() throws {
        let directory = try makeSlotDirectory()
        let v2 = try makeBundle(AppBundleIdentity.bundleName, in: directory)
        let legacyMacOS = directory.appendingPathComponent("\(AppBundleIdentity.legacyBundleName)/Contents/MacOS")
        try FileManager.default.createDirectory(at: legacyMacOS, withIntermediateDirectories: true)
        try FileManager.default.linkItem(at: v2.executable, to: legacyMacOS.appendingPathComponent("AgentLightsMenuBar"))

        let reason = try XCTUnwrap(AppBundleIdentity.rejection(bundleURL: v2.bundle, executableURL: v2.executable, environment: [:]))
        XCTAssertTrue(reason.contains("hard links"))
    }

    func testAppRunsOnlyFromItsVersionedBundle() throws {
        let directory = try makeSlotDirectory()
        let v2 = try makeBundle(AppBundleIdentity.bundleName, in: directory)
        let legacy = try makeBundle(AppBundleIdentity.legacyBundleName, in: directory)
        let bare = directory.appendingPathComponent("debug")

        XCTAssertEqual(AppBundleIdentity.bundleName, "AgentLightsMenuBar-v2.app")
        XCTAssertNil(AppBundleIdentity.rejection(bundleURL: v2.bundle.appendingPathComponent("."), executableURL: v2.executable, environment: [:]))
        // The current binary packaged by a pre-v2 launcher must not run.
        XCTAssertNotNil(AppBundleIdentity.rejection(bundleURL: legacy.bundle, executableURL: legacy.executable, environment: [:]))
        XCTAssertNotNil(AppBundleIdentity.rejection(bundleURL: bare, executableURL: nil, environment: [:]))
        XCTAssertNil(AppBundleIdentity.rejection(bundleURL: bare, executableURL: nil, environment: ["AGENT_LIGHTS_ALLOW_ANY_BUNDLE": "1"]))
    }

    func testAppHeartbeatIsWrittenAndOnlyRemovedByItsOwner() throws {
        let directory = try makeSlotDirectory()
        try AppHeartbeat.write(directory: directory, pid: 4242, processStartTime: 1234.5)
        let url = directory.appendingPathComponent(AppHeartbeat.fileName)
        let object = try XCTUnwrap(JSONSerialization.jsonObject(with: Data(contentsOf: url)) as? [String: Any])
        XCTAssertEqual(object["pid"] as? Int, 4242)
        XCTAssertEqual(object["process_start_time"] as? Double, 1234.5)

        // ensure() leaves our own heartbeat alone and repairs anyone else's.
        XCTAssertTrue(AppHeartbeat.ensure(directory: directory, pid: 4242, processStartTime: 1234.5))
        try #"{"pid":7,"pool":"v2","process_start_time":1.0}"#.write(to: url, atomically: true, encoding: .utf8)
        XCTAssertTrue(AppHeartbeat.ensure(directory: directory, pid: 4242, processStartTime: 1234.5))
        let repaired = try XCTUnwrap(JSONSerialization.jsonObject(with: Data(contentsOf: url)) as? [String: Any])
        XCTAssertEqual(repaired["pid"] as? Int, 4242)
        try FileManager.default.removeItem(at: url)
        XCTAssertTrue(AppHeartbeat.ensure(directory: directory, pid: 4242, processStartTime: 1234.5))
        XCTAssertFalse(AppHeartbeat.ensure(directory: URL(fileURLWithPath: "/dev/null/nope"), pid: 4242, processStartTime: nil))

        // Same pid but a different start time (recycled pid): repaired too.
        try #"{"pid":4242,"pool":"v2","process_start_time":1.0}"#.write(to: url, atomically: true, encoding: .utf8)
        XCTAssertTrue(AppHeartbeat.ensure(directory: directory, pid: 4242, processStartTime: 1234.5))
        let restamped = try XCTUnwrap(JSONSerialization.jsonObject(with: Data(contentsOf: url)) as? [String: Any])
        XCTAssertEqual(restamped["process_start_time"] as? Double, 1234.5)

        AppHeartbeat.removeIfOwned(directory: directory, pid: 9999)
        XCTAssertTrue(FileManager.default.fileExists(atPath: url.path))
        AppHeartbeat.removeIfOwned(directory: directory, pid: 4242)
        XCTAssertFalse(FileManager.default.fileExists(atPath: url.path))
    }
}
