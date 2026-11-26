"""Shared fakes so tests never touch a real org, network or embedding model."""

from __future__ import annotations

import hashlib
import math
import re

import pytest

from sf_copilot.config import Settings


class FakeSObject:
    def __init__(self, sf, name):
        self.sf, self.name = sf, name

    def describe(self):
        return self.sf.object_describes[self.name]

    def get(self, record_id):
        return {"attributes": {"type": self.name}, "Id": record_id, "Name": "Acme"}

    def create(self, data):
        self.sf.created.append((self.name, data))
        return {"id": "001000000000001AAA", "success": True}

    def update(self, record_id, data):
        self.sf.updated.append((self.name, record_id, data))
        return 204


class FakeSalesforce:
    def __init__(self):
        self.queries, self.created, self.updated = [], [], []
        self.object_describes = {
            "Account": {
                "name": "Account",
                "label": "Account",
                "fields": [
                    {"name": "Id", "label": "Account ID", "type": "id", "nillable": False, "createable": False},
                    {"name": "Name", "label": "Account Name", "type": "string", "nillable": False,
                     "createable": True, "defaultedOnCreate": False},
                    {"name": "Industry", "label": "Industry", "type": "picklist", "nillable": True,
                     "createable": True, "picklistValues": [
                         {"value": "Banking", "active": True}, {"value": "Old", "active": False}]},
                    {"name": "OwnerId", "label": "Owner", "type": "reference", "nillable": False,
                     "createable": True, "defaultedOnCreate": True, "referenceTo": ["User"],
                     "relationshipName": "Owner"},
                ],
                "childRelationships": [{"relationshipName": "Contacts"}, {"relationshipName": None}],
            }
        }
        self.tooling_pages = {}

    def query(self, q):
        self.queries.append(q)
        return {
            "totalSize": 2,
            "done": True,
            "records": [
                {"attributes": {"type": "Account"}, "Id": "001A", "Name": "Acme",
                 "Owner": {"attributes": {"type": "User"}, "Name": "Gus"}},
                {"attributes": {"type": "Account"}, "Id": "001B", "Name": "Globex", "Owner": None},
            ],
        }

    def describe(self):
        return {"sobjects": [
            {"name": "Account", "label": "Account", "custom": False, "queryable": True},
            {"name": "Invoice__c", "label": "Invoice", "custom": True, "queryable": True},
            {"name": "AccountHistory", "label": "Account History", "custom": False, "queryable": False},
        ]}

    def toolingexecute(self, action, method="GET", data=None, **kwargs):
        for key, page in self.tooling_pages.items():
            if key in action:
                return page
        return {"done": True, "records": []}

    def __getattr__(self, name):
        if name[0].isupper():
            return FakeSObject(self, name)
        raise AttributeError(name)


class HashEmbedding:
    """Deterministic bag-of-words embedding: similar words -> similar vectors. No model download."""

    def __init__(self, dim: int = 256):
        self.dim = dim

    def __call__(self, input):  # noqa: A002 - chromadb's EmbeddingFunction signature
        vectors = []
        for text in input:
            vec = [0.0] * self.dim
            for token in re.findall(r"[a-z]+", text.lower()):
                vec[int(hashlib.md5(token.encode()).hexdigest(), 16) % self.dim] += 1.0
            norm = math.sqrt(sum(v * v for v in vec)) or 1.0
            vectors.append([v / norm for v in vec])
        return vectors

    # chromadb >= 1.0 inspects these on custom embedding functions
    def name(self):
        return "hash-embedding"

    def embed_query(self, input):  # noqa: A002
        return self(input)

    def is_legacy(self):
        return True

    def get_config(self):
        return {}

    def default_space(self):
        return "cosine"

    def supported_spaces(self):
        return ["cosine", "l2", "ip"]


@pytest.fixture
def fake_sf():
    return FakeSalesforce()


@pytest.fixture
def settings(tmp_path):
    return Settings(allow_writes=True, max_rows=50, chroma_path=str(tmp_path / "chroma"))
