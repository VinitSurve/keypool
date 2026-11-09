# Environment Inspection Report — KEYPOOL

This document records the actual hardware, operating system, toolchain, and runtime environment inspected prior to implementing **KEYPOOL**.

## 1. System & Hardware Summary

| Property | Observed Value |
|---|---|
| **Machine / CPU** | Apple M5 |
| **CPU Architecture** | `arm64` (Apple Silicon) |
| **System Memory (RAM)** | 16 GB (`17,179,869,184` bytes) |
| **Operating System** | macOS `26.5.1` (Build `25F80`) |
| **Shell** | `zsh` (`/bin/zsh`) |
| **Working Directory** | `/Users/vinit/Vinit/Keypool` |
| **Available Disk Space** | `291 GiB` free of `460 GiB` (33% used on `/System/Volumes/Data`) |

## 2. Installed Toolchains & Package Managers

| Tool | Location | Version / Notes |
|---|---|---|
| **Homebrew** | `/opt/homebrew/bin/brew` | Homebrew `7.0.13` (native Apple Silicon prefix `/opt/homebrew`) |
| **Git** | `/usr/bin/git` | `git version 2.50.1 (Apple Git-155)` |
| **pipx** | `/opt/homebrew/bin/pipx` | `1.18.16` (`PIPX_BIN_DIR=/Users/vinit/.local/bin`, `PIPX_DEFAULT_PYTHON=/opt/homebrew/opt/python@3.15/bin/python3.15`) |

## 3. Discovered Python Runtimes

Multiple Python installations are present on the machine:

1. **Homebrew Python 3.15 (`/opt/homebrew/bin/python3.15`)** — `Python 3.15.2` (`arm64` native Mach-O executable). This is the active default runtime for `pipx` (`PIPX_DEFAULT_PYTHON`).
2. **Homebrew Python 3.11 (`/opt/homebrew/bin/python3.11`)** — `Python 3.11.18` (`arm64` native Mach-O executable).
3. **Framework Python 3.14 (`/usr/local/bin/python3.14`)** — `Python 3.14.7` (universal2 binary: `arm64` + `x86_64`).
4. **Anaconda Python 3.11 (`/opt/anaconda3/bin/python3`)** — `Python 3.11.7` (`x86_64` Rosetta binary, `pip 23.3.1`).
5. **macOS System Python (`/usr/bin/python3`)** — `Python 3.9.6`.

### Selected Development & Packaging Runtime

- **Package Compatibility Target:** `requires-python = ">=3.11"`
- **Primary Isolated Environment (`.venv`) & `pipx` Runtime:** `/opt/homebrew/bin/python3.15` (`Python 3.15.2`, native `arm64`), matching `PIPX_DEFAULT_PYTHON` so local testing in `.venv` and global installation via `pipx install .` execute against the exact same native Apple Silicon interpreter and native wheel set (`sqlalchemy`, `pydantic-core`, `keyring`, `httpx`, `typer`, `rich`, `ruff`, `mypy`, `pytest`).

## 4. Initial Workspace & Application State

- **Directory State at Inspection:** Empty directory (`/Users/vinit/Vinit/Keypool`), no pre-existing files, not previously a Git repository (`git init` initialized for KEYPOOL).
- **Existing Global Installation:** None (`which keypool` returned not found).
- **Existing Application Data Directory:** `~/Library/Application Support/keypool` did not exist prior to development.
- **Interactive Shell PATH:** `/Users/vinit/.local/bin` is already first in `$PATH` in interactive `zsh` sessions, allowing `pipx`-installed binaries to resolve globally without modifying shell startup files.

