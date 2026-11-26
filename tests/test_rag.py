import chromadb
import pytest

from sf_copilot.rag import ApexIndex, chunk_code, fetch_apex_sources

from .conftest import HashEmbedding


def test_chunk_code_windows_and_line_numbers():
    body = "\n".join(f"line {i}" for i in range(1, 131))  # 130 lines
    chunks = chunk_code("Foo", "ApexClass", body, max_lines=60, overlap=10)
    assert [(c.metadata["start_line"], c.metadata["end_line"]) for c in chunks] == [(1, 60), (51, 110), (101, 130)]
    assert chunks[0].text.startswith("// ApexClass Foo (lines 1-60)")
    assert chunk_code("Empty", "ApexClass", "") == []
    with pytest.raises(ValueError):
        chunk_code("Foo", "ApexClass", body, max_lines=10, overlap=10)


def test_fetch_follows_pagination(fake_sf):
    fake_sf.tooling_pages = {
        "FROM%20ApexClass": {"done": False, "nextRecordsUrl": "/services/data/v59.0/tooling/query/01gPAGE2-2000",
                             "records": [{"Name": "A", "Body": "class A {}"}]},
        "01gPAGE2": {"done": True, "records": [{"Name": "B", "Body": "class B {}"}]},
        "FROM%20ApexTrigger": {"done": True, "records": [{"Name": "T", "Body": "trigger T on Account (before insert) {}"}]},
    }
    sources = fetch_apex_sources(fake_sf)
    assert [(s["name"], s["kind"]) for s in sources] == [("A", "ApexClass"), ("B", "ApexClass"), ("T", "ApexTrigger")]


def test_index_and_search_finds_relevant_code(tmp_path):
    index = ApexIndex(client=chromadb.PersistentClient(path=str(tmp_path)), embedding_function=HashEmbedding())
    stats = index.build([
        {"name": "InvoiceService", "kind": "ApexClass",
         "body": "public class InvoiceService {\n  public static void sendInvoice(Id invoiceId) {\n"
                 "    // calculate tax and send invoice email\n  }\n}"},
        {"name": "LeadRouter", "kind": "ApexClass",
         "body": "public class LeadRouter {\n  // assign lead to queue by region\n}"},
    ])
    assert stats == {"files": 2, "chunks": 2}
    hits = index.search("how is the invoice email sent with tax?", k=1)
    assert hits[0]["file"] == "InvoiceService (ApexClass)"
    assert hits[0]["lines"] == "1-5"


def test_search_on_empty_index_returns_nothing(tmp_path):
    index = ApexIndex(client=chromadb.PersistentClient(path=str(tmp_path)), embedding_function=HashEmbedding())
    assert index.search("anything") == []
