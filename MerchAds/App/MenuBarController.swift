import SwiftUI
import AppKit

/// The menu-bar status item, owned in AppKit instead of via SwiftUI's
/// `MenuBarExtra`.
///
/// `MenuBarExtra` re-sets its `NSStatusBarButton` image on every scene update
/// (`MenuBarExtraController.updateButton` → `NSStatusBarButton.setImage`). On
/// macOS 26 that image swap throws inside AppKit's window-constraint layout
/// pass (`-[NSWindow _postWindowNeedsUpdateConstraints]`) whenever the swap
/// lands in the middle of a layout commit — for example the commit a screen
/// navigation kicks off — and the thrown exception is fatal (SIGTRAP). Making
/// the SwiftUI icon static only narrowed the window; the crash still fired
/// (two separate crash reports on 2026-08-11, one from a build predating any
/// dashboard change).
///
/// Owning the `NSStatusItem` ourselves means we set the button image exactly
/// once, at install, and never touch it again. There is no image swap for a
/// layout pass to choke on, so the crash cannot happen. The popover content is
/// still the same SwiftUI `MenuBarStatusView` — only the button lifecycle
/// moved to AppKit.
@MainActor
final class MenuBarController: NSObject {
    private var statusItem: NSStatusItem?
    private let popover = NSPopover()
    private var attached = false

    /// Build the popover once, with the shared app state in its environment.
    func attach(appState: AppState) {
        guard !attached else { return }
        attached = true
        let host = NSHostingController(rootView: MenuBarStatusView().environment(appState))
        host.sizingOptions = [.preferredContentSize]   // popover follows the SwiftUI content
        popover.behavior = .transient
        popover.contentViewController = host
    }

    /// Insert or remove the status item to match the Settings toggle. Idempotent.
    func setVisible(_ visible: Bool) {
        if visible {
            guard statusItem == nil else { return }
            let item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
            if let button = item.button {
                button.image = MenuBarController.risingBarsImage()   // set ONCE, never again
                button.setAccessibilityLabel("Merch Ads")
                button.target = self
                button.action = #selector(togglePopover)
            }
            statusItem = item
        } else {
            if let item = statusItem { NSStatusBar.system.removeStatusItem(item) }
            statusItem = nil
        }
    }

    /// The status-bar glyph: four rising bars, matching the app icon. Drawn once
    /// with a `drawingHandler` so it re-renders crisply at any device scale, and
    /// `isTemplate` so the menu bar tints it black/white for light/dark itself.
    private static func risingBarsImage() -> NSImage {
        let size = NSSize(width: 17, height: 13)
        let image = NSImage(size: size, flipped: false) { rect in
            let n = 4
            let gap = rect.width * 0.085
            let barW = (rect.width - CGFloat(n - 1) * gap) / CGFloat(n)
            let scale: [CGFloat] = [0.42, 0.60, 0.80, 1.0]   // ascending
            NSColor.black.setFill()
            for i in 0..<n {
                let x = rect.minX + CGFloat(i) * (barW + gap)
                let h = rect.height * scale[i]
                let r = min(barW * 0.32, 1.5)
                NSBezierPath(roundedRect: CGRect(x: x, y: rect.minY, width: barW, height: h),
                             xRadius: r, yRadius: r).fill()
            }
            return true
        }
        image.isTemplate = true
        return image
    }

    @objc private func togglePopover() {
        guard let button = statusItem?.button else { return }
        if popover.isShown {
            popover.performClose(nil)
        } else {
            NSApp.activate(ignoringOtherApps: true)
            popover.show(relativeTo: button.bounds, of: button, preferredEdge: .minY)
        }
    }

    /// The main window's title, and the PREFIX its on-screen title starts with.
    ///
    /// The window carries a subtitle now (the app version), and macOS shows the
    /// two joined — "Merch Ads – v0.2.5" is what the accessibility layer and the
    /// title bar report. Matching the full string exactly would therefore break
    /// the moment the version changed, and silently: no crash, just an "Open
    /// Merch Ads" button that stops doing anything.
    /// `nonisolated` because it is a constant string, not state. The class is
    /// @MainActor for the status item; a title anyone may compare against should
    /// not drag callers onto the main actor to read it.
    nonisolated static let mainWindowTitle = "Merch Ads"

    /// True when this window is the app's main window.
    ///
    /// Prefix, not equality, so a subtitle can be added or changed without
    /// anyone remembering this function exists.
    static func isMainWindow(_ window: NSWindow) -> Bool {
        window.canBecomeMain && window.title.hasPrefix(mainWindowTitle)
    }

    /// Bring an existing main window to the front — the popover's "Open Merch
    /// Ads" button. `openWindow` reopens a fully-closed window; this covers the
    /// common minimized / behind-another-app cases.
    static func bringMainWindowFront() {
        for window in NSApp.windows where isMainWindow(window) {
            window.deminiaturize(nil)
            window.makeKeyAndOrderFront(nil)
            return
        }
    }
}

/// Hosts the AppKit status item for the whole run. SwiftUI still drives the app
/// lifecycle; the delegate just gives us a long-lived object to hang the
/// `NSStatusItem` off.
@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {
    let menuBar = MenuBarController()
}
