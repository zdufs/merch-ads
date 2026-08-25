import SwiftUI

/// Local rules library: a gallery of ready-made, validated economics-first rule
/// templates.
///
/// It used to be import-only — every card said "Import", dropped a disabled
/// copy into My Rules, and forgot it. Nothing on the card told you whether that
/// template was already running, so the Library was a catalogue you read once
/// and never came back to. Now each card carries the live state of its rule and
/// the switch that turns it on, and "Import…" stays for the case it is actually
/// for: taking a copy under your own name to edit.
///
/// Turning one on enables it in REVIEW mode. A review rule proposes into the
/// Approval Queue and writes nothing, which is the right default for a rule you
/// enabled with one click.
struct RulesLibraryView: View {
    /// The selected profile's advertiser family — "merch" | "kdp". The gallery
    /// shows only this family's templates, so picking KDP never surfaces the
    /// Merch tee rules. Saved rules are already scoped to the profile upstream.
    let kind: String
    /// The operator's saved rules, so a card can show whether it is live.
    let rules: [RuleSummary]
    /// (template, enabled) → error, or nil on success. Creates the rule the
    /// first time; after that it only flips the flag and never touches text the
    /// operator may have edited.
    let onSetEnabled: (RuleTemplate, Bool) async -> String?
    /// (template, "review" | "auto") → error, or nil on success.
    let onSetMode: (RuleTemplate, String) async -> String?
    /// (template, name, mode, enabled) → error, or nil on success.
    let onImport: (RuleTemplate, String, String, Bool) async -> String?
    /// Enable/disable a saved rule that isn't from the library (by name).
    let onSetRuleEnabled: (RuleSummary, Bool) async -> String?
    /// Set a saved rule's mode (review | auto).
    let onSetRuleMode: (RuleSummary, String) async -> String?

    @State private var detail: RuleTemplate?         // "Open" sheet
    @State private var configuring: RuleTemplate?    // "Import a copy" dialog
    @State private var busy: String?                 // template name mid-write
    @State private var error: String?

    private let columns = [GridItem(.adaptive(minimum: 340, maximum: 460),
                                    spacing: Layout.Spacing.md, alignment: .top)]

    /// The starter templates for the selected profile's family. Empty for KDP —
    /// every starter today is a Merch tee rule.
    private var templates: [RuleTemplate] { RuleTemplates.all(for: kind) }

