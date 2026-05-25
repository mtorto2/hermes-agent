// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "AgentLightsMenuBar",
    platforms: [.macOS(.v14)],
    products: [
        .executable(name: "AgentLightsMenuBar", targets: ["AgentLightsMenuBar"]),
        .library(name: "AgentLightsCore", targets: ["AgentLightsCore"]),
    ],
    targets: [
        .target(name: "AgentLightsCore"),
        .executableTarget(
            name: "AgentLightsMenuBar",
            dependencies: ["AgentLightsCore"],
            linkerSettings: [.linkedFramework("AppKit")]
        ),
        .testTarget(name: "AgentLightsCoreTests", dependencies: ["AgentLightsCore"]),
    ]
)
