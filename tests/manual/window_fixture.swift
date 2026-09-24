// Local, opt-in AppKit fixture for exact-window capture QA. Never run in CI.
// Build: swiftc tests/manual/window_fixture.swift -o .seer/qa/SeerWindowFixture
// Commands on stdin: move, reorder, close, recreate, v06-start, v06-static,
// v06-settle, v06-pulse, v06-outside, v07-start, v07-loading,
// v07-ready-delayed, v07-close, quit.
import AppKit

let app = NSApplication.shared
app.setActivationPolicy(.regular)
var windows: [String: NSWindow] = [:]
var v06Window: NSWindow?
var v06InsidePatch: ColorPatchView?
var v06OutsidePatch: ColorPatchView?
var v06Timers: [String: Timer] = [:]
var v06Generation = 0
var v07Window: NSWindow?
var v07StatusLabel: NSTextField?
var v07Generation = 0

final class ColorPatchView: NSView {
    private(set) var hue: CGFloat

    init(frame: NSRect, hue: CGFloat) {
        self.hue = hue
        super.init(frame: frame)
    }

    required init?(coder: NSCoder) {
        fatalError("init(coder:) has not been implemented")
    }

    override var isOpaque: Bool { true }

    func setHue(_ hue: CGFloat) {
        self.hue = hue
        needsDisplay = true
    }

    override func draw(_ dirtyRect: NSRect) {
        NSColor(calibratedHue: hue, saturation: 0.9, brightness: 0.95, alpha: 1).setFill()
        // AppKit may supply an expanded dirty rect for non-clipping views.
        // Paint only this patch's bounds, never neighboring fixture content.
        bounds.fill()
    }
}

final class CanvasEvidenceView: NSView {
    override var isOpaque: Bool { true }

    override func draw(_ dirtyRect: NSRect) {
        NSColor(calibratedRed: 0.08, green: 0.12, blue: 0.17, alpha: 1).setFill()
        bounds.fill()

        let text = NSAttributedString(
            string: "Canvas Evidence",
            attributes: [
                .font: NSFont.systemFont(ofSize: 27, weight: .semibold),
                .foregroundColor: NSColor.white,
            ]
        )
        text.draw(in: NSRect(x: 20, y: 38, width: bounds.width - 40, height: 38))
    }
}

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

func stopV06Animations() {
    v06Generation += 1
    for timer in v06Timers.values {
        timer.invalidate()
    }
    v06Timers.removeAll()
}

func makeV06Window() {
    guard v06Window == nil else {
        v06Window?.makeKeyAndOrderFront(nil)
        return
    }

    let window = NSWindow(
        contentRect: NSRect(x: 900, y: 200, width: 420, height: 300),
        styleMask: [.titled, .closable], backing: .buffered, defer: false
    )
    window.title = "Seer QA v0.6"
    window.isReleasedWhenClosed = false
    window.backgroundColor = NSColor(srgbRed: 0.12, green: 0.12, blue: 0.14, alpha: 1)

    // These AppKit point rectangles make the fixture's dynamic regions
    // repeatable. The Python verifier derives the actual pixel mask from PNG
    // diffs, which includes Retina scale and the captured titlebar offset.
    let inside = ColorPatchView(frame: NSRect(x: 40, y: 40, width: 96, height: 72), hue: 0.58)
    let outside = ColorPatchView(frame: NSRect(x: 260, y: 40, width: 72, height: 72), hue: 0.08)
    window.contentView?.addSubview(inside)
    window.contentView?.addSubview(outside)
    window.makeKeyAndOrderFront(nil)

    v06Window = window
    v06InsidePatch = inside
    v06OutsidePatch = outside
}

func setV06Static() {
    stopV06Animations()
    makeV06Window()
    v06InsidePatch?.setHue(0.58)
    v06OutsidePatch?.setHue(0.08)
}

