import Foundation

public struct GatewayHealthResponse: Codable {
    public let status: String
    public let version: String
    public let totalCredentials: Int
    public let enabledCredentials: Int
    public let uptimeSeconds: Double

    enum CodingKeys: String, CodingKey {
        case status
        case version
        case totalCredentials = "total_credentials"
        case enabledCredentials = "enabled_credentials"
        case uptimeSeconds = "uptime_seconds"
    }
}

public struct ModelCatalogItem: Identifiable, Codable {
    public var id: String
    public let object: String
    public let ownedBy: String

    enum CodingKeys: String, CodingKey {
        case id
        case object
        case ownedBy = "owned_by"
    }
}

public struct ModelListResponse: Codable {
    public let object: String
    public let data: [ModelCatalogItem]
}
