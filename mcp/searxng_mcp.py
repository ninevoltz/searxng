#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Minimal stdio MCP server for querying a SearXNG instance."""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


JSONRPC_VERSION = "2.0"
DEFAULT_PROTOCOL_VERSION = "2025-03-26"
DEFAULT_SEARXNG_URL = "http://127.0.0.1:8080"
SERVER_NAME = "searxng-mcp"
SERVER_VERSION = "0.1.0"


class JsonRpcError(Exception):
    """Error that can be returned as a JSON-RPC error object."""

    def __init__(self, code: int, message: str, data: Any | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data


def _env_float(name: str, default: float) -> float:
    value = os.environ.get(name)
    if not value:
        return default
    try:
        return float(value)
    except ValueError as exc:
        raise JsonRpcError(-32603, f"{name} must be a number") from exc


def _json_response(request_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": JSONRPC_VERSION, "id": request_id, "result": result}


def _json_error(request_id: Any, exc: JsonRpcError) -> dict[str, Any]:
    error: dict[str, Any] = {"code": exc.code, "message": exc.message}
    if exc.data is not None:
        error["data"] = exc.data
    return {"jsonrpc": JSONRPC_VERSION, "id": request_id, "error": error}


def _load_json_object(line: str) -> dict[str, Any] | list[Any]:
    try:
        payload = json.loads(line)
    except json.JSONDecodeError as exc:
        raise JsonRpcError(-32700, "Parse error", str(exc)) from exc
    if not isinstance(payload, (dict, list)):
        raise JsonRpcError(-32600, "Invalid Request")
    return payload


def _headers() -> dict[str, str]:
    headers = {
        "Accept": "application/json",
        "User-Agent": f"{SERVER_NAME}/{SERVER_VERSION}",
    }
    raw_headers = os.environ.get("SEARXNG_MCP_HTTP_HEADERS")
    if raw_headers:
        try:
            parsed = json.loads(raw_headers)
        except json.JSONDecodeError as exc:
            raise JsonRpcError(-32603, "SEARXNG_MCP_HTTP_HEADERS must be a JSON object") from exc
        if not isinstance(parsed, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in parsed.items()):
            raise JsonRpcError(-32603, "SEARXNG_MCP_HTTP_HEADERS must map strings to strings")
        headers.update(parsed)
    return headers


def _search_url(base_url: str, arguments: dict[str, Any]) -> str:
    query = arguments.get("query")
    if not isinstance(query, str) or not query.strip():
        raise JsonRpcError(-32602, "query is required")

    params: dict[str, str] = {"q": query.strip(), "format": "json"}
    optional_strings = ("categories", "engines", "language", "time_range")
    for key in optional_strings:
        value = arguments.get(key)
        if isinstance(value, str) and value.strip():
            params[key] = value.strip()

    pageno = arguments.get("pageno")
    if pageno is not None:
        if not isinstance(pageno, int) or pageno < 1:
            raise JsonRpcError(-32602, "pageno must be a positive integer")
        params["pageno"] = str(pageno)

    safesearch = arguments.get("safesearch")
    if safesearch is not None:
        if not isinstance(safesearch, int) or safesearch < 0 or safesearch > 2:
            raise JsonRpcError(-32602, "safesearch must be an integer from 0 to 2")
        params["safesearch"] = str(safesearch)

    site = arguments.get("site")
    if isinstance(site, str) and site.strip():
        params["q"] = f"site:{site.strip()} {params['q']}"

    endpoint = urllib.parse.urljoin(base_url.rstrip("/") + "/", "search")
    return f"{endpoint}?{urllib.parse.urlencode(params)}"


def _fetch_search_results(arguments: dict[str, Any]) -> dict[str, Any]:
    base_url = os.environ.get("SEARXNG_URL", DEFAULT_SEARXNG_URL)
    timeout = _env_float("SEARXNG_MCP_TIMEOUT", 15.0)
    url = _search_url(base_url, arguments)
    request = urllib.request.Request(url, headers=_headers(), method="GET")

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - user-configured endpoint
            charset = response.headers.get_content_charset("utf-8")
            data = json.loads(response.read().decode(charset))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        raise JsonRpcError(-32000, f"SearXNG returned HTTP {exc.code}", body[:1000]) from exc
    except urllib.error.URLError as exc:
        raise JsonRpcError(-32000, f"Could not reach SearXNG at {base_url}", str(exc.reason)) from exc
    except TimeoutError as exc:
        raise JsonRpcError(-32000, f"SearXNG request timed out after {timeout:g}s") from exc
    except json.JSONDecodeError as exc:
        raise JsonRpcError(-32000, "SearXNG did not return valid JSON", str(exc)) from exc

    if not isinstance(data, dict):
        raise JsonRpcError(-32000, "SearXNG returned an unexpected JSON payload")

    count = arguments.get("count", 10)
    if not isinstance(count, int) or count < 1 or count > 50:
        raise JsonRpcError(-32602, "count must be an integer from 1 to 50")

    results = data.get("results", [])
    if isinstance(results, list):
        data["results"] = results[:count]
    data["query_url"] = url
    return data


def _format_search_text(data: dict[str, Any]) -> str:
    lines: list[str] = []
    answers = data.get("answers")
    if isinstance(answers, list) and answers:
        lines.append("Answers:")
        lines.extend(f"- {answer}" for answer in answers if isinstance(answer, str))
        lines.append("")

    results = data.get("results")
    if isinstance(results, list) and results:
        lines.append("Results:")
        for index, result in enumerate(results, start=1):
            if not isinstance(result, dict):
                continue
            title = str(result.get("title") or "Untitled")
            url = str(result.get("url") or "")
            content = str(result.get("content") or "").strip()
            engines = result.get("engines")
            engine_text = f" [{', '.join(engines)}]" if isinstance(engines, list) else ""
            lines.append(f"{index}. {title}{engine_text}")
            if url:
                lines.append(f"   {url}")
            if content:
                lines.append(f"   {content}")
    else:
        lines.append("No results returned.")

    suggestions = data.get("suggestions")
    if isinstance(suggestions, list) and suggestions:
        lines.append("")
        lines.append("Suggestions:")
        lines.extend(f"- {suggestion}" for suggestion in suggestions if isinstance(suggestion, str))

    return "\n".join(lines)


def _tool_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query."},
            "count": {
                "type": "integer",
                "description": "Maximum result count to return.",
                "minimum": 1,
                "maximum": 50,
                "default": 10,
            },
            "categories": {
                "type": "string",
                "description": "Optional comma-separated SearXNG categories, for example general, news, it.",
            },
            "engines": {
                "type": "string",
                "description": "Optional comma-separated SearXNG engine names.",
            },
            "language": {
                "type": "string",
                "description": "Optional SearXNG language code, for example en, en-US, or all.",
            },
            "time_range": {
                "type": "string",
                "description": "Optional SearXNG time range: day, week, month, or year.",
                "enum": ["day", "week", "month", "year"],
            },
            "pageno": {
                "type": "integer",
                "description": "SearXNG result page number.",
                "minimum": 1,
                "default": 1,
            },
            "safesearch": {
                "type": "integer",
                "description": "SearXNG safesearch level.",
                "minimum": 0,
                "maximum": 2,
            },
            "site": {
                "type": "string",
                "description": "Optional domain to restrict the query with site:.",
            },
        },
        "required": ["query"],
        "additionalProperties": False,
    }


