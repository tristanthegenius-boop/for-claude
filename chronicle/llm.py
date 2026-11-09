"""OpenRouter client. Any model on OpenRouter works: pass its id (e.g. 'vendor/model')."""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field

import requests

API = "https://openrouter.ai/api/v1"


class LLMError(Exception):
    pass


@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost: float = 0.0
    calls: int = 0

    def add(self, u: dict | None) -> None:
        if not u:
            return
        self.calls += 1
        self.prompt_tokens += int(u.get("prompt_tokens") or 0)
        self.completion_tokens += int(u.get("completion_tokens") or 0)
        self.cost += float(u.get("cost") or 0.0)


@dataclass
class OpenRouter:
    api_key: str | None = None
    timeout: int = 600
    max_retries: int = 5
    usage: Usage = field(default_factory=Usage)

    def __post_init__(self):
        self.api_key = self.api_key or os.environ.get("OPENROUTER_API_KEY")

    def _headers(self) -> dict:
        if not self.api_key:
            raise LLMError("OPENROUTER_API_KEY is not set. Export it or put it in the project .env file.")
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "X-Title": "Chronicle Runtime",
        }

    def chat(
        self,
        model: str,
        system: str,
        user: str,
        *,
        temperature: float = 0.8,
        max_tokens: int = 12000,
        json_mode: bool = False,
        reasoning: dict | None = None,
        extra: dict | None = None,
    ) -> str:
        sys_content: object = system
        if model.startswith("anthropic/"):
            # Prompt caching: the engine spec is large and identical on every call.
            sys_content = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
        body = {
            "model": model,
            "messages": [
                {"role": "system", "content": sys_content},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "usage": {"include": True},
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        if reasoning:
            body["reasoning"] = reasoning
        body.update(extra or {})
        delay = 2.0
        last = None
        for _ in range(self.max_retries):
            try:
                r = requests.post(f"{API}/chat/completions", headers=self._headers(), json=body, timeout=self.timeout)
            except requests.RequestException as e:
                last = str(e)
            else:
                if r.status_code == 200:
                    data = r.json()
                    if "error" in data:
                        last = json.dumps(data["error"])[:500]
                    else:
                        self.usage.add(data.get("usage"))
                        choice = data["choices"][0]
                        content = choice["message"].get("content") or ""
                        if content.strip():
                            return content
                        if choice.get("finish_reason") == "length":
                            raise LLMError(
                                f"{model}: output budget ({max_tokens} tokens) used up before any answer "
                                "(reasoning tokens count against it); raise max_tokens or lower the reasoning budget"
                            )
                        last = "empty completion"
                elif r.status_code in (408, 429, 500, 502, 503, 504):
                    last = f"HTTP {r.status_code}: {r.text[:300]}"
                else:
                    raise LLMError(f"OpenRouter HTTP {r.status_code}: {r.text[:500]}")
            time.sleep(delay)
            delay *= 2
        raise LLMError(f"OpenRouter failed after {self.max_retries} attempts: {last}")


def list_models(query: str | None = None) -> list[dict]:
    r = requests.get(f"{API}/models", timeout=60)
    r.raise_for_status()
    models = r.json().get("data", [])
    if query:
        q = query.lower()
        models = [m for m in models if q in m["id"].lower() or q in (m.get("name") or "").lower()]
    return models


def parse_json(text: str) -> dict:
    """Tolerant JSON extraction (models sometimes wrap JSON in prose or fences)."""
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else t
        t = t.rsplit("```", 1)[0]
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        s, e = t.find("{"), t.rfind("}")
        if s >= 0 and e > s:
            return json.loads(t[s : e + 1])
        raise
