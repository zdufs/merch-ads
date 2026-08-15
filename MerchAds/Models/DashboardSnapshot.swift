import Foundation
import Observation

struct DashboardDelta: Equatable, Sendable {
    enum Unit: Equatable, Sendable {
        case relativePercent
        case percentagePoints
    }

    enum Tone: Equatable, Sendable {
        case positive
        case negative
        case neutral
    }

    let value: Double
    let unit: Unit
    let tone: Tone

    var displayText: String {
        switch unit {
        case .relativePercent:
            let sign = value > 0 ? "+" : ""
            return "\(sign)\(String(format: "%.1f", value * 100))% vs prior 30d, est."
        case .percentagePoints:
            let sign = value > 0 ? "+" : ""
            return "\(sign)\(String(format: "%.1f", value * 100)) pp vs prior 30d, est."
        }
    }

    var symbol: String {
        if value > 0 { return "arrow.up.right" }
        if value < 0 { return "arrow.down.right" }
        return "arrow.right"
    }
}

struct DashboardWindowTotals: Equatable, Sendable {
    let spend: Double
    let sales: Double
    let orders: Int

    var acos: Double? {
        sales > 0 ? spend / sales : nil
    }
}

struct DashboardDeltas: Equatable, Sendable {
    let current: DashboardWindowTotals
    let previous: DashboardWindowTotals
    let spend: DashboardDelta?
    let sales: DashboardDelta?
    let orders: DashboardDelta?
    let acos: DashboardDelta?
}

enum DashboardDeltaCalculator {
    /// Anchors both windows to the newest banked date. Any absent calendar day
    /// in the complete 60-day span suppresses every delta.
    static func compute(days: [DailyDay], calendar: Calendar = .current) -> DashboardDeltas? {
        let dated = days.compactMap { day -> (Date, DailyDay)? in
            guard let date = Format.date(day.date) else { return nil }
            return (calendar.startOfDay(for: date), day)
        }
        guard let latest = dated.map(\.0).max() else { return nil }
        let byDate = Dictionary(dated.map { (calendar.startOfDay(for: $0.0), $0.1) },
                                uniquingKeysWith: { _, newest in newest })
        let expectedDates = (0..<60).compactMap {
            calendar.date(byAdding: .day, value: -$0, to: latest).map(calendar.startOfDay(for:))
        }
        guard expectedDates.count == 60,
              expectedDates.allSatisfy({ byDate[$0] != nil }) else { return nil }

        let currentDays = expectedDates.prefix(30).compactMap { byDate[$0] }
        let previousDays = expectedDates.dropFirst(30).compactMap { byDate[$0] }
        guard currentDays.count == 30, previousDays.count == 30 else { return nil }
        let current = totals(currentDays)
        let previous = totals(previousDays)
        return DashboardDeltas(
            current: current,
            previous: previous,
            spend: relative(current.spend, previous.spend, lowerIsBetter: true),
            sales: relative(current.sales, previous.sales, lowerIsBetter: false),
            orders: relative(Double(current.orders), Double(previous.orders), lowerIsBetter: false),
            acos: difference(current.acos, previous.acos, lowerIsBetter: true))
    }

    private static func totals(_ days: [DailyDay]) -> DashboardWindowTotals {
        DashboardWindowTotals(
            spend: days.reduce(0) { $0 + $1.spend },
            sales: days.reduce(0) { $0 + $1.sales },
            orders: days.reduce(0) { $0 + $1.orders })
    }

    private static func relative(_ current: Double, _ previous: Double,
                                 lowerIsBetter: Bool) -> DashboardDelta? {
        guard previous != 0 else { return nil }
        let value = (current - previous) / abs(previous)
        return DashboardDelta(value: value, unit: .relativePercent,
                              tone: tone(value, lowerIsBetter: lowerIsBetter))
    }

    private static func difference(_ current: Double?, _ previous: Double?,
                                   lowerIsBetter: Bool) -> DashboardDelta? {
        guard let current, let previous else { return nil }
        let value = current - previous
        return DashboardDelta(value: value, unit: .percentagePoints,
                              tone: tone(value, lowerIsBetter: lowerIsBetter))
    }

    private static func tone(_ value: Double, lowerIsBetter: Bool) -> DashboardDelta.Tone {
        guard value != 0 else { return .neutral }
        let improved = lowerIsBetter ? value < 0 : value > 0
        return improved ? .positive : .negative
    }
}

struct DashboardSection<Value> {
    var value: Value?
    var isLoading = false
    var error: String?
    var fetchedAt: Date?
    var dataAsOf: String?

    var hasLastGoodValue: Bool { value != nil }
}

struct DashboardSnapshot {
    let market: String
    var metrics = DashboardSection<MetricsResponse>()
    var profit = DashboardSection<ProfitResponse>()
    var periods = DashboardSection<PeriodsResponse>()
    var daily = DashboardSection<DailyResponse>()
    var monthly = DashboardSection<MonthlyResponse>()
    var alerts = DashboardSection<AlertsResponse>()
    var killList = DashboardSection<KillListResponse>()
    var health = DashboardSection<HealthResponse>()

    mutating func markLoading() {
        metrics.isLoading = true
        profit.isLoading = true
        periods.isLoading = true
        daily.isLoading = true
        monthly.isLoading = true
        alerts.isLoading = true
        killList.isLoading = true
        health.isLoading = true
        metrics.error = nil
        profit.error = nil
        periods.error = nil
        daily.error = nil
        monthly.error = nil
        alerts.error = nil
        killList.error = nil
        health.error = nil
    }
}

@MainActor
@Observable
final class DashboardSnapshotCoordinator {
    private(set) var snapshot: DashboardSnapshot?
    private var cache: [String: DashboardSnapshot] = [:]
    private var generation = UUID()

