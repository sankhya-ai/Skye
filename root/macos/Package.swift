// swift-tools-version:5.9
// root for macOS: the app around root's local server. Built by scripts/package-macos.sh.
import PackageDescription

let package = Package(
    name: "root",
    platforms: [.macOS(.v13)],
    dependencies: [
        // The standard updater for Mac apps distributed outside the App Store.
        .package(url: "https://github.com/sparkle-project/Sparkle", exact: "2.8.1"),
    ],
    targets: [
        .executableTarget(
            name: "root",
            dependencies: [.product(name: "Sparkle", package: "Sparkle")],
            path: "Sources/Root",
            linkerSettings: [.unsafeFlags(["-Xlinker", "-rpath", "-Xlinker", "@executable_path/../Frameworks"])]
        ),
    ]
)
