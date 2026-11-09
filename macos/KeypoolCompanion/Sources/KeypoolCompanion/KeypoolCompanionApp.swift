import SwiftUI
import AppKit

@main
struct KeypoolCompanionApp: App {
    @StateObject private var bridge = KeypoolBridge()

    var body: some Scene {
        MenuBarExtra("Keypool", systemImage: bridge.isGatewayOnline ? "key.fill" : "key") {
            CompanionMenuView(bridge: bridge)
        }
        .menuBarExtraStyle(.window)
    }
}
