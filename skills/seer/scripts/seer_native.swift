import ApplicationServices
import CoreGraphics
import Foundation
import ImageIO
import Vision

private let maximumNodes = 2_000
private let maximumDepth = 40
private let messagingTimeout: Float = 1.0

private enum OutputSource: String {
    case accessibility
    case ocr
    case window
}

private struct HelperError: Error {
    let code: String
    let message: String
    let source: OutputSource
}

private struct WindowSnapshot {
    let id: UInt32
    let pid: pid_t
    let title: String
    let bounds: CGRect

    var json: [String: Any] {
        [
            "window_id": Int(id),
            "pid": Int(pid),
            "title": title,
            "bounds": boundsJSON(bounds),
        ]
    }
}

private struct AXWindowCandidate {
    let element: AXUIElement
    let position: CGPoint?
    let size: CGSize?
    let title: String?
}

private struct AttributeResult {
    let error: AXError
    let value: CFTypeRef?
}

private struct AXElementIdentity: Hashable {
    let element: AXUIElement

    static func == (lhs: AXElementIdentity, rhs: AXElementIdentity) -> Bool {
        CFEqual(lhs.element, rhs.element)
    }

    func hash(into hasher: inout Hasher) {
        // Keep CFEqual as the collision check; CFHash alone is not identity.
        hasher.combine(CFHash(element))
    }
}

private func boundsJSON(_ rect: CGRect) -> [String: Double] {
    [
        "x": Double(rect.origin.x),
        "y": Double(rect.origin.y),
        "width": Double(rect.size.width),
        "height": Double(rect.size.height),
    ]
}

private func jsonNull(_ value: Any?) -> Any {
    value ?? NSNull()
}

private func emit(_ object: [String: Any]) {
    do {
        let data = try JSONSerialization.data(withJSONObject: object, options: [.sortedKeys, .fragmentsAllowed])
        FileHandle.standardOutput.write(data)
        FileHandle.standardOutput.write(Data([0x0A]))
    } catch {
        let fallback = "{\"schema_version\":1,\"status\":\"error\",\"source\":\"window\",\"complete\":false,\"elements\":[],\"error\":{\"code\":\"serialization_error\",\"message\":\"could not encode JSON output\"}}\n"
        FileHandle.standardOutput.write(Data(fallback.utf8))
        fputs("error: could not encode JSON output: \(error)\n", stderr)
    }
}

private func emitError(_ error: HelperError) -> Int32 {
    emit([
        "schema_version": 1,
        "status": "error",
        "source": error.source.rawValue,
        "complete": false,
        "elements": [],
        "error": ["code": error.code, "message": error.message],
    ])
    fputs("error: \(error.message)\n", stderr)
    return 2
}

private func makeError(_ source: OutputSource, _ code: String, _ message: String) -> HelperError {
    HelperError(code: code, message: message, source: source)
}

private func usage() {
    let text = """
    Usage:
      seer_native window --window-id ID
      seer_native ax --window-id ID
      seer_native ocr --image FILE
    """
    fputs(text + "\n", stderr)
}

private func parseWindowID(_ rawValue: String, source: OutputSource) throws -> UInt32 {
    guard !rawValue.isEmpty,
          rawValue.utf8.allSatisfy({ $0 >= 48 && $0 <= 57 }),
          let value = UInt64(rawValue),
          value >= 1,
          value <= UInt64(UInt32.max) else {
        throw makeError(source, "invalid_arguments", "window ID must be between 1 and 4294967295")
    }
    return UInt32(value)
}

private func parseFlagValue(_ arguments: [String], flag: String, source: OutputSource) throws -> String {
    guard arguments.count == 2, arguments[0] == flag, !arguments[1].isEmpty else {
        throw makeError(source, "invalid_arguments", "expected \(flag) followed by a value")
    }
    return arguments[1]
}

private func cgNumber(_ value: Any?) -> Double? {
    guard let value, CFGetTypeID(value as CFTypeRef) != CFBooleanGetTypeID(),
          let number = value as? NSNumber else {
        return nil
    }
    let result = number.doubleValue
    return result.isFinite ? result : nil
}

