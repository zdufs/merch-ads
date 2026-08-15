import SwiftUI

/// Which sub-tab of the Import screen is showing.
enum ImportSegment: String, CaseIterable {
    case newDesigns, sales, ads
    var label: String {
        switch self {
        case .newDesigns: "New Designs"
        case .sales: "Sales"
        case .ads: "Ads"
        }
    }
}

/// One Import tab for every droppable file. A segmented control switches
/// between the New Designs build workflow, the Sales sub-tab (the Merch sales
/// report — organic royalty) and the Ads sub-tab (the console monthly-history
/// export). Each child owns its drop zone; a file dropped on the wrong one is
/// handed back here, which flips the segment and loads it in the right child.
struct ImportHubView: View {
    @Environment(AppState.self) private var appState
    @AppStorage("import.segment") private var segmentRaw = ImportSegment.newDesigns.rawValue
    /// A file handed from one sub-tab to another, keyed to the segment it is
    /// meant for. Keying it (rather than a bare `URL?`) closes a race: without a
    /// target, a manual flip back to the ORIGINAL segment mid-import would hand
    /// the file to it too, and it would import a file meant for another child.
    /// With a target, only the intended child's `incomingFile` ever goes non-nil.
    @State private var handoff: Handoff?

    private struct Handoff: Equatable { let target: ImportSegment; let url: URL }

    private var segment: ImportSegment { ImportSegment(rawValue: segmentRaw) ?? .newDesigns }

    var body: some View {
        VStack(spacing: 0) {
            PageHeader(title: "Import", subtitle: appState.selectedMarket, help: .dataImport)
            Picker("Import section", selection: Binding(
                get: { segment },
                set: { segmentRaw = $0.rawValue })) {
                ForEach(ImportSegment.allCases, id: \.self) { Text($0.label).tag($0) }
            }
            .pickerStyle(.segmented)
            .labelsHidden()
            .padding(.horizontal, Layout.Spacing.lg)
            .padding(.vertical, Layout.Spacing.sm)
            Divider()
            switch segment {
            case .newDesigns:
                NewDesignsBuildView(
                    incomingFile: handoff?.target == .newDesigns ? handoff?.url : nil,
                    onConsumeIncoming: { if handoff?.target == .newDesigns { handoff = nil } },
                    onMisroutedDataCSV: { url in
                        // A data file dropped on New Designs goes to Sales by
                        // default — the more common of the two data shapes.
                        handoff = Handoff(target: .sales, url: url)
                        segmentRaw = ImportSegment.sales.rawValue
                    },
                    showsHeader: false)
            case .sales:
                SalesImportView(
                    incomingFile: handoff?.target == .sales ? handoff?.url : nil,
                    onConsumeIncoming: { if handoff?.target == .sales { handoff = nil } },
                    onMisroutedExport: { url in
                        handoff = Handoff(target: .newDesigns, url: url)
                        segmentRaw = ImportSegment.newDesigns.rawValue
                    },
                    showsHeader: false)
            case .ads:
                AdsImportView(
                    incomingFile: handoff?.target == .ads ? handoff?.url : nil,
                    onConsumeIncoming: { if handoff?.target == .ads { handoff = nil } },
                    onMisroutedExport: { url in
                        handoff = Handoff(target: .newDesigns, url: url)
                        segmentRaw = ImportSegment.newDesigns.rawValue
                    },
                    showsHeader: false)
            }
        }
        .background(Theme.Colors.canvas)
    }
}

#Preview {
    ImportHubView()
        .environment(AppState())
}
