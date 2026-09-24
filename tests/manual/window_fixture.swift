// Local, opt-in AppKit fixture for exact-window capture QA. Never run in CI.
// Build: swiftc tests/manual/window_fixture.swift -o .seer/qa/SeerWindowFixture
// Commands on stdin: move, reorder, close, recreate, quit.
import AppKit

let app = NSApplication.shared
app.setActivationPolicy(.regular)
var windows: [String: NSWindow] = [:]

func makeWindow(_ name: String, color: NSColor, x: CGFloat) {
    let window = NSWindow(
        contentRect: NSRect(x: x, y: 200, width: 360, height: 240),
        styleMask: [.titled, .closable], backing: .buffered, defer: false
    )
    window.title = "Seer QA \(name)"
    window.isReleasedWhenClosed = false
    window.backgroundColor = color
    let label = NSTextField(labelWithString: "Seer QA \(name)")
    label.font = .systemFont(ofSize: 38, weight: .bold)
    label.textColor = .white
    label.frame = NSRect(x: 30, y: 100, width: 300, height: 60)
    window.contentView?.addSubview(label)
    window.makeKeyAndOrderFront(nil)
    windows[name] = window
}

func acknowledge(_ command: String) {
    print(command)
    fflush(stdout)
}

makeWindow("A", color: NSColor(srgbRed: 0.8, green: 0.1, blue: 0.1, alpha: 1), x: 100)
makeWindow("B", color: NSColor(srgbRed: 0.1, green: 0.2, blue: 0.8, alpha: 1), x: 500)
app.activate(ignoringOtherApps: true)
acknowledge("ready")

DispatchQueue.global().async {
    while let command = readLine() {
        DispatchQueue.main.async {
            switch command {
            case "move":
                windows["A"]?.setFrameOrigin(NSPoint(x: 200, y: 300))
            case "reorder":
                windows["A"]?.makeKeyAndOrderFront(nil)
            case "close":
                windows.removeValue(forKey: "A")?.close()
            case "recreate":
                if windows["A"] == nil {
                    makeWindow("A", color: NSColor(srgbRed: 0.8, green: 0.1, blue: 0.1, alpha: 1), x: 200)
                }
            case "quit":
                app.terminate(nil)
            default:
                break
            }
            acknowledge(command)
        }
    }
    DispatchQueue.main.async { app.terminate(nil) }
}
app.run()
