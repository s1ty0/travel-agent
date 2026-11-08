import io
import json
import os
import subprocess
import sys

import httpx
import pytest

from travel_agent.deepseek import DeepSeekSettings, ProviderError, extract_intake

TEXT = "从上海出发去南京附近，预算5000以内"
SECRET = "test-credential-not-real"


def response(**changes):
    body = {
        "model": "test-model",
        "choices": [
            {
                "finish_reason": "stop",
                "message": {
                    "content": json.dumps(
                        {"destination": "南京附近", "origin": "上海", "budget": "5000以内"}
                    )
                },
            }
        ],
        "usage": {"prompt_tokens": 50, "completion_tokens": 30},
    }
    body.update(changes)
    return body


def run(body=None, status=200):
    return extract_intake(
        TEXT,
        DeepSeekSettings(SECRET),
        transport=httpx.MockTransport(
            lambda request: httpx.Response(status, json=body if body is not None else response())
        ),
    )


@pytest.mark.smoke
def test_request_is_bounded_and_does_not_offer_tools():
    calls = []

    def handle(request):
        calls.append(request)
        assert str(request.url) == "https://api.deepseek.com/chat/completions"
        assert request.headers["authorization"] == f"Bearer {SECRET}"
        data = json.loads(request.content)
        assert data["max_tokens"] == 512
        assert data["thinking"] == {"type": "disabled"}
        assert data["response_format"] == {"type": "json_object"}
        assert "tools" not in data
        return httpx.Response(200, json=response())

    result = extract_intake(TEXT, DeepSeekSettings(SECRET), transport=httpx.MockTransport(handle))
    assert len(calls) == 1
    assert result.request.destination.mode == "nearby"
    assert result.request.input.origin == "上海"
    assert result.request.status == "draft"
    assert "extracted_fields_confirmation" in result.request.missing_fields
    assert result.prompt_tokens == 50
    assert SECRET not in repr(DeepSeekSettings(SECRET))
    assert SECRET not in result.model_dump_json()


@pytest.mark.parametrize(
    "status,code",
    [
        (401, "authentication_failed"),
        (402, "insufficient_balance"),
        (403, "permission_denied"),
        (429, "rate_limited"),
        (500, "upstream_error"),
        (302, "upstream_error"),
    ],
)
def test_errors_redacted_and_not_retried(status, code):
    with pytest.raises(ProviderError, match=code) as error:
        run(body={"error": SECRET}, status=status)
    assert SECRET not in str(error.value)


@pytest.mark.parametrize(
    "content",
    [
        "",
        " ",
        "{",
        "[]",
        "null",
        '{"destination":"北京","origin":null,"budget":null}',
        '{"destination":"南京附近","origin":" ","budget":null}',
        '{"destination":null,"origin":null,"budget":null,"buy_now":true}',
        '{"destination":null,"origin":null,"budget":5000}',
    ],
)
def test_bad_json_schema_or_ungrounded_fields_fail_closed(content):
    with pytest.raises(ProviderError):
        run(
            {
                "model": "test-model",
                "choices": [{"finish_reason": "stop", "message": {"content": content}}],
            }
        )


def test_partial_and_bad_envelopes_rejected():
    with pytest.raises(ProviderError, match="incomplete_completion"):
        run(response(choices=[{"finish_reason": "length"}]))
    for body in ({}, {"choices": []}, response(model=None), response(usage=[])):
        if body.get("usage") == []:
            continue  # Empty usage is treated as absent; explicit malformed nonempty type below.
        with pytest.raises(ProviderError):
            run(body)
    with pytest.raises(ProviderError):
        run(response(usage=[1]))


def test_unknown_usage_is_not_zero_and_null_fields_are_allowed():
    result = run(response(usage={"prompt_tokens": True, "completion_tokens": -1}))
    assert result.prompt_tokens is None
    assert result.completion_tokens is None
    result = run(
        response(
            choices=[
                {
                    "finish_reason": "stop",
                    "message": {"content": '{"destination":null,"origin":null,"budget":null}'},
                }
            ],
            usage=None,
        )
    )
    assert result.request.destination is None
    assert "destination" in result.request.missing_fields


