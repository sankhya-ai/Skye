// The bundled root server: started on a free loopback port with the owner's
// login-shell environment (a GUI app does not inherit the terminal's PATH, and
// the agent runs the owner's tools), stopped when the app quits.

import Foundation

let logDirectory = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Logs/root")

final class Server {
    let port: Int
    private var process: Process?
    private let resources: URL
    var onUnexpectedExit: ((Int32) -> Void)?
    private var stopping = false

    init(resources: URL) {
        self.resources = resources
        self.port = Server.freePort()
    }

    var url: URL { URL(string: "http://127.0.0.1:\(port)/")! }
    var logFile: URL { logDirectory.appendingPathComponent("server.log") }
    var python: URL { resources.appendingPathComponent("python/bin/python3") }
    var rootResources: URL { resources.appendingPathComponent("root") }

    static func freePort() -> Int {
        let fd = socket(AF_INET, SOCK_STREAM, 0)
        defer { close(fd) }
        var address = sockaddr_in()
        address.sin_family = sa_family_t(AF_INET)
        address.sin_addr.s_addr = inet_addr("127.0.0.1")
        address.sin_port = 0
        var length = socklen_t(MemoryLayout<sockaddr_in>.size)
        _ = withUnsafeMutablePointer(to: &address) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) { bind(fd, $0, length) }
        }
        _ = withUnsafeMutablePointer(to: &address) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) { getsockname(fd, $0, &length) }
        }
        return Int(UInt16(bigEndian: address.sin_port))
    }

    func start() throws {
        try FileManager.default.createDirectory(at: logDirectory, withIntermediateDirectories: true)
        if !FileManager.default.fileExists(atPath: logFile.path) {
            FileManager.default.createFile(atPath: logFile.path, contents: nil)
        }
        let log = try FileHandle(forWritingTo: logFile)
        log.seekToEndOfFile()
        log.write("\n=== root starting on port \(port) at \(Date()) ===\n".data(using: .utf8)!)

        let process = Process()
        process.executableURL = python
        process.arguments = ["-m", "tura_code", "serve", "--host", "127.0.0.1", "--port", String(port)]
        process.environment = environment()
        process.currentDirectoryURL = FileManager.default.homeDirectoryForCurrentUser
        process.standardOutput = log
        process.standardError = log
        process.terminationHandler = { [weak self] finished in
            guard let self, !self.stopping else { return }
            DispatchQueue.main.async { self.onUnexpectedExit?(finished.terminationStatus) }
        }
        try process.run()
        self.process = process
    }

    /// What the server (and so the agent's commands) run with.
    func environment() -> [String: String] {
        var env = Server.loginShellEnvironment() ?? ProcessInfo.processInfo.environment
        for key in ["PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "__PYVENV_LAUNCHER__"] { env.removeValue(forKey: key) }
        env["TURA_CODE_RESOURCES"] = rootResources.path
        env["TURA_CODE_SECRETS"] = "keychain"
        env["PYTHONDONTWRITEBYTECODE"] = "1"  // the bundle is signed: nothing may be written into it
        env["PYTHONNOUSERSITE"] = "1"
        env["PYTHONUNBUFFERED"] = "1"
        // OAuth clients baked into this build (public identifiers, not secrets).
        let info = Bundle.main.infoDictionary ?? [:]
        for (plistKey, envKey) in [
            ("RootGoogleClientID", "TURA_CODE_GOOGLE_CLIENT_ID"),
            ("RootGoogleClientSecret", "TURA_CODE_GOOGLE_CLIENT_SECRET"),
            ("RootGitHubClientID", "TURA_CODE_GITHUB_CLIENT_ID"),
        ] {
            if let value = info[plistKey] as? String, !value.isEmpty, !value.hasPrefix("@"), env[envKey] == nil { env[envKey] = value }
        }
        return env
    }

    /// The environment of the owner's login shell (PATH from .zprofile/.zshrc), or nil.
    static func loginShellEnvironment() -> [String: String]? {
        let shell = ProcessInfo.processInfo.environment["SHELL"] ?? "/bin/zsh"
        let marker = "__ROOT_ENV_\(UUID().uuidString)__"
        let probe = Process()
        probe.executableURL = URL(fileURLWithPath: shell)
        probe.arguments = ["-ilc", "printf '%s' \(marker); /usr/bin/env -0; printf '%s' \(marker)"]
        let pipe = Pipe()
        probe.standardOutput = pipe
        probe.standardError = FileHandle.nullDevice
        probe.standardInput = FileHandle.nullDevice
        do { try probe.run() } catch { return nil }
        let deadline = Date().addingTimeInterval(8)
        while probe.isRunning && Date() < deadline { usleep(20_000) }
        if probe.isRunning {
            probe.terminate()
            return nil
        }
        let output = String(decoding: pipe.fileHandleForReading.readDataToEndOfFile(), as: UTF8.self)
        let parts = output.components(separatedBy: marker)
        guard parts.count >= 3 else { return nil }
        var env: [String: String] = [:]
        for entry in parts[1].split(separator: "\0") {
            guard let equals = entry.firstIndex(of: "=") else { continue }
            env[String(entry[..<equals])] = String(entry[entry.index(after: equals)...])
        }
        return env["PATH"] == nil ? nil : env
    }

    func waitUntilReady(timeout: TimeInterval, done: @escaping (Bool) -> Void) {
        let deadline = Date().addingTimeInterval(timeout)
        let health = url.appendingPathComponent("api/health")
        func attempt() {
            guard process?.isRunning == true else { return done(false) }
            var request = URLRequest(url: health)
            request.timeoutInterval = 2
            URLSession.shared.dataTask(with: request) { _, response, _ in
                if (response as? HTTPURLResponse)?.statusCode == 200 {
                    DispatchQueue.main.async { done(true) }
                } else if Date() > deadline {
                    DispatchQueue.main.async { done(false) }
                } else {
                    DispatchQueue.main.asyncAfter(deadline: .now() + 0.25, execute: attempt)
                }
            }.resume()
        }
        attempt()
    }

    /// The server's last words, for the problem page.
    func lastLogLine() -> String {
        guard let text = try? String(contentsOf: logFile, encoding: .utf8) else { return "" }
        return text.split(separator: "\n").last(where: { !$0.trimmingCharacters(in: .whitespaces).isEmpty }).map(String.init) ?? ""
    }

    func stop() {
        stopping = true
        guard let process, process.isRunning else { return }
        process.terminate()  // SIGTERM: the server closes its sessions and runtimes
        let deadline = Date().addingTimeInterval(8)
        while process.isRunning && Date() < deadline { usleep(50_000) }
        if process.isRunning { kill(process.processIdentifier, SIGKILL) }
    }
}
