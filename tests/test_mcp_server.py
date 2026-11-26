import asyncio
import json

import chromadb
import pytest
from mcp.server.mcpserver.exceptions import ToolError
from simple_salesforce.exceptions import SalesforceMalformedRequest

from sf_copilot import mcp_server
from sf_copilot.rag import ApexIndex

from .conftest import HashEmbedding


@pytest.fixture
def server(fake_sf, settings, tmp_path):
    mcp_server.runtime.settings = settings
    mcp_server.runtime.sf = fake_sf
    mcp_server.runtime.index = ApexIndex(client=chromadb.PersistentClient(path=str(tmp_path)), embedding_function=HashEmbedding())
    yield mcp_server.server
    mcp_server.runtime.sf = None
    mcp_server.runtime.index = None


def call(server, name, args):
    return asyncio.run(server.call_tool(name, args))


def test_exposes_expected_tools(server):
    tools = {t.name: t for t in asyncio.run(server.list_tools())}
    assert set(tools) == {"soql_query", "list_objects", "describe_object", "get_record",
                          "search_apex_code", "create_record", "update_record"}
    assert tools["soql_query"].annotations.read_only_hint is True
    assert tools["create_record"].annotations.read_only_hint is False
    assert "confirm" in tools["create_record"].input_schema["properties"]


def test_soql_tool_returns_json(server, fake_sf):
    result = call(server, "soql_query", {"query": "SELECT Id FROM Account"})
    assert not result.is_error
    payload = json.loads(result.content[0].text)
    assert payload["query"] == "SELECT Id FROM Account LIMIT 50"
    assert payload["returned"] == 2


def test_invalid_query_is_a_model_visible_tool_error(server):
    with pytest.raises(ToolError, match="Only SELECT"):
        call(server, "soql_query", {"query": "DELETE FROM Account"})


def test_salesforce_errors_are_shown_to_the_model(server, fake_sf):
    def broken_query(q):
        raise SalesforceMalformedRequest("url", 400, "query", [{"message": "No such column 'Foo' on Account"}])

    fake_sf.query = broken_query
    with pytest.raises(ToolError, match="No such column"):
        call(server, "soql_query", {"query": "SELECT Foo FROM Account"})


def test_search_apex_warns_when_index_empty(server):
    payload = json.loads(call(server, "search_apex_code", {"question": "invoice"}).content[0].text)
    assert payload["hits"] == [] and "index" in payload["warning"]