    /// The saved rule this template maps to, matched by name.
    private func rule(for t: RuleTemplate) -> RuleSummary? {
        rules.first { $0.name == t.name }
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: Layout.Spacing.md) {
                if let error {
                    Label(error, systemImage: "exclamationmark.triangle.fill")
                        .font(.caption)
                        .foregroundStyle(Theme.Colors.critical)
                }
                if templates.isEmpty {
                    emptyTemplatesState
                } else {
                    header
                    LazyVGrid(columns: columns, spacing: Layout.Spacing.md) {
                        ForEach(templates) { card($0) }
                    }
                }
                if !otherRules.isEmpty { yourRulesSection }
            }
            .padding(Layout.Spacing.lg)
        }
        .background(Theme.Colors.canvas)
        .sheet(item: $detail) { t in
            TemplateDetailSheet(template: t) { configuring = t }
        }
        .sheet(item: $configuring) { t in
            RuleImportSheet(template: t, onImport: onImport)
        }
    }

    private var header: some View {
        let live = templates.filter { rule(for: $0)?.enabled == true }.count
        return VStack(alignment: .leading, spacing: 2) {
            Text(live == 0
                 ? "No library rule is on yet — flip a switch to enable one."
                 : "\(live) of \(templates.count) library rules are on.")
                .font(Typography.cardCaptionEmphasis)
                .foregroundStyle(Theme.Colors.textSecondary)
            Text("Enabling puts a rule in Review mode: it proposes into the Approval Queue and writes nothing until you approve. Switch it to Auto once you trust it.")
                .font(Typography.cardCaption)
                .foregroundStyle(Theme.Colors.muted)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    /// Shown when the selected profile has no starter templates — today that is
    /// KDP, whose rules stay separate from the Merch tee library. Any rules the
    /// operator has authored for this profile still appear under "Your rules".
    private var emptyTemplatesState: some View {
        VStack(alignment: .leading, spacing: 6) {
            Label("No starter templates for this profile yet",
                  systemImage: "books.vertical")
                .font(Typography.cardTitle)
                .foregroundStyle(Theme.Colors.textPrimary)
            Text("The library ships Merch tee rules, and KDP keeps a separate rule set. Author a rule for this profile in My Rules — or switch to a Merch profile to browse the starters.")
                .font(Typography.cardCaption)
                .foregroundStyle(Theme.Colors.textSecondary)
                .fixedSize(horizontal: false, vertical: true)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(Layout.Spacing.md)
        .mdCard()
    }

    private func card(_ t: RuleTemplate) -> some View {
        let saved = rule(for: t)
        let isOn = saved?.enabled == true
        return VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .firstTextBaseline, spacing: Layout.Spacing.xs) {
                Text(t.name)
                    .font(Typography.cardTitle)
                    .foregroundStyle(Theme.Colors.textPrimary)
                Spacer(minLength: 0)
                if busy == t.name { ProgressView().controlSize(.small) }
            }
            statusLine(saved)
            Text(t.blurb)
                .font(Typography.cardCaption)
                .foregroundStyle(Theme.Colors.textSecondary)
                .fixedSize(horizontal: false, vertical: true)
            RuleSnippet(text: t.text)
            Divider()
            HStack(spacing: Layout.Spacing.sm) {
                Button("Open") { detail = t }
                    .controlSize(.small)
                    .help("Full description, the whole rule, and a copy you can rename")
                Spacer(minLength: Layout.Spacing.xs)
                if saved != nil {
                    Picker("", selection: modeBinding(t, current: saved?.mode ?? "review")) {
                        Text("Review").tag("review")
                        Text("Auto").tag("auto")
                    }
                    .labelsHidden()
                    .pickerStyle(.menu)
                    .frame(width: 92)
                    .controlSize(.small)
                    .help(saved?.mode == "auto"
                          ? "Auto: applies on the nightly run without asking"
                          : "Review: proposes into the Approval Queue, never writes on its own")
                }
                Toggle("", isOn: enabledBinding(t, current: isOn))
                    .labelsHidden()
                    .toggleStyle(.switch)
                    .controlSize(.small)
                    .help(isOn ? "On — turn it off" : "Off — turn it on in Review mode")
                    .accessibilityLabel(isOn ? "Disable \(t.name)" : "Enable \(t.name)")
            }
            .disabled(busy != nil)
        }
        .padding(Layout.Spacing.md)
        .mdCard()
        .overlay(RoundedRectangle(cornerRadius: Layout.Radius.medium, style: .continuous)
            .strokeBorder(isOn ? Theme.Colors.accent.opacity(0.55) : .clear, lineWidth: 1.5))
    }

    @ViewBuilder
    private func statusLine(_ saved: RuleSummary?) -> some View {
        HStack(spacing: 6) {
            if let saved {
                Circle()
                    .fill(saved.enabled ? Theme.Colors.positive : Theme.Colors.muted)
                    .frame(width: 7, height: 7)
                Text(saved.enabled
                     ? (saved.mode == "auto" ? "On · applies automatically"
                                             : "On · proposes for approval")
                     : "In your rules, switched off")
            } else {
                Circle().strokeBorder(Theme.Colors.separator, lineWidth: 1)
                    .frame(width: 7, height: 7)
                Text("Not in your rules yet")
            }
            Spacer(minLength: 0)
        }
        .font(Typography.microLabel)
        .foregroundStyle(Theme.Colors.muted)
    }

    private func enabledBinding(_ t: RuleTemplate, current: Bool) -> Binding<Bool> {
        Binding(get: { current },
                set: { want in Task { await run(t) { await onSetEnabled(t, want) } } })
    }

    private func modeBinding(_ t: RuleTemplate, current: String) -> Binding<String> {
        Binding(get: { current },
                set: { want in
                    guard want != current else { return }
                    Task { await run(t) { await onSetMode(t, want) } }
                })
    }

    private func run(_ t: RuleTemplate, _ work: () async -> String?) async {
        await runKey(t.name, work)
    }

    private func runKey(_ key: String, _ work: () async -> String?) async {
        guard busy == nil else { return }
        busy = key
        error = nil
        if let reason = await work() { error = reason }
        busy = nil
    }

    // MARK: - Your rules (saved rules that aren't from the library)

    /// The rules you authored or that predate the library — they aren't template
    /// cards, so they'd otherwise be invisible here. Shown so the Library is the
    /// whole picture: every rule, and which are on.
    private var otherRules: [RuleSummary] {
        let templateNames = Set(templates.map(\.name))
        return rules.filter { !templateNames.contains($0.name) }
                    .sorted { $0.name.localizedCaseInsensitiveCompare($1.name) == .orderedAscending }
    }

    private var yourRulesSection: some View {
        VStack(alignment: .leading, spacing: Layout.Spacing.sm) {
            Divider().padding(.vertical, Layout.Spacing.xs)
            let on = otherRules.filter(\.enabled).count
            VStack(alignment: .leading, spacing: 2) {
                Text("Your rules").font(Typography.cardTitle)
                    .foregroundStyle(Theme.Colors.textPrimary)
                Text(on == otherRules.count
                     ? "\(otherRules.count) rule\(otherRules.count == 1 ? "" : "s") you authored — all on. Edit them in My Rules."
                     : "\(on) of \(otherRules.count) on. These aren't from the library; edit them in My Rules.")
                    .font(Typography.cardCaption)
                    .foregroundStyle(Theme.Colors.muted)
            }
            ForEach(otherRules) { otherRuleRow($0) }
        }
    }

    private func otherRuleRow(_ r: RuleSummary) -> some View {
        HStack(spacing: Layout.Spacing.sm) {
            Circle().fill(r.enabled ? Theme.Colors.positive : Theme.Colors.muted)
                .frame(width: 7, height: 7)
            VStack(alignment: .leading, spacing: 1) {
                Text(r.name).font(Typography.cardCaptionEmphasis)
                    .foregroundStyle(Theme.Colors.textPrimary)
                Text(r.enabled
                     ? (r.mode == "auto" ? "On · applies automatically" : "On · proposes for approval")
                     : "Off")
                    .font(Typography.microLabel).foregroundStyle(Theme.Colors.muted)
            }
            Spacer(minLength: Layout.Spacing.xs)
            if busy == r.name { ProgressView().controlSize(.small) }
            if r.enabled {
                Picker("", selection: savedModeBinding(r)) {
                    Text("Review").tag("review")
                    Text("Auto").tag("auto")
                }
                .labelsHidden().pickerStyle(.menu).frame(width: 92).controlSize(.small)
                .help(r.mode == "auto"
                      ? "Auto: applies on the nightly run without asking"
                      : "Review: proposes into the Approval Queue")
            }
            Toggle("", isOn: savedEnabledBinding(r))
                .labelsHidden().toggleStyle(.switch).controlSize(.small)
                .accessibilityLabel(r.enabled ? "Disable \(r.name)" : "Enable \(r.name)")
        }
        .disabled(busy != nil)
        .padding(.vertical, 6).padding(.horizontal, Layout.Spacing.md)
        .mdCard()
        .overlay(RoundedRectangle(cornerRadius: Layout.Radius.medium, style: .continuous)
            .strokeBorder(r.enabled ? Theme.Colors.accent.opacity(0.55) : .clear, lineWidth: 1.5))
    }

    private func savedEnabledBinding(_ r: RuleSummary) -> Binding<Bool> {
        Binding(get: { r.enabled },
                set: { want in Task { await runKey(r.name) { await onSetRuleEnabled(r, want) } } })
    }

    private func savedModeBinding(_ r: RuleSummary) -> Binding<String> {
        Binding(get: { r.mode },
                set: { want in
                    guard want != r.mode else { return }
                    Task { await runKey(r.name) { await onSetRuleMode(r, want) } }
                })
    }
}

