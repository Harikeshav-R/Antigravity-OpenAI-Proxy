from typing import Mapping
from app.client import UpstreamResponseError
from app.search.models import (
    SearchResponse,
    SearchResult,
    SearchCitation,
    SearchUsage,
    UpstreamGroundingMetadata,
    is_valid_source_url,
)

class GroundingAccumulator:
    def __init__(self, wire_model: str) -> None:
        self.wire_model = wire_model
        self.answer_parts: list[str] = []
        self.web_search_queries: list[str] = []
        self.seen_search_queries: set[str] = set()
        self.current_chunks: list[tuple[str, str]] = []  # (url, title) from latest event
        self.resolved_supports: list[tuple[str, str, str]] = []  # (url, title, cited_text)
        self.sources_dict: dict[str, str] = {}  # url -> title (preserves first-seen order)
        self.latest_model_version: str | None = None
        self.latest_usage: SearchUsage | None = None
        self.raw_finish_reason: str | None = None
        self.has_tool_call: bool = False

    def feed(self, item: Mapping[str, object]) -> None:
        # Append only non-thinking content_delta to answer; never expose reasoning_delta or signatures
        content_delta = item.get("content_delta")
        if isinstance(content_delta, str) and content_delta:
            self.answer_parts.append(content_delta)

        # Check for unexpected tool calls
        tool_calls = item.get("tool_call_delta")
        if tool_calls:
            self.has_tool_call = True

        # Model version
        model_version = item.get("model_version")
        if isinstance(model_version, str) and model_version:
            self.latest_model_version = model_version

        # Usage
        usage_dict = item.get("usage")
        if isinstance(usage_dict, dict):
            prompt = usage_dict.get("prompt_tokens", 0)
            completion = usage_dict.get("completion_tokens", 0)
            total = usage_dict.get("total_tokens", prompt + completion)
            if isinstance(prompt, int) and isinstance(completion, int) and isinstance(total, int):
                self.latest_usage = SearchUsage(
                    prompt_tokens=prompt,
                    completion_tokens=completion,
                    total_tokens=total,
                )

        # Raw finish reason
        raw_finish = item.get("raw_finish_reason")
        if isinstance(raw_finish, str) and raw_finish:
            self.raw_finish_reason = raw_finish

        # Grounding metadata
        grounding_meta_raw = item.get("grounding_metadata")
        if isinstance(grounding_meta_raw, dict):
            try:
                meta = UpstreamGroundingMetadata.model_validate(grounding_meta_raw)
            except Exception:
                # Tolerate malformed optional fields inside metadata
                return

            if meta.webSearchQueries:
                for q in meta.webSearchQueries:
                    if q and q not in self.seen_search_queries:
                        self.seen_search_queries.add(q)
                        self.web_search_queries.append(q)

            # If groundingChunks is provided, update current_chunks list
            if meta.groundingChunks is not None:
                new_chunks: list[tuple[str, str]] = []
                for chunk in meta.groundingChunks:
                    web = chunk.web
                    if web and web.uri and is_valid_source_url(web.uri):
                        title = web.title.strip() if web.title else ""
                        new_chunks.append((web.uri, title))
                    else:
                        new_chunks.append(("", ""))
                self.current_chunks = new_chunks

                for uri, title in self.current_chunks:
                    if uri:
                        if uri not in self.sources_dict:
                            self.sources_dict[uri] = title or uri
                        elif not self.sources_dict[uri] or self.sources_dict[uri] == uri:
                            if title:
                                self.sources_dict[uri] = title

            # Process grounding supports immediately against current_chunks
            if meta.groundingSupports:
                for supp in meta.groundingSupports:
                    segment = supp.segment
                    seg_text = segment.text if segment and segment.text else None
                    indices = supp.groundingChunkIndices or []
                    for idx in indices:
                        if 0 <= idx < len(self.current_chunks):
                            url, title = self.current_chunks[idx]
                            if url:
                                if url not in self.sources_dict:
                                    self.sources_dict[url] = title or url
                                elif not self.sources_dict[url] or self.sources_dict[url] == url:
                                    if title:
                                        self.sources_dict[url] = title
                                if seg_text:
                                    self.resolved_supports.append((url, self.sources_dict[url], seg_text))

    def finish(self, query: str, max_results: int) -> SearchResponse:
        if self.has_tool_call:
            raise UpstreamResponseError("Unexpected tool call in grounded search response", status_code=502)

        if not self.raw_finish_reason:
            raise UpstreamResponseError("Upstream stream ended without a terminal finish reason", status_code=502)

        finish_upper = self.raw_finish_reason.upper()
        if finish_upper not in ("STOP", "MAX_TOKENS"):
            raise UpstreamResponseError(
                f"Upstream search ended with invalid finish reason: {self.raw_finish_reason}",
                status_code=502,
            )

        segments_by_url: dict[str, list[str]] = {}
        seen_citations: set[tuple[str, str]] = set()
        all_citations: list[SearchCitation] = []

        for url, _, cited_text in self.resolved_supports:
            current_title = self.sources_dict.get(url, url)
            pair = (url, cited_text)
            if pair not in seen_citations:
                seen_citations.add(pair)
                all_citations.append(SearchCitation(url=url, title=current_title, cited_text=cited_text))
                if url not in segments_by_url:
                    segments_by_url[url] = []
                segments_by_url[url].append(cited_text)

        all_results: list[SearchResult] = []
        for url, title in self.sources_dict.items():
            segs = segments_by_url.get(url, [])
            if segs:
                content = "Grounded answer excerpt (model-generated, not page text): " + "\n".join(segs)
            else:
                content = ""
            all_results.append(SearchResult(title=title, url=url, content=content))

        sliced_results = all_results[:max_results]
        allowed_urls = {r.url for r in sliced_results}
        filtered_citations = [c for c in all_citations if c.url in allowed_urls]

        return SearchResponse(
            query=query,
            answer="".join(self.answer_parts),
            results=sliced_results,
            citations=filtered_citations,
            search_queries=list(self.web_search_queries),
            model=self.latest_model_version or self.wire_model,
            usage=self.latest_usage,
        )
