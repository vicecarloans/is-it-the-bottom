"""One small wrapper around Claude: ask a question and get structured fields back."""

from __future__ import annotations

from typing import Any, Protocol


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