private func cgWindowSnapshot(windowID: UInt32, source: OutputSource) throws -> WindowSnapshot {
    guard let rawWindows = CGWindowListCopyWindowInfo(
        [.optionOnScreenOnly, .excludeDesktopElements],
        kCGNullWindowID
    ) as? [[String: Any]] else {
        throw makeError(source, "window_unavailable", "could not read the visible window list")
    }

    for window in rawWindows {
        guard let rawID = cgNumber(window[kCGWindowNumber as String]),
              rawID == Double(windowID),
              let rawPID = cgNumber(window[kCGWindowOwnerPID as String]),
              rawPID > 0, rawPID <= Double(Int32.max),
              (window[kCGWindowIsOnscreen as String] as? Bool) == true,
              let layer = cgNumber(window[kCGWindowLayer as String]), layer == 0,
              let bounds = window[kCGWindowBounds as String] as? [String: Any],
              let x = cgNumber(bounds["X"]),
              let y = cgNumber(bounds["Y"]),
              let width = cgNumber(bounds["Width"]), width > 0,
              let height = cgNumber(bounds["Height"]), height > 0 else {
            continue
        }
        if let alpha = cgNumber(window[kCGWindowAlpha as String]), alpha <= 0 {
            continue
        }

        let title = window[kCGWindowName as String] as? String ?? ""
        return WindowSnapshot(
            id: windowID,
            pid: pid_t(rawPID),
            title: title,
            bounds: CGRect(x: x, y: y, width: width, height: height)
        )
    }

    throw makeError(source, "window_unavailable", "window \(windowID) is not currently visible")
}

private func close(_ lhs: Double, _ rhs: Double, tolerance: Double = 1.0) -> Bool {
    abs(lhs - rhs) <= tolerance
}

private func boundsMatch(position: CGPoint, size: CGSize, window: WindowSnapshot) -> Bool {
    close(Double(position.x), Double(window.bounds.origin.x)) &&
    close(Double(position.y), Double(window.bounds.origin.y)) &&
    close(Double(size.width), Double(window.bounds.width)) &&
    close(Double(size.height), Double(window.bounds.height))
}

private func copyAttribute(_ element: AXUIElement, _ name: String) -> AttributeResult {
    var value: CFTypeRef?
    let result = AXUIElementCopyAttributeValue(element, name as CFString, &value)
    return AttributeResult(error: result, value: value)
}

private func axString(_ value: CFTypeRef?) -> String? {
    guard let value else { return nil }
    return value as? String
}

private func axPoint(_ value: CFTypeRef?) -> CGPoint? {
    guard let value, CFGetTypeID(value) == AXValueGetTypeID() else { return nil }
    var point = CGPoint.zero
    guard AXValueGetValue(value as! AXValue, .cgPoint, &point),
          point.x.isFinite, point.y.isFinite else {
        return nil
    }
    return point
}

private func axSize(_ value: CFTypeRef?) -> CGSize? {
    guard let value, CFGetTypeID(value) == AXValueGetTypeID() else { return nil }
    var size = CGSize.zero
    guard AXValueGetValue(value as! AXValue, .cgSize, &size),
          size.width.isFinite, size.height.isFinite,
          size.width > 0, size.height > 0 else {
        return nil
    }
    return size
}

private func axValueJSON(_ value: CFTypeRef?) -> Any? {
    guard let value else { return nil }
    if CFGetTypeID(value) == CFStringGetTypeID() {
        return value as? String
    }
    if CFGetTypeID(value) == CFBooleanGetTypeID() {
        return (value as! CFBoolean) == kCFBooleanTrue
    }
    if CFGetTypeID(value) == CFNumberGetTypeID(), let number = value as? NSNumber {
        let doubleValue = number.doubleValue
        guard doubleValue.isFinite else { return nil }
        return number
    }
    return nil
}

private func titleCompatible(_ axTitle: String?, _ cgTitle: String) -> Bool {
    guard let axTitle, !axTitle.isEmpty, !cgTitle.isEmpty else { return true }
    return axTitle == cgTitle
}

private func readAXWindows(_ application: AXUIElement) throws -> [AXUIElement] {
    let result = copyAttribute(application, kAXWindowsAttribute as String)
    guard result.error == .success else {
        if result.error == .cannotComplete {
            throw makeError(.accessibility, "accessibility_timeout", "timed out while reading application windows")
        }
        throw makeError(.accessibility, "accessibility_failed", "could not read application accessibility windows (AX error \(result.error.rawValue))")
    }
    guard let windows = result.value as? [AXUIElement] else {
        throw makeError(.accessibility, "accessibility_failed", "application returned an invalid accessibility window list")
    }
    return windows
}

