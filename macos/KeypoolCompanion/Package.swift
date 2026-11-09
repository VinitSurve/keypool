// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "KeypoolCompanion",
    platforms: [
        .macOS(.v13)
    ],
    products: [
        .executable(
            name: "KeypoolCompanion",
            targets: ["KeypoolCompanion"]
        )
    ],
    dependencies: [],
    targets: [
        .executableTarget(
            name: "KeypoolCompanion",
            dependencies: [],
            path: "Sources/KeypoolCompanion"
        )
    ]
)