/// "Open" — full description + full, scrollable code, with the copy path.
private struct TemplateDetailSheet: View {
    let template: RuleTemplate
    let onImportTap: () -> Void
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        VStack(alignment: .leading, spacing: Layout.Spacing.md) {
            HStack(alignment: .firstTextBaseline) {
                Text(template.name).font(.title2.bold())
                Spacer()
                Button { dismiss() } label: { Image(systemName: "xmark.circle.fill") }
                    .buttonStyle(.plain).foregroundStyle(.secondary)
                    .accessibilityLabel("Close")
            }
            Text(template.blurb).foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            ScrollView { RuleSnippet(text: template.text) }
            Text("To run this as-is, use the switch on its card. Take a copy only when you want to change the thresholds or keep several variants.")
                .font(.caption).foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            HStack {
                Spacer()
                Button("Cancel") { dismiss() }
                    .keyboardShortcut(.cancelAction)
                Button {
                    dismiss(); onImportTap()
                } label: { Label("Take a copy…", systemImage: "doc.on.doc") }
                    .buttonStyle(.borderedProminent)
                    .keyboardShortcut(.defaultAction)
            }
        }
        .padding(Layout.Spacing.lg)
        .frame(width: 580, height: 520)
    }
}

/// "Take a copy" dialog — pick a name, a mode, and whether it starts on.
private struct RuleImportSheet: View {
    let template: RuleTemplate
    let onImport: (RuleTemplate, String, String, Bool) async -> String?
    @Environment(\.dismiss) private var dismiss
    @State private var name: String
    @State private var mode = "review"
    @State private var activate = false
    @State private var busy = false
    @State private var error: String?

