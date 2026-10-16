from typing import Literal
from urllib.parse import urlsplit
from pydantic import BaseModel, ConfigDict, Field, field_validator

class SearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str
    max_results: int = 5
    include_raw_content: Literal[False] = False
    include_images: Literal[False] = False

    @field_validator("query", mode="before")
    @classmethod
    def validate_query(cls, v: object) -> str:
        if not isinstance(v, str):
            raise ValueError("query must be a string")
        trimmed = v.strip()
        if not (1 <= len(trimmed) <= 8192):
            raise ValueError("query must be between 1 and 8192 characters after trimming")
        return trimmed

    @field_validator("max_results", mode="before")
    @classmethod
    def validate_max_results(cls, v: object) -> int:
        if isinstance(v, bool):
            raise ValueError("max_results must be an integer, not boolean")
        if not isinstance(v, int):
            raise ValueError("max_results must be an integer")
        if not (1 <= v <= 20):
            raise ValueError("max_results must be between 1 and 20")
        return v

class SearchResult(BaseModel):
    title: str
    url: str
    content: str

class SearchCitation(BaseModel):
    url: str
    title: str
    cited_text: str

class SearchUsage(BaseModel):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int

class SearchResponse(BaseModel):
    query: str
    answer: str
    results: list[SearchResult]
    citations: list[SearchCitation]
    search_queries: list[str]
    model: str
    usage: SearchUsage | None = None

# Upstream grounding structures

def is_valid_source_url(url: str) -> bool:
    try:
        parts = urlsplit(url)
        if parts.scheme.lower() not in ("http", "https"):
            return False
        if not parts.netloc or not parts.hostname:
            return False
        if parts.username or parts.password:
            return False
        return True
    except Exception:
        return False

class UpstreamWebSource(BaseModel):
    model_config = ConfigDict(extra="ignore")
    uri: str | None = None
    title: str | None = None

class UpstreamGroundingChunk(BaseModel):
    model_config = ConfigDict(extra="ignore")
    web: UpstreamWebSource | None = None

class UpstreamSegment(BaseModel):
    model_config = ConfigDict(extra="ignore")
    text: str | None = None
    startIndex: int | None = None
    endIndex: int | None = None

class UpstreamGroundingSupport(BaseModel):
    model_config = ConfigDict(extra="ignore")
    segment: UpstreamSegment | None = None
    groundingChunkIndices: list[int] | None = None

class UpstreamGroundingMetadata(BaseModel):
    model_config = ConfigDict(extra="ignore")
    webSearchQueries: list[str] | None = None
    groundingChunks: list[UpstreamGroundingChunk] | None = None
    groundingSupports: list[UpstreamGroundingSupport] | None = None
