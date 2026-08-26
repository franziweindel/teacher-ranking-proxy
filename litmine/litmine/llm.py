"""Minimal OpenAI-compatible chat client (OpenAI, DeepSeek) with retries,
JSON-object output, and a deterministic FakeLLM for tests.

API keys are read from env only; they are never logged or cached. Every call
records provider, model, temperature/reasoning, prompt version and the input
hash so results are reproducible and attributable.
"""
from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable

from .cache import Cache, cache_key, sha256_text
from .config import BackendConfig, backend_config

log = logging.getLogger("litmine.llm")

RETRYABLE_HTTP = {408, 409, 425, 429, 500, 502, 503, 504}


class LLMError(RuntimeError):
    pass


@dataclass
class LLMResult:
    backend: str
    model: str
    temperature: float | None
    reasoning_effort: str | None
    prompt_version: str
    input_hash: str
    raw_text: str
    parsed: Any
    usage: dict
    cached: bool

    def meta(self) -> dict:
        return {
            "backend": self.backend, "model": self.model,
            "temperature": self.temperature, "reasoning_effort": self.reasoning_effort,
            "prompt_version": self.prompt_version, "input_hash": self.input_hash,
            "usage": self.usage, "cached": self.cached,
        }


def parse_json_object(text: str) -> Any:
    """Tolerant JSON extraction: strips code fences, falls back to the outermost
    {...} block. Raises ValueError if nothing parses."""
    t = text.strip()
    if t.startswith("```"):
        t = t.strip("`")
        if t.lower().startswith("json"):
            t = t[4:]
        t = t.strip()
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        pass
    s, e = t.find("{"), t.rfind("}")
    if 0 <= s < e:
        return json.loads(t[s:e + 1])
    raise ValueError("no JSON object in LLM output")


