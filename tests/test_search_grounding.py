import pytest
from pydantic import ValidationError
from app.client import UpstreamResponseError
from app.search.models import SearchRequest, SearchResponse
from app.search.grounding import GroundingAccumulator

def test_search_request_validation():
    # Valid request
    req = SearchRequest(query="  python 3.13 release  ")
    assert req.query == "python 3.13 release"
    assert req.max_results == 5
    assert req.include_raw_content is False
    assert req.include_images is False

    # Whitespace-only query rejected
    with pytest.raises(ValidationError):
        SearchRequest(query="   ")

    # Empty query rejected
    with pytest.raises(ValidationError):
        SearchRequest(query="")

    # Max results: boolean rejected
    with pytest.raises(ValidationError):
        SearchRequest(query="valid", max_results=True)

    # Max results: out of range rejected
    with pytest.raises(ValidationError):
        SearchRequest(query="valid", max_results=0)
    with pytest.raises(ValidationError):
        SearchRequest(query="valid", max_results=21)

    # Extra fields forbidden
    with pytest.raises(ValidationError):
        SearchRequest(query="valid", unknown_field="test")

    # include_raw_content=True rejected
    with pytest.raises(ValidationError):
        SearchRequest(query="valid", include_raw_content=True)

    # include_images=True rejected
    with pytest.raises(ValidationError):
        SearchRequest(query="valid", include_images=True)

def test_two_different_cited_passages_and_urls():
    acc = GroundingAccumulator(wire_model="gemini-3.8-flash")
    # Feed answer text chunks
    acc.feed({"content_delta": "Python 3.13 has free threading. "})
    acc.feed({"content_delta": "It also has a JIT compiler."})

    # Feed metadata event with two chunks and two supports
    acc.feed({
        "grounding_metadata": {
            "webSearchQueries": ["python 3.13 features"],
            "groundingChunks": [
                {"web": {"uri": "https://docs.python.org/3.13/whatsnew/", "title": "What's New In Python 3.13"}},
                {"web": {"uri": "https://peps.python.org/pep-0703/", "title": "PEP 703"}},
            ],
            "groundingSupports": [
                {
                    "segment": {"text": "Python 3.13 has free threading."},
                    "groundingChunkIndices": [1],
                },
                {
                    "segment": {"text": "It also has a JIT compiler."},
                    "groundingChunkIndices": [0],
                },
            ],
        },
        "raw_finish_reason": "STOP",
        "model_version": "gemini-3.8-flash-001",
    })

    resp = acc.finish(query="python 3.13 features", max_results=5)
    assert resp.answer == "Python 3.13 has free threading. It also has a JIT compiler."
    assert len(resp.results) == 2
    # Check that descriptions are source-specific excerpts, not whole answer
    res_whatsnew = next(r for r in resp.results if "whatsnew" in r.url)
    res_pep = next(r for r in resp.results if "pep-0703" in r.url)

    assert "It also has a JIT compiler." in res_whatsnew.content
    assert "free threading" not in res_whatsnew.content
    assert res_whatsnew.content.startswith("Grounded answer excerpt (model-generated, not page text): ")

    assert "Python 3.13 has free threading." in res_pep.content
    assert "JIT compiler" not in res_pep.content
    assert res_pep.content.startswith("Grounded answer excerpt (model-generated, not page text): ")

    assert len(resp.citations) == 2
    assert resp.model == "gemini-3.8-flash-001"
    assert resp.search_queries == ["python 3.13 features"]

def test_reordered_chunk_list_preserves_past_associations():
    acc = GroundingAccumulator(wire_model="gemini-3.8-flash")
    # Event 1: chunks [URL_A, URL_B], support pointing to index 0 (URL_A)
    acc.feed({
        "content_delta": "Part A. ",
        "grounding_metadata": {
            "groundingChunks": [
                {"web": {"uri": "https://example.com/a", "title": "Page A"}},
                {"web": {"uri": "https://example.com/b", "title": "Page B"}},
            ],
            "groundingSupports": [
                {
                    "segment": {"text": "Part A."},
                    "groundingChunkIndices": [0],
                }
            ]
        }
    })

    # Event 2: chunks reversed [URL_B, URL_A], support pointing to index 0 (now URL_B)
    acc.feed({
        "content_delta": "Part B.",
        "grounding_metadata": {
            "groundingChunks": [
                {"web": {"uri": "https://example.com/b", "title": "Page B"}},
                {"web": {"uri": "https://example.com/a", "title": "Page A"}},
            ],
            "groundingSupports": [
                {
                    "segment": {"text": "Part B."},
                    "groundingChunkIndices": [0],
                }
            ]
        },
        "raw_finish_reason": "STOP",
    })

    resp = acc.finish(query="test", max_results=5)
    citation_a = next(c for c in resp.citations if c.url == "https://example.com/a")
    citation_b = next(c for c in resp.citations if c.url == "https://example.com/b")
    assert citation_a.cited_text == "Part A."
    assert citation_b.cited_text == "Part B."

