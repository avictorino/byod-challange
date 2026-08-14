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
from dataclasses import dataclass, field
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

    Token counts are the REAL numbers each API returns (Ollama's
    prompt_eval_count/eval_count, OpenAI's usage.prompt_tokens/
    completion_tokens) — _approx_tokens() is only a fallback if a response
    is ever missing them. OpenAI input and output tokens are kept separate
    because they're priced differently. Also grouped by `role`
    (generator/reviewer/repair/escalate) so the closing report shows where
    calls/tokens actually went, not just a flat total.
    """

    calls: int = 0
    openai_calls: int = 0
    ollama_tokens: int = 0
    openai_input_tokens: int = 0
    openai_output_tokens: int = 0
    by_role: dict = field(default_factory=dict)  # role -> {"calls", "provider", "tokens"}

    def add(self, provider: str, prompt_tokens: int, completion_tokens: int, role: str = "") -> None:
        self.calls += 1
        total = prompt_tokens + completion_tokens
        if provider == "openai":
            self.openai_calls += 1
            self.openai_input_tokens += prompt_tokens
            self.openai_output_tokens += completion_tokens
        else:
            self.ollama_tokens += total

        key = role or provider
        bucket = self.by_role.setdefault(key, {"calls": 0, "tokens": 0, "provider": provider})
        bucket["calls"] += 1
        bucket["tokens"] += total

    @property
    def openai_tokens(self) -> int:
        return self.openai_input_tokens + self.openai_output_tokens

    @property
    def approx_tokens(self) -> int:
        return self.ollama_tokens + self.openai_tokens

    @property
    def approx_cost_usd(self) -> float:
        # Only OpenAI tokens are priced — Ollama is always $0 regardless of
        # volume. Defaults are GPT-5.6 Luna's real published per-1M rates
        # (openai.com/index/gpt-5-6), since that's the model this project's
        # .env.example actually configures via OPENAI_MODEL. Input/output
        # are priced separately because they cost differently. Override via
        # OPENAI_INPUT_PRICE_PER_1M / OPENAI_OUTPUT_PRICE_PER_1M if
        # OPENAI_MODEL points at a different GPT-5.6 tier:
        #   Sol (flagship):   $5.00 in / $30.00 out
        #   Terra (balanced): $2.00 in / $12.00 out
        #   Luna (default):   $0.20 in /  $1.20 out
        input_rate = float(os.environ.get("OPENAI_INPUT_PRICE_PER_1M", "0.20"))
        output_rate = float(os.environ.get("OPENAI_OUTPUT_PRICE_PER_1M", "1.20"))
        cost = self.openai_input_tokens / 1_000_000 * input_rate
        cost += self.openai_output_tokens / 1_000_000 * output_rate
        return round(cost, 4)

    def summary_line(self) -> str:
        """One-line grouped breakdown for the Finalize log — which roles
        actually made calls, to which provider, and how many tokens each."""
        parts = [
            f"{role}={b['calls']}call/{b['tokens']}tok/{b['provider']}"
            for role, b in sorted(self.by_role.items())
        ]
        return " ".join(parts)


usage = Usage()


class LLMClient(ABC):
    """Base class for a chat-style LLM backend: system+user prompt in, text out."""

    provider: str = "base"

    def __init__(self, model: str, timeout: int = 240):
        self.model = model
        self.timeout = timeout

    @abstractmethod
    def _call(self, system: str, user: str) -> tuple[str, int, int]:
        """Provider-specific HTTP call. Returns (text, prompt_tokens, completion_tokens) —
        real counts from the API's own response, not an estimate."""

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
                text, prompt_tokens, completion_tokens = self._call(system, user)
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
        usage.add(self.provider, prompt_tokens, completion_tokens, role=label)
        logger.info(
            f"{label} -> {self.provider}:{self.model} "
            f"(ok, {prompt_tokens}+{completion_tokens} tok, {elapsed:.1f}s)",
            extra={"stage": "llm"},
        )
        return text


class OllamaClient(LLMClient):
    """Local Ollama backend, called via its HTTP API (no ollama-python SDK)."""

    provider = "ollama"

    def __init__(self, model: str, base_url: str | None = None, timeout: int = 240):
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

    def _call(self, system: str, user: str) -> tuple[str, int, int]:
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
        data = resp.json()
        text = data["message"]["content"]
        # Ollama returns real counts (its own tokenizer) in a non-streamed
        # response; fall back to the char estimate only if a field is missing.
        prompt_tokens = data.get("prompt_eval_count") or _approx_tokens(system + user)
        completion_tokens = data.get("eval_count") or _approx_tokens(text)
        return text, prompt_tokens, completion_tokens


class OpenAIClient(LLMClient):
    """OpenAI backend, called via the plain HTTP chat completions API."""

    provider = "openai"

    def __init__(self, model: str, api_key: str | None = None, timeout: int = 240):
        super().__init__(model, timeout)
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY")
        if not self.api_key:
            raise LLMConfigError(
                f"Model '{self.model}' requires OpenAI, but OPENAI_API_KEY is not set in .env "
                "(see .env.example)."
            )

    def _call(self, system: str, user: str) -> tuple[str, int, int]:
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
        data = resp.json()
        text = data["choices"][0]["message"]["content"]
        # OpenAI always returns real counts in `usage`; fall back to the
        # char estimate only if that field is ever missing.
        api_usage = data.get("usage") or {}
        prompt_tokens = api_usage.get("prompt_tokens") or _approx_tokens(system + user)
        completion_tokens = api_usage.get("completion_tokens") or _approx_tokens(text)
        return text, prompt_tokens, completion_tokens


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
