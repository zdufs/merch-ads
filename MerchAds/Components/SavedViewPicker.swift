import SwiftUI

struct SavedViewPicker<Row: Identifiable>: View {
    let tableID: String
    let filters: [String: String]
    let sortFields: [String: KeyPathComparator<Row>]
    let defaultSort: [KeyPathComparator<Row>]
    @Binding var sortOrder: [KeyPathComparator<Row>]
    @Binding var columns: TableColumnCustomization<Row>
    let applyFilters: ([String: String]) -> Void

    @State private var views: [SavedView] = []
    @State private var showingSave = false
    @State private var name = ""

    var body: some View {
        Menu {
            if views.isEmpty {
                Text("No saved views")
            } else {
                ForEach(views) { view in
                    Button(view.name) { apply(view) }
                }
                Divider()
                Menu("Delete") {
                    ForEach(views) { view in
                        Button(view.name, role: .destructive) {
                            SavedViewStore.delete(view)
                            reload()
                        }
                    }
                }
            }
            Divider()
            Button("Save Current View…") { showingSave = true }
            Button("Reset to Default") { reset() }
        } label: {
            Label("Views", systemImage: "bookmark")
        }
        .menuStyle(.button)
        .buttonStyle(.borderless)
        .fixedSize()
        .help("Save or restore filters, sort, and visible columns")
        .onAppear(perform: reload)
        .alert("Save View", isPresented: $showingSave) {
            TextField("Name", text: $name)
            Button("Cancel", role: .cancel) { name = "" }
            Button("Save") { save() }
                .disabled(name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
        } message: {
            Text("Saves this table's current filters, sort, order, and visible columns.")
        }
    }

    private func save() {
        let view = SavedView(
            tableID: tableID, name: name, filters: filters,
            sortDescriptors: SortPrefs.descriptors(sortOrder, fields: sortFields),
            columnCustomization: ColumnPrefs.encode(columns))
        SavedViewStore.save(view)
        name = ""
        reload()
    }

    private func apply(_ view: SavedView) {
        guard view.isValid(for: tableID) else { return }
        applyFilters(view.filters)
        sortOrder = SortPrefs.comparators(view.sortDescriptors, fields: sortFields,
                                          fallback: defaultSort)
        if let data = view.columnCustomization,
           let decoded = ColumnPrefs.decode(data, as: Row.self) {
            columns = decoded
        } else {
            columns = TableColumnCustomization<Row>()
        }
    }

    private func reset() {
        applyFilters([:])
        sortOrder = defaultSort
        columns = TableColumnCustomization<Row>()
    }

    private func reload() { views = SavedViewStore.load(tableID: tableID) }
}