    private enum Event {
        case metrics(Result<MetricsResponse, Error>, Date)
        case profit(Result<ProfitResponse, Error>, Date)
        case periods(Result<PeriodsResponse, Error>, Date)
        case daily(Result<DailyResponse, Error>, Date)
        case monthly(Result<MonthlyResponse, Error>, Date)
        case alerts(Result<AlertsResponse, Error>, Date)
        case killList(Result<KillListResponse, Error>, Date)
        case health(Result<HealthResponse, Error>, Date)
    }

    func load(market: String, bridge: PythonBridge, preloadedHealth: HealthResponse? = nil) async {
        let request = UUID()
        generation = request
        var working = cache[market] ?? DashboardSnapshot(market: market)
        working.markLoading()
        snapshot = working

        if let preloadedHealth {
            // AppState.refresh() already fetched health on this same trigger —
            // apply it directly instead of scanning every market DB again.
            apply(.health(.success(preloadedHealth), Date()), market: market, to: &working)
            cache[market] = working
            snapshot = working
        }

        await withTaskGroup(of: Event.self) { group in
            group.addTask {
                do { return .metrics(.success(try await bridge.call(MetricsResponse.self, ["metrics"], market: market)), Date()) }
                catch { return .metrics(.failure(error), Date()) }
            }
            group.addTask {
                do { return .profit(.success(try await bridge.call(ProfitResponse.self, ["profit"], market: market)), Date()) }
                catch { return .profit(.failure(error), Date()) }
            }
            group.addTask {
                do { return .periods(.success(try await bridge.call(PeriodsResponse.self, ["periods"], market: market)), Date()) }
                catch { return .periods(.failure(error), Date()) }
            }
            group.addTask {
                do { return .daily(.success(try await bridge.call(DailyResponse.self, ["daily", "--days", "60"], market: market)), Date()) }
                catch { return .daily(.failure(error), Date()) }
            }
            group.addTask {
                do { return .monthly(.success(try await bridge.call(MonthlyResponse.self, ["monthly"], market: market)), Date()) }
                catch { return .monthly(.failure(error), Date()) }
            }
            group.addTask {
                do { return .alerts(.success(try await bridge.call(AlertsResponse.self, ["alerts"], market: market)), Date()) }
                catch { return .alerts(.failure(error), Date()) }
            }
            group.addTask {
                do { return .killList(.success(try await bridge.call(KillListResponse.self, ["killlist"], market: market)), Date()) }
                catch { return .killList(.failure(error), Date()) }
            }
            if preloadedHealth == nil {
                group.addTask {
                    do { return .health(.success(try await bridge.call(HealthResponse.self, ["health"])), Date()) }
                    catch { return .health(.failure(error), Date()) }
                }
            }

            for await event in group {
                guard !Task.isCancelled, generation == request else {
                    group.cancelAll()
                    return
                }
                apply(event, market: market, to: &working)
                cache[market] = working
                snapshot = working
            }
        }
    }

    func failToStart(market: String, error: Error) {
        var working = cache[market] ?? DashboardSnapshot(market: market)
        let message = error.localizedDescription
        working.metrics.error = message
        working.profit.error = message
        working.periods.error = message
        working.daily.error = message
        working.monthly.error = message
        working.alerts.error = message
        working.killList.error = message
        working.health.error = message
        snapshot = working
    }

    private func apply(_ event: Event, market: String, to snapshot: inout DashboardSnapshot) {
        switch event {
        case .metrics(let result, let fetchedAt):
            apply(result, fetchedAt: fetchedAt, dataAsOf: { $0.trailing30?.asOf }, to: &snapshot.metrics)
        case .profit(let result, let fetchedAt):
            // The dashboard card shows the CURRENT MONTH, so its freshness is the
            // end of the month-to-date window, not the trailing-30 snapshot date.
            // Fall back to `asOf` when the engine banked no month data yet.
            apply(result, fetchedAt: fetchedAt,
                  dataAsOf: { $0.mtd.flatMap { mtd in
                      mtd.window.components(separatedBy: "→").last
                  } ?? $0.asOf },
                  to: &snapshot.profit)
        case .periods(let result, let fetchedAt):
            apply(result, fetchedAt: fetchedAt,
                  dataAsOf: { $0.coverage?.lastDay }, to: &snapshot.periods)
        case .daily(let result, let fetchedAt):
            apply(result, fetchedAt: fetchedAt, dataAsOf: { $0.days.map(\.date).max() }, to: &snapshot.daily)
        case .monthly(let result, let fetchedAt):
            apply(result, fetchedAt: fetchedAt, dataAsOf: { $0.months.map(\.month).max() }, to: &snapshot.monthly)
        case .alerts(let result, let fetchedAt):
            apply(result, fetchedAt: fetchedAt, dataAsOf: { _ in nil }, to: &snapshot.alerts)
        case .killList(let result, let fetchedAt):
            apply(result, fetchedAt: fetchedAt, dataAsOf: { _ in nil }, to: &snapshot.killList)
        case .health(let result, let fetchedAt):
            apply(result, fetchedAt: fetchedAt,
                  dataAsOf: { $0.markets.first(where: { $0.market == market })?.latestData },
                  to: &snapshot.health)
        }
    }

    private func apply<Value>(_ result: Result<Value, Error>, fetchedAt: Date,
                              dataAsOf: (Value) -> String?,
                              to section: inout DashboardSection<Value>) {
        section.isLoading = false
        section.fetchedAt = fetchedAt
        switch result {
        case .success(let value):
            section.value = value
            section.dataAsOf = dataAsOf(value)
            section.error = nil
        case .failure(let error):
            section.error = error.localizedDescription
        }
    }
}
