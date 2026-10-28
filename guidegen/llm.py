"""Minimal OpenAI-compatible LLM client for extraction.

Primary: DeepInfra (`/v1/openai/chat/completions`, documented strict json_schema).
Fallback: OpenCode Go (adds the required `x-opencode-session` header).

The key is read from the environment (`.env` is loaded by the caller) and is never
printed, logged or written to a record. Every call reports its usage so the job can
account for tokens.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field

from jsonschema import Draft202012Validator

from .errors import GuideError

RETRYABLE = {429, 500, 502, 503, 504}


@dataclass
class LLMResult:
    data: dict
    model: str
    ms: int
    usage: dict | None
    attempts: int = 1


@dataclass
class LLMClient:
    provider: str
    base_url: str
    api_key: str = field(repr=False)
    model: str
    session_id: str
    max_attempts: int = 5
    timeout: float = 120.0

    @classmethod
    def from_env(cls, provider: str | None = None) -> "LLMClient":
        name = (provider or os.environ.get("LLM_PROVIDER") or "deepinfra").strip().lower()
        if name == "deepinfra":
            key = os.environ.get("DEEPINFRA_API_KEY", "")
            base = os.environ.get("DEEPINFRA_BASE_URL", "https://api.deepinfra.com/v1/openai")
            model = os.environ.get("LLM_MODEL", "deepseek-ai/DeepSeek-V4.1-Flash-1017")
        elif name in ("opencode", "opencode-go"):
            key = os.environ.get("OPENCODE_API_KEY", "")
            base = os.environ.get("OPENCODE_BASE_URL", "https://opencode.ai/inference/go")
            model = os.environ.get("LLM_MODEL", "claude-haiku-5-5")
        else:
            raise GuideError("LLM_NOT_CONFIGURED", f"unknown LLM_PROVIDER {name!r}")
        if not key:
            raise GuideError("LLM_NOT_CONFIGURED", f"missing key for provider {name!r}")
        return cls(
            provider=name,
            base_url=base.rstrip("/"),
            api_key=key,
            model=model,
            session_id="ses_" + uuid.uuid4().hex,
        )

    @property
    def chat_url(self) -> str:
        return self.base_url + "/chat/completions"

    def chat_json(self, messages: list[dict], schema: dict, schema_name: str,
                  max_tokens: int = 3000, temperature: float = 0.0) -> LLMResult:
        payload = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": schema_name, "strict": True, "schema": schema},
            },
        }
        body, ms, attempts = self._post(payload)
        try:
            envelope = json.loads(body)
            message = envelope["choices"][0]["message"]
            if message.get("refusal"):
                raise GuideError("LLM_REFUSED", str(message["refusal"])[:300])
            content = message.get("content")
            if not content or not str(content).strip():
                raise GuideError("LLM_BAD_OUTPUT", "empty content")
            data = json.loads(content)
        except GuideError:
            raise
        except Exception as exc:  # noqa: BLE001 - malformed model output is a known failure
            snippet = str(locals().get("content") or body)[:300]
            if snippet.strip().lower().startswith(("i can't", "i cannot", "i'm sorry", "sorry,")):
                raise GuideError("LLM_REFUSED", snippet) from exc
            raise GuideError("LLM_BAD_OUTPUT", f"response was not JSON: {body[:300]}") from exc

        errors = sorted(Draft202012Validator(schema).iter_errors(data), key=lambda e: list(e.path))
        if errors:
            first = errors[0]
            raise GuideError("LLM_BAD_OUTPUT", f"schema {list(first.path)}: {first.message}")
        return LLMResult(data=data, model=self.model, ms=ms, usage=envelope.get("usage"), attempts=attempts)

    def _post(self, payload: dict) -> tuple[str, int, int]:
        data = json.dumps(payload).encode()
        headers = {"Content-Type": "application/json", "User-Agent": "oauth-guide-gen/0.1"}
        if self.provider == "deepinfra":
            headers["Authorization"] = "Bearer " + self.api_key
        else:
            headers["Authorization"] = "Bearer " + self.api_key
            headers["x-opencode-session"] = self.session_id

        last_error: str | None = None
        for attempt in range(1, self.max_attempts + 1):
            req = urllib.request.Request(self.chat_url, data=data, method="POST", headers=headers)
            started = time.monotonic()
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    return resp.read().decode("utf-8", "replace"), int((time.monotonic() - started) * 1000), attempt
            except urllib.error.HTTPError as exc:
                body = exc.read().decode("utf-8", "replace")
                last_error = f"HTTP {exc.code}: {body[:300]}"
                if exc.code in (401, 403):
                    raise GuideError("LLM_AUTH_FAILED", last_error) from exc
                if exc.code == 404:
                    raise GuideError("LLM_MODEL_UNAVAILABLE", last_error) from exc
                if exc.code in RETRYABLE and attempt < self.max_attempts:
                    time.sleep(min(20, 2 ** attempt))
                    continue
                code = "LLM_RATE_LIMITED" if exc.code == 429 else "LLM_BAD_OUTPUT"
                raise GuideError(code, last_error) from exc
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                if attempt < self.max_attempts:
                    time.sleep(2 * attempt)
                    continue
                raise GuideError("LLM_TIMEOUT", last_error) from exc
        raise GuideError("LLM_BAD_OUTPUT", last_error or "no response")
