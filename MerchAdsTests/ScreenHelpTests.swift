import XCTest
@testable import Merch_Ads

/// The help text is the only documentation the operator sees inside the app.
/// A screen with a stub entry (empty summary, no steps) is worse than no "?" at
/// all — it promises an explanation and gives nothing. These tests fail before
/// that ships.
final class ScreenHelpTests: XCTestCase {

    func testEveryScreenHasASummaryAndSteps() {
        for screen in Screen.allCases {
            let help = screen.help
            XCTAssertFalse(help.summary.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
                           "\(screen.rawValue) has no summary")
            XCTAssertFalse(help.source.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
                           "\(screen.rawValue) does not say where its numbers come from")
            XCTAssertFalse(help.steps.isEmpty, "\(screen.rawValue) has no instructions")
            for step in help.steps {
                XCTAssertFalse(step.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
                               "\(screen.rawValue) has an empty step")
            }
            for note in help.notes {
                XCTAssertFalse(note.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
                               "\(screen.rawValue) has an empty note")
            }
        }
    }

    /// The summary is the first thing read and it is not a tooltip. One clause
    /// is too thin to be worth a click.
    func testSummariesAreFullDescriptionsNotOneLiners() {
        for screen in Screen.allCases {
            XCTAssertGreaterThan(screen.help.summary.count, 60,
                                 "\(screen.rawValue) summary is too short to be a description")
        }
    }

    /// Every screen keeps its one-sentence hover blurb too — the "?" adds to it
    /// rather than replacing it.
    func testEveryScreenStillHasAHoverBlurb() {
        for screen in Screen.allCases {
            XCTAssertFalse(screen.blurb.isEmpty, "\(screen.rawValue) lost its sidebar tooltip")
        }
    }

    /// Screens where reading the numbers naively leads to a wrong money
    /// decision must say so out loud. These are the caveats the docs call out.
    func testTheScreensThatCanMisleadCarryTheirCaveat() {
        let required: [Screen: String] = [
            .halo: "UPPER BOUND",
            .profit: "modeled royalty",
            .dashboard: "Profit is modeled",
            .accumulatedAsins: "trailing 30 days",
            .approvals: "cannot be undone",
            .strategyBuilder: "permanent",
            .campaigns: "permanent",
            .killList: "normal result",
        ]
        for (screen, phrase) in required {
            let text = (screen.help.notes + [screen.help.summary, screen.help.source])
                .joined(separator: "\n")
            XCTAssertTrue(text.contains(phrase),
                          "\(screen.rawValue) help no longer mentions \"\(phrase)\"")
        }
    }
}
