from __future__ import annotations

import hashlib
import json
import os
import sys
from dataclasses import dataclass, field
from typing import Any

import httpx

try:
    from oauth_cli_kit import get_token, login_oauth_interactive
except ImportError:
    get_token = None
    login_oauth_interactive = None


DEFAULT_CODEX_URL = "https://chatgpt.com/backend-api/codex/responses"
DEFAULT_ORIGINATOR = "learn-claude-code"
_FINISH_REASON_MAP = {
    "completed": "end_turn",
    "incomplete": "max_tokens",
    "failed": "error",
    "cancelled": "error",
}


@dataclass
class TextBlock:
    text: str
    type: str = field(default="text", init=False)

    def __str__(self) -> str:
        return self.text


@dataclass
class ToolUseBlock:
    id: str
    name: str
    input: dict[str, Any]
    type: str = field(default="tool_use", init=False)

    def __str__(self) -> str:
        return json.dumps(
            {"type": self.type, "id": self.id, "name": self.name, "input": self.input},
            ensure_ascii=False,
        )


@dataclass
class MessageResponse:
    content: list[Any]
    stop_reason: str


class _MessagesAPI:
    def __init__(self, client: "Anthropic"):
        self._client = client

    def create(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        system: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> MessageResponse:
        _ = max_tokens, kwargs
        instructions, input_items = _convert_messages(messages, system)
        body: dict[str, Any] = {
            "model": _strip_model_prefix(model),
            "store": False,
            "stream": True,
            "instructions": instructions,
            "input": input_items,
            "text": {"verbosity": os.getenv("OPENAI_CODEX_VERBOSITY", "medium")},
            "include": ["reasoning.encrypted_content"],
            "prompt_cache_key": _prompt_cache_key(system, messages),
            "tool_choice": "auto",
            "parallel_tool_calls": True,
        }
        if tools:
            body["tools"] = _convert_tools(tools)

        token = ensure_openai_codex_auth()
        headers = _build_headers(
            account_id=getattr(token, "account_id", ""),
            access_token=getattr(token, "access", ""),
            originator=self._client.originator,
        )
        try:
            content, tool_calls, finish_reason = _request_codex(
                url=self._client.base_url,
                headers=headers,
                body=body,
                timeout_seconds=self._client.timeout_seconds,
                verify_ssl=self._client.verify_ssl,
            )
        except RuntimeError as exc:
            should_retry = "invalid or expired" in str(exc) and sys.stdin.isatty() and sys.stdout.isatty()
            if not should_retry:
                raise
            token = _ensure_openai_codex_auth(interactive=True, force_login=True)
            headers = _build_headers(
                account_id=getattr(token, "account_id", ""),
                access_token=getattr(token, "access", ""),
                originator=self._client.originator,
            )
            content, tool_calls, finish_reason = _request_codex(
                url=self._client.base_url,
                headers=headers,
                body=body,
                timeout_seconds=self._client.timeout_seconds,
                verify_ssl=self._client.verify_ssl,
            )

        blocks: list[Any] = []
        if content:
            blocks.append(TextBlock(content))
        for tool_call in tool_calls:
            blocks.append(
                ToolUseBlock(
                    id=tool_call["id"],
                    name=tool_call["name"],
                    input=tool_call["input"],
                )
            )
        if not blocks:
            blocks.append(TextBlock(""))
        stop_reason = "tool_use" if tool_calls else _FINISH_REASON_MAP.get(
            finish_reason, "end_turn"
        )
        return MessageResponse(content=blocks, stop_reason=stop_reason)


class Anthropic:
    def __init__(self, base_url: str | None = None, **kwargs: Any):
        _ = kwargs
        self.base_url = _resolve_codex_url(base_url)
        self.originator = os.getenv("OPENAI_CODEX_ORIGINATOR", DEFAULT_ORIGINATOR)
        self.verify_ssl = _env_bool("OPENAI_CODEX_VERIFY_SSL", True)
        self.timeout_seconds = float(os.getenv("OPENAI_CODEX_TIMEOUT", "60"))
        self.messages = _MessagesAPI(self)


def ensure_openai_codex_auth(interactive: bool | None = None):
    return _ensure_openai_codex_auth(interactive=interactive, force_login=False)


def refresh_openai_codex_auth(interactive: bool | None = True):
    return _ensure_openai_codex_auth(interactive=interactive, force_login=True)


def _ensure_openai_codex_auth(
    *, interactive: bool | None, force_login: bool
):
    if get_token is None:
        raise RuntimeError(
            "oauth-cli-kit is not installed. Run `uv sync` before using OpenAI Codex."
        )

    token = None
    if not force_login:
        try:
            token = get_token()
        except Exception:
            token = None

    if token and getattr(token, "access", None):
        return token

    if interactive is None:
        interactive = _env_bool("OPENAI_CODEX_AUTO_LOGIN", True) and sys.stdin.isatty() and sys.stdout.isatty()

    if not interactive:
        raise RuntimeError(
            "OpenAI Codex OAuth login required. Run `uv run python agents/login_openai_codex.py`."
        )
    if login_oauth_interactive is None:
        raise RuntimeError(
            "oauth-cli-kit is installed without interactive login support."
        )

    token = login_oauth_interactive(print_fn=print, prompt_fn=input)
    if token and getattr(token, "access", None):
        return token
    raise RuntimeError("OpenAI Codex OAuth login failed.")


def _resolve_codex_url(base_url: str | None) -> str:
    value = os.getenv("OPENAI_CODEX_BASE_URL") or base_url or DEFAULT_CODEX_URL
    if "anthropic" in value and "chatgpt.com" not in value:
        return DEFAULT_CODEX_URL
    if value.endswith("/backend-api"):
        return value + "/codex/responses"
    return value.rstrip("/")


def _build_headers(account_id: str, access_token: str, originator: str) -> dict[str, str]:
    if not access_token:
        raise RuntimeError("OpenAI Codex OAuth token is empty.")
    headers = {
        "Authorization": f"Bearer {access_token}",
        "OpenAI-Beta": "responses=experimental",
        "originator": originator,
        "User-Agent": "learn-claude-code (python)",
        "accept": "text/event-stream",
        "content-type": "application/json",
    }
    if account_id:
        headers["chatgpt-account-id"] = account_id
    return headers


def _request_codex(
    *,
    url: str,
    headers: dict[str, str],
    body: dict[str, Any],
    timeout_seconds: float,
    verify_ssl: bool,
) -> tuple[str, list[dict[str, Any]], str]:
    try:
        return _request_codex_once(
            url=url,
            headers=headers,
            body=body,
            timeout_seconds=timeout_seconds,
            verify_ssl=verify_ssl,
        )
    except Exception as exc:
        if "CERTIFICATE_VERIFY_FAILED" not in str(exc) or not verify_ssl:
            raise
        return _request_codex_once(
            url=url,
            headers=headers,
            body=body,
            timeout_seconds=timeout_seconds,
            verify_ssl=False,
        )


def _request_codex_once(
    *,
    url: str,
    headers: dict[str, str],
    body: dict[str, Any],
    timeout_seconds: float,
    verify_ssl: bool,
) -> tuple[str, list[dict[str, Any]], str]:
    with httpx.Client(timeout=timeout_seconds, verify=verify_ssl) as client:
        with client.stream("POST", url, headers=headers, json=body) as response:
            if response.status_code != 200:
                raw = response.read().decode("utf-8", "ignore")
                raise RuntimeError(_friendly_error(response.status_code, raw))
            return _consume_sse(response)


def _consume_sse(response: httpx.Response) -> tuple[str, list[dict[str, Any]], str]:
    content = ""
    tool_calls: list[dict[str, Any]] = []
    tool_call_buffers: dict[str, dict[str, Any]] = {}
    finish_reason = "completed"

    for event in _iter_sse(response):
        event_type = event.get("type")
        if event_type == "response.output_item.added":
            item = event.get("item") or {}
            if item.get("type") == "function_call":
                call_id = item.get("call_id")
                if call_id:
                    tool_call_buffers[call_id] = {
                        "id": item.get("id") or "fc_0",
                        "name": item.get("name") or "",
                        "arguments": item.get("arguments") or "",
                    }
        elif event_type == "response.output_text.delta":
            content += event.get("delta") or ""
        elif event_type == "response.function_call_arguments.delta":
            call_id = event.get("call_id")
            if call_id and call_id in tool_call_buffers:
                tool_call_buffers[call_id]["arguments"] += event.get("delta") or ""
        elif event_type == "response.function_call_arguments.done":
            call_id = event.get("call_id")
            if call_id and call_id in tool_call_buffers:
                tool_call_buffers[call_id]["arguments"] = event.get("arguments") or ""
        elif event_type == "response.output_item.done":
            item = event.get("item") or {}
            if item.get("type") == "function_call":
                call_id = item.get("call_id")
                if not call_id:
                    continue
                buffer = tool_call_buffers.get(call_id) or {}
                raw_args = buffer.get("arguments") or item.get("arguments") or "{}"
                try:
                    parsed_args = json.loads(raw_args)
                except Exception:
                    parsed_args = {"raw": raw_args}
                tool_calls.append(
                    {
                        "id": f"{call_id}|{buffer.get('id') or item.get('id') or 'fc_0'}",
                        "name": buffer.get("name") or item.get("name") or "",
                        "input": parsed_args,
                    }
                )
        elif event_type == "response.completed":
            finish_reason = (event.get("response") or {}).get("status") or "completed"
        elif event_type in {"error", "response.failed"}:
            raise RuntimeError("OpenAI Codex response failed.")

    return content, tool_calls, finish_reason


def _iter_sse(response: httpx.Response):
    buffer: list[str] = []
    for line in response.iter_lines():
        if line == "":
            if not buffer:
                continue
            payload = []
            for item in buffer:
                if item.startswith("data:"):
                    payload.append(item[5:].strip())
            buffer = []
            raw = "\n".join(payload).strip()
            if not raw or raw == "[DONE]":
                continue
            try:
                yield json.loads(raw)
            except Exception:
                continue
            continue
        buffer.append(line)


def _convert_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    converted = []
    for tool in tools:
        name = tool.get("name")
        if not name:
            continue
        converted.append(
            {
                "type": "function",
                "name": name,
                "description": tool.get("description") or "",
                "parameters": tool.get("input_schema") or {"type": "object", "properties": {}},
            }
        )
    return converted


def _convert_messages(
    messages: list[dict[str, Any]], system: str | None
) -> tuple[str, list[dict[str, Any]]]:
    instructions = [system] if system else []
    input_items: list[dict[str, Any]] = []

    for index, message in enumerate(messages):
        role = message.get("role")
        content = message.get("content")

        if role == "system":
            system_text = _stringify_text(content)
            if system_text:
                instructions.append(system_text)
            continue

        if role == "user":
            input_items.extend(_convert_user_content(content))
            continue

        if role == "assistant":
            input_items.extend(_convert_assistant_content(content, index))
            continue

    return "\n\n".join(part for part in instructions if part), input_items


def _convert_user_content(content: Any) -> list[dict[str, Any]]:
    if isinstance(content, str):
        return [_user_text_message(content)]

    if not isinstance(content, list):
        return [_user_text_message("")]

    items: list[dict[str, Any]] = []
    text_parts: list[dict[str, Any]] = []
    for part in content:
        if not isinstance(part, dict):
            continue
        part_type = part.get("type")
        if part_type == "text":
            text_parts.append({"type": "input_text", "text": str(part.get("text", ""))})
        elif part_type == "image_url":
            image_url = (part.get("image_url") or {}).get("url")
            if image_url:
                text_parts.append(
                    {"type": "input_image", "image_url": image_url, "detail": "auto"}
                )
        elif part_type == "tool_result":
            call_id, _ = _split_tool_call_id(part.get("tool_use_id"))
            items.append(
                {
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": _stringify_tool_output(part.get("content")),
                }
            )
    if text_parts:
        items.insert(0, {"role": "user", "content": text_parts})
    return items or [_user_text_message("")]


def _convert_assistant_content(content: Any, index: int) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    text_parts: list[dict[str, Any]] = []

    if isinstance(content, str):
        return [_assistant_text_message(content, index)] if content else []

    if not isinstance(content, list):
        return []

    for part in content:
        part_type = _part_type(part)
        if part_type == "text":
            text = _part_text(part)
            if text:
                text_parts.append({"type": "output_text", "text": text})
        elif part_type == "tool_use":
            tool_id = _part_attr(part, "id") or f"call_{index}"
            call_id, item_id = _split_tool_call_id(tool_id)
            items.append(
                {
                    "type": "function_call",
                    "id": item_id or f"fc_{index}",
                    "call_id": call_id,
                    "name": _part_attr(part, "name") or "",
                    "arguments": json.dumps(_part_attr(part, "input") or {}, ensure_ascii=False),
                }
            )

    if text_parts:
        items.insert(
            0,
            {
                "type": "message",
                "role": "assistant",
                "content": text_parts,
                "status": "completed",
                "id": f"msg_{index}",
            },
        )
    return items


def _user_text_message(text: str) -> dict[str, Any]:
    return {"role": "user", "content": [{"type": "input_text", "text": text}]}


def _assistant_text_message(text: str, index: int) -> dict[str, Any]:
    return {
        "type": "message",
        "role": "assistant",
        "content": [{"type": "output_text", "text": text}],
        "status": "completed",
        "id": f"msg_{index}",
    }


def _part_type(part: Any) -> str | None:
    if isinstance(part, dict):
        return part.get("type")
    return getattr(part, "type", None)


def _part_text(part: Any) -> str:
    if isinstance(part, dict):
        return str(part.get("text", ""))
    return str(getattr(part, "text", ""))


def _part_attr(part: Any, name: str) -> Any:
    if isinstance(part, dict):
        return part.get(name)
    return getattr(part, name, None)


def _stringify_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                parts.append(str(part.get("text", "")))
            elif hasattr(part, "text"):
                parts.append(str(getattr(part, "text", "")))
        return "\n".join(part for part in parts if part)
    return ""


def _stringify_tool_output(content: Any) -> str:
    if isinstance(content, str):
        return content
    return json.dumps(content, ensure_ascii=False, default=str)


def _split_tool_call_id(value: Any) -> tuple[str, str | None]:
    if isinstance(value, str) and value:
        if "|" in value:
            call_id, item_id = value.split("|", 1)
            return call_id, item_id or None
        return value, None
    return "call_0", None


def _strip_model_prefix(model: str) -> str:
    if model.startswith("openai-codex/") or model.startswith("openai_codex/"):
        return model.split("/", 1)[1]
    return model


def _prompt_cache_key(system: str | None, messages: list[dict[str, Any]]) -> str:
    raw = json.dumps({"system": system or "", "messages": messages}, default=str, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _friendly_error(status_code: int, raw: str) -> str:
    if status_code == 401:
        return "OpenAI Codex OAuth token is invalid or expired. Re-run `uv run python agents/login_openai_codex.py`."
    if status_code == 403:
        return "OpenAI Codex access denied. Check that the account has ChatGPT Plus/Pro access."
    if status_code == 429:
        return "ChatGPT quota exceeded or rate limited. Try again later."
    return f"HTTP {status_code}: {raw}"


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}
