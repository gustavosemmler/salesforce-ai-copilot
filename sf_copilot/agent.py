"""Chat agent: Claude + the MCP server's tools, running the tool-use loop by hand.

The agent does not duplicate tool definitions. It asks the MCP server for its tool list
(name, description, JSON schema), hands that to Claude, and executes whatever tool Claude
requests through the same server. That is exactly what an MCP client like Claude Desktop does.
"""

from __future__ import annotations

import json

SYSTEM_PROMPT = """You are Salesforce AI Copilot, an assistant for Salesforce developers and admins.
You have tools to query the org, inspect its schema and search its Apex code.
Rules:
- Reply in the same language the user writes in.
- Never guess field API names: call describe_object before writing SOQL against an object you have not described.
- When explaining code, cite the file and line range returned by search_apex_code.
- For create_record / update_record: call first WITHOUT confirm, show the preview, and only call with confirm=true
  after the user explicitly says yes in a later message.
- Be concise. Summarize large query results instead of dumping every row."""

MAX_TOOL_ROUNDS = 10


def _result_text(result) -> str:
    parts = [getattr(c, "text", "") for c in result.content]
    return "\n".join(p for p in parts if p) or "(empty result)"


class CopilotAgent:
    def __init__(self, server, client, model: str, max_tokens: int = 2048, on_tool_call=None):
        self.server = server
        self.client = client  # anthropic.AsyncAnthropic (or a fake in tests)
        self.model = model
        self.max_tokens = max_tokens
        self.on_tool_call = on_tool_call
        self.messages: list[dict] = []
        self._tools: list[dict] | None = None

    async def tools(self) -> list[dict]:
        if self._tools is None:
            self._tools = [
                {"name": t.name, "description": t.description or "", "input_schema": t.input_schema}
                for t in await self.server.list_tools()
            ]
        return self._tools

    async def ask(self, user_text: str) -> str:
        self.messages.append({"role": "user", "content": user_text})
        tools = await self.tools()
        for _ in range(MAX_TOOL_ROUNDS):
            response = await self.client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                system=SYSTEM_PROMPT,
                tools=tools,
                messages=self.messages,
            )
            self.messages.append({"role": "assistant", "content": response.content})
            if response.stop_reason != "tool_use":
                return "".join(b.text for b in response.content if b.type == "text").strip()

            tool_results = []
            for block in response.content:
                if block.type != "tool_use":
                    continue
                if self.on_tool_call:
                    self.on_tool_call(block.name, block.input)
                try:
                    result = await self.server.call_tool(block.name, block.input)
                    content, is_error = _result_text(result), bool(getattr(result, "is_error", False))
                except Exception as exc:  # tool errors go back to Claude so it can self-correct
                    content, is_error = str(exc), True
                tool_results.append(
                    {"type": "tool_result", "tool_use_id": block.id, "content": content, "is_error": is_error}
                )
            self.messages.append({"role": "user", "content": tool_results})
        return "I stopped after too many tool calls. Try a more specific question."


def format_tool_call(name: str, args: dict) -> str:
    return f"  → {name}({json.dumps(args, ensure_ascii=False)[:200]})"
