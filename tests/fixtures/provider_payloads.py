"""Reusable mock provider payloads (never contains real API credentials)."""

from __future__ import annotations

from typing import Any

# Synthetically constructed test tokens (clearly fake, non-functional strings)
FAKE_GROQ_KEY_1 = "gsk_TESTFAKEKEY000000000000000000001"
FAKE_GROQ_KEY_2 = "gsk_TESTFAKEKEY000000000000000000002"
FAKE_GROQ_KEY_3 = "gsk_TESTFAKEKEY000000000000000000003"
FAKE_GEMINI_KEY_1 = "AIzaSyTESTFAKEKEY00000000000000000001"
FAKE_GEMINI_KEY_2 = "AIzaSyTESTFAKEKEY00000000000000000002"

GROQ_MODELS_SUCCESS_PAYLOAD: dict[str, Any] = {
    "object": "list",
    "data": [
        {
            "id": "llama-3.1-8b-instant",
            "object": "model",
            "owned_by": "Meta",
            "active": True,
        },
        {
            "id": "llama-3.3-70b-versatile",
            "object": "model",
            "owned_by": "Meta",
            "active": True,
        },
        {
            "id": "whisper-large-v3",
            "object": "model",
            "owned_by": "OpenAI",
            "active": True,
        },
    ],
}

GROQ_CHAT_COMPLETION_PAYLOAD: dict[str, Any] = {
    "id": "chatcmpl-test123",
    "object": "chat.completion",
    "model": "llama-3.1-8b-instant",
    "choices": [
        {
            "index": 0,
            "message": {"role": "assistant", "content": "pong"},
            "finish_reason": "length",
        }
    ],
    "usage": {
        "prompt_tokens": 4,
        "completion_tokens": 1,
        "total_tokens": 5,
    },
}

GEMINI_MODELS_SUCCESS_PAYLOAD: dict[str, Any] = {
    "models": [
        {
            "name": "models/gemini-2.5-flash",
            "displayName": "Gemini 2.5 Flash",
            "supportedGenerationMethods": ["generateContent", "countTokens"],
        },
        {
            "name": "models/gemini-2.5-pro",
            "displayName": "Gemini 2.5 Pro",
            "supportedGenerationMethods": ["generateContent", "countTokens"],
        },
        {
            "name": "models/text-embedding-004",
            "displayName": "Text Embedding 004",
            "supportedGenerationMethods": ["embedContent"],
        },
    ]
}

GEMINI_GENERATE_SUCCESS_PAYLOAD: dict[str, Any] = {
    "candidates": [
        {
            "content": {
                "parts": [{"text": "p"}],
                "role": "model",
            },
            "finishReason": "MAX_TOKENS",
        }
    ],
    "usageMetadata": {
        "promptTokenCount": 2,
        "candidatesTokenCount": 1,
        "totalTokenCount": 3,
    },
}
