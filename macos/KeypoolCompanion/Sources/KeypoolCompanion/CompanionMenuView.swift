import SwiftUI
import AppKit

public struct CompanionMenuView: View {
    @ObservedObject public var bridge: KeypoolBridge

    public init(bridge: KeypoolBridge) {
        self.bridge = bridge
    }

    public var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            // Header
            HStack {
                Image(systemName: "key.horizontal.fill")
                    .font(.title2)
                    .foregroundColor(.accentColor)
                VStack(alignment: .leading, spacing: 2) {
                    Text("KEYPOOL")
                        .font(.headline)
                        .fontWeight(.bold)
                    Text("AI Credential Manager & Gateway")
                        .font(.caption2)
                        .foregroundColor(.secondary)
                }
                Spacer()
                HStack(spacing: 6) {
                    Circle()
                        .fill(bridge.isGatewayOnline ? Color.green : Color.red)
                        .frame(width: 8, height: 8)
                    Text(bridge.isGatewayOnline ? "Online" : "Offline")
                        .font(.caption)
                        .fontWeight(.medium)
                        .foregroundColor(bridge.isGatewayOnline ? .green : .red)
                }
                .padding(.horizontal, 8)
                .padding(.vertical, 4)
                .background(Color(NSColor.controlBackgroundColor))
                .cornerRadius(6)
            }

            Divider()

            // Gateway Metrics Grid
            VStack(alignment: .leading, spacing: 8) {
                Text("LOCAL GATEWAY (http://127.0.0.1:8765)")
                    .font(.caption)
                    .fontWeight(.bold)
                    .foregroundColor(.secondary)

                HStack(spacing: 16) {
                    VStack(alignment: .leading) {
                        Text("Active Credentials")
                            .font(.caption2)
                            .foregroundColor(.secondary)
                        Text("\(bridge.enabledCredentials) / \(bridge.totalCredentials)")
                            .font(.title3)
                            .fontWeight(.semibold)
                    }

                    VStack(alignment: .leading) {
                        Text("Available Models")
                            .font(.caption2)
                            .foregroundColor(.secondary)
                        Text("\(bridge.models.count)")
                            .font(.title3)
                            .fontWeight(.semibold)
                    }

                    VStack(alignment: .leading) {
                        Text("Gateway Uptime")
                            .font(.caption2)
                            .foregroundColor(.secondary)
                        Text(formatUptime(bridge.uptimeSeconds))
                            .font(.title3)
                            .fontWeight(.semibold)
                    }
                }
                .padding(10)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(Color(NSColor.controlBackgroundColor))
                .cornerRadius(8)
            }

            // Quick Actions
            VStack(alignment: .leading, spacing: 6) {
                Text("QUICK ACTIONS")
                    .font(.caption)
                    .fontWeight(.bold)
                    .foregroundColor(.secondary)

                Button(action: {
                    if !bridge.gatewayToken.isEmpty {
                        NSPasteboard.general.clearContents()
                        NSPasteboard.general.setString(bridge.gatewayToken, forType: .string)
                    }
                }) {
                    Label("Copy Gateway API Key", systemImage: "doc.on.doc")
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
                .buttonStyle(.bordered)

                Button(action: {
                    if let url = URL(string: "http://127.0.0.1:8765/docs") {
                        NSWorkspace.shared.open(url)
                    }
                }) {
                    Label("Open Gateway API Docs", systemImage: "safari")
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
                .buttonStyle(.bordered)

                Button(action: {
                    bridge.checkHealth()
                }) {
                    Label("Refresh Status Now", systemImage: "arrow.clockwise")
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
                .buttonStyle(.bordered)
            }

            Divider()

            // Footer
            HStack {
                Text("Keypool v\(bridge.gatewayVersion)")
                    .font(.caption2)
                    .foregroundColor(.secondary)
                Spacer()
                Button("Quit") {
                    NSApplication.shared.terminate(nil)
                }
                .buttonStyle(.borderless)
                .foregroundColor(.red)
            }
        }
        .padding(14)
        .frame(width: 320)
    }

    private func formatUptime(_ seconds: Double) -> String {
        let mins = Int(seconds) / 60
        if mins < 60 {
            return "\(mins)m"
        }
        return "\(mins / 60)h \(mins % 60)m"
    }
}
