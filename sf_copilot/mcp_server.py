"""MCP server: exposes the Salesforce tools to any MCP client (Claude Desktop, Claude Code, Cursor...)."""

from __future__ import annotations

import functools
from typing import Any

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError as MCPToolError
from mcp.types import ToolAnnotations
from simple_salesforce.exceptions import SalesforceError

from . import tools
from .config import Settings
from .rag import ApexIndex
from .salesforce import AuthError, connect

INSTRUCTIONS = """Tools for exploring and working with a Salesforce org.
- Before writing SOQL, call describe_object to confirm field API names.
- Use search_apex_code to answer questions about how the org's Apex code works, and cite file and lines.
- create_record / update_record return a preview first. Only call them with confirm=true after the user explicitly approves."""


class Runtime:
    """Lazily creates the Salesforce client and the RAG index (tests replace these with fakes)."""

    def __init__(self, settings: Settings | None = None):
        self._settings = settings
        self._sf = None
        self._index = None

    @property
    def settings(self) -> Settings:
        if self._settings is None:
            self._settings = Settings.from_env()
        return self._settings

    @settings.setter
    def settings(self, value: Settings):
        self._settings = value

    @property
    def sf(self):
        if self._sf is None:
            self._sf = connect(self.settings)
        return self._sf

    @sf.setter
    def sf(self, value):
        self._sf = value

    @property
    def index(self) -> ApexIndex:
        if self._index is None:
            self._index = ApexIndex(self.settings.chroma_path)
        return self._index

    @index.setter
    def index(self, value):
        self._index = value


runtime = Runtime()


def _rt() -> Runtime:
    return runtime


READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=True)
WRITE = ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=True)

server = MCPServer(name="salesforce-ai-copilot", instructions=INSTRUCTIONS)


def model_visible_errors(fn):
    """Turn validation and Salesforce API errors into MCP ToolErrors.

    MCP hides the text of unexpected exceptions from the client. For our own validation errors
    and Salesforce errors (e.g. a malformed SOQL query) we WANT the model to see the message,
    so it can fix the query and try again.
    """

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except (tools.ToolError, AuthError) as exc:
            raise MCPToolError(str(exc)) from exc
        except SalesforceError as exc:
            raise MCPToolError(f"Salesforce returned an error: {exc}") from exc

    return wrapper


@server.tool(annotations=READ_ONLY)
@model_visible_errors
def soql_query(query: str) -> dict:
    """Run a read-only SOQL SELECT query against the org. A LIMIT is added automatically if missing."""
    rt = _rt()
    return tools.run_soql(rt.sf, query, max_rows=rt.settings.max_rows)


@server.tool(annotations=READ_ONLY)
@model_visible_errors
def list_objects(search: str | None = None, custom_only: bool = False) -> dict:
    """List queryable objects in the org. Filter by a substring of the API name or label."""
    return tools.list_objects(_rt().sf, search=search, custom_only=custom_only)


@server.tool(annotations=READ_ONLY)
@model_visible_errors
def describe_object(object_name: str) -> dict:
    """Get an object's fields (API names, types, required, picklist values) and child relationships."""
    return tools.describe_object(_rt().sf, object_name)


@server.tool(annotations=READ_ONLY)
@model_visible_errors
def get_record(object_name: str, record_id: str, fields: list[str] | None = None) -> dict:
    """Fetch one record by Id, optionally only some fields."""
    return tools.get_record(_rt().sf, object_name, record_id, fields)


@server.tool(annotations=READ_ONLY)
@model_visible_errors
def search_apex_code(question: str, k: int = 5) -> dict:
    """Semantic search over the org's Apex classes and triggers (RAG). Returns the most relevant code chunks."""
    index = _rt().index
    if index.count() == 0:
        return {"hits": [], "warning": "The Apex index is empty. Run `sf-copilot index` first."}
    return {"hits": index.search(question, k=k)}


@server.tool(annotations=WRITE)
@model_visible_errors
def create_record(object_name: str, data: dict[str, Any], confirm: bool = False) -> dict:
    """Create a record. Without confirm=true it returns a preview only; confirm after the user approves."""
    rt = _rt()
    return tools.create_record(rt.sf, object_name, data, rt.settings.allow_writes, confirm)


@server.tool(annotations=WRITE)
@model_visible_errors
def update_record(object_name: str, record_id: str, data: dict[str, Any], confirm: bool = False) -> dict:
    """Update a record. Without confirm=true it returns a before/after preview; confirm after the user approves."""
    rt = _rt()
    return tools.update_record(rt.sf, object_name, record_id, data, rt.settings.allow_writes, confirm)


def main() -> None:
    server.run("stdio")


if __name__ == "__main__":
    main()
