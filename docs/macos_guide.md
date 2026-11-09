# KEYPOOL macOS Native Companion Application

`KeypoolCompanion` is a lightweight native macOS MenuBar utility built with Swift and SwiftUI. It connects directly to the local KEYPOOL SQLite metadata engine and the Local AI Gateway daemon.

---

## 1. Features
- **MenuBar Status Indicator:** Live green/yellow/red icon showing overall credential health across all configured providers.
- **Gateway Quick-Toggle:** Start or stop the local OpenAI-compatible HTTP gateway with a single click.
- **Real-Time Key Health Dashboard:** Quick summary of active, in-cooldown, and failing keys.
- **Notifications:** Native macOS notifications for 429 rate limit triggers and rotation overdue warnings.

---

## 2. Directory Structure

```
macos/
└── KeypoolCompanion/
    ├── Package.swift
    └── Sources/
        ├── App/
        │   └── KeypoolCompanionApp.swift
        ├── Models/
        │   └── KeypoolState.swift
        └── Views/
            ├── MenuBarView.swift
            └── SettingsView.swift
```

---

## 3. Building & Running the Companion App

### Compile with Swift Package Manager:
```bash
cd macos/KeypoolCompanion
swift build -c release
```

### Run Executable:
```bash
.build/release/KeypoolCompanion
```

The app will appear in your macOS status bar as a key icon.
