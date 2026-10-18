"""AI services a viewer can pick on the configure page (PG_USER_KEYS=on), and the steps to get a free key.

Every one speaks the OpenAI chat format. Free limits change often: these were checked on 18 Oct 2026.
A title takes about 4,000 tokens over 2 calls, which is where the "titles a day" figures come from.
"""
from __future__ import annotations

from typing import List, Optional

PROVIDERS = [
    {
        "id": "groq",
        "name": "Groq",
        "base_url": "https://api.groq.com/openai/v1",
        # Groq's free limits apply to each model separately, so when one model runs out the next takes over.
        "models": ["openai/gpt-oss-120b", "qwen/qwen3.8-27b", "openai/gpt-oss-20b"],
        "titles": "About 150 Titles a Day",
        "blurb": "Recommended. Fast, reliable, and the model Couch Check is tuned on. No card, no phone number.",
        "signup": "https://console.groq.com",
        "keys": "https://console.groq.com/keys",
        "steps": [
            'Open <a href="https://console.groq.com" target="_blank" rel="noopener">console.groq.com</a> and sign up '
            "with Google, GitHub or an email address.",
            'Go to <a href="https://console.groq.com/keys" target="_blank" rel="noopener">API Keys</a> and click '
            "<b>Create API Key</b>. Name it Couch Check.",
            "Copy the key (it starts with <code>gsk_</code>). Groq shows it only once.",
        ],
        "notes": "Don't add a payment method: the free plan simply stops for the day when it runs out, so it never costs anything.",
    },
    {
        "id": "gemini",
        "name": "Google Gemini",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "models": ["gemini-3.5-flash-lite", "gemini-3.1-flash-lite"],
        "titles": "About 250 Titles a Day",
        "blurb": "The most free titles a day, and you only need a Google account.",
        "signup": "https://aistudio.google.com",
        "keys": "https://aistudio.google.com/apikey",
        "steps": [
            'Open <a href="https://aistudio.google.com" target="_blank" rel="noopener">Google AI Studio</a> and sign in '
            "with your Google account.",
            'Go to <a href="https://aistudio.google.com/apikey" target="_blank" rel="noopener">Get API Key</a> and click '
            "<b>Create API Key</b>.",
            "Copy the key (it starts with <code>AIza</code>).",
        ],
        "notes": "On the free plan, Google may use what's sent to improve its products. Here that's only IMDb's public "
                 "scene descriptions, never anything about you.",
    },
    {
        "id": "mistral",
        "name": "Mistral",
        "base_url": "https://api.mistral.ai/v1",
        "models": ["mistral-medium-latest", "mistral-small-latest"],
        "titles": "Hundreds of Titles a Day",
        "blurb": "Generous free plan, but it asks for a phone number.",
        "signup": "https://console.mistral.ai",
        "keys": "https://console.mistral.ai/api-keys",
        "steps": [
            'Open <a href="https://console.mistral.ai" target="_blank" rel="noopener">console.mistral.ai</a> and sign up.',
            "Choose the free <b>Experiment</b> plan and confirm your email and phone number.",
            'Go to <a href="https://console.mistral.ai/api-keys" target="_blank" rel="noopener">API Keys</a>, click '
            "<b>Create new key</b> and copy it.",
        ],
        "notes": "The free plan lets Mistral use what's sent to train its models: only IMDb's public scene descriptions.",
    },
    {
        "id": "openrouter",
        "name": "OpenRouter",
        "base_url": "https://openrouter.ai/api/v1",
        "models": ["nvidia/nemotron-3-super-120b-a12b:free", "google/gemma-4-31b-it:free"],
        "titles": "About 25 Titles a Day",
        "blurb": "Many free models in one place, with a small daily allowance.",
        "signup": "https://openrouter.ai",
        "keys": "https://openrouter.ai/settings/keys",
        "steps": [
            'Open <a href="https://openrouter.ai" target="_blank" rel="noopener">openrouter.ai</a> and sign up.',
            'In <a href="https://openrouter.ai/settings/privacy" target="_blank" rel="noopener">Privacy settings</a>, '
            "allow free models (otherwise they answer “model not found”).",
            'Go to <a href="https://openrouter.ai/settings/keys" target="_blank" rel="noopener">Keys</a>, click '
            "<b>Create Key</b> and copy it (it starts with <code>sk-or-</code>).",
        ],
        "notes": "Free model names change often. If you see “AI Model Not Found”, pick another free model.",
    },
]
CUSTOM = {"id": "custom", "name": "Other (OpenAI-Compatible)", "base_url": "", "models": [""],
          "titles": "", "blurb": "Any service that speaks the OpenAI chat format: paste its address and model name."}


def by_base_url(base_url: str) -> Optional[dict]:
    base_url = (base_url or "").rstrip("/")
    return next((p for p in PROVIDERS if p["base_url"] == base_url), None)


def models_for(base_url: str, model: str) -> List[str]:
    """The model to try first, then the provider's other free models as backups (for the preset's main model only,
    so a viewer who picked a model on purpose gets just that one)."""
    preset = by_base_url(base_url)
    if preset and model == preset["models"][0]:
        return list(preset["models"])
    return [model]
