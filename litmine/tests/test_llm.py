import io
import json
import urllib.error

import pytest

from litmine.cache import Cache
from litmine.config import BackendConfig
from litmine.llm import FakeLLM, LLMClient, LLMError, parse_json_object


def test_parse_json_tolerates_fences_and_prose():
    assert parse_json_object('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json_object('Sure: {"a": {"b": 2}} done') == {"a": {"b": 2}}
    with pytest.raises(ValueError):
        parse_json_object("no json")


class _Resp(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _client(tmp_path, monkeypatch, responses, name="openai"):
    """responses: list of (status, body) served in order by a patched urlopen."""
    cfg = BackendConfig(name=name, base_url="https://x.test/v1", api_key_env="TEST_KEY",
                        model="m", temperature=None, reasoning_effort="low")
    monkeypatch.setenv("TEST_KEY", "sk-secret-123")
    calls = []

    def fake_urlopen(req, timeout=0):
        calls.append(json.loads(req.data))
        status, body = responses.pop(0)
        if status != 200:
            raise urllib.error.HTTPError(req.full_url, status, "err", {}, io.BytesIO(body))
        return _Resp(body)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    c = LLMClient(cfg, Cache(tmp_path / "c"), max_retries=3, sleep=lambda s: None)
    return c, calls


def _ok(obj):
    return (200, json.dumps({"choices": [{"message": {"content": json.dumps(obj)}, "finish_reason": "stop"}],
                             "usage": {"prompt_tokens": 1, "completion_tokens": 1}}).encode())


def test_retry_on_429_then_success_and_cache(tmp_path, monkeypatch):
    c, calls = _client(tmp_path, monkeypatch, [(429, b"slow down"), _ok({"v": 1})])
    r = c.complete_json("s", "v1", "sys", "usr")
    assert r.parsed == {"v": 1} and not r.cached and len(calls) == 2
    assert calls[0]["model"] == "m" and calls[0]["reasoning_effort"] == "low"
    assert "temperature" not in calls[0] and calls[0]["response_format"] == {"type": "json_object"}
    r2 = c.complete_json("s", "v1", "sys", "usr")
    assert r2.cached and r2.parsed == {"v": 1} and len(calls) == 2
    # a new prompt version misses the cache
    with pytest.raises(IndexError):        # no more canned responses -> proves a live call was attempted
        c.complete_json("s", "v2", "sys", "usr")


def test_non_retryable_error_raises_immediately(tmp_path, monkeypatch):
    c, calls = _client(tmp_path, monkeypatch, [(400, b"bad request"), _ok({})])
    with pytest.raises(LLMError, match="HTTP 400"):
        c.complete_json("s", "v1", "sys", "usr")
    assert len(calls) == 1


def test_gives_up_after_max_retries(tmp_path, monkeypatch):
    c, calls = _client(tmp_path, monkeypatch, [(503, b"")] * 3)
    with pytest.raises(LLMError, match="giving up"):
        c.complete_json("s", "v1", "sys", "usr")
    assert len(calls) == 3


def test_invalid_output_is_re_asked_with_validator(tmp_path, monkeypatch):
    c, calls = _client(tmp_path, monkeypatch, [_ok({"wrong": 1}), _ok({"right": 1})])

    def val(o):
        if "right" not in o:
            raise ValueError("missing 'right'")
        return o
    r = c.complete_json("s", "v1", "sys", "usr", validator=val)
    assert r.parsed == {"right": 1} and len(calls) == 2
    assert "missing 'right'" in calls[1]["messages"][-1]["content"]


def test_api_key_never_written_to_cache(tmp_path, monkeypatch):
    c, _ = _client(tmp_path, monkeypatch, [_ok({"v": 1})])
    c.complete_json("s", "v1", "sys", "usr")
    for p in (tmp_path / "c").rglob("*.json"):
        assert "sk-secret-123" not in p.read_text()


def test_deepseek_payload(tmp_path, monkeypatch):
    c, calls = _client(tmp_path, monkeypatch, [_ok({"v": 1})], name="deepseek")
    c.cfg.temperature = 0.0
    c.complete_json("s", "v1", "sys", "usr")
    assert calls[0]["temperature"] == 0.0 and calls[0]["thinking"] == {"type": "disabled"}
    assert "max_tokens" in calls[0]


def test_fake_llm_records_prompts(tmp_path):
    f = FakeLLM(Cache(tmp_path), lambda st, s, u: {"stage": st, "len": len(u)})
    r = f.complete_json("screen", "v", "S", "U" * 10)
    assert r.parsed == {"stage": "screen", "len": 10} and f.seen[0][0] == "screen"


def test_cached_output_is_revalidated_with_new_validator(tmp_path, monkeypatch):
    c, calls = _client(tmp_path, monkeypatch, [_ok({"label": {"value": "x"}}), _ok({"label": "y"})])
    r = c.complete_json("s", "v1", "sys", "usr")
    assert r.parsed == {"label": {"value": "x"}}
    # a stricter validator applied to the cached raw text -> transformed, no API call
    r2 = c.complete_json("s", "v1", "sys", "usr", validator=lambda o: {"label": str(o["label"].get("value", o["label"]))})
    assert r2.cached and r2.parsed == {"label": "x"} and len(calls) == 1
    # validator that rejects the cached text -> live re-query
    r3 = c.complete_json("s", "v1", "sys", "usr", validator=lambda o: (_ for _ in ()).throw(ValueError("no")) if isinstance(o["label"], dict) else o)
    assert not r3.cached and r3.parsed == {"label": "y"} and len(calls) == 2
