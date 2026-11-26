"""RAG over the org's Apex code.

1. Fetch Apex classes and triggers through the Tooling API.
2. Split them into overlapping line-window chunks.
3. Store the chunks in ChromaDB, which turns each chunk into an embedding (a vector of numbers).
4. At question time, embed the question and return the most similar chunks.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote

import chromadb

COLLECTION = "apex_code"
SOURCES = {
    "ApexClass": "SELECT Id, Name, Body FROM ApexClass WHERE NamespacePrefix = null",
    "ApexTrigger": "SELECT Id, Name, TableEnumOrId, Body FROM ApexTrigger WHERE NamespacePrefix = null",
}


@dataclass
class Chunk:
    id: str
    text: str
    metadata: dict


def chunk_code(name: str, kind: str, body: str, max_lines: int = 60, overlap: int = 10) -> list[Chunk]:
    """Split source code into overlapping windows, keeping line numbers for citations."""
    if overlap >= max_lines:
        raise ValueError("overlap must be smaller than max_lines")
    lines = (body or "").splitlines()
    if not lines:
        return []
    chunks, start, step = [], 0, max_lines - overlap
    while start < len(lines):
        end = min(start + max_lines, len(lines))
        header = f"// {kind} {name} (lines {start + 1}-{end})\n"
        chunks.append(
            Chunk(
                id=f"{kind}:{name}:{start + 1}",
                text=header + "\n".join(lines[start:end]),
                metadata={"name": name, "kind": kind, "start_line": start + 1, "end_line": end},
            )
        )
        if end == len(lines):
            break
        start += step
    return chunks


def fetch_apex_sources(sf) -> list[dict]:
    """Return [{'name', 'kind', 'body'}] for every non-managed Apex class and trigger."""
    sources = []
    for kind, soql in SOURCES.items():
        response = sf.toolingexecute(f"query/?q={quote(soql)}")
        while True:
            for rec in response.get("records", []):
                sources.append({"name": rec["Name"], "kind": kind, "body": rec.get("Body") or ""})
            next_url = response.get("nextRecordsUrl")
            if response.get("done", True) or not next_url:
                break
            response = sf.toolingexecute(next_url.split("/tooling/", 1)[1])
    return sources


class ApexIndex:
    def __init__(self, path: str = ".chroma", client=None, embedding_function=None):
        self.client = client or chromadb.PersistentClient(path=path)
        self.embedding_function = embedding_function
        self.collection = self._collection()

    def _collection(self):
        kwargs = {"embedding_function": self.embedding_function} if self.embedding_function else {}
        return self.client.get_or_create_collection(COLLECTION, metadata={"hnsw:space": "cosine"}, **kwargs)

    def count(self) -> int:
        return self.collection.count()

    def build(self, sources: list[dict], reset: bool = True, batch_size: int = 100) -> dict:
        if reset:
            self.client.delete_collection(COLLECTION)
            self.collection = self._collection()
        chunks = [c for s in sources for c in chunk_code(s["name"], s["kind"], s["body"])]
        for i in range(0, len(chunks), batch_size):
            batch = chunks[i : i + batch_size]
            self.collection.upsert(
                ids=[c.id for c in batch],
                documents=[c.text for c in batch],
                metadatas=[c.metadata for c in batch],
            )
        return {"files": len(sources), "chunks": len(chunks)}

    def search(self, question: str, k: int = 5) -> list[dict]:
        if self.count() == 0:
            return []
        res = self.collection.query(query_texts=[question], n_results=min(k, self.count()))
        hits = []
        for doc, meta, dist in zip(res["documents"][0], res["metadatas"][0], res["distances"][0]):
            hits.append(
                {
                    "file": f"{meta['name']} ({meta['kind']})",
                    "lines": f"{meta['start_line']}-{meta['end_line']}",
                    "similarity": round(1 - dist, 3),
                    "code": doc,
                }
            )
        return hits
