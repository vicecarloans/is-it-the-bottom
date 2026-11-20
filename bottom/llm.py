"""One small wrapper around Claude: ask a question and get structured fields back."""

from __future__ import annotations

from typing import Any, Protocol


def describe_error(exc: Exception) -> str:
    """A short, readable reason for a failed model call, safe to show on the page."""
    status = getattr(exc, "status_code", None)
    body = getattr(exc, "body", None)
    message = ""
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            message = str(error.get("message") or "")
    if not message:
        message = type(exc).__name__
    message = message.split(". Please")[0].strip().rstrip(".")
    if len(message) > 120:
        message = message[:117] + "..."
    return f"Claude API {status}: {message}" if status else f"Claude API: {message}"


class ToolCaller(Protocol):
    def __call__(self, system: str, prompt: str, tool: dict[str, Any]) -> dict[str, Any]: ...


class Claude:
    """Forces the model to answer by calling one tool, so the reply is always structured."""

    def __init__(self, api_key: str, model: str, max_tokens: int = 1200) -> None:
        import anthropic

        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = model
        self.max_tokens = max_tokens

    def __call__(self, system: str, prompt: str, tool: dict[str, Any]) -> dict[str, Any]:
        response = self.client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system,
            tools=[tool],
            tool_choice={"type": "tool", "name": tool["name"]},
            messages=[{"role": "user", "content": prompt}],
        )
        for block in response.content:
            if block.type == "tool_use":
                return dict(block.input)
        raise RuntimeError("the model did not return structured output")
