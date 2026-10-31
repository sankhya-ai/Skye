"""The host's own model calls: the learning reviewer and the coding-flow judge.

These are short, single-shot completions that expect JSON back — not agent
turns — so they go straight to the chat completions endpoint with the
tenant's key. ``TURA_CODE_SCRIPTED_LLM`` (a JSON object mapping a purpose such
as ``"review"`` or ``"judge"`` to a list of answers) replaces the model in
tests and offline demos, the way ``TURA_CODE_SCRIPTED_ECHO`` does for turns.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import httpx

SCRIPTED_LLM_ENV = "TURA_CODE_SCRIPTED_LLM"
_scripted_positions: dict[tuple[str, str], int] = {}


class LLMError(RuntimeError):
    pass


def _scripted(purpose: str) -> str | None:
    source = os.environ.get(SCRIPTED_LLM_ENV, "").strip()
    if not source:
        return None
    try:
        answers = json.loads(Path(source).read_text("utf-8")).get(purpose, [])
    except (OSError, ValueError, AttributeError) as error:
        raise LLMError(f"unreadable scripted answers: {error}") from error
    position = _scripted_positions.get((source, purpose), 0)
    if position >= len(answers):
        raise LLMError(f"scripted answers for {purpose!r} are exhausted")
    _scripted_positions[(source, purpose)] = position + 1
    answer = answers[position]
    return answer if isinstance(answer, str) else json.dumps(answer)


async def complete(
    messages: list[dict[str, str]],
    *,
    base_url: str,
    api_key: str,
    model: str,
    purpose: str,
    max_tokens: int = 1_500,
    timeout: float = 120.0,
    json_mode: bool = False,
) -> str:
    """One completion's text. Raises ``LLMError`` on any failure.

    ``json_mode`` asks for a JSON object where the provider supports it and
    quietly drops the request where it does not. A reply cut off at
    ``max_tokens`` is asked for once more with twice the room: a truncated JSON
    answer is the usual way a review is lost.
    """
    scripted = _scripted(purpose)
    if scripted is not None:
        return scripted
    if not api_key:
        raise LLMError("no API key is configured")
    headers = {"Authorization": f"Bearer {api_key}", "HTTP-Referer": "https://github.com/sankhya-ai/root", "X-Title": "root"}
    budget = max_tokens
    structured = json_mode
    text = ""
    for _ in range(3):
        body: dict = {"model": model, "messages": messages, "max_tokens": budget, "temperature": 0.2}
        if structured:
            body["response_format"] = {"type": "json_object"}
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(f"{base_url.rstrip('/')}/chat/completions", json=body, headers=headers)
        except httpx.HTTPError as error:
            raise LLMError(str(error)) from error
        if response.status_code == 400 and structured and "response_format" in response.text:
            structured = False
            continue
        if response.status_code != 200:
            raise LLMError(f"HTTP {response.status_code}: {response.text[:300]}")
        try:
            choice = response.json()["choices"][0]
            text = str(choice["message"]["content"] or "")
        except (KeyError, IndexError, ValueError, TypeError) as error:
            raise LLMError(f"unexpected reply: {response.text[:300]}") from error
        if choice.get("finish_reason") == "length" and budget < max_tokens * 2:
            budget = max_tokens * 2
            continue
        return text
    return text


def extract_json_object(text: str) -> str:
    """The outermost JSON object in a reply that may be fenced or chatty."""
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.split("\n", 1)[-1].rsplit("```", 1)[0]
    start, end = stripped.find("{"), stripped.rfind("}")
    if start == -1 or end < start:
        raise ValueError("no JSON object in the reply")
    return stripped[start : end + 1]


def _close(text: str) -> str | None:
    """``text`` with its open strings, arrays and objects closed, or None."""
    stack: list[str] = []
    in_string = escaped = False
    for char in text:
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char in "{[":
            stack.append("}" if char == "{" else "]")
        elif char in "}]":
            if not stack or stack.pop() != char:
                return None
    if in_string:
        return None
    return text + "".join(reversed(stack))


def _escape_raw_newlines(text: str) -> str:
    out: list[str] = []
    in_string = escaped = False
    for char in text:
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            elif char == "\n":
                out.append("\\n")
                continue
            elif char == "\t":
                out.append("\\t")
                continue
        elif char == '"':
            in_string = True
        out.append(char)
    return "".join(out)


def loads_lenient(text: str) -> object:
    """The JSON object in a model's reply, repaired where that is safe.

    Lossless first: a fence or prose around it, trailing commas, raw newlines
    inside strings. Then, for an answer that was cut off or broken part-way
    (an unescaped quote, a token limit), the longest prefix that ends on a
    complete element is kept and closed — the tail is lost, never invented.
    Raises ValueError when nothing usable is left.
    """
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.split("\n", 1)[-1].rsplit("```", 1)[0]
    start = stripped.find("{")
    if start == -1:
        raise ValueError("no JSON object in the reply")
    body = stripped[start:]
    end = body.rfind("}")
    candidates = [body[: end + 1]] if end != -1 else []
    candidates.append(body)
    for candidate in candidates:
        for fixed in (candidate, _TRAILING_COMMA.sub(r"\1", _escape_raw_newlines(candidate))):
            try:
                return json.loads(fixed)
            except ValueError:
                pass
    body = _TRAILING_COMMA.sub(r"\1", _escape_raw_newlines(body))
    try:
        json.loads(body)
    except json.JSONDecodeError as error:
        limit = error.pos
    else:  # pragma: no cover - handled above
        limit = len(body)
    # Cut back to a separator before the fault, close what is open, try again.
    cuts = [i for i, char in enumerate(body[:limit]) if char in ",[{"]
    for cut in reversed(cuts[-200:]):
        if body[cut] != "," and body[:cut].rstrip().endswith(","):
            continue  # an element that had only begun: cut at the comma before it instead
        head = body[:cut] if body[cut] == "," else body[: cut + 1]
        closed = _close(head.rstrip().rstrip(","))
        if closed is None:
            continue
        try:
            value = json.loads(closed)
        except ValueError:
            continue
        if isinstance(value, dict) and value:
            return value
    raise ValueError(f"unparseable JSON in the reply (at character {limit})")


_TRAILING_COMMA = re.compile(r",\s*([}\]])")
