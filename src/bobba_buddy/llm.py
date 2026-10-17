"""Claude API calls: request shaping, pause_turn handling, and error classification.

Attributes:
    MAX_CONTINUATIONS (int): How many times a ``pause_turn`` response is resumed before
        giving up.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol

import anthropic

from .budget import estimate_cost
from .config import ClaudeConfig, PricingConfig

log = logging.getLogger(__name__)

MAX_CONTINUATIONS = 2
_TOOL_BLOCK_TYPES = {"server_tool_use", "web_search_tool_result"}


class ReplyKind(Enum):
    """The outcome of a Claude call.

    Attributes:
        OK: An answer to post.
        REFUSAL: The model declined; nothing to retry.
        BUSY: Rate limit, overload, timeout or network trouble: try again later.
        FATAL: Bad key, no permission or unknown model: the owner must fix config.
        BAD_REQUEST: A 400: a bug, or an input the API rejected.
    """

    OK = "ok"
    REFUSAL = "refusal"  # the model declined; nothing to retry
    BUSY = "busy"  # rate limit, overload, timeout, network: try again later
    FATAL = "fatal"  # bad key, no permission, unknown model: the owner must fix config
    BAD_REQUEST = "bad_request"  # 400: a bug or an input the API rejected


@dataclass(frozen=True)
class Reply:
    """The result of one command's Claude call(s), with usage for logging and budget.

    Attributes:
        kind: The outcome.
        text: The answer (only for ``OK``).
        cost_usd: Estimated cost of every call made, including continuations.
        input_tokens: Total input tokens.
        output_tokens: Total output tokens.
        searches: Number of web searches run.
        stop_reason: The last response's stop reason.
        request_id: The last API request id, for support.
        error: Error description for failed calls.
    """

    kind: ReplyKind
    text: str = ""
    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    searches: int = 0
    stop_reason: str | None = None
    request_id: str | None = None
    error: str | None = None


class Responder(Protocol):
    """Anything that can answer a command; the bot uses it so tests can fake Claude."""

    def respond(
        self,
        *,
        system: str,
        messages: Sequence[dict[str, Any]],
        web_search: bool,
        claude: ClaudeConfig,
    ) -> Reply:
        """Answer one command.

        Args:
            system: System prompt.
            messages: Conversation, ending with the user message.
            web_search: Whether the web search tool may be used.
            claude: Model and request settings.

        Returns:
            The outcome and usage.
        """
        ...


def extract_answer(blocks: Sequence[Any]) -> str:
    """Text written after the last web-search step.

    With web search, the model often writes a preamble ("Let me look that up") before
    searching. In chat only the final answer should be posted.

    Args:
        blocks: Content blocks of the response(s), in order.

    Returns:
        The text after the last search step, or all text if there was none.
    """
    last_tool = -1
    for i, block in enumerate(blocks):
        if getattr(block, "type", None) in _TOOL_BLOCK_TYPES:
            last_tool = i
    tail = [b.text for b in blocks[last_tool + 1 :] if getattr(b, "type", None) == "text"]
    if any(t.strip() for t in tail):
        return "".join(tail).strip()
    return "".join(b.text for b in blocks if getattr(b, "type", None) == "text").strip()


class ClaudeResponder:
    """:class:`Responder` backed by the Anthropic Messages API."""

    def __init__(self, client: anthropic.Anthropic, pricing: Callable[[], PricingConfig]) -> None:
        """Wrap an API client.

        Args:
            client: The Anthropic client (credentials from ``ANTHROPIC_API_KEY``).
            pricing: Returns current prices, for the cost estimate.
        """
        self._client = client
        self._pricing = pricing

    def respond(
        self,
        *,
        system: str,
        messages: Sequence[dict[str, Any]],
        web_search: bool,
        claude: ClaudeConfig,
    ) -> Reply:
        """Call Claude, resuming ``pause_turn`` responses, and classify the result.

        Never raises for API errors: they become ``FATAL``, ``BUSY`` or
        ``BAD_REQUEST`` replies.

        Args:
            system: System prompt.
            messages: Conversation, ending with the user message.
            web_search: Add the ``web_search_20250305`` tool.
            claude: Model, effort, token, timeout and retry settings.

        Returns:
            The outcome, answer text and usage.
        """
        client = self._client.with_options(timeout=claude.timeout_s, max_retries=claude.max_retries)
        request: dict[str, Any] = {
            "model": claude.model,
            "max_tokens": claude.max_tokens,
            "system": system,
            "output_config": {"effort": claude.effort},
        }
        if web_search:
            # web_search_20260209 (dynamic filtering) is documented for Opus/Sonnet only.
            request["tools"] = [
                {
                    "type": "web_search_20250305",
                    "name": "web_search",
                    "max_uses": claude.web_search_max_uses,
                }
            ]

        msgs = list(messages)
        blocks: list[Any] = []
        cost = 0.0
        input_tokens = output_tokens = searches = 0
        request_id: str | None = None
        try:
            for _ in range(MAX_CONTINUATIONS + 1):
                response = client.messages.create(messages=msgs, **request)
                request_id = response._request_id
                usage = response.usage
                cost += estimate_cost(usage, self._pricing())
                input_tokens += usage.input_tokens or 0
                output_tokens += usage.output_tokens or 0
                if usage.server_tool_use is not None:
                    searches += usage.server_tool_use.web_search_requests or 0
                blocks.extend(response.content)
                if response.stop_reason != "pause_turn":
                    break
                # Server-side tool loop hit its iteration cap; re-send to let it resume.
                msgs = [*msgs, {"role": "assistant", "content": response.content}]
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as exc:
            return self._failed(ReplyKind.FATAL, exc, cost)
        except anthropic.NotFoundError as exc:  # usually a wrong model id
            return self._failed(ReplyKind.FATAL, exc, cost)
        except anthropic.BadRequestError as exc:
            return self._failed(ReplyKind.BAD_REQUEST, exc, cost)
        except anthropic.RateLimitError as exc:
            return self._failed(ReplyKind.BUSY, exc, cost)
        except anthropic.APIStatusError as exc:  # 5xx, overloaded, other statuses
            kind = ReplyKind.BUSY if exc.status_code >= 500 else ReplyKind.BAD_REQUEST
            return self._failed(kind, exc, cost)
        except anthropic.APIConnectionError as exc:  # includes APITimeoutError
            return self._failed(ReplyKind.BUSY, exc, cost)

        stop_reason = response.stop_reason
        metrics = {
            "cost_usd": cost,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "searches": searches,
            "stop_reason": stop_reason,
            "request_id": request_id,
        }
        if stop_reason == "refusal":
            return Reply(ReplyKind.REFUSAL, **metrics)
        text = extract_answer(blocks)
        if not text:
            # Empty answer, e.g. max_tokens hit during thinking or a pause_turn left unfinished.
            return Reply(ReplyKind.BUSY, error=f"empty answer ({stop_reason})", **metrics)
        return Reply(ReplyKind.OK, text=text, **metrics)

    @staticmethod
    def _failed(kind: ReplyKind, exc: anthropic.APIError, cost: float) -> Reply:
        """Log a failed call and turn it into a reply.

        Args:
            kind: How to classify the failure.
            exc: The API error.
            cost: Cost of any calls that succeeded before the failure.

        Returns:
            A reply carrying the error description and request id.
        """
        request_id = getattr(exc, "request_id", None)
        status = getattr(exc, "status_code", None)
        log.warning(
            "claude call failed (%s, status=%s, request_id=%s): %s",
            kind.value,
            status,
            request_id,
            exc,
        )
        error = f"{type(exc).__name__}: {exc}"
        return Reply(kind, cost_usd=cost, request_id=request_id, error=error)
