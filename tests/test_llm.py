"""ClaudeResponder against a fake API client: request shape, pause_turn, errors."""

from types import SimpleNamespace

import anthropic
import httpx2
import pytest

from bobba_buddy.config import ClaudeConfig, PricingConfig
from bobba_buddy.llm import ClaudeResponder, ReplyKind, extract_answer

REQ = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")


def block(kind, text=""):
    """A fake response content block.

    Args:
        kind: Block type, e.g. ``"text"`` or ``"server_tool_use"``.
        text: Block text.

    Returns:
        An object with ``type`` and ``text``.
    """
    return SimpleNamespace(type=kind, text=text)


def response(blocks, stop="end_turn", searches=0):
    """A fake API response.

    Args:
        blocks: Content blocks.
        stop: Stop reason.
        searches: Web searches to report in usage.

    Returns:
        An object shaped like an Anthropic ``Message``.
    """
    usage = SimpleNamespace(
        input_tokens=100,
        output_tokens=50,
        cache_creation_input_tokens=0,
        cache_read_input_tokens=0,
        server_tool_use=SimpleNamespace(web_search_requests=searches) if searches else None,
    )
    return SimpleNamespace(content=blocks, stop_reason=stop, usage=usage, _request_id="req_1")


def status_error(cls, code):
    """Build an Anthropic API status error.

    Args:
        cls: The error class, e.g. ``anthropic.RateLimitError``.
        code: HTTP status code.

    Returns:
        The error instance.
    """
    return cls("boom", response=httpx2.Response(code, request=REQ), body=None)


class FakeClient:
    """A fake ``anthropic.Anthropic`` that returns or raises scripted outcomes.

    Attributes:
        outcomes: Responses to return or exceptions to raise, in order.
        calls: Keyword arguments of every ``create`` call.
        options: Keyword arguments of every ``with_options`` call.
        messages: Itself, so ``client.messages.create`` works.
    """

    def __init__(self, *outcomes):
        """Script the outcomes.

        Args:
            *outcomes: Responses or exceptions, one per ``create`` call.
        """
        self.outcomes = list(outcomes)
        self.calls = []
        self.options = []
        self.messages = self

    def with_options(self, **kwargs):
        """Record per-request options.

        Args:
            **kwargs: The options (timeout, max_retries).

        Returns:
            Itself.
        """
        self.options.append(kwargs)
        return self

    def create(self, **kwargs):
        """Record the request and return or raise the next outcome.

        Args:
            **kwargs: The request.

        Returns:
            The next scripted response.

        Raises:
            Exception: The next scripted exception.
        """
        self.calls.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def respond(client, web_search=False):
    """Call ``ClaudeResponder.respond`` with a minimal request.

    Args:
        client: The fake client.
        web_search: Whether to enable web search.

    Returns:
        The reply.
    """
    responder = ClaudeResponder(client, pricing=PricingConfig)
    return responder.respond(
        system="sys",
        messages=[{"role": "user", "content": "hi"}],
        web_search=web_search,
        claude=ClaudeConfig(),
    )


def test_ok_request_shape():
    client = FakeClient(response([block("thinking"), block("text", "Hello!")]))
    reply = respond(client)
    assert reply.kind is ReplyKind.OK and reply.text == "Hello!"
    call = client.calls[0]
    assert call["model"] == "claude-haiku-5-5"
    assert call["output_config"] == {"effort": "low"}
    assert "tools" not in call and "temperature" not in call
    assert client.options[0] == {"timeout": 45.0, "max_retries": 2}
    assert reply.cost_usd > 0 and reply.request_id == "req_1"


def test_web_search_tool_and_preamble_dropped():
    client = FakeClient(
        response(
            [
                block("text", "Let me search that. "),
                block("server_tool_use"),
                block("web_search_tool_result"),
                block("text", "It's 31°C "),
                block("text", "in KL."),
            ],
            searches=1,
        )
    )
    reply = respond(client, web_search=True)
    assert client.calls[0]["tools"] == [
        {"type": "web_search_20250305", "name": "web_search", "max_uses": 2}
    ]
    assert reply.text == "It's 31°C in KL."
    assert reply.searches == 1
    assert reply.cost_usd == pytest.approx((100 * 0.10 + 50 * 0.50) / 1e6 + 0.01)


def test_pause_turn_is_resumed():
    paused = response([block("server_tool_use")], stop="pause_turn", searches=1)
    client = FakeClient(paused, response([block("web_search_tool_result"), block("text", "Done")]))
    reply = respond(client, web_search=True)
    assert reply.text == "Done"
    assert len(client.calls) == 2
    assert client.calls[1]["messages"][-1] == {"role": "assistant", "content": paused.content}


def test_refusal():
    reply = respond(FakeClient(response([], stop="refusal")))
    assert reply.kind is ReplyKind.REFUSAL


def test_empty_answer_is_busy():
    reply = respond(FakeClient(response([block("thinking")], stop="max_tokens")))
    assert reply.kind is ReplyKind.BUSY


@pytest.mark.parametrize(
    ("error", "kind"),
    [
        (status_error(anthropic.AuthenticationError, 401), ReplyKind.FATAL),
        (status_error(anthropic.PermissionDeniedError, 403), ReplyKind.FATAL),
        (status_error(anthropic.NotFoundError, 404), ReplyKind.FATAL),
        (status_error(anthropic.BadRequestError, 400), ReplyKind.BAD_REQUEST),
        (status_error(anthropic.RateLimitError, 429), ReplyKind.BUSY),
        (status_error(anthropic.APIStatusError, 503), ReplyKind.BUSY),
        (anthropic.APITimeoutError(request=REQ), ReplyKind.BUSY),
        (anthropic.APIConnectionError(request=REQ), ReplyKind.BUSY),
    ],
)
def test_error_mapping(error, kind):
    reply = respond(FakeClient(error))
    assert reply.kind is kind
    assert reply.error


def test_extract_answer_without_tools():
    assert extract_answer([block("text", "a"), block("text", "b")]) == "ab"
    assert extract_answer([]) == ""
