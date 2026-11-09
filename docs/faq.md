# KEYPOOL Frequently Asked Questions (FAQ)

### Q: Where does KEYPOOL actually store my API keys?
**A:** Only inside the encrypted **macOS Keychain**. The SQLite database (`~/.config/keypool/keypool.db`) stores only non-sensitive metadata such as a UUID reference identifier (`keypool:<provider>:<uuid>`), provider name, health status, and timestamps. API keys never touch disk files.

---

### Q: How does KEYPOOL handle rate limits (HTTP 429)?
**A:** KEYPOOL automatically parses `Retry-After` headers and provider response payloads. When a 429 is encountered:
1. The credential state is updated to `RATE_LIMITED` with an exponential cooldown timer.
2. The selection engine immediately fails over to the next eligible healthy credential in the same profile or quota domain.
3. Once the cooldown expires, the credential is automatically restored to the rotation pool.

---

### Q: What selection strategies are supported?
**A:** KEYPOOL supports 9 distinct multi-key selection strategies:
1. `priority` (highest assigned priority score first)
2. `round_robin` (balanced alternating cycle across keys)
3. `lru` (least-recently-used timestamp first)
4. `health_first` (lowest error rate and fewest consecutive failures)
5. `latency` (lowest observed probe and execution response time)
6. `cost` (prefer lowest-cost model and credential pricing tier)
7. `random` (uniform pseudo-random distribution across healthy keys)
8. `weighted` (exponential weighted probability based on priority)
9. `manual` (strict fixed sequence defined in profile assignment)

---

### Q: Can I use KEYPOOL with existing OpenAI SDK scripts?
**A:** Yes! Start the local gateway:
```bash
keypool gateway start --port 8080
```
Then configure your OpenAI client:
```python
from openai import OpenAI

client = OpenAI(
    base_url="http://127.0.0.1:8080/v1",
    api_key="ignored-by-gateway",
)

response = client.chat.completions.create(
    model="llama-3.3-70b-versatile",
    messages=[{"role": "user", "content": "Hello!"}],
)
```
The gateway terminates incoming calls, authenticates via ephemeral bearer tokens, rotates keys automatically, and logs local token analytics.
