import SwiftUI

@main
struct MerchAdsApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate
    @State private var appState = AppState()
    @AppStorage(AppSettings.showMenuBarKey) private var showMenuBar = true

    init() {
        // Warm the login-shell python3 lookup off the main thread. The lazy
        // static runs `/bin/zsh -lc` with waitUntilExit — 100-800ms under a
        // heavy .zshrc — and used to fire inside the first @MainActor
        // makeBridge() (or SettingsView.body), freezing the UI on first load.
        // Lazy statics are thread-safe, so touching it here caches the result
        // before any view needs it.
        Task.detached(priority: .utility) {
            _ = PythonBridge.loginShellPython
        }
    }

    var body: some Scene {
        Window("Merch Ads", id: "main") {
            if AppSettings.isUnitTesting {
                Color.clear
                    .frame(width: 1, height: 1)
            } else {
                ContentView()
                    .environment(appState)
                    .task { await appState.bootstrap() }
                    .onAppear {
                        // The menu-bar status item is AppKit-owned (see
                        // MenuBarController) — attach the shared state once, then
                        // show/hide it to match the Settings toggle.
                        appDelegate.menuBar.attach(appState: appState)
                        appDelegate.menuBar.setVisible(showMenuBar)
                    }
                    .onChange(of: showMenuBar) { _, visible in
                        appDelegate.menuBar.setVisible(visible)
                    }
            }
        }
        .defaultSize(width: 1150, height: 760)
        .windowToolbarStyle(.unified)
        .windowResizability(.contentMinSize)
        .commands {
            // ⌘, opens the in-app Settings tab, not a separate window. There is
            // no `Settings` scene any more — Settings is a sidebar screen, so the
            // standard App-menu item routes there and brings the main window up.
            CommandGroup(replacing: .appSettings) {
                SettingsMenuCommand(appState: appState)
            }
            CommandGroup(after: .toolbar) {
                Button("Refresh") {
                    Task { await appState.refresh() }
                }
                .keyboardShortcut("r", modifiers: .command)
                Button("Command Palette…") {
                    appState.showingCommandPalette = true
                }
                .keyboardShortcut("k", modifiers: .command)
            }
            CommandMenu("Market") {
                MarketCommands(appState: appState)
            }
        }
        // The menu-bar status item is deliberately NOT a SwiftUI MenuBarExtra —
        // MenuBarExtra crashed on macOS 26 when it re-set its status-button
        // image during a layout commit. It is owned by AppKit now; see
        // MenuBarController and the Window scene's onAppear above.
    }
}

/// The App-menu "Settings…" item (⌘,). Replaces the old floating Settings
/// window: it opens/raises the main window and selects the in-app Settings tab.
private struct SettingsMenuCommand: View {
    var appState: AppState
    @Environment(\.openWindow) private var openWindow

    var body: some View {
        Button("Settings…") {
            openWindow(id: "main")
            NSApp.activate(ignoringOtherApps: true)
            MenuBarController.bringMainWindowFront()
            appState.navigate(to: .screen(.settings))
        }
        .keyboardShortcut(",", modifiers: .command)
    }
}

/// ⌘1…⌘9 jump straight to a market, mirroring the toolbar picker. The market
/// list comes from the engine and already grew from 6 to 7 with USKDP, so
/// anything past the ninth is listed without a shortcut — `Character("10")`
/// would trap at launch.
private struct MarketCommands: View {
    var appState: AppState

    private static let shortcutDigits = Array("123456789")

    var body: some View {
        ForEach(Array(appState.markets.enumerated()), id: \.element.code) { index, market in
            let button = Button {
                appState.selectedMarket = market.code
            } label: {
                if market.code == appState.selectedMarket {
                    Text("\(market.code) ✓")
                } else {
                    Text(market.code)
                }
            }
            .disabled(!market.hasData)

            if index < Self.shortcutDigits.count {
                button.keyboardShortcut(KeyEquivalent(Self.shortcutDigits[index]), modifiers: .command)
            } else {
                button
            }
        }
    }
}