class LLMClient:
    """One backend. `complete_json` is cached per (stage, prompt_version, model,
    temperature, input hash); the cache is the resumability mechanism."""

    def __init__(self, cfg: BackendConfig, cache: Cache, max_retries: int = 5,
                 sleep: Callable[[float], None] = time.sleep):
        self.cfg = cfg
        self.cache = cache
        self.max_retries = max_retries
        self._sleep = sleep
        self.calls = 0          # live API calls made (not cache hits)

    @property
    def name(self) -> str:
        return self.cfg.name

    # ---- transport -------------------------------------------------------
    def _payload(self, messages: list[dict], max_tokens: int) -> dict:
        payload: dict[str, Any] = {"model": self.cfg.model, "messages": messages,
                                   "response_format": {"type": "json_object"}}
        if self.cfg.name == "openai":
            payload["max_completion_tokens"] = max_tokens
            if self.cfg.reasoning_effort:
                payload["reasoning_effort"] = self.cfg.reasoning_effort
            if self.cfg.temperature is not None:
                payload["temperature"] = self.cfg.temperature
        else:
            payload["max_tokens"] = max_tokens
            if self.cfg.temperature is not None:
                payload["temperature"] = self.cfg.temperature
            if self.cfg.name == "deepseek":
                payload["thinking"] = {"type": "disabled"}
        return payload

    def _post(self, payload: dict) -> dict:
        body = json.dumps(payload).encode()
        req = urllib.request.Request(
            f"{self.cfg.base_url.rstrip('/')}/chat/completions", data=body,
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {self.cfg.api_key}"})
        last: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                with urllib.request.urlopen(req, timeout=self.cfg.timeout) as r:
                    return json.loads(r.read())
            except urllib.error.HTTPError as e:
                detail = ""
                try:
                    detail = e.read().decode()[:500]
                except Exception:
                    pass
                last = LLMError(f"HTTP {e.code} from {self.cfg.name}: {detail}")
                if e.code not in RETRYABLE_HTTP:
                    raise last
            except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
                last = LLMError(f"network error from {self.cfg.name}: {e}")
            wait = min(60.0, 2.0 ** attempt * 2)
            log.warning("%s call failed (attempt %d/%d): %s; retry in %.0fs",
                        self.cfg.name, attempt + 1, self.max_retries, last, wait)
            self._sleep(wait)
        raise LLMError(f"giving up after {self.max_retries} attempts: {last}")

    def raw_complete(self, messages: list[dict], max_tokens: int) -> tuple[str, dict]:
        self.calls += 1
        out = self._post(self._payload(messages, max_tokens))
        choice = out["choices"][0]
        content = choice["message"].get("content") or ""
        if choice.get("finish_reason") == "length":
            log.warning("%s output truncated at max_tokens=%d", self.cfg.name, max_tokens)
        return content, out.get("usage", {})

    # ---- cached structured completion ------------------------------------
    def complete_json(self, stage: str, prompt_version: str, system: str, user: str,
                      validator: Callable[[Any], Any] | None = None,
                      max_tokens: int = 16000, extra_key: Any = None,
                      force: bool = False) -> LLMResult:
        input_hash = sha256_text(system + "\n\x00\n" + user)
        key = cache_key(stage=stage, prompt_version=prompt_version, backend=self.cfg.name,
                        model=self.cfg.model, temperature=self.cfg.temperature,
                        reasoning=self.cfg.reasoning_effort, input_hash=input_hash,
                        extra=extra_key)
        hit = None if force else self.cache.get(f"llm_{stage}", key)
        if hit is not None:
            # Re-validate the cached raw text so validator/schema fixes apply to
            # cached results without an API call; fall through on failure.
            try:
                parsed = parse_json_object(hit["raw_text"])
                if validator is not None:
                    parsed = validator(parsed)
                return LLMResult(cached=True, parsed=parsed, **{k: hit[k] for k in (
                    "backend", "model", "temperature", "reasoning_effort", "prompt_version",
                    "input_hash", "raw_text", "usage")})
            except (ValueError, KeyError, TypeError) as e:
                log.warning("cached %s output no longer validates (%s); re-querying", stage, e)
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        parsed = None
        raw = ""
        usage: dict = {}
        err: Exception | None = None
        for attempt in range(3):   # re-ask on unparseable / schema-invalid output
            raw, usage = self.raw_complete(messages, max_tokens)
            try:
                parsed = parse_json_object(raw)
                if validator is not None:
                    parsed = validator(parsed)
                err = None
                break
            except (ValueError, KeyError, TypeError) as e:
                err = e
                log.warning("%s returned invalid structured output (attempt %d): %s",
                            self.cfg.name, attempt + 1, e)
                messages = messages[:2] + [
                    {"role": "assistant", "content": raw},
                    {"role": "user", "content": f"Your previous output was invalid: {e}. "
                     "Return ONLY a JSON object that satisfies the required schema."}]
        if err is not None:
            raise LLMError(f"{self.cfg.name} never produced valid output for stage {stage}: {err}")
        rec = {"backend": self.cfg.name, "model": self.cfg.model,
               "temperature": self.cfg.temperature,
               "reasoning_effort": self.cfg.reasoning_effort,
               "prompt_version": prompt_version, "input_hash": input_hash,
               "raw_text": raw, "parsed": parsed, "usage": usage,
               "stage": stage, "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        self.cache.put(f"llm_{stage}", key, rec)
        return LLMResult(cached=False, **{k: rec[k] for k in (
            "backend", "model", "temperature", "reasoning_effort", "prompt_version",
            "input_hash", "raw_text", "parsed", "usage")})


class FakeLLM(LLMClient):
    """Deterministic test backend. `responder(stage, system, user) -> dict|str`."""

    def __init__(self, cache: Cache, responder: Callable[[str, str, str], Any],
                 name: str = "fake"):
        cfg = backend_config("fake")
        cfg.name = name
        cfg.model = f"{name}-model"
        super().__init__(cfg, cache, sleep=lambda s: None)
        self.responder = responder
        self._stage = ""
        self.seen: list[tuple[str, str, str]] = []

    def complete_json(self, stage, prompt_version, system, user, **kw):
        self._stage = stage
        return super().complete_json(stage, prompt_version, system, user, **kw)

    def raw_complete(self, messages, max_tokens):
        self.calls += 1
        system, user = messages[0]["content"], messages[1]["content"]
        self.seen.append((self._stage, system, user))
        out = self.responder(self._stage, system, user)
        if not isinstance(out, str):
            out = json.dumps(out)
        return out, {"prompt_tokens": len(system + user) // 4, "completion_tokens": len(out) // 4}


def make_client(name: str, cache: Cache) -> LLMClient:
    return LLMClient(backend_config(name), cache)
