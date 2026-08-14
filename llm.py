"""LLM client abstraction.

One abstract base class (`LLMClient`), two concrete providers
(`OllamaClient`, `OpenAIClient`), and a `LLMClientFactory` that picks one
based on the `.env` config. Every call goes through `requests` directly —
no provider SDKs, per the project's constraints.

There is deliberately **no fallback** between providers: if the model
configured for a role isn't available (Ollama doesn't have the tag pulled,
or `OPENAI_API_KEY` is missing), the client raises `LLMConfigError`
immediately at construction time, before the graph does any real work.
"""
from __future__ import annotations

import logging
import os
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Literal

import requests

logger = logging.getLogger("agent")

_ROLE_ENV_VAR = {
    "generator": "CODE_GENERATOR_MODEL",
    "reviewer": "CODE_REVIEW_MODEL",
}


class LLMConfigError(RuntimeError):
    """A configured model/provider can't be used (missing key, model not pulled, unreachable host)."""


def _approx_tokens(text: str) -> int:
    # No tokenizer dependency in this project: ~4 chars/token is the standard
    # rough estimate, good enough for the cost summary printed at the end.
    return max(1, len(text) // 4)


@dataclass
class Usage:
    """Running token/cost tally across the whole run, for the final cost summary.

    Ollama and OpenAI tokens are tallied separately (not a proportional
    guess over the whole run's total) — cost is computed only from real
    OpenAI usage, since Ollama runs locally and is always $0.
    """

    calls: int = 0
    openai_calls: int = 0
    ollama_tokens: int = 0
    openai_tokens: int = 0

    def add(self, provider: str, prompt: str, completion: str) -> None:
        self.calls += 1
        tokens = _approx_tokens(prompt) + _approx_tokens(completion)
        if provider == "openai":
            self.openai_calls += 1
            self.openai_tokens += tokens
        else:
            self.ollama_tokens += tokens

    @property
    def approx_tokens(self) -> int:
        return self.ollama_tokens + self.openai_tokens

    @property
    def approx_cost_usd(self) -> float:
        # Only OPENAI_TOKENS are priced — Ollama contributes 0 regardless of
        # volume. The $/1K rate is an estimate, not a real price table: this
        # project doesn't know the actual billed rate for whatever model
        # name ends up in OPENAI_MODEL (e.g. gpt-5.6-luna isn't a model this
        # code has pricing data for). Set OPENAI_PRICE_PER_1K_TOKENS in .env
        # to your real billed rate for an accurate number.
        rate = float(os.environ.get("OPENAI_PRICE_PER_1K_TOKENS", "0.50"))
        return round(self.openai_tokens / 1000 * rate, 4)


usage = Usage()


class LLMClient(ABC):
    """Base class for a chat-style LLM backend: system+user prompt in, text out."""

    provider: str = "base"

    def __init__(self, model: str, timeout: int = 240):
        self.model = model
        self.timeout = timeout

    @abstractmethod
    def _call(self, system: str, user: str) -> str:
        """Provider-specific HTTP call. Returns the raw completion text."""

    def complete(self, system: str, user: str, *, role: str = "") -> str:
        """Run one completion and log the outcome under the LLM stage.

        One transient-network retry (timeout/connection reset) against the
        SAME configured provider — not a fallback to a different one, just
        the ordinary resilience a local model under load needs. A large
        generation prompt legitimately took >120s during testing, hence
        both this retry and the higher default timeout above.
        """
        label = role or self.provider
        start = time.monotonic()
        for attempt in (1, 2):
            try:
                text = self._call(system, user)
                break
            except requests.exceptions.RequestException as exc:
                if attempt == 2:
                    logger.info(
                        f"{label} -> {self.provider}:{self.model} FAILED ({exc})",
                        extra={"stage": "llm"},
                    )
                    raise
                logger.info(
                    f"{label} -> {self.provider}:{self.model} network error ({exc}), retrying once...",
                    extra={"stage": "llm"},
                )
                time.sleep(2)
        elapsed = time.monotonic() - start
        usage.add(self.provider, system + user, text)
        logger.info(
            f"{label} -> {self.provider}:{self.model} "
            f"(ok, ~{_approx_tokens(system + user + text)} tok, {elapsed:.1f}s)",
            extra={"stage": "llm"},
        )
        return text


class OllamaClient(LLMClient):
    """Local Ollama backend, called via its HTTP API (no ollama-python SDK)."""

    provider = "ollama"

    def __init__(self, model: str, base_url: str | None = None, timeout: int = 120):
        super().__init__(model, timeout)
        self.base_url = (base_url or os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")).rstrip("/")
        self._verify_model_available()

    def _verify_model_available(self) -> None:
        try:
            resp = requests.get(f"{self.base_url}/api/tags", timeout=5)
            resp.raise_for_status()
        except requests.RequestException as exc:
            raise LLMConfigError(
                f"Could not reach Ollama at {self.base_url} ({exc}). Is `ollama serve` running?"
            ) from exc
        names = {m.get("name") or m.get("model") for m in resp.json().get("models", [])}
        if self.model not in names:
            raise LLMConfigError(
                f"Model '{self.model}' is not pulled in Ollama at {self.base_url}. "
                f"Available: {sorted(n for n in names if n)}. Run `ollama pull {self.model}` "
                "or point CODE_GENERATOR_MODEL/CODE_REVIEW_MODEL at a different model."
            )

    def _call(self, system: str, user: str) -> str:
        resp = requests.post(
            f"{self.base_url}/api/chat",
            json={
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "stream": False,
            },
            timeout=self.timeout,
        )
        resp.raise_for_status()
        return resp.json()["message"]["content"]


class OpenAIClient(LLMClient):
    """OpenAI backend, called via the plain HTTP chat completions API."""

    provider = "openai"

    def __init__(self, model: str, api_key: str | None = None, timeout: int = 120):
        super().__init__(model, timeout)
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY")
        if not self.api_key:
            raise LLMConfigError(
                f"Model '{self.model}' requires OpenAI, but OPENAI_API_KEY is not set in .env "
                "(see .env.example)."
            )

    def _call(self, system: str, user: str) -> str:
        resp = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            },
            timeout=self.timeout,
        )
        if resp.status_code == 404:
            raise LLMConfigError(f"OpenAI model '{self.model}' not found (404): {resp.text}")
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]


