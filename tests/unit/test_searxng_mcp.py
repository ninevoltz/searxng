# SPDX-License-Identifier: AGPL-3.0-or-later

from __future__ import annotations

import importlib.util
import json
import unittest
import urllib.parse
from pathlib import Path
from unittest import TestCase
from unittest.mock import Mock, patch


MODULE_PATH = Path(__file__).resolve().parents[2] / "mcp" / "searxng_mcp.py"
SPEC = importlib.util.spec_from_file_location("searxng_mcp", MODULE_PATH)
assert SPEC is not None
searxng_mcp = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(searxng_mcp)


class TestSearxngMcp(TestCase):
    def test_initialize_echoes_protocol_version(self):
        response = searxng_mcp._handle_message(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2025-03-26"},
            }
        )

        self.assertEqual(response["id"], 1)
        self.assertEqual(response["result"]["protocolVersion"], "2025-03-26")
        self.assertEqual(response["result"]["serverInfo"]["name"], "searxng-mcp")
        self.assertIn("tools", response["result"]["capabilities"])

    def test_tools_list_returns_search_tool(self):
        response = searxng_mcp._handle_message(
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}
        )

        tools = response["result"]["tools"]
        self.assertEqual(tools[0]["name"], "searxng_search")
        self.assertEqual(tools[0]["inputSchema"]["required"], ["query"])

    def test_search_url_includes_supported_parameters(self):
        url = searxng_mcp._search_url(
            "http://localhost:8888/",
            {
                "query": "python mcp",
                "count": 5,
                "categories": "it,general",
                "engines": "duckduckgo,wikipedia",
                "language": "en-US",
                "time_range": "week",
                "pageno": 2,
                "safesearch": 0,
                "site": "docs.example",
            },
        )

        self.assertTrue(url.startswith("http://localhost:8888/search?"))
        params = {
            key: values[0]
            for key, values in urllib.parse.parse_qs(url.split("?", 1)[1]).items()
        }
        self.assertEqual(params["format"], "json")
        self.assertEqual(params["q"], "site:docs.example python mcp")
        self.assertEqual(params["categories"], "it,general")
        self.assertEqual(params["engines"], "duckduckgo,wikipedia")
        self.assertEqual(params["language"], "en-US")
        self.assertEqual(params["time_range"], "week")
        self.assertEqual(params["pageno"], "2")
        self.assertEqual(params["safesearch"], "0")

    @patch.object(searxng_mcp.urllib.request, "urlopen")
    def test_tools_call_returns_text_and_structured_content(self, urlopen_mock):
        response_mock = Mock()
        response_mock.headers.get_content_charset.return_value = "utf-8"
        response_mock.read.return_value = json.dumps(
            {
                "query": "searxng",
                "results": [
                    {
                        "title": "First",
                        "url": "https://example.test/1",
                        "content": "First result",
                        "engines": ["test"],
                    },
                    {
                        "title": "Second",
                        "url": "https://example.test/2",
                        "content": "Second result",
                    },
                ],
                "suggestions": ["searxng docs"],
            }
        ).encode()
        urlopen_mock.return_value.__enter__.return_value = response_mock

        response = searxng_mcp._handle_message(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "searxng_search",
                    "arguments": {"query": "searxng", "count": 1},
                },
            }
        )

        self.assertEqual(response["id"], 3)
        self.assertEqual(len(response["result"]["structuredContent"]["results"]), 1)
        text = response["result"]["content"][0]["text"]
        self.assertIn("First", text)
        self.assertNotIn("Second", text)

    def test_missing_query_returns_jsonrpc_error(self):
        response = searxng_mcp._handle_message(
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {"name": "searxng_search", "arguments": {}},
            }
        )

        self.assertEqual(response["error"]["code"], -32602)
        self.assertIn("query is required", response["error"]["message"])


if __name__ == "__main__":
    unittest.main()
