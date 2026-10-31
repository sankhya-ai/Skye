// root for macOS: a native window around root's local server.
//
// The app starts the bundled server (Server.swift), shows its UI in a WebKit
// window, and behaves like a Mac app: closing the window keeps root (and any
// agent at work) running until you quit, the Dock brings it back, quitting
// asks while a session is still working, notifications and the Dock badge say
// when root finished or needs you, and Sparkle keeps it up to date.

import Cocoa
import ServiceManagement
import Sparkle
import WebKit

let appName = "root"

final class AppDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate, WKNavigationDelegate, WKUIDelegate, WKDownloadDelegate {
    var window: NSWindow!
    var webView: WKWebView!
    var server: Server!
    let bridge = Bridge()
    var popups: [NSWindow] = []
    var updater: SPUStandardUpdaterController?
    var quitting = false

    func applicationDidFinishLaunching(_ notification: Notification) {
        if let bundleID = Bundle.main.bundleIdentifier,
           let other = NSRunningApplication.runningApplications(withBundleIdentifier: bundleID).first(where: { $0 != .current }) {
            other.activate()
            NSApp.terminate(nil)
            return
        }
        if Install.offerMoveToApplications() {
            NSApp.terminate(nil)
            return
        }
        // Sparkle, when this build names a feed and its signing key.
        let info = Bundle.main.infoDictionary ?? [:]
        if let feed = info["SUFeedURL"] as? String, !feed.isEmpty, !feed.hasPrefix("@"),
           let key = info["SUPublicEDKey"] as? String, !key.isEmpty, !key.hasPrefix("@") {
            updater = SPUStandardUpdaterController(startingUpdater: true, updaterDelegate: nil, userDriverDelegate: nil)
        }
        buildMenu()
        buildWindow()
        guard let resources = Bundle.main.resourceURL else { return }
        server = Server(resources: resources)
        server.onUnexpectedExit = { [weak self] code in self?.showProblem("The root server stopped (exit \(code)).") }
        startServer()
    }

    func startServer() {
        showMessage("Starting root…")
        do {
            try server.start()
        } catch {
            showProblem("Could not start the root server: \(error.localizedDescription)")
            return
        }
        server.waitUntilReady(timeout: 90) { [weak self] ready in
            guard let self else { return }
            if ready {
                self.webView.load(URLRequest(url: self.server.url))
            } else {
                self.showProblem("The root server did not start.")
            }
        }
    }

