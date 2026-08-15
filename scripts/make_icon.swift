import AppKit

// MerchAds app icon: four black bars, rising left to right, on a plain white
// square. The white fills the whole tile — macOS rounds the corners itself.
//
// Reproducible:
//   swift scripts/make_icon.swift <out-dir>
// It renders every size (16 … 512@2x) into a temporary iconset and runs
// iconutil to make AppIcon.icns in out-dir. Default out-dir is the current
// folder. Only the .icns lands in the project, so nothing else is copied into
// the app bundle.
//
// The icon ships as a legacy .icns (CFBundleIconFile), NOT as an asset-catalog
// AppIcon. On macOS 26 an asset-catalog icon is drawn on the system's light
// "platter", which shows as a grey rim around a white icon. The .icns path is
// rendered flat, which is the look we want. See docs/packaging.md.

// ---- art, laid out on a 1024-unit board -------------------------------------
let board: CGFloat = 1024
let barWidth: CGFloat = 150
let barGap: CGFloat = 44
let barRadius: CGFloat = 24
let leftInset: CGFloat = 146      // symmetric: 4 bars + 3 gaps = 732 units wide
let baseline: CGFloat = 210       // distance from the bottom edge
let barHeights: [CGFloat] = [200, 330, 460, 600]

func drawIcon(pixels: Int) -> NSBitmapImageRep {
    let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: pixels, pixelsHigh: pixels,
                               bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true,
                               isPlanar: false, colorSpaceName: .deviceRGB,
                               bytesPerRow: 0, bitsPerPixel: 0)!
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)
    NSGraphicsContext.current?.imageInterpolation = .high

    let scale = CGFloat(pixels) / board
    let full = NSRect(x: 0, y: 0, width: CGFloat(pixels), height: CGFloat(pixels))

    // plain white background, opaque to every corner (no self-rounding)
    NSColor.white.setFill()
    full.fill()

    NSColor.black.setFill()
    for (index, height) in barHeights.enumerated() {
        let x = leftInset + CGFloat(index) * (barWidth + barGap)
        let rect = NSRect(x: x * scale, y: baseline * scale,
                          width: barWidth * scale, height: height * scale)
        let radius = min(barRadius * scale, rect.width / 2)
        NSBezierPath(roundedRect: rect, xRadius: radius, yRadius: radius).fill()
    }

    NSGraphicsContext.restoreGraphicsState()
    return rep
}

// ---- render every size, then pack the .icns ---------------------------------
let outDir = CommandLine.arguments.count > 1 ? CommandLine.arguments[1] : FileManager.default.currentDirectoryPath
let staging = URL(fileURLWithPath: NSTemporaryDirectory())
    .appendingPathComponent("merchads-icon-\(ProcessInfo.processInfo.processIdentifier)")
let iconset = staging.appendingPathComponent("AppIcon.iconset")
try FileManager.default.createDirectory(at: iconset, withIntermediateDirectories: true)

// every size macOS asks for, so nothing is ever upscaled
let sizes: [(name: String, pixels: Int)] = [
    ("icon_16x16", 16), ("icon_16x16@2x", 32),
    ("icon_32x32", 32), ("icon_32x32@2x", 64),
    ("icon_128x128", 128), ("icon_128x128@2x", 256),
    ("icon_256x256", 256), ("icon_256x256@2x", 512),
    ("icon_512x512", 512), ("icon_512x512@2x", 1024),
]
for size in sizes {
    let png = drawIcon(pixels: size.pixels).representation(using: .png, properties: [:])!
    try png.write(to: iconset.appendingPathComponent("\(size.name).png"))
}

let icns = URL(fileURLWithPath: outDir).appendingPathComponent("AppIcon.icns")
let task = Process()
task.executableURL = URL(fileURLWithPath: "/usr/bin/iconutil")
task.arguments = ["-c", "icns", iconset.path, "-o", icns.path]
try task.run()
task.waitUntilExit()
guard task.terminationStatus == 0 else {
    FileHandle.standardError.write("iconutil failed\n".data(using: .utf8)!)
    exit(1)
}
try? FileManager.default.removeItem(at: staging)
print("wrote \(icns.path)")