func animateV06Patch(_ name: String, view: ColorPatchView, settleAfter: TimeInterval? = nil) {
    stopV06Animations()
    let generation = v06Generation
    var phase = 0
    let timer = Timer(timeInterval: 0.13, repeats: true) { [weak view] _ in
        phase = (phase + 1) % 360
        view?.setHue(CGFloat(phase) / 360)
    }
    v06Timers[name] = timer
    RunLoop.main.add(timer, forMode: .common)

    if let settleAfter {
        DispatchQueue.main.asyncAfter(deadline: .now() + settleAfter) {
            guard generation == v06Generation else { return }
            v06Timers.removeValue(forKey: name)?.invalidate()
            view.setHue(name == "inside" ? 0.58 : 0.08)
        }
    }
}

func cancelV07Transition() {
    v07Generation += 1
}

func setV07Status(_ value: String) {
    v07StatusLabel?.stringValue = value
}

func makeV07Window() {
    guard v07Window == nil else {
        v07Window?.makeKeyAndOrderFront(nil)
        return
    }

    let window = NSWindow(
        contentRect: NSRect(x: 900, y: 240, width: 520, height: 360),
        styleMask: [.titled, .closable], backing: .buffered, defer: false
    )
    window.title = "Seer QA v0.7"
    window.isReleasedWhenClosed = false
    window.backgroundColor = NSColor(calibratedRed: 0.11, green: 0.14, blue: 0.18, alpha: 1)

    let status = NSTextField(labelWithString: "Seer Ready")
    status.font = .systemFont(ofSize: 30, weight: .semibold)
    status.textColor = .white
    status.frame = NSRect(x: 36, y: 275, width: 448, height: 42)
    status.setAccessibilityLabel("Status")
    window.contentView?.addSubview(status)

    let continueButton = NSButton(title: "Continue", target: nil, action: nil)
    continueButton.bezelStyle = .rounded
    continueButton.isEnabled = true
    continueButton.frame = NSRect(x: 36, y: 212, width: 180, height: 38)
    window.contentView?.addSubview(continueButton)

    let unavailableButton = NSButton(title: "Unavailable", target: nil, action: nil)
    unavailableButton.bezelStyle = .rounded
    unavailableButton.isEnabled = false
    unavailableButton.frame = NSRect(x: 236, y: 212, width: 180, height: 38)
    window.contentView?.addSubview(unavailableButton)

    // Deliberately paint the evidence text in a plain NSView. The view has no
    // accessibility label, role, or children, so OCR can find text that AX
    // cannot expose.
    let canvas = CanvasEvidenceView(frame: NSRect(x: 36, y: 54, width: 448, height: 112))
    window.contentView?.addSubview(canvas)

    v07Window = window
    v07StatusLabel = status
    window.makeKeyAndOrderFront(nil)
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
            case "v06-start":
                makeV06Window()
            case "v06-static":
                setV06Static()
            case "v06-settle":
                setV06Static()
                if let inside = v06InsidePatch {
                    animateV06Patch("inside", view: inside, settleAfter: 1.4)
                }
            case "v06-pulse":
                setV06Static()
                if let inside = v06InsidePatch {
                    animateV06Patch("inside", view: inside)
                }
            case "v06-outside":
                setV06Static()
                if let outside = v06OutsidePatch {
                    animateV06Patch("outside", view: outside)
                }
            case "v07-start":
                cancelV07Transition()
                makeV07Window()
                setV07Status("Seer Ready")
            case "v07-loading":
                cancelV07Transition()
                makeV07Window()
                setV07Status("Loading")
            case "v07-ready-delayed":
                cancelV07Transition()
                makeV07Window()
                setV07Status("Loading")
                let generation = v07Generation
                DispatchQueue.main.asyncAfter(deadline: .now() + 3.0) {
                    guard generation == v07Generation, v07Window != nil else { return }
                    setV07Status("Seer Ready")
                }
            case "v07-close":
                cancelV07Transition()
                v07Window?.close()
                v07Window = nil
                v07StatusLabel = nil
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
