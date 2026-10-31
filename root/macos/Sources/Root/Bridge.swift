// The page's native side (ui/src/lib/native.ts): a folder picker, notifications
// and the Dock badge. The page posts to `window.webkit.messageHandlers.root`;
// answers come back through `window.__rootResolve(id, value)`.

import Cocoa
import UserNotifications
import WebKit

let bridgeScript = """
(() => {
  const pending = new Map();
  let next = 0;
  const post = (message) => window.webkit.messageHandlers.root.postMessage(message);
  const call = (type, payload) => new Promise((resolve) => {
    const id = ++next;
    pending.set(id, resolve);
    post({ ...payload, type, id });
  });
  window.__rootResolve = (id, value) => {
    const resolve = pending.get(id);
    pending.delete(id);
    if (resolve) resolve(value);
  };
  window.rootNative = {
    version: "\(Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "")",
    pickFolder: () => call("pickFolder", {}),
    notify: (title, body, sessionId) => post({ type: "notify", title, body, sessionId: sessionId || "" }),
    setBadge: (count) => post({ type: "badge", count }),
    revealInFinder: (path) => post({ type: "reveal", path }),
  };
})();
"""

final class Bridge: NSObject, WKScriptMessageHandler, UNUserNotificationCenterDelegate {
    weak var window: NSWindow?
    weak var webView: WKWebView?
    /// Opens a session in the page (a notification was clicked).
    var openSession: ((String) -> Void)?

    func install(on controller: WKUserContentController) {
        controller.addUserScript(WKUserScript(source: bridgeScript, injectionTime: .atDocumentStart, forMainFrameOnly: true))
        controller.add(self, name: "root")
        let center = UNUserNotificationCenter.current()
        center.delegate = self
        center.requestAuthorization(options: [.alert, .sound, .badge]) { _, _ in }
    }

    func userContentController(_ controller: WKUserContentController, didReceive message: WKScriptMessage) {
        guard let body = message.body as? [String: Any], let type = body["type"] as? String else { return }
        switch type {
        case "pickFolder":
            pickFolder(id: body["id"] as? Int ?? 0)
        case "notify":
            notify(title: body["title"] as? String ?? "root", body: body["body"] as? String ?? "", session: body["sessionId"] as? String ?? "")
        case "badge":
            let count = body["count"] as? Int ?? 0
            NSApp.dockTile.badgeLabel = count > 0 ? String(count) : nil
        case "reveal":
            if let path = body["path"] as? String { NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: path)]) }
        default:
            break
        }
    }

    private func pickFolder(id: Int) {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.canCreateDirectories = true
        panel.allowsMultipleSelection = false
        panel.prompt = "Open"
        panel.message = "Choose the folder root works in."
        let reply: (String?) -> Void = { [weak self] path in
            let value = path.map { "\"\($0.replacingOccurrences(of: "\\", with: "\\\\").replacingOccurrences(of: "\"", with: "\\\""))\"" } ?? "null"
            self?.webView?.evaluateJavaScript("window.__rootResolve(\(id), \(value))")
        }
        if let window {
            panel.beginSheetModal(for: window) { reply($0 == .OK ? panel.url?.path : nil) }
        } else {
            reply(panel.runModal() == .OK ? panel.url?.path : nil)
        }
    }

    private func notify(title: String, body: String, session: String) {
        let content = UNMutableNotificationContent()
        content.title = title
        content.body = body
        content.sound = .default
        content.userInfo = ["session": session]
        UNUserNotificationCenter.current().add(UNNotificationRequest(identifier: UUID().uuidString, content: content, trigger: nil))
    }

    func userNotificationCenter(_ center: UNUserNotificationCenter, didReceive response: UNNotificationResponse, withCompletionHandler done: @escaping () -> Void) {
        if let session = response.notification.request.content.userInfo["session"] as? String, !session.isEmpty {
            DispatchQueue.main.async { self.openSession?(session) }
        }
        done()
    }
}