def _handle_initialize(request_id: Any, params: dict[str, Any] | None) -> dict[str, Any]:
    protocol_version = DEFAULT_PROTOCOL_VERSION
    if isinstance(params, dict) and isinstance(params.get("protocolVersion"), str):
        protocol_version = params["protocolVersion"]
    return _json_response(
        request_id,
        {
            "protocolVersion": protocol_version,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        },
    )


def _handle_tools_list(request_id: Any) -> dict[str, Any]:
    return _json_response(
        request_id,
        {
            "tools": [
                {
                    "name": "searxng_search",
                    "description": "Search the configured SearXNG instance and return concise web results.",
                    "inputSchema": _tool_schema(),
                }
            ]
        },
    )


def _handle_tools_call(request_id: Any, params: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(params, dict):
        raise JsonRpcError(-32602, "params must be an object")
    if params.get("name") != "searxng_search":
        raise JsonRpcError(-32602, f"Unknown tool: {params.get('name')}")
    arguments = params.get("arguments") or {}
    if not isinstance(arguments, dict):
        raise JsonRpcError(-32602, "arguments must be an object")

    data = _fetch_search_results(arguments)
    return _json_response(
        request_id,
        {
            "content": [{"type": "text", "text": _format_search_text(data)}],
            "structuredContent": data,
        },
    )


def _handle_request(message: dict[str, Any]) -> dict[str, Any] | None:
    request_id = message.get("id")
    method = message.get("method")
    params = message.get("params")

    if not isinstance(method, str):
        raise JsonRpcError(-32600, "Invalid Request")

    # Notifications do not have an id and must not receive a response.
    is_notification = "id" not in message
    if method in {"notifications/initialized", "notifications/cancelled"}:
        return None

    if method == "initialize":
        return _handle_initialize(request_id, params if isinstance(params, dict) else None)
    if method == "ping":
        return _json_response(request_id, {})
    if method == "tools/list":
        return _handle_tools_list(request_id)
    if method == "tools/call":
        return _handle_tools_call(request_id, params if isinstance(params, dict) else None)

    if is_notification:
        return None
    raise JsonRpcError(-32601, f"Method not found: {method}")


def _handle_message(payload: dict[str, Any] | list[Any]) -> dict[str, Any] | list[Any] | None:
    if isinstance(payload, list):
        responses = []
        for item in payload:
            if not isinstance(item, dict):
                responses.append(_json_error(None, JsonRpcError(-32600, "Invalid Request")))
                continue
            try:
                response = _handle_request(item)
                if response is not None:
                    responses.append(response)
            except JsonRpcError as exc:
                responses.append(_json_error(item.get("id"), exc))
        return responses or None

    try:
        return _handle_request(payload)
    except JsonRpcError as exc:
        return _json_error(payload.get("id"), exc)


def main() -> int:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            payload = _load_json_object(line)
            response = _handle_message(payload)
        except JsonRpcError as exc:
            response = _json_error(None, exc)
        if response is not None:
            print(json.dumps(response, separators=(",", ":")), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
