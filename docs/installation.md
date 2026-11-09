# KEYPOOL Installation & Quick-Start Guide

## System Requirements
- **Operating System:** macOS Sonoma (14.0+) or macOS Sequoia (15.0+)
- **Python:** Python 3.11, 3.12, 3.13, 3.14, or 3.15
- **Security:** Apple Keychain Access enabled with login keychain unlocked
- **Architecture:** Apple Silicon (M1/M2/M3/M4) or Intel x86_64

---

## 1. Quick-Start (Installation)

### Option A: From Source / Editable Pip Install
```bash
# Clone the repository
git clone https://github.com/vinit/keypool.git
cd keypool

# Create and activate a virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install KEYPOOL and developer dependencies
pip install -e ".[dev]"
```

### Option B: Wheel Distribution
```bash
pip install dist/keypool-5.0.0-py3-none-any.whl
```

### Option C: Homebrew (Local Formula)
```bash
brew install keypool
```

---

## 2. Shell Autocompletion Setup

KEYPOOL supports native shell tab-completion for `zsh`, `bash`, and `fish`:

```bash
# For Zsh (macOS Default)
keypool completion zsh > ~/.zsh/completion/_keypool
echo 'fpath=(~/.zsh/completion $fpath)' >> ~/.zshrc
echo 'autoload -U compinit && compinit' >> ~/.zshrc

# For Bash
keypool completion bash > ~/.bash_completion.d/keypool
echo 'source ~/.bash_completion.d/keypool' >> ~/.bash_profile

# For Fish
keypool completion fish > ~/.config/fish/completions/keypool.fish
```

---

## 3. Immediate Verification & Doctor

Run the comprehensive self-diagnostic suite:
```bash
keypool doctor
```
The diagnostic verifies:
1. macOS Keychain connectivity and entitlement isolation.
2. SQLite 3 database and Write-Ahead Logging (WAL) journal configuration.
3. Strict filesystem permissions (`0700` directories, `0600` databases and configs).
4. Local Gateway status and port availability.
5. Installed provider adapters and capabilities.

---

## 4. First Steps

### Add your first API key
```bash
keypool add groq --name "groq-dev" --secret "gsk_..."
keypool add gemini --name "gemini-dev" --secret "AIza..."
```

### Verify credential connectivity
```bash
keypool test --all
```

### Run child CLI with transparent key injection
```bash
# Zero plaintext secrets on disk; injected directly into child process memory
keypool exec --profile development -- curl https://api.groq.com/openai/v1/models \
  -H "Authorization: Bearer \$GROQ_API_KEY"
```

### Start the Local AI Gateway
```bash
keypool gateway start --port 8080
```

### Launch Interactive TUI
```bash
keypool tui
```
