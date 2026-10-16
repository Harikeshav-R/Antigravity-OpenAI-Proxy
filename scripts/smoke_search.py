import os
import sys
from urllib.parse import urlsplit
import httpx

def is_valid_http_url(url: str) -> bool:
    try:
        parts = urlsplit(url)
        return parts.scheme.lower() in ("http", "https") and bool(parts.netloc and parts.hostname)
    except Exception:
        return False

def main() -> None:
    proxy_url = os.getenv("PROXY_URL", "http://127.0.0.1:8000").rstrip("/")
    api_key = os.getenv("SEARCH_API_KEY", "").strip()

    if not api_key:
        print("ERROR: SEARCH_API_KEY environment variable is not set.", file=sys.stderr)
        sys.exit(1)

    search_endpoint = f"{proxy_url}/v1/search"
    payload = {
        "query": "site:python.org Python release announcements",
        "max_results": 3,
        "include_raw_content": False,
        "include_images": False,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    print(f"Sending live search request to {search_endpoint}...")
    try:
        with httpx.Client(timeout=60.0) as client:
            resp = client.post(search_endpoint, json=payload, headers=headers)
    except httpx.HTTPError as err:
        print(f"ERROR: HTTP connection failed: {err}", file=sys.stderr)
        sys.exit(1)

    if resp.status_code != 200:
        print(f"ERROR: Expected status 200, got {resp.status_code}: {resp.text}", file=sys.stderr)
        sys.exit(1)

    try:
        data = resp.json()
    except Exception as err:
        print(f"ERROR: Failed to parse JSON response: {err}", file=sys.stderr)
        sys.exit(1)

    results = data.get("results")
    if not isinstance(results, list) or len(results) == 0:
        print(f"ERROR: Expected non-empty results list, got: {results}", file=sys.stderr)
        sys.exit(1)

    result_urls = set()
    for idx, r in enumerate(results):
        url = r.get("url", "")
        title = r.get("title", "")
        content = r.get("content", "")

        if not is_valid_http_url(url):
            print(f"ERROR: Result [{idx}] has invalid URL: {url}", file=sys.stderr)
            sys.exit(1)

        result_urls.add(url)

        if content and not content.startswith("Grounded answer excerpt (model-generated, not page text): "):
            print(f"ERROR: Result [{idx}] content must be empty or labeled model-generated excerpt: {content[:80]}", file=sys.stderr)
            sys.exit(1)

        print(f"[{idx + 1}] {title} -> {url}")

    citations = data.get("citations", [])
    if isinstance(citations, list):
        for idx, c in enumerate(citations):
            c_url = c.get("url", "")
            if c_url not in result_urls:
                print(f"ERROR: Citation [{idx}] URL {c_url} is absent from results: {result_urls}", file=sys.stderr)
                sys.exit(1)

    queries = data.get("search_queries", [])
    model = data.get("model", "unknown")
    print(f"\nSUCCESS: Grounded search probe passed!")
    print(f"Returned {len(results)} source results using model {model}.")
    print(f"Search queries executed: {queries}")

if __name__ == "__main__":
    main()
