import SwiftUI
import UniformTypeIdentifiers

// "Export any view to CSV" — a tiny document type + encoder shared by the
// table screens' Export buttons (used with .fileExporter).

struct CSVDocument: FileDocument {
    static let readableContentTypes: [UTType] = [.commaSeparatedText]

    var data: Data

    init(headers: [String], rows: [[String]]) {
        let lines = [headers.map(Self.quote).joined(separator: ",")]
            + rows.map { $0.map(Self.quote).joined(separator: ",") }
        data = Data(lines.joined(separator: "\n").utf8)
    }

    init(configuration: ReadConfiguration) throws {
        data = configuration.file.regularFileContents ?? Data()
    }

    func fileWrapper(configuration: WriteConfiguration) throws -> FileWrapper {
        FileWrapper(regularFileWithContents: data)
    }

    private static func quote(_ field: String) -> String {
        if field.contains(where: { $0 == "," || $0 == "\"" || $0 == "\n" }) {
            return "\"" + field.replacingOccurrences(of: "\"", with: "\"\"") + "\""
        }
        return field
    }
}

/// A standard toolbar-ish export button that drives a `.fileExporter`.
struct ExportButton: View {
    let filename: String
    let make: () -> CSVDocument

    @State private var exporting = false
    @State private var document: CSVDocument?

    var body: some View {
        Button {
            document = make()
            exporting = true
        } label: {
            Label("Export CSV", systemImage: "square.and.arrow.up")
        }
        .fileExporter(isPresented: $exporting,
                      document: document,
                      contentType: .commaSeparatedText,
                      defaultFilename: filename) { _ in }
        .help("Export this view as CSV")
    }
}
