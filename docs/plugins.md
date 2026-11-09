# KEYPOOL Plugin Architecture & Custom Provider Extension

KEYPOOL features an extensible plugin discovery system allowing teams to register internal model servers, private fine-tunes, or proprietary LLM endpoints without altering core KEYPOOL source code.

---

## 1. Plugin Directory

KEYPOOL discovers plugins dynamically from:
- `~/.config/keypool/plugins/`
- Any directory specified in `KEYPOOL_PLUGIN_PATH`

---

## 2. Implementing a Custom Provider Plugin

A provider plugin inherits from `BaseProvider`:

```python
# ~/.config/keypool/plugins/internal_llm.py
from keypool.providers.base import (
    BaseProvider,
    ConnectionTestResult,
    ModelDiscoveryResult,
    ModelInfo,
    ProviderErrorClassification,
)
from keypool.storage.models import HealthState
import httpx

class InternalLLMProvider(BaseProvider):
    name = "internal_llm"
    display_name = "Corporate Private LLM"
    base_url = "https://ai.internal.corp/v1"

    def get_auth_headers(self, secret: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {secret.strip()}"}

    def test_connection(self, secret: str, model: str | None = None) -> ConnectionTestResult:
        headers = self.get_auth_headers(secret)
        try:
            resp = self._http_client.get(f"{self.base_url}/models", headers=headers, timeout=5.0)
            if resp.status_code == 200:
                return ConnectionTestResult(
                    healthy=True,
                    state=HealthState.HEALTHY,
                    message="Corporate LLM reachable",
                    auth_ok=True,
                )
            return ConnectionTestResult(
                healthy=False,
                state=HealthState.AUTH_FAILED,
                message=f"HTTP {resp.status_code}",
                auth_ok=False,
            )
        except Exception as exc:
            return ConnectionTestResult(
                healthy=False,
                state=HealthState.NETWORK_ERROR,
                message=str(exc),
                auth_ok=False,
            )

    def discover_models(self, secret: str) -> ModelDiscoveryResult:
        return ModelDiscoveryResult(
            models=[
                ModelInfo(id="corp-llama3-70b", name="Corporate Llama 3 70B", provider="internal_llm")
            ]
        )

    def classify_error(self, response: httpx.Response | None, exception: Exception | None = None) -> ProviderErrorClassification:
        if response is not None and response.status_code == 429:
            return ProviderErrorClassification(state=HealthState.RATE_LIMITED, is_rate_limit=True)
        return ProviderErrorClassification(state=HealthState.UNEXPECTED_ERROR)
```

---

## 3. Managing Plugins via CLI

```bash
# List discovered plugins
keypool plugin list

# Validate plugin conformance
keypool plugin validate internal_llm

# Enable / Disable plugin
keypool plugin enable internal_llm
keypool plugin disable internal_llm
```