private func matchAXWindow(_ application: AXUIElement, to cgWindow: WindowSnapshot) throws -> AXWindowCandidate {
    let axWindows = try readAXWindows(application)
    var matches: [AXWindowCandidate] = []
    var uncertain: [AXWindowCandidate] = []

    for element in axWindows {
        let positionResult = copyAttribute(element, kAXPositionAttribute as String)
        let sizeResult = copyAttribute(element, kAXSizeAttribute as String)
        let titleResult = copyAttribute(element, kAXTitleAttribute as String)
        let candidate = AXWindowCandidate(
            element: element,
            position: axPoint(positionResult.value),
            size: axSize(sizeResult.value),
            title: axString(titleResult.value)
        )

        guard titleCompatible(candidate.title, cgWindow.title) else { continue }
        if let position = candidate.position, let size = candidate.size {
            if boundsMatch(position: position, size: size, window: cgWindow) {
                matches.append(candidate)
            }
        } else {
            uncertain.append(candidate)
        }
    }

    if matches.count > 1 || (!matches.isEmpty && !uncertain.isEmpty) || uncertain.count > 1 {
        throw makeError(.accessibility, "window_ambiguous", "could not uniquely match the visible window to an accessibility window")
    }
    guard let matched = matches.first else {
        throw makeError(.accessibility, "window_unavailable", "window \(cgWindow.id) has no matching accessibility window")
    }
    return matched
}

private final class AXTraversal {
    private let origin: CGPoint
    private let windowSize: CGSize
    private(set) var elements: [[String: Any]] = []
    private(set) var issues: [String] = []
    private var seenElements = Set<AXElementIdentity>()
    private var visitedNodes = 0
    private var stoppedAtNodeLimit = false

    init(origin: CGPoint, windowSize: CGSize) {
        self.origin = origin
        self.windowSize = windowSize
    }

    var complete: Bool { issues.isEmpty }

    func run(root: AXUIElement) {
        visit(root, depth: 0)
    }

    private func recordIssue(_ message: String) {
        if !issues.contains(message) {
            issues.append(message)
        }
    }

    private func readOptional(_ element: AXUIElement, _ attribute: String, issue: String) -> AttributeResult {
        let result = copyAttribute(element, attribute)
        if result.error != .success && result.error != .attributeUnsupported && result.error != .noValue {
            recordIssue(issue)
        }
        return result
    }

    private func readChildren(_ element: AXUIElement) -> [AXUIElement]? {
        let result = copyAttribute(element, kAXChildrenAttribute as String)
        if result.error == .attributeUnsupported || result.error == .noValue {
            return []
        }
        guard result.error == .success else {
            recordIssue("children_unavailable")
            return nil
        }
        guard let children = result.value as? [AXUIElement] else {
            recordIssue("children_invalid")
            return nil
        }
        return children
    }

    private func isMeaningful(role: String?, name: String?, value: Any?) -> Bool {
        let textAndControlRoles: Set<String> = [
            "AXButton", "AXCheckBox", "AXComboBox", "AXIncrementor", "AXLink", "AXMenuButton",
            "AXMenuItem", "AXPopUpButton", "AXRadioButton", "AXSearchField", "AXSecureTextField",
            "AXSlider", "AXStaticText", "AXTextArea", "AXTextField", "AXTab", "AXSwitch",
        ]
        if let role, textAndControlRoles.contains(role) { return true }

        let containerRoles: Set<String> = [
            "AXApplication", "AXBrowser", "AXColumn", "AXGroup", "AXLayoutArea", "AXList",
            "AXOutline", "AXRow", "AXScrollArea", "AXSplitGroup", "AXTable", "AXTabGroup",
            "AXToolbar", "AXWebArea", "AXWindow",
        ]
        if let role, containerRoles.contains(role) { return false }
        return name != nil || value != nil
    }

    private func hiddenState(_ element: AXUIElement) -> Bool {
        let result = copyAttribute(element, kAXHiddenAttribute as String)
        if result.error == .attributeUnsupported || result.error == .noValue {
            return false
        }
        guard result.error == .success else {
            recordIssue("hidden_state_unavailable")
            return false
        }
        guard let raw = result.value else { return false }
        if CFGetTypeID(raw) == CFBooleanGetTypeID() {
            return (raw as! CFBoolean) == kCFBooleanTrue
        }
        if let number = raw as? NSNumber {
            return number.boolValue
        }
        recordIssue("hidden_state_invalid")
        return false
    }

