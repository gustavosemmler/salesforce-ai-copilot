import asyncio
from types import SimpleNamespace

import chromadb

from sf_copilot import mcp_server
from sf_copilot.agent import CopilotAgent
from sf_copilot.rag import ApexIndex

from .conftest import HashEmbedding


def text(t):
    return SimpleNamespace(type="text", text=t)


def tool_use(id_, name, input_):
    return SimpleNamespace(type="tool_use", id=id_, name=name, input=input_)


class FakeMessages:
    """Scripted Claude: first asks for a SOQL query, then answers using the tool result."""

    def __init__(self):
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if len(self.calls) == 1:
            return SimpleNamespace(stop_reason="tool_use", content=[
                text("Let me check."), tool_use("tu_1", "soql_query", {"query": "SELECT Name FROM Account"})])
        return SimpleNamespace(stop_reason="end_turn", content=[text("You have 2 accounts: Acme and Globex.")])


def test_agent_runs_tool_loop(fake_sf, settings, tmp_path):
    mcp_server.runtime.settings = settings
    mcp_server.runtime.sf = fake_sf
    mcp_server.runtime.index = ApexIndex(client=chromadb.PersistentClient(path=str(tmp_path)), embedding_function=HashEmbedding())
    messages = FakeMessages()
    seen = []
    agent = CopilotAgent(mcp_server.server, SimpleNamespace(messages=messages), model="test-model",
                         on_tool_call=lambda n, a: seen.append(n))

    answer = asyncio.run(agent.ask("Quantas contas eu tenho?"))

    assert answer == "You have 2 accounts: Acme and Globex."
    assert seen == ["soql_query"]
    assert fake_sf.queries == ["SELECT Name FROM Account LIMIT 50"]
    # Claude received the tool list from the MCP server, and the tool result in the 2nd call
    assert {t["name"] for t in messages.calls[0]["tools"]} >= {"soql_query", "search_apex_code"}
    tool_result = messages.calls[1]["messages"][2]["content"][0]  # [user, assistant, tool results, ...]
    assert tool_result["type"] == "tool_result" and tool_result["tool_use_id"] == "tu_1"
    assert "Globex" in tool_result["content"] and tool_result["is_error"] is False


class BadQueryThenFix(FakeMessages):
    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if len(self.calls) == 1:
            return SimpleNamespace(stop_reason="tool_use", content=[
                tool_use("tu_1", "soql_query", {"query": "DELETE FROM Account"})])
        return SimpleNamespace(stop_reason="end_turn", content=[text("I can only run SELECT queries.")])


def test_tool_errors_are_sent_back_to_claude(fake_sf, settings, tmp_path):
    mcp_server.runtime.settings = settings
    mcp_server.runtime.sf = fake_sf
    messages = BadQueryThenFix()
    agent = CopilotAgent(mcp_server.server, SimpleNamespace(messages=messages), model="test-model")

    answer = asyncio.run(agent.ask("apaga tudo"))

    tool_result = messages.calls[1]["messages"][2]["content"][0]
    assert tool_result["is_error"] is True and "Only SELECT" in tool_result["content"]
    assert answer == "I can only run SELECT queries."