class LLMClientFactory:
    """Builds the right `LLMClient` subclass for a role, from `.env` config.

    Routing rule: a model name containing ":" follows Ollama's tag
    convention (e.g. "gemma4:12b") and is routed to `OllamaClient`; any
    other name (e.g. "gpt-5.6-luna") is treated as an OpenAI model id and
    routed to `OpenAIClient`. No fallback for `create()` — a missing/
    unavailable model raises `LLMConfigError` instead of silently trying
    the other provider. `create_escalation()` is the one deliberate
    exception: an explicit, opt-in, capped-at-once-per-run escalation to
    OpenAI, used by graph.py only after every local retry is spent.
    """

    @staticmethod
    def create(role: Literal["generator", "reviewer"]) -> LLMClient:
        env_var = _ROLE_ENV_VAR[role]
        model = os.environ.get(env_var)
        if not model:
            raise LLMConfigError(f"{env_var} is not set in .env (see .env.example).")

        client: LLMClient = OllamaClient(model) if ":" in model else OpenAIClient(model)

        logger.info(f"{role} -> {client.provider}:{client.model}", extra={"stage": "factory"})
        return client

    @staticmethod
    def create_escalation() -> LLMClient | None:
        """Build the last-resort OpenAI client for a repair escalation.

        Returns None if OPENAI_API_KEY isn't set — escalation is opt-in by
        the presence of that key alone, no separate flag to configure.
        """
        if not os.environ.get("OPENAI_API_KEY"):
            return None
        model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
        return OpenAIClient(model)
