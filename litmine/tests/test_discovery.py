from conftest import ARXIV_ID, FakeFetcher, atom_feed, default_routes
from litmine.discovery import Discovery, build_arxiv_queries, build_free_text_queries


def test_queries_are_many_and_combinatorial():
    qs = build_arxiv_queries()
    assert len(qs) > 50 and len(set(qs)) == len(qs)
    assert any("teacher" in q and "agent" in q for q in qs)
    assert len(build_free_text_queries()) >= 5


def test_arxiv_search_parses_and_dedupes(cache):
    routes = default_routes()
    routes["https://export.arxiv.org/api/query?search_query"] = (
        200, atom_feed([{"id": "2501.11111", "title": "Paper A", "abstract": "abs a"},
                        {"id": "2501.22222", "title": "Paper B"}]), {})
    f = FakeFetcher(cache, routes)
    d = Discovery(f, cache, per_query=5)
    r1 = d.arxiv_search("q1")
    r2 = d.arxiv_search("q2")
    assert {c.arxiv_id for c in r1} == {"2501.11111", "2501.22222"}
    assert len(d.candidates) == 2
    assert d.candidates["arxiv:2501.11111"].sources == ["arxiv:q1", "arxiv:q2"]
    n = len(f.requested)
    d.arxiv_search("q1")                                    # cached: no new HTTP
    assert len(f.requested) == n


def test_hf_tags_yield_paper_candidates(cache):
    routes = default_routes()
    routes["https://huggingface.co/api/datasets?"] = (200, [
        {"id": "org/ds", "tags": ["arxiv:2501.33333", "task_categories:text-generation"]},
        {"id": "org/other", "tags": []}], {})
    d = Discovery(FakeFetcher(cache, routes), cache)
    out = d.hf_search("teacher trajectories")
    assert [c.arxiv_id for c in out] == ["2501.33333"]
    assert out[0].tags == ["hf_dataset:org/ds"]


def test_openalex_expansion_follows_citations_and_references(cache):
    routes = default_routes()
    work = {"id": "https://openalex.org/W1", "title": "Seed", "publication_year": 2026,
            "locations": [{"landing_page_url": f"https://arxiv.org/abs/{ARXIV_ID}"}],
            "referenced_works": ["https://openalex.org/W2"], "ids": {}}
    citing = {"id": "https://openalex.org/W3", "title": "Citing paper", "ids": {},
              "locations": [{"landing_page_url": "https://arxiv.org/abs/2502.00003"}],
              "abstract_inverted_index": {"We": [0], "cite": [1]}}
    ref = {"id": "https://openalex.org/W2", "title": "Referenced paper", "ids": {"doi": "https://doi.org/10.1/x"},
           "locations": []}

    def route(url):
        if "filter=locations.landing_page_url" in url:
            return 200, {"results": [work]}, {}
        if "filter=cites" in url:
            return 200, {"results": [citing]}, {}
        if "filter=openalex" in url:
            return 200, {"results": [ref]}, {}
        return 200, {"results": []}, {}
    routes["https://api.openalex.org/works?"] = route
    d = Discovery(FakeFetcher(cache, routes), cache)
    seed = d.add_seed(ARXIV_ID)
    d.expand_citations([seed], depth=1)
    keys = set(d.candidates)
    assert "arxiv:2502.00003" in keys and "url:https://doi.org/10.1/x" in keys
    assert d.candidates["arxiv:2502.00003"].abstract == "We cite" and d.candidates["arxiv:2502.00003"].hops == 1


def test_run_without_search_only_seeds(cache):
    d = Discovery(FakeFetcher(cache, default_routes()), cache)
    cands = d.run([ARXIV_ID], arxiv_queries=[], free_queries=[], hf_queries=[], github_queries=[],
                  citation_depth=0)
    assert [c.key for c in cands] == [f"arxiv:{ARXIV_ID}"] and cands[0].sources == ["seed"]