    private func visit(_ element: AXUIElement, depth: Int) {
        guard visitedNodes < maximumNodes else {
            if !stoppedAtNodeLimit {
                recordIssue("node_limit_reached")
                stoppedAtNodeLimit = true
            }
            return
        }
        visitedNodes += 1

        guard seenElements.insert(AXElementIdentity(element: element)).inserted else {
            recordIssue("accessibility_cycle_detected")
            return
        }

        let roleResult = readOptional(element, kAXRoleAttribute as String, issue: "role_unavailable")
        let role = axString(roleResult.value)
        if role == nil {
            recordIssue("role_unavailable")
        }

        if hiddenState(element) {
            return
        }

        let titleResult = readOptional(element, kAXTitleAttribute as String, issue: "name_unavailable")
        var name = axString(titleResult.value)
        if name == nil || name?.isEmpty == true {
            let descriptionResult = readOptional(element, kAXDescriptionAttribute as String, issue: "name_unavailable")
            name = axString(descriptionResult.value)
        }

        let subroleResult = readOptional(element, kAXSubroleAttribute as String, issue: "subrole_unavailable")
        let isSecureField = role == "AXSecureTextField" ||
            axString(subroleResult.value) == (kAXSecureTextFieldSubrole as String)

        var value: Any? = nil
        if isSecureField {
            recordIssue("secure_field_value_not_read")
        } else {
            let valueResult = readOptional(element, kAXValueAttribute as String, issue: "value_unavailable")
            value = axValueJSON(valueResult.value)
        }

        let enabledResult = readOptional(element, kAXEnabledAttribute as String, issue: "enabled_unavailable")
        let enabled: Bool? = {
            guard let value = enabledResult.value else { return nil }
            if CFGetTypeID(value) == CFBooleanGetTypeID() {
                return (value as! CFBoolean) == kCFBooleanTrue
            }
            return (value as? NSNumber)?.boolValue
        }()

        let positionResult = copyAttribute(element, kAXPositionAttribute as String)
        let sizeResult = copyAttribute(element, kAXSizeAttribute as String)
        let position = axPoint(positionResult.value)
        let size = axSize(sizeResult.value)
        var bounds: Any = NSNull()
        var isInWindow = true
        if let position, let size {
            let relativeBounds = CGRect(
                x: position.x - origin.x,
                y: position.y - origin.y,
                width: size.width,
                height: size.height
            )
            bounds = boundsJSON(relativeBounds)
            isInWindow = relativeBounds.intersects(CGRect(origin: .zero, size: windowSize))
        } else {
            if depth == 0 || isMeaningful(role: role, name: name, value: value) {
                recordIssue("element_bounds_unavailable")
            }
        }

        if isInWindow {
            let id = elements.count + 1
            elements.append([
                "id": id,
                "role": jsonNull(role),
                "name": jsonNull(name),
                "value": jsonNull(value),
                "bounds": bounds,
                "enabled": jsonNull(enabled),
                "confidence": NSNull(),
            ])
        }

        guard let children = readChildren(element), !children.isEmpty else { return }
        if depth >= maximumDepth {
            recordIssue("depth_limit_reached")
            return
        }
        for child in children {
            guard visitedNodes < maximumNodes else {
                if !stoppedAtNodeLimit {
                    recordIssue("node_limit_reached")
                    stoppedAtNodeLimit = true
                }
                return
            }
            visit(child, depth: depth + 1)
        }
    }
}

private func windowResult(_ window: WindowSnapshot) -> [String: Any] {
    [
        "schema_version": 1,
        "status": "pass",
        "source": OutputSource.window.rawValue,
        "complete": true,
        "window": window.json,
        "elements": [],
    ]
}

private func axResult(windowID: UInt32) throws -> [String: Any] {
    let cgWindow = try cgWindowSnapshot(windowID: windowID, source: .accessibility)
    guard AXIsProcessTrusted() else {
        throw makeError(.accessibility, "accessibility_required", "Accessibility permission is required to inspect this window")
    }

    let application = AXUIElementCreateApplication(cgWindow.pid)
    // A timeout on the application object alone does not cover child objects.
    // The system-wide object sets the default only within this helper process.
    guard AXUIElementSetMessagingTimeout(AXUIElementCreateSystemWide(), messagingTimeout) == .success else {
        throw makeError(.accessibility, "accessibility_failed", "could not set the accessibility messaging timeout")
    }

    let matched = try matchAXWindow(application, to: cgWindow)
    guard let origin = matched.position else {
        throw makeError(.accessibility, "window_unavailable", "matching accessibility window has no position")
    }

    let traversal = AXTraversal(
        origin: origin,
        windowSize: CGSize(width: cgWindow.bounds.width, height: cgWindow.bounds.height)
    )
    traversal.run(root: matched.element)

    let currentWindow = try cgWindowSnapshot(windowID: windowID, source: .accessibility)
    guard currentWindow.pid == cgWindow.pid,
          close(Double(currentWindow.bounds.origin.x), Double(cgWindow.bounds.origin.x), tolerance: 0.01),
          close(Double(currentWindow.bounds.origin.y), Double(cgWindow.bounds.origin.y), tolerance: 0.01),
          close(Double(currentWindow.bounds.width), Double(cgWindow.bounds.width), tolerance: 0.01),
          close(Double(currentWindow.bounds.height), Double(cgWindow.bounds.height), tolerance: 0.01) else {
        throw makeError(.accessibility, "window_unavailable", "window changed or became stale while it was being inspected")
    }

    let result: [String: Any] = [
        "schema_version": 1,
        "status": "pass",
        "source": OutputSource.accessibility.rawValue,
        "complete": traversal.complete,
        "window": cgWindow.json,
        "elements": traversal.elements,
        "issues": traversal.issues,
    ]
    return result
}