def test_support_only_late_event_uses_most_recent_chunk_list():
    acc = GroundingAccumulator(wire_model="gemini-3.8-flash")
    # Event 1: sets chunk list
    acc.feed({
        "content_delta": "Initial text. ",
        "grounding_metadata": {
            "groundingChunks": [
                {"web": {"uri": "https://example.com/1", "title": "Page 1"}},
                {"web": {"uri": "https://example.com/2", "title": "Page 2"}},
            ]
        }
    })

    # Event 2: late event omits groundingChunks but supplies groundingSupports
    acc.feed({
        "content_delta": "Late text.",
        "grounding_metadata": {
            "groundingSupports": [
                {
                    "segment": {"text": "Late text."},
                    "groundingChunkIndices": [1],
                }
            ]
        },
        "raw_finish_reason": "STOP",
    })

    resp = acc.finish(query="test", max_results=5)
    assert len(resp.citations) == 1
    assert resp.citations[0].url == "https://example.com/2"
    assert resp.citations[0].cited_text == "Late text."

def test_duplicate_uri_upgrades_empty_title_and_preserves_order():
    acc = GroundingAccumulator(wire_model="gemini-3.8-flash")
    # Event 1: URL A with empty title, URL B with title B
    acc.feed({
        "grounding_metadata": {
            "groundingChunks": [
                {"web": {"uri": "https://example.com/a", "title": ""}},
                {"web": {"uri": "https://example.com/b", "title": "Title B"}},
            ]
        }
    })

    # Event 2: URL A with new non-empty title "Title A Upgraded"
    acc.feed({
        "grounding_metadata": {
            "groundingChunks": [
                {"web": {"uri": "https://example.com/a", "title": "Title A Upgraded"}},
            ]
        },
        "raw_finish_reason": "STOP",
    })

    resp = acc.finish(query="test", max_results=5)
    assert len(resp.results) == 2
    assert resp.results[0].url == "https://example.com/a"
    assert resp.results[0].title == "Title A Upgraded"
    assert resp.results[1].url == "https://example.com/b"
    assert resp.results[1].title == "Title B"

def test_deduplicates_citations_and_repeated_supports():
    acc = GroundingAccumulator(wire_model="gemini-3.8-flash")
    acc.feed({
        "grounding_metadata": {
            "groundingChunks": [
                {"web": {"uri": "https://example.com/a", "title": "Title A"}},
            ],
            "groundingSupports": [
                {"segment": {"text": "Identical segment"}, "groundingChunkIndices": [0]},
                {"segment": {"text": "Identical segment"}, "groundingChunkIndices": [0]},
            ]
        },
        "raw_finish_reason": "STOP",
    })

    resp = acc.finish(query="test", max_results=5)
    assert len(resp.citations) == 1
    assert resp.citations[0].cited_text == "Identical segment"
    # Content has unique segments only
    assert resp.results[0].content == "Grounded answer excerpt (model-generated, not page text): Identical segment"

def test_missing_supports_produce_empty_content():
    acc = GroundingAccumulator(wire_model="gemini-3.8-flash")
    acc.feed({
        "content_delta": "Answer without explicit support link.",
        "grounding_metadata": {
            "groundingChunks": [
                {"web": {"uri": "https://example.com/unsupported", "title": "Unsupported Source"}},
            ],
            "groundingSupports": [],
        },
        "raw_finish_reason": "STOP",
    })

    resp = acc.finish(query="test", max_results=5)
    assert len(resp.results) == 1
    assert resp.results[0].content == ""
    assert len(resp.citations) == 0

def test_invalid_urls_and_negative_indices_skipped():
    acc = GroundingAccumulator(wire_model="gemini-3.8-flash")
    acc.feed({
        "grounding_metadata": {
            "groundingChunks": [
                {"web": {"uri": "ftp://bad-scheme.com", "title": "FTP"}},
                {"web": {"uri": "https://user:pass@bad-userinfo.com", "title": "Userinfo"}},
                {"web": {"uri": "/relative/path", "title": "Relative"}},
                {"web": {"uri": "https://valid.com", "title": "Valid"}},
            ],
            "groundingSupports": [
                {"segment": {"text": "Valid text"}, "groundingChunkIndices": [3]},
                {"segment": {"text": "Invalid index text"}, "groundingChunkIndices": [-1, 0, 1, 99]},
            ]
        },
        "raw_finish_reason": "STOP",
    })

    resp = acc.finish(query="test", max_results=5)
    assert len(resp.results) == 1
    assert resp.results[0].url == "https://valid.com"
    assert len(resp.citations) == 1
    assert resp.citations[0].url == "https://valid.com"
    assert resp.citations[0].cited_text == "Valid text"