    init(template: RuleTemplate,
         onImport: @escaping (RuleTemplate, String, String, Bool) async -> String?) {
        self.template = template
        self.onImport = onImport
        _name = State(initialValue: "\(template.name) copy")
    }

    var body: some View {
        VStack(alignment: .leading, spacing: Layout.Spacing.md) {
            Text("Take a copy").font(.title2.bold())
            Text("A separate rule you can edit, based on “\(template.name)”.")
                .foregroundStyle(.secondary)
            Divider()

            VStack(alignment: .leading, spacing: 6) {
                Text("Name").font(.headline)
                TextField("Rule name", text: $name).textFieldStyle(.roundedBorder)
            }
            VStack(alignment: .leading, spacing: 6) {
                Text("Mode").font(.headline)
                Picker("", selection: $mode) {
                    Text("Review — propose into the Approval Queue").tag("review")
                    Text("Auto — apply on the nightly run").tag("auto")
                }
                .pickerStyle(.radioGroup)
                .labelsHidden()
            }
            Toggle(isOn: $activate) {
                Text("Turn it on right away").font(.headline)
            }
            .toggleStyle(.checkbox)
            Text("Leave it off to preview the copy in the editor first.")
                .font(.caption).foregroundStyle(.secondary)

            if let error {
                Text(error).font(.caption).foregroundStyle(Theme.Colors.critical)
            }
            Spacer()
            HStack {
                Spacer()
                Button("Cancel") { dismiss() }
                    .keyboardShortcut(.cancelAction)
                Button {
                    Task { await run() }
                } label: {
                    if busy { ProgressView().controlSize(.small) }
                    else { Label("Create copy", systemImage: "doc.on.doc") }
                }
                .buttonStyle(.borderedProminent)
                .keyboardShortcut(.defaultAction)
                .disabled(busy || name.trimmingCharacters(in: .whitespaces).isEmpty)
            }
        }
        .padding(Layout.Spacing.lg)
        .frame(width: 500, height: 420)
    }

