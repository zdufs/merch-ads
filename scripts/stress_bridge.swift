import Foundation

// Stampede regression test for the worker pool: fire many CONCURRENT mixed
// requests at the SAME market worker and verify every response decodes as the
// type its request asked for. The old chained-Task design could cross-wire
// replies under exactly this load; the actor-queue design must never.

// Engine root: ADS_REPO if set, else the current working directory.
let root = URL(fileURLWithPath: ProcessInfo.processInfo.environment["ADS_REPO"]
    ?? FileManager.default.currentDirectoryPath)

@main
struct Stress {
    static func main() async {
        let bridge: PythonBridge
        do {
            bridge = try PythonBridge(engineRoot: root)
        } catch {
            print("bridge init failed: \(error)")
            exit(1)
        }

        var failures = 0
        // 3 rounds of a 5-way stampede on the US worker + the "" worker pair
        for round in 1...3 {
            await withTaskGroup(of: (String, Bool).self) { group in
                group.addTask {
                    ("metrics", (try? await bridge.call(MetricsResponse.self, ["metrics"], market: "US")) != nil)
                }
                group.addTask {
                    ("monthly", (try? await bridge.call(MonthlyResponse.self, ["monthly"], market: "US")) != nil)
                }
                group.addTask {
                    ("nudges", (try? await bridge.call(NudgesResponse.self, ["nudges"], market: "US")) != nil)
                }
                group.addTask {
                    ("killlist", (try? await bridge.call(KillListResponse.self, ["killlist"], market: "US")) != nil)
                }
                group.addTask {
                    ("campaigns", (try? await bridge.call(CampaignsResponse.self, ["campaigns"], market: "US")) != nil)
                }
                // the refresh() pair that races on the no-market worker
                group.addTask {
                    ("markets", (try? await bridge.call(MarketsResponse.self, ["markets"])) != nil)
                }
                group.addTask {
                    ("health", (try? await bridge.call(HealthResponse.self, ["health"])) != nil)
                }
                for await (name, ok) in group {
                    if !ok {
                        print("round \(round): \(name) FAILED to decode as its own type")
                        failures += 1
                    }
                }
            }
        }
        if failures > 0 {
            print("❌ \(failures) cross-wired/failed responses")
            exit(1)
        }
        print("✅ 21 concurrent requests, every response matched its request")
    }
}
