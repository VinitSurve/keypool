import Foundation
import Combine

@MainActor
public class KeypoolBridge: ObservableObject {
    @Published public var isGatewayOnline: Bool = false
    @Published public var gatewayVersion: String = "1.0.0"
    @Published public var totalCredentials: Int = 0
    @Published public var enabledCredentials: Int = 0
    @Published public var uptimeSeconds: Double = 0.0
    @Published public var gatewayToken: String = ""
    @Published public var models: [ModelCatalogItem] = []
    @Published public var lastCheckTime: Date? = nil

    private var timer: Timer?
    private let gatewayURL = URL(string: "http://127.0.0.1:8765")!

    public init() {
        self.loadGatewayToken()
        self.checkHealth()
        // Refresh every 5 seconds
        self.timer = Timer.scheduledTimer(withTimeInterval: 5.0, repeats: true) { [weak self] _ in
            Task { @MainActor in
                self?.checkHealth()
            }
        }
    }

    public func loadGatewayToken() {
        let homeDir = FileManager.default.homeDirectoryForCurrentUser
        let tokenFile = homeDir.appendingPathComponent(".keypool/gateway_token")
        if let token = try? String(contentsOf: tokenFile, encoding: .utf8) {
            self.gatewayToken = token.trimmingCharacters(in: .whitespacesAndNewlines)
        }
    }

    public func checkHealth() {
        let healthURL = gatewayURL.appendingPathComponent("health")
        var request = URLRequest(url: healthURL)
        request.timeoutInterval = 2.0

        URLSession.shared.dataTask(with: request) { [weak self] data, response, error in
            guard let self = self else { return }
            Task { @MainActor in
                self.lastCheckTime = Date()
                if let httpResp = response as? HTTPURLResponse, httpResp.statusCode == 200, let data = data {
                    if let health = try? JSONDecoder().decode(GatewayHealthResponse.self, from: data) {
                        self.isGatewayOnline = true
                        self.gatewayVersion = health.version
                        self.totalCredentials = health.totalCredentials
                        self.enabledCredentials = health.enabledCredentials
                        self.uptimeSeconds = health.uptimeSeconds
                        self.fetchModels()
                        return
                    }
                }
                self.isGatewayOnline = false
            }
        }.resume()
    }

    public func fetchModels() {
        guard !gatewayToken.isEmpty else { return }
        let modelsURL = gatewayURL.appendingPathComponent("v1/models")
        var request = URLRequest(url: modelsURL)
        request.setValue("Bearer \(gatewayToken)", forHTTPHeaderField: "Authorization")
        request.timeoutInterval = 2.0

        URLSession.shared.dataTask(with: request) { [weak self] data, response, error in
            guard let self = self, let data = data else { return }
            Task { @MainActor in
                if let res = try? JSONDecoder().decode(ModelListResponse.self, from: data) {
                    self.models = res.data
                }
            }
        }.resume()
    }
}
