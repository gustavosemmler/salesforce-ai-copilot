"""Salesforce operations exposed to the LLM.

Every function receives the Salesforce client as an argument, so the logic can be
unit-tested with a fake client and reused by both the MCP server and the chat agent.
"""

from __future__ import annotations

import re
from typing import Any

SELECT_RE = re.compile(r"^\s*select\s", re.IGNORECASE)
LIMIT_RE = re.compile(r"\blimit\s+\d+\s*(offset\s+\d+\s*)?$", re.IGNORECASE)
API_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
RECORD_ID_RE = re.compile(r"^[A-Za-z0-9]{15}([A-Za-z0-9]{3})?$")
MAX_PICKLIST_VALUES = 25
MAX_CHILD_RELATIONSHIPS = 30


class ToolError(ValueError):
    """Raised for invalid input; the message is shown to the model so it can self-correct."""


def _check_api_name(name: str) -> str:
    if not API_NAME_RE.match(name or ""):
        raise ToolError(f"'{name}' is not a valid Salesforce API name.")
    return name


def _check_record_id(record_id: str) -> str:
    if not RECORD_ID_RE.match(record_id or ""):
        raise ToolError(f"'{record_id}' is not a valid 15/18-character Salesforce record Id.")
    return record_id


def _clean(value: Any) -> Any:
    """Drop the noisy 'attributes' metadata Salesforce adds to every record."""
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items() if k != "attributes"}
    if isinstance(value, list):
        return [_clean(v) for v in value]
    return value


def run_soql(sf, query: str, max_rows: int = 200) -> dict:
    """Run a read-only SOQL query. A LIMIT is appended when missing to protect the context window."""
    q = (query or "").strip().rstrip(";").strip()
    if not SELECT_RE.match(q):
        raise ToolError("Only SELECT queries are allowed.")
    if not LIMIT_RE.search(q):
        q = f"{q} LIMIT {max_rows}"
    result = sf.query(q)
    records = _clean(result.get("records", []))[:max_rows]
    return {
        "query": q,
        "total_size": result.get("totalSize", len(records)),
        "returned": len(records),
        "records": records,
    }


def list_objects(sf, search: str | None = None, custom_only: bool = False, limit: int = 50) -> dict:
    """List queryable objects, optionally filtered by a name/label substring."""
    objects = []
    term = (search or "").lower()
    for obj in sf.describe().get("sobjects", []):
        if not obj.get("queryable"):
            continue
        if custom_only and not obj.get("custom"):
            continue
        if term and term not in obj["name"].lower() and term not in obj.get("label", "").lower():
            continue
        objects.append({"name": obj["name"], "label": obj.get("label"), "custom": obj.get("custom", False)})
    return {"count": len(objects), "objects": objects[:limit], "truncated": len(objects) > limit}


def describe_object(sf, object_name: str) -> dict:
    """Return a compact schema (fields, types, picklists, relationships) for one object."""
    _check_api_name(object_name)
    desc = getattr(sf, object_name).describe()
    fields = []
    for f in desc.get("fields", []):
        field = {
            "name": f["name"],
            "label": f.get("label"),
            "type": f.get("type"),
            "required": bool(f.get("createable") and not f.get("nillable") and not f.get("defaultedOnCreate")),
        }
        if f.get("referenceTo"):
            field["reference_to"] = f["referenceTo"]
            field["relationship_name"] = f.get("relationshipName")
        values = [p["value"] for p in f.get("picklistValues", []) if p.get("active")]
        if values:
            field["picklist_values"] = values[:MAX_PICKLIST_VALUES]
        fields.append(field)
    children = [c["relationshipName"] for c in desc.get("childRelationships", []) if c.get("relationshipName")]
    return {
        "name": desc.get("name", object_name),
        "label": desc.get("label"),
        "field_count": len(fields),
        "fields": fields,
        "child_relationships": children[:MAX_CHILD_RELATIONSHIPS],
    }


def get_record(sf, object_name: str, record_id: str, fields: list[str] | None = None) -> dict:
    """Fetch a single record, optionally limited to some fields."""
    _check_api_name(object_name)
    _check_record_id(record_id)
    if fields:
        for f in fields:
            if not re.match(r"^[A-Za-z][A-Za-z0-9_.]*$", f):
                raise ToolError(f"'{f}' is not a valid field name.")
        result = sf.query(f"SELECT {', '.join(fields)} FROM {object_name} WHERE Id = '{record_id}' LIMIT 1")
        records = _clean(result.get("records", []))
        if not records:
            raise ToolError(f"No {object_name} found with Id {record_id}.")
        return records[0]
    return _clean(dict(getattr(sf, object_name).get(record_id)))


def _write_guard(allow_writes: bool) -> None:
    if not allow_writes:
        raise ToolError("Writes are disabled. Set SF_ALLOW_WRITES=true to enable create/update.")


def create_record(sf, object_name: str, data: dict, allow_writes: bool, confirm: bool = False) -> dict:
    """Create a record. Without confirm=True it only returns a preview (human-in-the-loop)."""
    _check_api_name(object_name)
    _write_guard(allow_writes)
    if not data:
        raise ToolError("'data' must contain at least one field.")
    if not confirm:
        return {
            "dry_run": True,
            "would_create": {"object": object_name, "data": data},
            "next_step": "Show this preview to the user and call again with confirm=true only after they approve.",
        }
    result = getattr(sf, object_name).create(data)
    return {"created": True, "object": object_name, "id": result.get("id")}


def update_record(sf, object_name: str, record_id: str, data: dict, allow_writes: bool, confirm: bool = False) -> dict:
    """Update a record. Without confirm=True it only returns a before/after preview."""
    _check_api_name(object_name)
    _check_record_id(record_id)
    _write_guard(allow_writes)
    if not data:
        raise ToolError("'data' must contain at least one field.")
    if not confirm:
        current = get_record(sf, object_name, record_id, fields=list(data.keys()))
        return {
            "dry_run": True,
            "record": {"object": object_name, "id": record_id},
            "before": {k: current.get(k) for k in data},
            "after": data,
            "next_step": "Show this preview to the user and call again with confirm=true only after they approve.",
        }
    getattr(sf, object_name).update(record_id, data)
    return {"updated": True, "object": object_name, "id": record_id, "fields": sorted(data)}
