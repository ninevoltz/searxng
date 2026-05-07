# SPDX-License-Identifier: AGPL-3.0-or-later
"""MCP protocol handling for SearXNG's HTTP app."""

from __future__ import annotations

import json
from typing import Any

import searx.search
from searx.exceptions import SearxParameterException
from searx import webutils
from searx.webadapter import get_search_query_from_webapp


JSONRPC_VERSION = "2.0"
DEFAULT_PROTOCOL_VERSION = "2025-03-26"
SERVER_NAME = "searxng-mcp"
SERVER_VERSION = "0.1.0"


class JsonRpcError(Exception):
    """Error that can be returned as a JSON-RPC error object."""

    def __init__(self, code: int, message: str, data: Any | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data


def json_response(request_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": JSONRPC_VERSION, "id": request_id, "result": result}


def json_error(request_id: Any, exc: JsonRpcError) -> dict[str, Any]:
    error: dict[str, Any] = {"code": exc.code, "message": exc.message}
    if exc.data is not None:
        error["data"] = exc.data
    return {"jsonrpc": JSONRPC_VERSION, "id": request_id, "error": error}


def dumps_message(message: dict[str, Any] | list[Any]) -> str:
    return json.dumps(message, separators=(",", ":"))


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


def _search_form(arguments: dict[str, Any]) -> dict[str, str]:
    query = arguments.get("query")
    if not isinstance(query, str) or not query.strip():
        raise JsonRpcError(-32602, "query is required")

    form = {"q": query.strip()}
    site = arguments.get("site")
    if isinstance(site, str) and site.strip():
        form["q"] = f"site:{site.strip()} {form['q']}"

    optional_strings = ("categories", "engines", "language", "time_range")
    for key in optional_strings:
        value = arguments.get(key)
        if isinstance(value, str) and value.strip():
            form[key] = value.strip()

    pageno = arguments.get("pageno")
    if pageno is not None:
        if not isinstance(pageno, int) or pageno < 1:
            raise JsonRpcError(-32602, "pageno must be a positive integer")
        form["pageno"] = str(pageno)

    safesearch = arguments.get("safesearch")
    if safesearch is not None:
        if not isinstance(safesearch, int) or safesearch < 0 or safesearch > 2:
            raise JsonRpcError(-32602, "safesearch must be an integer from 0 to 2")
        form["safesearch"] = str(safesearch)

    return form


def _format_search_text(data: dict[str, Any]) -> str:
    lines: list[str] = []
    answers = data.get("answers")
    if isinstance(answers, list) and answers:
        lines.append("Answers:")
        for answer in answers:
            if isinstance(answer, str):
                lines.append(f"- {answer}")
            elif isinstance(answer, dict):
                lines.append(
                    f"- {answer.get('answer') or answer.get('content') or answer}"
                )
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
            engine = result.get("engine")
            engines = result.get("engines")
            if isinstance(engines, list):
                engine_text = f" [{', '.join(engines)}]"
            elif isinstance(engine, str) and engine:
                engine_text = f" [{engine}]"
            else:
                engine_text = ""
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
        lines.extend(
            f"- {suggestion}"
            for suggestion in suggestions
            if isinstance(suggestion, str)
        )

    return "\n".join(lines)


def _fetch_search_results(arguments: dict[str, Any], request: Any) -> dict[str, Any]:
    count = arguments.get("count", 10)
    if not isinstance(count, int) or count < 1 or count > 50:
        raise JsonRpcError(-32602, "count must be an integer from 1 to 50")

    form = _search_form(arguments)
    try:
        search_query, _, _, _, _ = get_search_query_from_webapp(
            request.preferences, form
        )
        search_obj = searx.search.SearchWithPlugins(
            search_query, request, request.user_plugins
        )
        result_container = search_obj.search()
    except SearxParameterException as exc:
        raise JsonRpcError(-32602, exc.message) from exc
    except Exception as exc:
        raise JsonRpcError(-32000, "SearXNG search failed", str(exc)) from exc

    if result_container.redirect_url:
        return {
            "query": search_query.query,
            "number_of_results": 0,
            "results": [],
            "answers": [],
            "corrections": [],
            "infoboxes": [],
            "suggestions": [],
            "unresponsive_engines": [],
            "redirect_url": result_container.redirect_url,
        }

    data = json.loads(webutils.get_json_response(search_query, result_container))
    results = data.get("results", [])
    if isinstance(results, list):
        data["results"] = results[:count]
    return data


def _handle_initialize(
    request_id: Any, params: dict[str, Any] | None
) -> dict[str, Any]:
    protocol_version = DEFAULT_PROTOCOL_VERSION
    if isinstance(params, dict) and isinstance(params.get("protocolVersion"), str):
        protocol_version = params["protocolVersion"]
    return json_response(
        request_id,
        {
            "protocolVersion": protocol_version,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        },
    )


def _handle_tools_list(request_id: Any) -> dict[str, Any]:
    return json_response(
        request_id,
        {
            "tools": [
                {
                    "name": "searxng_search",
                    "description": "Search this SearXNG instance and return concise web results.",
                    "inputSchema": _tool_schema(),
                }
            ]
        },
    )


def _handle_tools_call(
    request_id: Any, params: dict[str, Any] | None, request: Any
) -> dict[str, Any]:
    if not isinstance(params, dict):
        raise JsonRpcError(-32602, "params must be an object")
    if params.get("name") != "searxng_search":
        raise JsonRpcError(-32602, f"Unknown tool: {params.get('name')}")
    arguments = params.get("arguments") or {}
    if not isinstance(arguments, dict):
        raise JsonRpcError(-32602, "arguments must be an object")

    data = _fetch_search_results(arguments, request)
    return json_response(
        request_id,
        {
            "content": [{"type": "text", "text": _format_search_text(data)}],
            "structuredContent": data,
        },
    )


def _handle_request(message: dict[str, Any], request: Any) -> dict[str, Any] | None:
    request_id = message.get("id")
    method = message.get("method")
    params = message.get("params")

    if not isinstance(method, str):
        raise JsonRpcError(-32600, "Invalid Request")

    is_notification = "id" not in message
    if method in {"notifications/initialized", "notifications/cancelled"}:
        return None

    if method == "initialize":
        return _handle_initialize(
            request_id, params if isinstance(params, dict) else None
        )
    if method == "ping":
        return json_response(request_id, {})
    if method == "tools/list":
        return _handle_tools_list(request_id)
    if method == "tools/call":
        return _handle_tools_call(
            request_id, params if isinstance(params, dict) else None, request
        )

    if is_notification:
        return None
    raise JsonRpcError(-32601, f"Method not found: {method}")


def handle_message(
    payload: dict[str, Any] | list[Any], request: Any
) -> dict[str, Any] | list[Any] | None:
    if isinstance(payload, list):
        responses = []
        for item in payload:
            if not isinstance(item, dict):
                responses.append(
                    json_error(None, JsonRpcError(-32600, "Invalid Request"))
                )
                continue
            try:
                response = _handle_request(item, request)
                if response is not None:
                    responses.append(response)
            except JsonRpcError as exc:
                responses.append(json_error(item.get("id"), exc))
        return responses or None

    if not isinstance(payload, dict):
        return json_error(None, JsonRpcError(-32600, "Invalid Request"))

    try:
        return _handle_request(payload, request)
    except JsonRpcError as exc:
        return json_error(payload.get("id"), exc)
