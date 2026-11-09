# KEYPOOL Python SDK Reference

The KEYPOOL Python SDK exposes both synchronous and asynchronous programmatic interfaces for OpenAI-compatible chat completions, text embeddings, token budget enforcement, and multi-credential rotation.

---

## 1. Installation

```bash
pip install keypool
```

---

## 2. Synchronous Client (`KeypoolClient`)

```python
from keypool.sdk import KeypoolClient, ChatMessage

# Initialize client using active KEYPOOL profile
client = KeypoolClient(profile="development")

# 1. Chat Completion (Non-streaming)
response = client.chat.completions.create(
    model="llama-3.3-70b-versatile",
    messages=[
        ChatMessage(role="system", content="You are a helpful coding assistant."),
        ChatMessage(role="user", content="Write a Python Fibonacci generator."),
    ],
    temperature=0.7,
    max_tokens=500,
)

print(response.choices[0].message.content)
print(f"Observed tokens: {response.usage.total_tokens if response.usage else 'n/a'}")

# 2. SSE Streaming Chat Completion
stream = client.chat.completions.create(
    model="llama-3.3-70b-versatile",
    messages=[ChatMessage(role="user", content="Count from 1 to 10")],
    stream=True,
)

for chunk in stream:
    if chunk.choices and chunk.choices[0].delta.content:
        print(chunk.choices[0].delta.content, end="", flush=True)

# 3. Text Embeddings
embeddings_resp = client.embeddings.create(
    model="text-embedding-3-small",
    input=["Retrieval Augmented Generation", "Universal AI Credentials"],
)

for item in embeddings_resp.data:
    print(f"Embedding index {item.index}, vector length: {len(item.embedding)}")
```

---

## 3. Asynchronous Client (`AsyncKeypoolClient`)

```python
import asyncio
from keypool.sdk import AsyncKeypoolClient, ChatMessage

async def main():
    async with AsyncKeypoolClient(profile="production") as client:
        # Chat completion
        resp = await client.chat.completions.create(
            model="gpt-4o",
            messages=[ChatMessage(role="user", content="Hello world!")],
        )
        print(resp.choices[0].message.content)

        # Async embeddings
        emb = await client.embeddings.create(
            model="text-embedding-3-small",
            input="Semantic search query",
        )
        print(emb.data[0].embedding[:5])

asyncio.run(main())
```

---

## 4. Key Management & Context Manager

Retrieve secrets programmatically with automatic auditing:

```python
with KeypoolClient() as client:
    key = client.get_key(provider="groq")
    print(f"Selected key: {key.credential_name} ({key.credential_id})")
    # key.secret contains the decrypted API key from macOS Keychain
```

---

## 5. Budget Protection & Forecasting

The SDK automatically evaluates active budgets prior to making network requests. If a daily, weekly, or monthly hard limit is reached for the selected provider or model, a `ProviderExecutionError` is raised immediately before any network traffic is initiated.
