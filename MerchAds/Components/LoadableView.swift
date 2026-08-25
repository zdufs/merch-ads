import SwiftUI

struct LoadableView<Content: View>: View {
    let isLoading: Bool
    let error: String?
    let isEmpty: Bool
    let loadingTitle: String
    let emptyTitle: String
    let emptyDescription: String
    let systemImage: String
    let retry: () -> Void
    @ViewBuilder let content: Content

    init(isLoading: Bool, error: String?, isEmpty: Bool,
         loadingTitle: String, emptyTitle: String, emptyDescription: String,
         systemImage: String, retry: @escaping () -> Void,
         @ViewBuilder content: () -> Content) {
        self.isLoading = isLoading
        self.error = error
        self.isEmpty = isEmpty
        self.loadingTitle = loadingTitle
        self.emptyTitle = emptyTitle
        self.emptyDescription = emptyDescription
        self.systemImage = systemImage
        self.retry = retry
        self.content = content()
    }

    // The content is ALWAYS rendered; loading / error / empty are drawn as an
    // opaque overlay LAYER over it. On macOS 26 an if/else that toggles a greedy
    // Table's presence (which this view's old body did) renders the whole detail
    // as empty placeholder rows and blanks the sidebar — see
    // CrossPurchaseView's doc comment. Drawing the states as a layer keeps the
    // table permanently in the tree, so every screen that wraps a Table in
    // LoadableView is safe from that failure without changing its call site.
    var body: some View {
        content
            .overlay { stateLayer }
    }

    @ViewBuilder
    private var stateLayer: some View {
        if isLoading {
            ProgressView(loadingTitle)
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                .background(Theme.Colors.canvas)
        } else if let error {
            ContentUnavailableView {
                Label("\(emptyTitle) unavailable", systemImage: systemImage)
            } description: {
                Text(error)
            } actions: {
                Button("Retry", action: retry)
            }
            .topAlignedEmptyState()
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .background(Theme.Colors.canvas)
        } else if isEmpty {
            ContentUnavailableView {
                Label(emptyTitle, systemImage: systemImage)
            } description: {
                Text(emptyDescription)
            }
            .topAlignedEmptyState()
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .background(Theme.Colors.canvas)
        }
        // else: nothing drawn — the content shows through.
    }
}