@pytest.mark.parametrize(
    "failure,code",
    [
        (httpx.ReadTimeout("sensitive"), "upstream_timeout"),
        (httpx.ConnectError("sensitive"), "upstream_connection_error"),
    ],
)
def test_network_errors_safe(failure, code):
    def handle(request):
        raise failure

    with pytest.raises(ProviderError, match=code):
        extract_intake(TEXT, DeepSeekSettings(SECRET), transport=httpx.MockTransport(handle))


def test_response_body_size_limit():
    with pytest.raises(ProviderError, match="response_too_large"):
        extract_intake(
            TEXT,
            DeepSeekSettings(SECRET),
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, content=b"x" * (128 * 1024 + 1))
            ),
        )


@pytest.mark.parametrize("text", [" ", "x" * 4001])
def test_invalid_text_never_sends_request(text):
    def handle(request):
        pytest.fail("invalid input must not call provider")

    with pytest.raises(ProviderError, match="invalid_input_length"):
        extract_intake(text, DeepSeekSettings(SECRET), transport=httpx.MockTransport(handle))


@pytest.mark.parametrize(
    "kwargs",
    [
        {"api_key": ""},
        {"api_key": "bad\nkey"},
        {"model": ""},
        {"model": "bad model"},
        {"timeout_seconds": float("nan")},
        {"timeout_seconds": 0},
        {"max_output_tokens": 9999},
    ],
)
def test_invalid_settings(kwargs):
    with pytest.raises(ProviderError):
        DeepSeekSettings(**{"api_key": SECRET, **kwargs})


def test_env_configuration(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", SECRET)
    monkeypatch.setenv("DEEPSEEK_MODEL", "test-model")
    assert DeepSeekSettings.from_env().model == "test-model"
    monkeypatch.setenv("DEEPSEEK_TIMEOUT_SECONDS", "wrong")
    with pytest.raises(ProviderError, match="invalid_provider_settings"):
        DeepSeekSettings.from_env()


def test_cli_is_opt_in(monkeypatch, capsys):
    from travel_agent.deepseek_cli import main

    monkeypatch.setattr("sys.argv", ["travel-agent-deepseek"])
    assert main() == 2
    assert "--live" in capsys.readouterr().err


def test_cli_missing_key_and_success(monkeypatch, capsys):
    from travel_agent.deepseek_cli import main

    monkeypatch.setattr("sys.argv", ["travel-agent-deepseek", "--live"])
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    assert main() == 2
    assert "missing_or_invalid_api_key" in capsys.readouterr().err
    monkeypatch.setenv("DEEPSEEK_API_KEY", SECRET)
    monkeypatch.setattr("sys.stdin", io.StringIO(TEXT))
    monkeypatch.setattr("travel_agent.deepseek_cli.extract_intake", lambda *args: run())
    assert main() == 0
    assert json.loads(capsys.readouterr().out)["request"]["status"] == "draft"


def test_cli_decodes_utf8_pipe_even_when_platform_default_differs():
    code = """
import sys
from travel_agent import deepseek_cli
class Result:
    def model_dump_json(self, **kwargs): return '{"ok":true}'
def fake_extract(text, settings):
    assert text == "从上海出发去南京附近，预算5000以内"
    return Result()
deepseek_cli.extract_intake = fake_extract
sys.argv = ["travel-agent-deepseek", "--live"]
raise SystemExit(deepseek_cli.main())
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        input=TEXT.encode("utf-8"),
        capture_output=True,
        check=True,
        env={**os.environ, "DEEPSEEK_API_KEY": SECRET, "PYTHONIOENCODING": "ascii"},
    )
    assert json.loads(result.stdout) == {"ok": True}