def test_max_results_slicing():
    acc = GroundingAccumulator(wire_model="gemini-3.8-flash")
    acc.feed({
        "content_delta": "Full answer retained.",
        "grounding_metadata": {
            "webSearchQueries": ["q1", "q2"],
            "groundingChunks": [
                {"web": {"uri": "https://example.com/1", "title": "1"}},
                {"web": {"uri": "https://example.com/2", "title": "2"}},
                {"web": {"uri": "https://example.com/3", "title": "3"}},
            ],
            "groundingSupports": [
                {"segment": {"text": "Seg 1"}, "groundingChunkIndices": [0]},
                {"segment": {"text": "Seg 2"}, "groundingChunkIndices": [1]},
                {"segment": {"text": "Seg 3"}, "groundingChunkIndices": [2]},
            ]
        },
        "raw_finish_reason": "STOP",
    })

    resp = acc.finish(query="test", max_results=1)
    assert resp.answer == "Full answer retained."
    assert resp.search_queries == ["q1", "q2"]
    assert len(resp.results) == 1
    assert resp.results[0].url == "https://example.com/1"
    # Citations only returned for sliced results
    assert len(resp.citations) == 1
    assert resp.citations[0].url == "https://example.com/1"

def test_thought_and_signature_filtered_from_answer():
    acc = GroundingAccumulator(wire_model="gemini-3.8-flash")
    acc.feed({
        "content_delta": "Public answer.",
        "reasoning_delta": "Hidden thinking...",
        "thought_signature": "sig_secret",
        "raw_finish_reason": "STOP",
    })

    resp = acc.finish(query="test", max_results=5)
    assert resp.answer == "Public answer."
    assert "Hidden thinking" not in resp.answer

def test_incomplete_stream_raises():
    acc = GroundingAccumulator(wire_model="gemini-3.8-flash")
    acc.feed({"content_delta": "Incomplete answer without finish reason."})

    with pytest.raises(UpstreamResponseError, match="without a terminal finish reason"):
        acc.finish(query="test", max_results=5)

def test_invalid_finish_reason_raises():
    acc = GroundingAccumulator(wire_model="gemini-3.8-flash")
    acc.feed({
        "content_delta": "Answer blocked by safety.",
        "raw_finish_reason": "SAFETY",
    })

    with pytest.raises(UpstreamResponseError, match="invalid finish reason"):
        acc.finish(query="test", max_results=5)

def test_tool_call_raises():
    acc = GroundingAccumulator(wire_model="gemini-3.8-flash")
    acc.feed({
        "tool_call_delta": [{"function": {"name": "unexpected_tool"}}],
        "raw_finish_reason": "STOP",
    })

    with pytest.raises(UpstreamResponseError, match="Unexpected tool call"):
        acc.finish(query="test", max_results=5)

def test_max_tokens_finish_accepted():
    acc = GroundingAccumulator(wire_model="gemini-3.8-flash")
    acc.feed({
        "content_delta": "Partial answer cut off by token limit.",
        "grounding_metadata": {
            "groundingChunks": [{"web": {"uri": "https://example.com", "title": "Ex"}}],
            "groundingSupports": [{"segment": {"text": "Partial answer"}, "groundingChunkIndices": [0]}],
        },
        "raw_finish_reason": "MAX_TOKENS",
    })

    resp = acc.finish(query="test", max_results=5)
    assert resp.answer == "Partial answer cut off by token limit."
    assert len(resp.results) == 1

def test_no_sources_returns_empty_results_and_citations():
    acc = GroundingAccumulator(wire_model="gemini-3.8-flash")
    acc.feed({
        "content_delta": "Direct answer without web grounding.",
        "raw_finish_reason": "STOP",
    })

    resp = acc.finish(query="test", max_results=5)
    assert resp.answer == "Direct answer without web grounding."
    assert resp.results == []
    assert resp.citations == []

def test_late_usage_after_finish_preserved():
    acc = GroundingAccumulator(wire_model="gemini-3.8-flash")
    acc.feed({
        "content_delta": "Answer.",
        "raw_finish_reason": "STOP",
    })
    # Late usage event
    acc.feed({
        "usage": {"prompt_tokens": 15, "completion_tokens": 25, "total_tokens": 40},
    })

    resp = acc.finish(query="test", max_results=5)
    assert resp.usage is not None
    assert resp.usage.prompt_tokens == 15
    assert resp.usage.completion_tokens == 25
    assert resp.usage.total_tokens == 40
