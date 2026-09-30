# Antigravity OpenAI-Compatible Proxy

A high-performance, containerized, OpenAI-compatible proxy that seamlessly translates standard `/v1/chat/completions` and `/v1/models` requests to Google Cloud Code Assist's (CCA) internal Antigravity backend.

Provides transparent access to top-tier frontier models—including **Claude 3.7 Sonnet**, **Claude Opus**, **Gemini 3.8 Flash**, and **Gemini 3 Pro**—under an active Google Cloud Code Assist / Antigravity subscription, with full support for streaming SSE, tool calling, extended reasoning, automatic token refreshes, and LiteLLM sidecar routing.

---

## Table of Contents

- [Architecture & Flow](#architecture--flow)
- [Key Capabilities](#key-capabilities)
- [Supported Models & Routing](#supported-models--routing)
- [Core Translation Invariants](#core-translation-invariants)
  - [Claude Model Handling](#claude-model-handling)
  - [Gemini Model Enums](#gemini-model-enums)
  - [Cryptographic Thought Signatures](#cryptographic-thought-signatures)
  - [Planning Leak Sanitizer](#planning-leak-sanitizer)
  - [Reliability & Empty-Stream Retries](#reliability--empty-stream-retries)
- [Authentication & Token Lifecycle](#authentication--token-lifecycle)
  - [Single-Flight Token Refresh](#single-flight-token-refresh)
  - [Web OAuth Login Flow](#web-oauth-login-flow)
  - [Zero-OAuth CLI Import (macOS)](#zero-oauth-cli-import-macos)
  - [Native Multi-Account Pool & Quota Failover](#native-multi-account-pool--quota-failover)
  - [Credentials Storage & Environment Variables](#credentials-storage--environment-variables)
- [API Reference](#api-reference)
  - [Chat Completions](#post-v1chatcompletions)
  - [Status & Account Pool](#get-status)
  - [Account Management API](#get-v1accounts)
- [Quick Start](#quick-start)
  - [1. Local Background Service (Recommended on macOS)](#1-local-background-service-recommended-on-macos)
  - [2. Standalone Docker](#2-standalone-docker)
  - [3. Docker Compose with LiteLLM Sidecar](#3-docker-compose-with-litellm-sidecar)
- [Client Integration Guides](#client-integration-guides)
  - [Python OpenAI SDK](#python-openai-sdk)
  - [TypeScript / Node.js OpenAI SDK](#typescript--nodejs-openai-sdk)
  - [Cursor IDE](#cursor-ide)
  - [Cline / OpenCode / Continue](#cline--opencode--continue)
  - [LibreChat & OpenWebUI](#librechat--openwebui)
- [Testing & Verification](#testing--verification)
- [License](#license)

---

## Architecture & Flow

```
                      +------------------------------------------+
                      | Client (Cursor, OpenAI SDK, LibreChat)   |
                      +------------------------------------------+
                                           |
                                           v
                      +------------------------------------------+
                      | LiteLLM Proxy (:4000) [Optional Sidecar] |
                      | - Unified model routing & load balancing |
                      +------------------------------------------+
                                           |
                                           v
+------------------------------------------------------------------------------------+
|                         Antigravity Proxy (:8000)                                  |
|                                                                                    |
|  +------------------------------------------------------------------------------+  |
|  | Ingress Router: /v1/chat/completions, /v1/models, /status, /login            |  |
|  +------------------------------------------------------------------------------+  |
|         |                                                      |                   |
|         v                                                      v                   |
|  +-----------------------------+               +--------------------------------+  |
|  | AuthManager                 |               | Translator (Ingress)           |  |
|  | - Single-flight refresh     |               | - Schema stripping ($schema)   |  |
|  | - asyncio.Lock()            |               | - Claude 64k token clamp       |  |
|  | - 60s skew check            |               | - Mode: VALIDATED              |  |
|  | - Auto-discovery of Project |               | - thoughtSignature preservation|  |
|  +-----------------------------+               +--------------------------------+  |
|                                                                |                   |
|                                                                v                   |
|                                                +--------------------------------+  |
|                                                | Upstream Client (HTTPX)        |  |
|                                                | - Primary / Sandbox Failover   |  |
|                                                | - Empty 200 Stream Retries     |  |
|                                                | - Exponential Backoff          |  |
|                                                +--------------------------------+  |
|                                                                |                   |
|                                                                v                   |
|                                                +--------------------------------+  |
|                                                | Egress Stream Sanitizer        |  |
|                                                | - Flash Planning Leak Filter   |  |
|                                                | - Reasoning vs Text Splitter   |  |
|                                                | - SSE Format & Finish Reasons  |  |
|                                                +--------------------------------+  |
+------------------------------------------------------------------------------------+
                                           |
                                           v
             +-----------------------------------------------------------+
             | Google Cloud Code Assist API (daily-cloudcode-pa)         |
             +-----------------------------------------------------------+
```

---

## Key Capabilities

- **Drop-in OpenAI API**: Compatible with official OpenAI SDKs (v1.0+), LangChain, LiteLLM, Cursor, LibreChat, OpenCode, and Cline.
- **Bi-directional Streaming (SSE)**: Streams incremental content deltas, reasoning content deltas, and tool calls in real time.
- **Non-Streaming Aggregation**: In-memory response reconstruction for traditional synchronous requests.
- **Automated OAuth Refresh**: Background token refreshes using `asyncio.Lock` to avoid thundering herds when tokens expire during concurrent bursts.
- **Failover & Resilience**: Transparent fallback from `daily-cloudcode-pa.googleapis.com` to `daily-cloudcode-pa.sandbox.googleapis.com`, with automatic retry on empty 200 streams.
- **Gemini Planning Leak Stripper**: Detects and strips internal JSON planning leaks (`{"thought": ...}`) emitted by Gemini Flash models while preserving legitimate user-requested JSON outputs.
- **Cryptographic State Retention**: Preserves upstream `thoughtSignature` attributes in multi-turn tool calling conversations.

---

## Supported Models & Routing

The proxy normalizes incoming model names (stripping prefixes like `openai/`, `google/`, or `anthropic/`) and translates them to Google Cloud Code Assist internal wire IDs:

| Client Model Name | Wire Model ID | Max Output Tokens | Special Headers / Labels |
|---|---|---|---|
| `claude-3.7-sonnet` / `claude-3-7-sonnet` | `claude-sonnet-4-6` | 64,000 | `anthropic-beta: interleaved-thinking-2025-05-14`<br>`used_claude: true`<br>`toolConfig mode: VALIDATED` |
| `claude-sonnet-4-6` / `claude-sonnet-4.6` | `claude-sonnet-4-6` | 64,000 | Same as above |
| `claude-opus-4-6` / `claude-opus-4.6` | `claude-opus-4-6-thinking` | 64,000 | Same as above (reasoning / thinking enabled) |
| `gemini-3.8-flash` / `gemini-3-flash` | `gemini-3-flash-agent` | 65,536 | `model_enum: MODEL_PLACEHOLDER_M132` |
| `gemini-3.1-pro` / `gemini-3-pro` | `gemini-pro-agent` | 65,535 | `model_enum: MODEL_PLACEHOLDER_M16` |
| `gemini-2.5-pro` | `gemini-2.5-pro` | 65,536 | Standard Gemini profile |
| `gemini-2.5-flash` | `gemini-2.5-flash` | 65,536 | Standard Gemini profile |
| `gemini-2.0-flash` | `gemini-2.0-flash` | 65,536 | Standard Gemini profile |
| `gemini-2.0-flash-lite` | `gemini-2.0-flash-lite` | 65,536 | Standard Gemini profile |
| `gemini-2.0-pro-exp` | `gemini-2.0-pro-exp` | 65,536 | Standard Gemini profile |
| `gemini-1.5-pro` | `gemini-1.5-pro` | 65,536 | Standard Gemini profile |
| `gemini-1.5-flash` | `gemini-1.5-flash` | 65,536 | Standard Gemini profile |

---

## Core Translation Invariants

### Claude Model Handling
1. **Output Token Capping**: Claude models reject generation configs exceeding 64,000 tokens. The proxy automatically clamps `max_tokens` and `max_completion_tokens` to `64000`.
2. **Function Calling Validation**: Claude models enforce strict tool parameter checking via `request.toolConfig.functionCallingConfig.mode = "VALIDATED"`.
3. **Beta Headers**: Claude requests inject the header `anthropic-beta: interleaved-thinking-2025-05-14`.

### Gemini Model Enums
Gemini agent models require internal metadata flags passed under labels:
- `gemini-3.8-flash` $\to$ `labels.model_enum = "MODEL_PLACEHOLDER_M132"`
- `gemini-3-pro` $\to$ `labels.model_enum = "MODEL_PLACEHOLDER_M16"`

### Cryptographic Thought Signatures
In multi-turn tool calling, Google's backend generates a cryptographic `thoughtSignature` string attached to `functionCall` items. If a client executes a tool and returns the result, the prior assistant message **must** echo back the original `thoughtSignature` in the tool call history. The proxy automatically extracts `thought_signature` / `thoughtSignature` from OpenAI history and embeds it in the upstream envelope.

### Planning Leak Sanitizer
Gemini Flash agent models often leak raw JSON planning artifacts at the start of streams:
```json
{"thought": "Searching for files...", "command": "find"}
```
The proxy's `PlanningLeakFilter` buffers initial tokens starting with `{`, detects whether the structured content matches internal planning schemas, and silently drops the planning leak while preserving valid JSON outputs requested by users.

### Reliability & Empty-Stream Retries
Google's daily internal endpoints occasionally return `HTTP 200` with immediate `finishReason: "STOP"` and 0 tokens. The proxy traps this condition and automatically retries up to 3 times with exponential backoff (1s, 2s, 4s) before attempting failover to the sandbox endpoint or surfacing an error.

---

## Authentication & Token Lifecycle

### Single-Flight Token Refresh
When an OAuth token approaches expiration (`now + 60s >= expires_at`):
1. An `asyncio.Lock` ensures that only **one** coroutine initiates an HTTP request to `https://oauth2.googleapis.com/token`.
2. All other concurrent requests wait on the lock.
3. Once the lock is released, waiting coroutines see the updated in-memory token and proceed immediately without triggering redundant OAuth refresh calls.

### Web OAuth Login Flow
If you don't have credentials pre-configured, launch the proxy and open:
```
http://localhost:8000/login
```
Click **"Sign in with Google"** and complete OAuth consent. The proxy exchanges the authorization code, calls `v1internal:loadCodeAssist` to discover your active `cloudaicompanionProject` ID, and persists credentials to `credentials.json`.
### Zero-OAuth CLI Import (macOS)
If you are already logged into the **Antigravity CLI** on your Mac, you can skip web OAuth consent entirely. Antigravity stores OAuth session tokens in the macOS Keychain (`service: gemini`, `account: antigravity`).

Run the automated import helper:
```bash
.venv/bin/python scripts/import_cli_credentials.py
```
This extracts your active access and refresh tokens, extracts your Google email, and writes or appends the account to `credentials.json`.

### Native Multi-Account Pool & Quota Failover
You can pool multiple Google accounts with Cloud Code Assist subscriptions:
- **Round-Robin Balancing**: Requests are distributed across all active pool accounts.
- **Independent Token Refresh Locks**: Per-account `asyncio.Lock` ensures a token refresh on Account 1 never blocks requests on Account 2.
- **Automatic 10-Minute Cooldown**: If an account hits `HTTP 429` or `403 RESOURCE_EXHAUSTED`, it is placed in a 10-minute cooldown window.
- **In-Flight Transparent Failover**: Non-streaming and streaming requests failover to the next healthy account in the pool before returning errors to the client.
- **Adding Accounts**: Visit `http://localhost:8000/login` in your browser and click **"Add Another Google Account"**.

### Credentials Storage & Environment Variables
Credentials can be loaded from file or environment variables:

1. **File**: Specified by `CREDENTIALS_PATH` (defaults to `credentials.json` or `/data/credentials.json` in Docker). Supports both multi-account arrays and single-account objects:
```json
[
  {
    "email": "dev1@gmail.com",
    "access_token": "ya29.a0...",
    "refresh_token": "1//0...",
    "project_id": "cloudaicompanion-project-1",
    "expires_at": 1740000000000
  },
  {
    "email": "dev2@gmail.com",
    "access_token": "ya29.a0...",
    "refresh_token": "1//0...",
    "project_id": "cloudaicompanion-project-2",
    "expires_at": 1740000000000
  }
]
```

2. **Environment Variables**:
```bash
export ANTIGRAVITY_REFRESH_TOKEN="1//0..."
export ANTIGRAVITY_PROJECT_ID="your-cloudaicompanion-project-id"
export ANTIGRAVITY_ACCESS_TOKEN="ya29..." # Optional; will refresh automatically if omitted
```

---

## API Reference

### `GET /v1/models`
Returns an OpenAI-compliant list of supported models.

**Response**:
```json
{
  "object": "list",
  "data": [
    {
      "id": "claude-3.7-sonnet",
      "object": "model",
      "created": 1727650000,
      "owned_by": "google-antigravity"
    },
    ...
  ]
}
```

### `POST /v1/chat/completions`
Translates an OpenAI chat completion request to Antigravity CCA format. Supports both streaming (`"stream": true`) and non-streaming (`"stream": false`).

**Supported Parameters**:
- `model` (string, required)
- `messages` (list of message objects, required)
- `stream` (boolean, optional)
- `tools` (list of function definitions, optional)
- `temperature` (float, optional)
- `top_p` (float, optional)
- `max_tokens` or `max_completion_tokens` (integer, optional)

### `GET /status`
Health check and multi-account pool status report.

**Response**:
```json
{
  "status": "ok",
  "service": "antigravity-proxy",
  "authenticated": true,
  "total_accounts": 2,
  "active_accounts": 2,
  "email": "dev1@gmail.com",
  "project_id": "cloudaicompanion-project-1",
  "accounts": [
    {
      "email": "dev1@gmail.com",
      "project_id": "cloudaicompanion-project-1",
      "is_cooling_down": false,
      "cooldown_remaining_seconds": 0,
      "is_expired": false
    },
    {
      "email": "dev2@gmail.com",
      "project_id": "cloudaicompanion-project-2",
      "is_cooling_down": false,
      "cooldown_remaining_seconds": 0,
      "is_expired": false
    }
  ]
}
```

### `GET /v1/accounts`
List all configured accounts in the pool with cooldown and expiry states.

### `DELETE /v1/accounts/{identifier}`
Remove an account from the pool by email or project ID.

---

## Quick Start

### 1. Local Background Service (Recommended on macOS)

Running locally consumes **~100 MB RAM** (vs 3+ GB for Docker Desktop VM) and has sub-millisecond loopback latency. The included `./service.sh` runs services with `nohup` so they **keep running even if you close your terminal tab**.

```bash
# Start Antigravity Proxy (:8000) and LiteLLM (:4000) in the background
./service.sh start

# Inspect service status, PIDs, and active accounts
./service.sh status

# View live logs
./service.sh logs proxy
./service.sh logs litellm

# Stop background services
./service.sh stop
```

### 2. Standalone Docker

```bash
# Build the container
docker build -t antigravity-proxy .

# Run with mounted credentials
docker run -d \
  --name antigravity-proxy \
  -p 8000:8000 \
  -v $(pwd)/data:/data \
  -e CREDENTIALS_PATH=/data/credentials.json \
  antigravity-proxy
```

### 3. Docker Compose with LiteLLM Sidecar

Run Antigravity Proxy alongside LiteLLM for unified multi-model routing and proxying:

```yaml
# docker-compose.yml
services:
  antigravity-proxy:
    build: .
    container_name: antigravity-proxy
    restart: unless-stopped
    ports:
      - "8000:8000"
    volumes:
      - ./data:/data
    environment:
      - CREDENTIALS_PATH=/data/credentials.json
      - PORT=8000

  litellm:
    image: ghcr.io/berriai/litellm:main-latest
    container_name: litellm-load-balancer
    restart: unless-stopped
    ports:
      - "4000:4000"
    volumes:
      - ./litellm-config.yaml:/app/config.yaml
    command: ["--config", "/app/config.yaml", "--port", "4000"]
    depends_on:
      - antigravity-proxy
```

Start the stack:
```bash
docker compose up -d
```

---

## Client Integration Guides

### Python OpenAI SDK

```python
import os
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:8000/v1",
    api_key="dummy"
)

# Streaming with reasoning content
stream = client.chat.completions.create(
    model="claude-3.7-sonnet",
    messages=[{"role": "user", "content": "Explain quantum computing briefly."}],
    stream=True
)

for chunk in stream:
    delta = chunk.choices[0].delta
    if hasattr(delta, "reasoning_content") and delta.reasoning_content:
        print(delta.reasoning_content, end="", flush=True)
    if delta.content:
        print(delta.content, end="", flush=True)
```

### TypeScript / Node.js OpenAI SDK

```typescript
import OpenAI from "openai";

const client = new OpenAI({
  baseURL: "http://localhost:8000/v1",
  apiKey: "dummy",
});

async function main() {
  const completion = await client.chat.completions.create({
    model: "gemini-3.8-flash",
    messages: [{ role: "user", content: "Write a hello-world in Rust" }],
    stream: true,
  });

  for await (const chunk of completion) {
    process.stdout.write(chunk.choices[0]?.delta?.content || "");
  }
}

main();
```

### Cursor IDE

In Cursor Settings $\to$ **Models** $\to$ **OpenAI API**:
1. Check **Override OpenAI Base URL**.
2. Set URL to: `http://localhost:8000/v1`
3. Enter API Key: `dummy`
4. Add models: `claude-3.7-sonnet`, `gemini-3.8-flash`, `gemini-3-pro`

### Cline / OpenCode / Continue

In your extension configuration:
```json
{
  "api_provider": "openai",
  "api_base": "http://localhost:8000/v1",
  "api_key": "dummy",
  "model": "claude-3.7-sonnet"
}
```

### LibreChat & OpenWebUI

In `librechat.yaml`:
```yaml
endpoints:
  custom:
    - name: "Antigravity"
      apiKey: "dummy"
      baseURL: "http://host.docker.internal:8000/v1"
      models:
        default: ["claude-3.7-sonnet", "gemini-3.8-flash", "gemini-3-pro"]
        fetch: true
```

---

## Testing & Verification

Run the full automated test suite:
```bash
pytest tests/ -v
```

Run the live integration smoke test against an active server:
```bash
# In terminal 1:
python -m uvicorn app.main:app --port 8000

# In terminal 2:
python tests/smoke_test.py
```

---

## License

MIT License. See [LICENSE](LICENSE) for details.