    // MARK: lifecycle

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { false }

    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows: Bool) -> Bool {
        if !hasVisibleWindows { window.makeKeyAndOrderFront(nil) }
        return true
    }

    /// Closing the window hides it: agent work goes on until root quits.
    func windowShouldClose(_ sender: NSWindow) -> Bool {
        guard sender === window else { return true }
        window.orderOut(nil)
        return false
    }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        if quitting || webView?.url?.host != "127.0.0.1" { return .terminateNow }
        webView.evaluateJavaScript("window.__rootBusy ? window.__rootBusy() : 0") { [weak self] value, _ in
            guard let self else { return }
            let busy = (value as? Int) ?? 0
            if busy == 0 {
                self.quitting = true
                NSApp.reply(toApplicationShouldTerminate: true)
                return
            }
            self.window.makeKeyAndOrderFront(nil)
            let alert = NSAlert()
            alert.messageText = busy == 1 ? "root is still working on a session" : "root is still working on \(busy) sessions"
            alert.informativeText = "Quitting stops the work in progress. Your files keep whatever it changed so far."
            alert.addButton(withTitle: "Keep Working")
            alert.addButton(withTitle: "Quit Anyway")
            alert.beginSheetModal(for: self.window) { answer in
                self.quitting = answer == .alertSecondButtonReturn
                NSApp.reply(toApplicationShouldTerminate: self.quitting)
            }
        }
        return .terminateLater
    }

    func applicationWillTerminate(_ notification: Notification) { server?.stop() }

    // MARK: window

    private func buildWindow() {
        let configuration = WKWebViewConfiguration()
        configuration.websiteDataStore = .default()
        configuration.preferences.setValue(true, forKey: "developerExtrasEnabled")
        bridge.install(on: configuration.userContentController)
        webView = WKWebView(frame: .zero, configuration: configuration)
        webView.navigationDelegate = self
        webView.uiDelegate = self
        webView.setValue(false, forKey: "drawsBackground")
        window = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 1320, height: 860),
            styleMask: [.titled, .closable, .miniaturizable, .resizable],
            backing: .buffered,
            defer: false
        )
        window.title = appName
        window.minSize = NSSize(width: 760, height: 520)
        window.contentView = webView
        window.delegate = self
        window.isReleasedWhenClosed = false
        window.tabbingMode = .disallowed
        window.backgroundColor = NSColor(name: nil) { $0.bestMatch(from: [.darkAqua, .aqua]) == .darkAqua ? NSColor(white: 0.07, alpha: 1) : NSColor(white: 0.98, alpha: 1) }
        window.setFrameAutosaveName("root.main")
        if !window.setFrameUsingName("root.main") { window.center() }
        bridge.window = window
        bridge.webView = webView
        bridge.openSession = { [weak self] id in
            self?.window.makeKeyAndOrderFront(nil)
            NSApp.activate(ignoringOtherApps: true)
            self?.menu("session:\(id)")
        }
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    private func page(_ body: String) -> String {
        """
        <!doctype html><meta charset=utf-8><meta name=color-scheme content="light dark">
        <body style="font:14px -apple-system,system-ui;display:grid;place-items:center;height:92vh;margin:0;color:#888">
        <div style="max-width:440px;text-align:center">\(body)</div>
        """
    }

    func showMessage(_ text: String) { webView.loadHTMLString(page("<p>\(text)</p>"), baseURL: nil) }

    func showProblem(_ text: String) {
        let path = server?.logFile.path ?? logDirectory.path
        let reason = (server?.lastLogLine() ?? "").replacingOccurrences(of: "<", with: "&lt;")
        webView.loadHTMLString(page("<p><b>\(text)</b></p>\(reason.isEmpty ? "" : "<p>\(reason)</p>")<p>The log is at <code>\(path)</code>.</p><p>Choose <b>Help → Restart Server</b> to try again.</p>"), baseURL: nil)
    }

    private func isLocal(_ url: URL?) -> Bool {
        guard let url, let server else { return false }
        return url.host == "127.0.0.1" && url.port == server.port
    }

    // Links and pop-ups that leave root (GitHub sign-in, Google consent, docs) open in the browser.
    func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction, decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        guard let url = action.request.url else { return decisionHandler(.allow) }
        if action.shouldPerformDownload {
            decisionHandler(.download)
        } else if isLocal(url) || ["about", "data", "blob"].contains(url.scheme ?? "") {
            decisionHandler(.allow)
        } else {
            NSWorkspace.shared.open(url)
            decisionHandler(.cancel)
        }
    }

    // A file root serves as an attachment is a download, not a page.
    func webView(_ webView: WKWebView, decidePolicyFor response: WKNavigationResponse, decisionHandler: @escaping (WKNavigationResponsePolicy) -> Void) {
        let disposition = (response.response as? HTTPURLResponse)?.value(forHTTPHeaderField: "Content-Disposition") ?? ""
        decisionHandler(disposition.lowercased().hasPrefix("attachment") || !response.canShowMIMEType ? .download : .allow)
    }

    func webView(_ webView: WKWebView, navigationAction: WKNavigationAction, didBecome download: WKDownload) { download.delegate = self }
    func webView(_ webView: WKWebView, navigationResponse: WKNavigationResponse, didBecome download: WKDownload) { download.delegate = self }

    func download(_ download: WKDownload, decideDestinationUsing response: URLResponse, suggestedFilename: String, completionHandler: @escaping (URL?) -> Void) {
        let downloads = FileManager.default.urls(for: .downloadsDirectory, in: .userDomainMask)[0]
        var target = downloads.appendingPathComponent(suggestedFilename)
        let stem = target.deletingPathExtension().lastPathComponent, ext = target.pathExtension
        var n = 1
        while FileManager.default.fileExists(atPath: target.path) {
            n += 1
            target = downloads.appendingPathComponent(ext.isEmpty ? "\(stem) \(n)" : "\(stem) \(n).\(ext)")
        }
        completionHandler(target)
    }

    func downloadDidFinish(_ download: WKDownload) {
        if let url = download.progress.fileURL { NSWorkspace.shared.activateFileViewerSelecting([url]) }
    }

    /// window.open: root's own pages (a produced PDF) open in an app window that keeps the session; others in the browser.
    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration, for action: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        guard let url = action.request.url else { return nil }
        guard isLocal(url) else {
            NSWorkspace.shared.open(url)
            return nil
        }
        let popupView = WKWebView(frame: .zero, configuration: configuration)
        popupView.navigationDelegate = self
        popupView.uiDelegate = self
        let popup = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 900, height: 760), styleMask: [.titled, .closable, .miniaturizable, .resizable], backing: .buffered, defer: false)
        popup.title = url.lastPathComponent
        popup.contentView = popupView
        popup.isReleasedWhenClosed = false
        popup.center()
        popup.makeKeyAndOrderFront(nil)
        popups = popups.filter { $0.isVisible } + [popup]
        return popupView
    }

    func webView(_ webView: WKWebView, runOpenPanelWith parameters: WKOpenPanelParameters, initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping ([URL]?) -> Void) {
        let panel = NSOpenPanel()
        panel.allowsMultipleSelection = parameters.allowsMultipleSelection
        panel.canChooseDirectories = parameters.allowsDirectories
        panel.beginSheetModal(for: window) { completionHandler($0 == .OK ? panel.urls : nil) }
    }

    func webView(_ webView: WKWebView, runJavaScriptAlertPanelWithMessage message: String, initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping () -> Void) {
        let alert = NSAlert()
        alert.messageText = message
        alert.beginSheetModal(for: window) { _ in completionHandler() }
    }

    func webView(_ webView: WKWebView, runJavaScriptConfirmPanelWithMessage message: String, initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping (Bool) -> Void) {
        let alert = NSAlert()
        alert.messageText = message
        alert.addButton(withTitle: "OK")
        alert.addButton(withTitle: "Cancel")
        alert.beginSheetModal(for: window) { completionHandler($0 == .alertFirstButtonReturn) }
    }

    // MARK: menu

    /// Tell the page a menu item was chosen (ui/src/App.tsx listens for `root-menu`).
    func menu(_ action: String) {
        window.makeKeyAndOrderFront(nil)
        webView.evaluateJavaScript("window.dispatchEvent(new CustomEvent('root-menu', { detail: \(String(reflecting: action)) }))")
    }

    @objc func newSession() { menu("newSession") }
    @objc func openFolder() { menu("openFolder") }
    @objc func openSettings() { menu("settings") }
    @objc func reload() { if isLocal(webView.url) { webView.reload() } else if let server { webView.load(URLRequest(url: server.url)) } }
    @objc func zoomIn() { webView.pageZoom = min(webView.pageZoom + 0.1, 2) }
    @objc func zoomOut() { webView.pageZoom = max(webView.pageZoom - 0.1, 0.6) }
    @objc func actualSize() { webView.pageZoom = 1 }
    @objc func openInBrowser() { if let server { NSWorkspace.shared.open(server.url) } }
    @objc func showLogs() { NSWorkspace.shared.open(logDirectory) }
    @objc func installCommand() { Install.commandLineTool(server: server) }

    /// Open root when you log in, so schedules keep their times (macOS's standard login item).
    @objc func toggleOpenAtLogin(_ sender: NSMenuItem) {
        let service = SMAppService.mainApp
        do {
            if service.status == .enabled {
                try service.unregister()
            } else {
                try service.register()
            }
        } catch {
            let alert = NSAlert()
            alert.messageText = "root could not change its login item"
            alert.informativeText = "\(error.localizedDescription)\nYou can add it in System Settings → General → Login Items."
            alert.runModal()
        }
        sender.state = service.status == .enabled ? .on : .off
    }
    @objc func showMainWindow() { window.makeKeyAndOrderFront(nil) }
    @objc func restartServer() {
        server?.stop()
        guard let resources = Bundle.main.resourceURL else { return }
        server = Server(resources: resources)
        server.onUnexpectedExit = { [weak self] code in self?.showProblem("The root server stopped (exit \(code)).") }
        startServer()
    }

    private func buildMenu() {
        let main = NSMenu()
        func submenu(_ title: String, _ items: [NSMenuItem]) -> NSMenu {
            let holder = NSMenuItem()
            let menu = NSMenu(title: title)
            items.forEach(menu.addItem)
            holder.submenu = menu
            main.addItem(holder)
            return menu
        }
        func item(_ title: String, _ action: Selector?, _ key: String = "", _ modifiers: NSEvent.ModifierFlags = .command, target: AnyObject? = nil) -> NSMenuItem {
            let entry = NSMenuItem(title: title, action: action, keyEquivalent: key)
            entry.keyEquivalentModifierMask = modifiers
            entry.target = target
            return entry
        }
        var appItems: [NSMenuItem] = [item("About \(appName)", #selector(NSApplication.orderFrontStandardAboutPanel(_:)))]
        if let updater {
            appItems.append(item("Check for Updates…", #selector(SPUStandardUpdaterController.checkForUpdates(_:)), target: updater))
        }
        appItems += [
            .separator(),
            item("Settings…", #selector(openSettings), ","),
            item("Install Command Line Tool…", #selector(installCommand)),
            {
                let entry = item("Open at Login", #selector(toggleOpenAtLogin(_:)))
                entry.state = SMAppService.mainApp.status == .enabled ? .on : .off
                return entry
            }(),
            .separator(),
            item("Hide \(appName)", #selector(NSApplication.hide(_:)), "h"),
            item("Hide Others", #selector(NSApplication.hideOtherApplications(_:)), "h", [.command, .option]),
            item("Show All", #selector(NSApplication.unhideAllApplications(_:))),
            .separator(),
            item("Quit \(appName)", #selector(NSApplication.terminate(_:)), "q"),
        ]
        _ = submenu(appName, appItems)
        _ = submenu("File", [
            item("New Session", #selector(newSession), "n"),
            item("Open Folder…", #selector(openFolder), "o"),
            .separator(),
            item("Close Window", #selector(NSWindow.performClose(_:)), "w"),
        ])
        _ = submenu("Edit", [
            item("Undo", Selector(("undo:")), "z"),
            item("Redo", Selector(("redo:")), "z", [.command, .shift]),
            .separator(),
            item("Cut", #selector(NSText.cut(_:)), "x"),
            item("Copy", #selector(NSText.copy(_:)), "c"),
            item("Paste", #selector(NSText.paste(_:)), "v"),
            item("Select All", #selector(NSText.selectAll(_:)), "a"),
        ])
        _ = submenu("View", [
            item("Reload", #selector(reload), "r"),
            .separator(),
            item("Actual Size", #selector(actualSize), "0"),
            item("Zoom In", #selector(zoomIn), "="),
            item("Zoom Out", #selector(zoomOut), "-"),
            .separator(),
            item("Enter Full Screen", #selector(NSWindow.toggleFullScreen(_:)), "f", [.command, .control]),
        ])
        let windowMenu = submenu("Window", [
            item("Minimize", #selector(NSWindow.performMiniaturize(_:)), "m"),
            item("Zoom", #selector(NSWindow.performZoom(_:))),
            .separator(),
            item(appName, #selector(showMainWindow), "1"),
        ])
        NSApp.windowsMenu = windowMenu
        _ = submenu("Help", [
            item("Open in Browser", #selector(openInBrowser), "o", [.command, .shift]),
            item("Show Logs", #selector(showLogs)),
            item("Restart Server", #selector(restartServer), "r", [.command, .shift]),
        ])
        NSApp.mainMenu = main
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.regular)
app.run()