    private func run() async {
        busy = true; defer { busy = false }
        error = nil
        // Show what actually went wrong: a bridge failure is not fixed by
        // picking a different name.
        if let reason = await onImport(template, name.trimmingCharacters(in: .whitespaces),
                                       mode, activate) {
            error = reason
        } else {
            dismiss()
        }
    }
}

/// A read-only, dashed-border code snippet with the same token colors as the
/// rules editor.
///
/// `.textSelection(.enabled)` is on, but drag-to-select does not survive the
/// lazy grid the cards sit in — the same class of gap that made table cells
/// unselectable and gave us `Copyable.swift`. So the snippet carries its own
/// Copy button (on hover) and a right-click Copy, and both hand over the WHOLE
/// rule, not the visible fragment. Dragging out a partial selection was never
/// what you wanted from a rule anyway.
private struct RuleSnippet: View {
    let text: String
    @State private var hovering = false
    @State private var copied = false

    var body: some View {
        Text(highlighted)
            .font(.caption.monospaced())
            .textSelection(.enabled)
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(10)
            .background(Theme.Colors.controlTrack, in: RoundedRectangle(cornerRadius: 8))
            .overlay(RoundedRectangle(cornerRadius: 8)
                .strokeBorder(Theme.Colors.separator, style: StrokeStyle(lineWidth: 1, dash: [3, 3])))
            .overlay(alignment: .topTrailing) { copyButton }
            .onHover { hovering = $0 }
            .contextMenu { Button("Copy rule") { copy() } }
    }

    /// Sits over the snippet's top-right corner and only appears on hover, so a
    /// card at rest still reads as a block of code.
    @ViewBuilder
    private var copyButton: some View {
        if hovering || copied {
            Button { copy() } label: {
                Label(copied ? "Copied" : "Copy",
                      systemImage: copied ? "checkmark" : "doc.on.doc")
                    .font(Typography.microLabel)
                    .padding(.horizontal, 6)
                    .padding(.vertical, 3)
                    .background(Theme.Colors.surface, in: Capsule())
                    .overlay(Capsule().strokeBorder(Theme.Colors.separator, lineWidth: 1))
            }
            .buttonStyle(.plain)
            .foregroundStyle(copied ? Theme.Colors.positive : Theme.Colors.textSecondary)
            .padding(6)
            .help("Copy the whole rule")
            .accessibilityLabel("Copy rule")
        }
    }

    private func copy() {
        Clipboard.copy(text)
        copied = true
        Task {
            try? await Task.sleep(for: .seconds(1.6))
            copied = false
        }
    }

    private var highlighted: AttributedString {
        var out = AttributedString()
        for (i, line) in text.split(separator: "\n", omittingEmptySubsequences: false).enumerated() {
            if i > 0 { out += AttributedString("\n") }
            for token in line.split(separator: " ", omittingEmptySubsequences: false) {
                var a = AttributedString(String(token) + " ")
                let w = token.uppercased()
                if ["FOR", "EACH", "IF", "AND", "OR", "NOT", "IN", "LET", "END"].contains(w) {
                    a.foregroundColor = Color(nsColor: .systemPink)
                } else if token.contains("break_even") || token.contains("profit")
                            || token.contains("royalty") || token.contains("lifetime_sales")
                            || token.contains("is_cohort") {
                    a.foregroundColor = Color(nsColor: .systemGreen)
                } else if token.hasPrefix("\"") || token.hasSuffix("\")") || token.contains("\"") {
                    a.foregroundColor = Color(nsColor: .systemRed)
                } else {
                    a.foregroundColor = Theme.Colors.textSecondary
                }
                out += a
            }
        }
        return out
    }
}
