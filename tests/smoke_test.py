# tests/smoke_test.py
import os
import sys
import httpx

BASE_URL = os.getenv("PROXY_URL", "http://127.0.0.1:8000")

def main():
    print(f"1. Checking {BASE_URL}/status...")
    r = httpx.get(f"{BASE_URL}/status")
    print(f"   Status: {r.status_code}, response: {r.json()}")
    assert r.status_code == 200

    print(f"2. Checking {BASE_URL}/v1/models...")
    r = httpx.get(f"{BASE_URL}/v1/models")
    data = r.json()
    count = len(data.get("data", []))
    print(f"   Status: {r.status_code}, models available: {count}")
    assert r.status_code == 200
    assert count > 0

    print("Smoke test completed successfully!")

if __name__ == "__main__":
    main()
