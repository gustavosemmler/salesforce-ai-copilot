import pytest

from sf_copilot import tools
from sf_copilot.tools import ToolError


def test_soql_rejects_non_select(fake_sf):
    for bad in ["DELETE FROM Account", "update Account set Name='x'", ""]:
        with pytest.raises(ToolError):
            tools.run_soql(fake_sf, bad)
    assert fake_sf.queries == []


def test_soql_adds_limit_and_cleans_attributes(fake_sf):
    result = tools.run_soql(fake_sf, "SELECT Id, Name, Owner.Name FROM Account;", max_rows=10)
    assert fake_sf.queries == ["SELECT Id, Name, Owner.Name FROM Account LIMIT 10"]
    assert result["returned"] == 2
    assert "attributes" not in result["records"][0]
    assert result["records"][0]["Owner"] == {"Name": "Gus"}


def test_soql_keeps_existing_limit(fake_sf):
    tools.run_soql(fake_sf, "select Id from Account limit 5")
    assert fake_sf.queries == ["select Id from Account limit 5"]


def test_list_objects_filters(fake_sf):
    assert [o["name"] for o in tools.list_objects(fake_sf)["objects"]] == ["Account", "Invoice__c"]
    assert [o["name"] for o in tools.list_objects(fake_sf, custom_only=True)["objects"]] == ["Invoice__c"]
    assert [o["name"] for o in tools.list_objects(fake_sf, search="invoice")["objects"]] == ["Invoice__c"]


def test_describe_object_is_compact(fake_sf):
    desc = tools.describe_object(fake_sf, "Account")
    fields = {f["name"]: f for f in desc["fields"]}
    assert fields["Name"]["required"] is True
    assert fields["OwnerId"]["required"] is False  # defaulted on create
    assert fields["Industry"]["picklist_values"] == ["Banking"]  # inactive values dropped
    assert fields["OwnerId"]["reference_to"] == ["User"]
    assert desc["child_relationships"] == ["Contacts"]


def test_invalid_names_are_rejected(fake_sf):
    with pytest.raises(ToolError):
        tools.describe_object(fake_sf, "Account; DROP")
    with pytest.raises(ToolError):
        tools.get_record(fake_sf, "Account", "not-an-id")


def test_writes_blocked_when_disabled(fake_sf):
    with pytest.raises(ToolError, match="disabled"):
        tools.create_record(fake_sf, "Account", {"Name": "X"}, allow_writes=False, confirm=True)
    assert fake_sf.created == []


def test_create_requires_confirmation(fake_sf):
    preview = tools.create_record(fake_sf, "Account", {"Name": "X"}, allow_writes=True)
    assert preview["dry_run"] is True and fake_sf.created == []
    done = tools.create_record(fake_sf, "Account", {"Name": "X"}, allow_writes=True, confirm=True)
    assert done == {"created": True, "object": "Account", "id": "001000000000001AAA"}
    assert fake_sf.created == [("Account", {"Name": "X"})]


def test_update_preview_shows_before_and_after(fake_sf):
    record_id = "001000000000001AAA"
    preview = tools.update_record(fake_sf, "Account", record_id, {"Name": "New"}, allow_writes=True)
    assert preview["dry_run"] is True
    assert preview["before"] == {"Name": "Acme"} and preview["after"] == {"Name": "New"}
    assert fake_sf.updated == []
    tools.update_record(fake_sf, "Account", record_id, {"Name": "New"}, allow_writes=True, confirm=True)
    assert fake_sf.updated == [("Account", record_id, {"Name": "New"})]
