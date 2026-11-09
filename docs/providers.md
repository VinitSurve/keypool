# Provider Adapters — Groq & Google Gemini

KEYPOOL implements a provider-agnostic adapter layer (`BaseProvider` in `src/keypool/providers/base.py`) registered via `ProviderRegistry` (`src/keypool/providers/registry.py`).

---

## 1. Groq (`groq`)

| Attribute | Value |
|---|---|
| **Adapter Class** | `GroqProvider` (`src/keypool/providers/groq.py`) |
| **Environment Variable** | `GROQ_API_KEY` |
| **Base API URL** | `https://api.groq.com/openai/v1` |
| **Authentication Scheme** | HTTP header `Authorization: Bearer <GROQ_API_KEY>` |
| **Model Discovery Endpoint** | `GET /openai/v1/models` |
| **Functional Test Endpoint** | `POST /openai/v1/chat/completions` (`max_tokens: 1`, `temperature: 0`) |
| **Preferred Test Model** | `llama-3.1-8b-instant` (dynamically verified against discovered models) |

### Error & Rate-Limit Mapping

- **HTTP 401 (`invalid_api_key`):** Classified as `auth_failed` (non-retryable).
- **HTTP 403 (`permission_denied` / restricted):** Classified as `permission_denied` (non-retryable).
- **HTTP 404 (`model_not_found` / `model_decommissioned`):** Classified as `model_unavailable`.
- **HTTP 429 (`insufficient_quota` / `spend_limit`):** Classified as `quota_exhausted`.
- **HTTP 429 (rate limit):** Classified as `rate_limited`; parses `Retry-After`, `x-ratelimit-reset-requests`, and `x-ratelimit-reset-tokens` (including Go-style durations such as `2m59.56s` or `450ms`).
- **HTTP 500/502/503/504:** Classified as `provider_unavailable`.

---

## 2. Google Gemini (`gemini`)

| Attribute | Value |
|---|---|
| **Adapter Class** | `GeminiProvider` (`src/keypool/providers/gemini.py`) |
| **Environment Variable** | `GEMINI_API_KEY` |
| **Base API URL** | `https://generativelanguage.googleapis.com/v1beta` |
| **Authentication Scheme** | HTTP header `x-goog-api-key: <GEMINI_API_KEY>` (never sent in URL query string) |
| **Model Discovery Endpoint** | `GET /v1beta/models` |
| **Functional Test Endpoint** | `POST /v1beta/models/{model}:generateContent` (`maxOutputTokens: 1`, `temperature: 0`) |
| **Preferred Test Model** | `gemini-2.5-flash` (dynamically verified against discovered models supporting `generateContent`) |

### Error & Rate-Limit Mapping

- **HTTP 400 (`INVALID_ARGUMENT` with `API_KEY_INVALID` / `API_KEY_EXPIRED`) or HTTP 401 (`UNAUTHENTICATED`):** Classified as `auth_failed` (non-retryable).
- **HTTP 403 (`PERMISSION_DENIED`, `API_KEY_SERVICE_BLOCKED`, `API_KEY_HTTP_REFERRER_BLOCKED`, `SERVICE_DISABLED`):** Classified as `permission_denied` (non-retryable).
- **HTTP 404 (`NOT_FOUND`):** Classified as `model_unavailable`.
- **HTTP 429 (`RESOURCE_EXHAUSTED`):**
  - If error message indicates billing, plan, or `limit: 0` exhaustion: classified as `quota_exhausted`.
  - Otherwise classified as `rate_limited`; parses `Retry-After` HTTP header and Google RPC `RetryInfo.retryDelay` (e.g. `"28s"`).
- **HTTP 500/502/503/504 (`INTERNAL`, `UNAVAILABLE`, `DEADLINE_EXCEEDED`):** Classified as `provider_unavailable`.

---

## 3. Shared Provider Quota Limitations

Multiple API keys created within the same provider organization or Google Cloud project often share project-level or organization-level rate limits and billing quotas. KEYPOOL is a credential management and reliability utility, not a quota-evasion mechanism. When a credential hits a cooldown and shares a `quota_domain`, `SelectionEngine` avoids rotating blindly to another key in the same cooldown domain.

