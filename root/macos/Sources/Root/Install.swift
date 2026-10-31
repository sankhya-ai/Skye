// Getting root where it belongs: into /Applications when it was opened from
// the disk image or Downloads, and its `root` command onto the owner's PATH.

import Cocoa

enum Install {
    /// Offer to move the app into /Applications; true when it relaunched from there.
    static func offerMoveToApplications() -> Bool {
        let bundle = Bundle.main.bundleURL
        let path = bundle.path
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        let inApplications = path.hasPrefix("/Applications/") || path.hasPrefix("\(home)/Applications/")
        guard !inApplications, path.hasPrefix("/Volumes/") || path.contains("/AppTranslocation/") || path.hasPrefix("\(home)/Downloads/") else { return false }
        let alert = NSAlert()
        alert.messageText = "Move root to your Applications folder?"
        alert.informativeText = "root runs best from Applications: it can update itself there, and macOS keeps its permissions."
        alert.addButton(withTitle: "Move to Applications")
        alert.addButton(withTitle: "Not Now")
        guard alert.runModal() == .alertFirstButtonReturn else { return false }
        let destination = URL(fileURLWithPath: "/Applications/root.app")
        do {
            if FileManager.default.fileExists(atPath: destination.path) {
                try FileManager.default.trashItem(at: destination, resultingItemURL: nil)
            }
            try FileManager.default.copyItem(at: bundle, to: destination)
        } catch {
            let failure = NSAlert()
            failure.messageText = "root could not be moved"
            failure.informativeText = "\(error.localizedDescription)\nDrag it to Applications in Finder instead."
            failure.runModal()
            return false
        }
        let relaunch = Process()
        relaunch.executableURL = URL(fileURLWithPath: "/usr/bin/open")
        relaunch.arguments = ["-n", destination.path]
        try? relaunch.run()
        return true
    }

    /// Put a `root` command in ~/.local/bin that runs this app's server and CLI.
    static func commandLineTool(server: Server?) {
        let bin = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(".local/bin")
        let target = bin.appendingPathComponent("root")
        let resources = Bundle.main.resourceURL!
        let shim = """
        #!/bin/sh
        # root's command line, from \(Bundle.main.bundlePath) (Install Command Line Tool…).
        export TURA_CODE_RESOURCES="\(resources.appendingPathComponent("root").path)"
        export TURA_CODE_SECRETS=keychain PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1
        exec "\(resources.appendingPathComponent("python/bin/python3").path)" -m tura_code "$@"

        """
        let alert = NSAlert()
        do {
            try FileManager.default.createDirectory(at: bin, withIntermediateDirectories: true)
            try shim.write(to: target, atomically: true, encoding: .utf8)
            try FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: target.path)
            let onPath = (Server.loginShellEnvironment()?["PATH"] ?? "").split(separator: ":").contains { $0 == bin.path }
            alert.messageText = "Installed the root command"
            alert.informativeText = "\(target.path)\n\nTry `root doctor`, or `root chamber status`." + (onPath ? "" : "\n\n~/.local/bin is not on your PATH yet: add it in your shell profile.")
        } catch {
            alert.messageText = "Could not install the root command"
            alert.informativeText = error.localizedDescription
        }
        alert.runModal()
    }
}