private func ocrResult(imagePath: String) throws -> [String: Any] {
    let source = OutputSource.ocr
    let url = URL(fileURLWithPath: imagePath)
    guard FileManager.default.fileExists(atPath: url.path) else {
        throw makeError(source, "filesystem_error", "image file does not exist")
    }
    guard let imageSource = CGImageSourceCreateWithURL(url as CFURL, nil),
          let image = CGImageSourceCreateImageAtIndex(imageSource, 0, nil) else {
        throw makeError(source, "invalid_image", "could not decode the image")
    }

    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    request.usesLanguageCorrection = false
    request.minimumTextHeight = 0.0

    do {
        let handler = VNImageRequestHandler(cgImage: image, orientation: .up, options: [:])
        try handler.perform([request])
    } catch {
        throw HelperError(code: "ocr_failed", message: "Vision text recognition failed: \(error.localizedDescription)", source: source)
    }

    let observations = request.results ?? []
    let elements: [[String: Any]] = observations.enumerated().compactMap { index, observation in
        guard let candidate = observation.topCandidates(1).first else { return nil }
        let rect = observation.boundingBox
        let x = Double(rect.origin.x) * Double(image.width)
        let y = (1.0 - Double(rect.origin.y) - Double(rect.height)) * Double(image.height)
        let width = Double(rect.width) * Double(image.width)
        let height = Double(rect.height) * Double(image.height)
        guard [x, y, width, height].allSatisfy(\.isFinite), width >= 0, height >= 0 else { return nil }
        return [
            "id": index + 1,
            "role": "text",
            "name": NSNull(),
            "value": candidate.string,
            "bounds": ["x": x, "y": y, "width": width, "height": height],
            "enabled": NSNull(),
            "confidence": Double(candidate.confidence),
        ]
    }

    return [
        "schema_version": 1,
        "status": "pass",
        "source": source.rawValue,
        "complete": true,
        "image_size": ["width": image.width, "height": image.height],
        "elements": elements,
        "issues": [],
    ]
}

private func execute(_ arguments: [String]) throws -> [String: Any] {
    guard let mode = arguments.first else {
        throw makeError(.window, "invalid_arguments", "a mode is required: window, ax, or ocr")
    }
    let rest = Array(arguments.dropFirst())
    switch mode {
    case "window":
        let rawID = try parseFlagValue(rest, flag: "--window-id", source: .window)
        let snapshot = try cgWindowSnapshot(windowID: parseWindowID(rawID, source: .window), source: .window)
        return windowResult(snapshot)
    case "ax":
        let rawID = try parseFlagValue(rest, flag: "--window-id", source: .accessibility)
        return try axResult(windowID: parseWindowID(rawID, source: .accessibility))
    case "ocr":
        let imagePath = try parseFlagValue(rest, flag: "--image", source: .ocr)
        return try ocrResult(imagePath: imagePath)
    default:
        throw makeError(.window, "invalid_arguments", "unknown mode \(mode); expected window, ax, or ocr")
    }
}

private enum SeerNative {
    static func main() {
        let arguments = Array(CommandLine.arguments.dropFirst())
        if arguments.contains("--help") || arguments.contains("-h") {
            usage()
            return
        }

        do {
            emit(try execute(arguments))
        } catch let helperError as HelperError {
            exit(emitError(helperError))
        } catch let unexpectedError {
            exit(emitError(makeError(.window, "internal_error", unexpectedError.localizedDescription)))
        }
    }
}

SeerNative.main()
