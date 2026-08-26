import pytest

from conftest import ARXIV_ID, SYNTH_HTML, FakeFetcher, default_routes
from litmine.retrieval import html_to_text, retrieve_document, strip_arxiv_chrome, norm_arxiv_id, pdf_to_text


def test_html_tables_and_spans_become_pipe_rows():
    t = html_to_text(SYNTH_HTML)
    assert "| T-Beta | 41.0 | 52.0 |" in t
    assert "| Avg turns | 7.1 |" in t          # ltx_td spans
    assert "## 3 Setup" in t
    assert "\n- \n" not in t                    # empty nav items removed


def test_strip_chrome():
    t = strip_arxiv_chrome(html_to_text(SYNTH_HTML))
    assert t.startswith("# Synthetic Teacher Study")


def test_norm_arxiv_id():
    assert norm_arxiv_id("https://arxiv.org/abs/2606.03461v2") == "2606.03461"
    assert norm_arxiv_id("arXiv:2606.03461") == "2606.03461"
    assert norm_arxiv_id("no id here") is None


def test_retrieve_prefers_html_and_hashes(cache, fetcher):
    d = retrieve_document(fetcher, cache, arxiv_id=ARXIV_ID)
    assert d["text_format"] == "arxiv_html" and d["title"] == "Synthetic Teacher Study"
    assert d["year"] == 2025 and len(d["doc_hash"]) == 64
    assert "| T-Alpha | 30.0 | 52.0 |" in d["text"]
    n = len(fetcher.requested)
    d2 = retrieve_document(fetcher, cache, arxiv_id=ARXIV_ID)
    assert d2 == d and len(fetcher.requested) == n   # document cache hit, no HTTP


def test_retrieve_falls_back_to_pdf_then_abstract(cache):
    from pypdf import PdfWriter
    import io
    routes = default_routes()
    del routes[f"https://arxiv.org/html/{ARXIV_ID}"]
    buf = io.BytesIO()
    w = PdfWriter(); w.add_blank_page(200, 200); w.write(buf)
    routes[f"https://arxiv.org/pdf/{ARXIV_ID}"] = (200, buf.getvalue(), {"Content-Type": "application/pdf"})
    f = FakeFetcher(cache, routes)
    d = retrieve_document(f, cache, arxiv_id=ARXIV_ID)
    # blank PDF yields <2000 chars -> abstract-only fallback, and errors are recorded
    assert d["text_format"] == "abstract_only"
    assert any(e.startswith("arxiv_html") for e in d["retrieval_errors"])
    assert "three teachers" in d["text"]


def test_truncation_flag(cache, fetcher):
    d = retrieve_document(fetcher, cache, arxiv_id=ARXIV_ID, max_chars=300)
    assert d["truncated"] and d["text"].endswith("[TRUNCATED BY PIPELINE]")


def test_fetcher_retries_on_503(cache):
    f = FakeFetcher(cache, default_routes())
    f.fail_first[f"https://arxiv.org/html/{ARXIV_ID}"] = 2
    status, body, _ = f.get(f"https://arxiv.org/html/{ARXIV_ID}")
    assert status == 200 and b"T-Alpha" in body
