# SearXNG MCP Server

This directory contains a small stdio MCP server that lets Codex call a local or
remote SearXNG instance as a search tool. It is model independent, so it works
the same way when Codex is configured to use local Ollama models.

## Requirements

- Python 3.10 or newer
- A running SearXNG instance with JSON output enabled

The server uses only the Python standard library. It calls:

```text
GET /search?q=<query>&format=json
```

## SearXNG Setup

In the SearXNG settings file used by your instance, make sure JSON is enabled:

```yaml
search:
  formats:
    - html
    - json
```

Start SearXNG however you normally run it. For local development, the default
MCP target is:

```text
http://127.0.0.1:8080
```

Override it with `SEARXNG_URL` when your instance uses another URL.

## Codex Configuration

Add an MCP server entry to your Codex config. Adjust the path to match your
checkout.

```toml
[mcp_servers.searxng]
command = "python3"
args = ["/home/john/git-projects/searxng/mcp/searxng_mcp.py"]
env = { SEARXNG_URL = "http://127.0.0.1:8080" }
```

When Codex is using local Ollama models, keep your Ollama model configuration
unchanged and add the MCP server entry alongside it.

## HTTP Endpoint

SearXNG also exposes the MCP server through the web app's Streamable HTTP
transport:

```text
http://127.0.0.1:8080/mcp
```

Point agents that support remote MCP servers at the `/mcp` URL for the SearXNG
instance. The endpoint accepts JSON-RPC MCP requests over HTTP `POST` and uses
the same in-process search path as the regular `/search` route.

For non-local access, put SearXNG behind HTTPS and authentication. The endpoint
rejects browser requests with a cross-host `Origin` header, but it does not add
new user authentication on its own.

Browser-based local clients, such as MCP inspectors running on another localhost
port, are supported with CORS preflight responses when both the client origin
and SearXNG host are loopback addresses.

## Tool

The server exposes one tool:

- `searxng_search`: queries SearXNG and returns concise text plus the raw JSON
  response as structured content.

Supported arguments:

- `query` required search query
- `count` result limit from 1 to 50, default 10
- `categories` comma-separated SearXNG categories
- `engines` comma-separated SearXNG engine names
- `language` SearXNG language code
- `time_range` one of `day`, `week`, `month`, `year`
- `pageno` result page number
- `safesearch` SearXNG safesearch level from 0 to 2
- `site` optional domain restriction, added as `site:<domain>`

## Optional Environment

- `SEARXNG_URL`: SearXNG base URL, default `http://127.0.0.1:8080`
- `SEARXNG_MCP_TIMEOUT`: request timeout in seconds, default `15`
- `SEARXNG_MCP_HTTP_HEADERS`: JSON object of extra HTTP headers, useful for a
  reverse proxy auth header

Example:

```toml
env = {
  SEARXNG_URL = "https://search.example.test",
  SEARXNG_MCP_TIMEOUT = "20",
  SEARXNG_MCP_HTTP_HEADERS = "{\"Authorization\":\"Bearer token\"}"
}
```

## Smoke Test

With SearXNG running:

```bash
printf '%s\n' \
  '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"smoke","version":"0"}}}' \
  '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}' \
  '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"searxng_search","arguments":{"query":"searxng","count":3}}}' \
  | python3 mcp/searxng_mcp.py
```
