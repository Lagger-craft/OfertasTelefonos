"""Tests for the weekly refurbished Galaxy S23/S23 FE price collector.

Covers S1 (refurbished classification and exact budget boundaries) and
S2 (UCP/MCP request parsing, product normalization/filtering, failure
isolation, idempotent weekly snapshots, and unchanged history on
rejected/invalid data) from odd/tasks/s23-weekly-price-tracking.md.

All source interactions are mocked; no network access happens here.
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import types
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from unittest import mock

SITE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SITE_ROOT / "scripts"))

import update_prices as up  # noqa: E402

PROFILE = "https://agents.example.com/profiles/s23-collector.json"
FIXED_NOW = datetime(2026, 2, 9, 12, 0, tzinfo=timezone.utc)  # Monday of ISO week 2026-W07
WEEK = "2026-W07"

REUSE_ROOT = "https://reusecl.myshopify.com"
REUSE_ENDPOINT = "https://reusecl.myshopify.com/api/ucp/mcp"
BOGEDA_ROOT = "https://bogedaoportunidades.myshopify.com"
BOGEDA_ENDPOINT = "https://bogedaoportunidades.myshopify.com/api/ucp/mcp"


def initial_history() -> dict:
    return {
        "schema_version": 1,
        "budget": {"currency": "CLP", "within_max": 340000, "near_max": 370000},
        "last_collected_at": None,
        "snapshots": {},
    }


def rpc_ok(payload_id: int, result: dict) -> bytes:
    return json.dumps({"jsonrpc": "2.0", "id": payload_id, "result": result}).encode()


def http_json(obj, status: int = 200, headers: dict | None = None):
    merged = {"content-type": "application/json"}
    merged.update(headers or {})
    return up.HttpResponse(status, merged, json.dumps(obj).encode())


class FakeTransport:
    """Records every request and delegates routing to `router`."""

    def __init__(self, router):
        self.calls: list[dict] = []
        self._router = router

    def __call__(self, method, url, headers=None, body=None):
        self.calls.append(
            {"method": method, "url": url, "headers": dict(headers or {}), "body": body}
        )
        return self._router(method, url, dict(headers or {}), body)

    def urls(self) -> list[str]:
        return [c["url"] for c in self.calls]


def build_router(sources: list[dict]):
    """Route discovery GETs and MCP JSON-RPC POSTs for a list of sources.

    Each source dict: {"root", "endpoint", "catalog", "status", "tools",
    "profile_doc"}. `catalog` may be a dict, a raw string, or a callable
    receiving the tools/call arguments.
    """
    by_root = {s["root"]: s for s in sources}
    by_endpoint = {s["endpoint"]: s for s in sources}

    def router(method, url, headers, body):
        if method == "GET" and url.endswith("/.well-known/ucp"):
            root = url[: -len("/.well-known/ucp")]
            src = by_root.get(root)
            if src is None:
                return http_json({"error": "unknown"}, status=404)
            doc = src.get("profile_doc", {"mcp": {"endpoint": src["endpoint"]}})
            status = src.get("discovery_status", 200)
            if status != 200:
                return up.HttpResponse(status, {"content-type": "application/json"}, b"denied")
            return http_json(doc)

        payload = json.loads(body.decode("utf-8"))
        src = by_endpoint.get(url)
        if src is None:
            return up.HttpResponse(404, {"content-type": "application/json"}, b"{}")
        method_name = payload.get("method")
        if method_name == "initialize":
            return up.HttpResponse(
                200,
                {"content-type": "application/json", "mcp-session-id": "sess-42"},
                rpc_ok(payload["id"], {"protocolVersion": up.MCP_PROTOCOL_VERSION,
                                       "serverInfo": {"name": "fake-merchant", "version": "1"}}),
            )
        if method_name == "notifications/initialized":
            return up.HttpResponse(202, {}, b"")
        if method_name == "tools/list":
            tools = src.get("tools", [{"name": "search_catalog"}])
            return up.HttpResponse(200, {"content-type": "application/json"},
                                   rpc_ok(payload["id"], {"tools": tools}))
        if method_name == "tools/call":
            params = payload.get("params", {})
            if params.get("name") != "search_catalog":
                raise AssertionError(f"read-only contract violated: called {params.get('name')!r}")
            status = src.get("status", 200)
            if status != 200:
                return up.HttpResponse(status, {"content-type": "application/json"}, b"denied")
            catalog = src.get("catalog", {"products": []})
            if callable(catalog):
                catalog = catalog(params.get("arguments"))
            text = catalog if isinstance(catalog, str) else json.dumps(catalog)
            return up.HttpResponse(
                200, {"content-type": "application/json"},
                rpc_ok(payload["id"], {"content": [{"type": "text", "text": text}]}),
            )
        raise AssertionError(f"unexpected MCP method {method_name!r}")

    return router


GOOD_ITEMS = [
    {
        "id": "p1",
        "title": "Samsung Galaxy S23 128GB Reacondicionado",
        "price": "$340.000",
        "url": "https://reusecl.myshopify.com/products/s23-128?utm=week",
        "condition": "Reacondicionado",
    },
    {
        "id": "p2",
        "title": "Samsung Galaxy S23 FE Azul Reacondicionado",
        "price": "355000",
        "url": "https://reusecl.myshopify.com/products/s23-fe",
    },
]
GOOD_BOGEDA_ITEMS = [
    {**item, "url": item["url"].replace("reusecl.myshopify.com",
                                        "bogedaoportunidades.myshopify.com")}
    for item in GOOD_ITEMS
]


class CollectorTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.history_path = Path(self.tmp.name) / "price-history.json"
        env = mock.patch.dict(os.environ, {up.PROFILE_ENV_VAR: PROFILE})
        env.start()
        self.addCleanup(env.stop)

    def use_transport(self, sources: list[dict]) -> FakeTransport:
        fake = FakeTransport(build_router(sources))
        patcher = mock.patch.object(up, "http_request", fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        return fake

    def write_history(self, obj: dict) -> None:
        self.history_path.write_text(json.dumps(obj, indent=2) + "\n", encoding="utf-8")

    def write_raw_history(self, text: str) -> None:
        self.history_path.write_text(text, encoding="utf-8")

    def read_raw(self) -> bytes:
        return self.history_path.read_bytes()

    def read_json(self) -> dict:
        return json.loads(self.read_raw().decode("utf-8"))

    def seed_history(self, weeks: list[str]) -> dict:
        history = initial_history()
        for i, week in enumerate(weeks):
            history["snapshots"][week] = {
                "collected_at": "2026-01-01T00:00:00+00:00",
                "sources": {REUSE_ROOT: "ok"},
                "offers": [{"key": f"seed-{i}", "price": 100000}],
            }
        self.write_history(history)
        return history


# ---------------------------------------------------------------------------
# S1 — refurbished classification and exact budget boundaries
# ---------------------------------------------------------------------------

class BudgetBoundaryTests(unittest.TestCase):
    def test_price_band_exact_boundaries(self):
        self.assertEqual(up.price_band(340000), "within_budget")
        self.assertEqual(up.price_band(340001), "near_budget")
        self.assertEqual(up.price_band(370000), "near_budget")
        self.assertEqual(up.price_band(370001), "excluded")
        self.assertEqual(up.price_band(999999), "excluded")

    def test_price_band_rejects_non_positive(self):
        with self.assertRaises(ValueError):
            up.price_band(0)
        with self.assertRaises(ValueError):
            up.price_band(-5)

    def test_parse_price_chilean_formats(self):
        self.assertEqual(up.parse_price("$340.000"), 340000)
        self.assertEqual(up.parse_price("340001"), 340001)
        self.assertEqual(up.parse_price("CLP 370.000"), 370000)
        self.assertEqual(up.parse_price("  340.000  "), 340000)
        self.assertEqual(up.parse_price(340000), 340000)
        self.assertEqual(up.parse_price(370001.0), 370001)

    def test_parse_price_rejects_invalid_values(self):
        for bad in (None, "", "not-a-price", "340.00", "0", "-1000", -5, 0, 12.5, True, []):
            self.assertIsNone(up.parse_price(bad), msg=repr(bad))


class ClassificationTests(unittest.TestCase):
    def test_detect_model(self):
        self.assertEqual(up.detect_model("Samsung Galaxy S23 128GB"), "s23")
        self.assertEqual(up.detect_model("GALAXY S23 FE 256GB"), "s23_fe")
        self.assertEqual(up.detect_model("Samsung Galaxy S23FE"), "s23_fe")
        self.assertEqual(up.detect_model("galaxy s23 fe"), "s23_fe")

    def test_detect_model_excludes_other_models(self):
        for title in (
            "Samsung Galaxy S23 Ultra 512GB",
            "Samsung Galaxy S23+ 256GB",
            "Samsung Galaxy S22 128GB",
            "Samsung Galaxy A54",
            "Galaxy Watch 6",
        ):
            self.assertIsNone(up.detect_model(title), msg=title)

    def test_refurbished_markers_are_clearly_refurbished(self):
        self.assertTrue(up.is_refurbished("Samsung Galaxy S23 Reacondicionado"))
        self.assertTrue(up.is_refurbished("Galaxy S23 FE - Refurbished"))
        self.assertTrue(up.is_refurbished("estado: REACONDICIONADO"))

    def test_used_only_listings_are_not_refurbished(self):
        for text in (
            "Samsung Galaxy S23 seminuevo",
            "Galaxy S23 FE usado excelente estado",
            "S23 segunda mano",
            "S23 like new",
            "S23 como nuevo",
        ):
            self.assertFalse(up.is_refurbished(text), msg=text)

    def test_normalize_offer_budget_boundaries_and_filters(self):
        def item(title, price, **extra):
            base = {"title": title, "price": price, "id": extra.pop("id", "x1"),
                    "url": extra.pop("url", "https://reusecl.myshopify.com/products/x")}
            base.update(extra)
            return base

        within = up.normalize_offer(
            item("Samsung Galaxy S23 Reacondicionado", "$340.000"), REUSE_ROOT)
        self.assertEqual(within["band"], "within_budget")
        self.assertEqual(within["price"], 340000)
        self.assertEqual(within["model"], "s23")
        self.assertEqual(within["currency"], "CLP")

        near_low = up.normalize_offer(
            item("Samsung Galaxy S23 Reacondicionado", "340001", id="x2"), REUSE_ROOT)
        self.assertEqual(near_low["band"], "near_budget")

        near_high = up.normalize_offer(
            item("Samsung Galaxy S23 Reacondicionado", "370000", id="x3"), REUSE_ROOT)
        self.assertEqual(near_high["band"], "near_budget")

        self.assertIsNone(up.normalize_offer(
            item("Samsung Galaxy S23 Reacondicionado", "370001", id="x4"), REUSE_ROOT))
        self.assertIsNone(up.normalize_offer(
            item("Samsung Galaxy S23 seminuevo", "300000", id="x5"), REUSE_ROOT))
        self.assertIsNone(up.normalize_offer(
            item("Samsung Galaxy S23 Ultra Reacondicionado", "350000", id="x6"), REUSE_ROOT))
        self.assertIsNone(up.normalize_offer(
            item("Samsung Galaxy S22 Reacondicionado", "250000", id="x7"), REUSE_ROOT))

    def test_normalize_offer_currency_gate(self):
        usd = {"title": "Samsung Galaxy S23 Reacondicionado", "price": 400,
               "currency": "USD", "id": "u1"}
        self.assertIsNone(up.normalize_offer(usd, REUSE_ROOT))
        nested_clp = {"title": "Samsung Galaxy S23 Reacondicionado",
                      "price": {"amount": "340000", "currency": "CLP"}, "id": "u2"}
        rec = up.normalize_offer(nested_clp, REUSE_ROOT)
        self.assertEqual(rec["price"], 340000)

    def test_normalize_offer_condition_field_can_mark_refurbished(self):
        rec = up.normalize_offer(
            {"title": "Samsung Galaxy S23 FE 128GB", "price": "330000",
             "condition": "Refurbished - Excellent", "id": "c1"}, REUSE_ROOT)
        self.assertIsNotNone(rec)
        self.assertEqual(rec["model"], "s23_fe")

    def test_normalize_offer_malformed_price_raises(self):
        with self.assertRaises(up.InvalidPayloadError):
            up.normalize_offer(
                {"title": "Samsung Galaxy S23 Reacondicionado", "price": "abc", "id": "m1"},
                REUSE_ROOT)
        with self.assertRaises(up.InvalidPayloadError):
            up.normalize_offer({"title": "Samsung Galaxy S23 Reacondicionado", "id": "m2"},
                               REUSE_ROOT)
        with self.assertRaises(up.InvalidPayloadError):
            up.normalize_offer({"price": "300000", "id": "m3"}, REUSE_ROOT)

    def test_normalize_catalog_skips_excluded_and_sorts_by_key(self):
        payload = {"products": GOOD_ITEMS + [
            {"id": "p3", "title": "Samsung Galaxy S23 seminuevo", "price": "290000"},
            {"id": "p4", "title": "Samsung Galaxy S23 Reacondicionado", "price": "370001"},
        ]}
        records = up.normalize_catalog(payload, REUSE_ROOT)
        self.assertEqual([r["key"] for r in records], sorted(r["key"] for r in records))
        self.assertEqual([r["band"] for r in records], ["within_budget", "near_budget"])

    def test_stable_offer_key_normalizes_url_and_prefers_id(self):
        a = up.stable_offer_key(REUSE_ROOT, "https://REUSECL.myshopify.com/p/1?utm=a", None, "T")
        b = up.stable_offer_key(REUSE_ROOT, "https://reusecl.myshopify.com/p/1?utm=b", None, "T")
        self.assertEqual(a, b)
        self.assertEqual(up.stable_offer_key(REUSE_ROOT, "https://x/1", "42", "T"),
                         up.stable_offer_key(REUSE_ROOT, "https://y/2", "42", "T"))
        self.assertNotEqual(
            up.stable_offer_key(REUSE_ROOT, None, None, "Samsung Galaxy S23"),
            up.stable_offer_key(REUSE_ROOT, None, None, "Samsung Galaxy S23 FE"))


# ---------------------------------------------------------------------------
# S1/S2 — Published UCP Product schema and source safety
# ---------------------------------------------------------------------------

class OfficialUcpProductTests(unittest.TestCase):
    def test_normalizes_product_description_variants_price_and_availability(self):
        product = {
            "id": "gid://shopify/Product/100",
            "title": "Samsung Galaxy S23 FE SM-S711U1 128GB",
            "description": {"plain": "Reacondicionado. Modelo SM-S711U1."},
            "url": "https://reuse.cl/products/s23-fe",
            "price_range": {
                "min": {"amount": 325000, "currency": "CLP"},
                "max": {"amount": 335000, "currency": "CLP"},
            },
            "variants": [
                {
                    "id": "gid://shopify/ProductVariant/101",
                    "title": "Graphite / 128GB",
                    "price": {"amount": 335000, "currency": "CLP"},
                    "url": "https://reuse.cl/products/s23-fe?variant=101",
                    "availability": {"available": True, "status": "in_stock"},
                },
                {
                    "id": "gid://shopify/ProductVariant/102",
                    "title": "Mint / 128GB",
                    "price": {"amount": 325000, "currency": "CLP"},
                    "url": "https://reuse.cl/products/s23-fe?variant=102",
                    "availability": {"available": False, "status": "out_of_stock"},
                },
            ],
        }
        records = up.normalize_catalog({"products": [product]}, REUSE_ROOT)
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record["price"], 335000)
        self.assertEqual(record["model"], "s23_fe")
        self.assertEqual(record["model_code"], "SM-S711U1")
        self.assertEqual(record["processor"], "Snapdragon 8 Gen 1")
        self.assertEqual(record["available"], True)
        self.assertIn("Graphite", record["title"])
        self.assertEqual(record["url"], "https://reuse.cl/products/s23-fe?variant=101")

    def test_unknown_fe_model_keeps_processor_unconfirmed(self):
        product = {
            "id": "p-fe",
            "title": "Samsung Galaxy S23 FE Reacondicionado",
            "url": "https://reuse.cl/products/s23-fe",
            "price_range": {"min": {"amount": 300000, "currency": "CLP"},
                            "max": {"amount": 300000, "currency": "CLP"}},
            "variants": [{"id": "v-fe", "title": "128GB",
                          "price": {"amount": 300000, "currency": "CLP"}}],
        }
        record = up.normalize_catalog({"products": [product]}, REUSE_ROOT)[0]
        self.assertIsNone(record["model_code"])
        self.assertEqual(record["processor"], "unknown")
        self.assertIsNone(record["available"])

    def test_rejects_untrusted_product_link(self):
        with self.assertRaises(up.InvalidPayloadError):
            up.normalize_offer({"id": "bad-link",
                                "title": "Samsung Galaxy S23 Reacondicionado",
                                "price": {"amount": 300000, "currency": "CLP"},
                                "url": "javascript:alert(1)"}, REUSE_ROOT)


class SourceTrustTests(CollectorTestCase):
    def test_rejects_mcp_endpoint_outside_the_source_host_before_posting(self):
        evil_endpoint = "https://127.0.0.1/api/ucp/mcp"
        fake = self.use_transport([{"root": REUSE_ROOT, "endpoint": evil_endpoint,
                                    "profile_doc": {"services": {
                                        "dev.ucp.shopping": [{"endpoint": evil_endpoint}]}}}])
        with self.assertRaises(up.InvalidPayloadError):
            up.collect_from_source(REUSE_ROOT, PROFILE, "Samsung Galaxy S23")
        self.assertEqual(fake.urls(), [f"{REUSE_ROOT}/.well-known/ucp"])

    def test_dlphone_product_url_is_trusted_across_its_hosts(self):
        for url in ("https://dlphone.cl/products/s23", "https://www.dlphone.cl/products/s23",
                    "https://d8f7f8-76.myshopify.com/products/s23"):
            self.assertIsNotNone(
                up._trusted_product_url(url, "https://dlphone.cl"), msg=url)
        for bad in ("http://dlphone.cl/products/s23",
                    "https://evil.example.com/products/s23"):
            with self.assertRaises(up.InvalidPayloadError, msg=bad):
                up._trusted_product_url(bad, "https://dlphone.cl")


# ---------------------------------------------------------------------------
# S2 — UCP/MCP request parsing
# ---------------------------------------------------------------------------

class UcpRequestTests(CollectorTestCase):
    def test_discovery_and_request_shapes(self):
        fake = self.use_transport([{"root": REUSE_ROOT, "endpoint": REUSE_ENDPOINT,
                                    "catalog": {"products": GOOD_ITEMS}}])
        records = up.collect_from_source(REUSE_ROOT, PROFILE, up.DEFAULT_QUERY)

        self.assertEqual(len(records), 2)
        self.assertEqual(fake.urls()[0], f"{REUSE_ROOT}/.well-known/ucp")

        init_call = next(c for c in fake.calls
                         if c["method"] == "POST" and json.loads(c["body"])["method"] == "initialize")
        init_body = json.loads(init_call["body"])
        self.assertEqual(init_body["params"]["protocolVersion"], "2025-03-26")
        self.assertEqual(init_body["params"]["clientInfo"]["name"], up.CLIENT_NAME)

        call = next(c for c in fake.calls
                    if c["method"] == "POST" and json.loads(c["body"])["method"] == "tools/call")
        self.assertEqual(call["url"], REUSE_ENDPOINT)
        lowered = {k.lower(): v for k, v in call["headers"].items()}
        self.assertEqual(lowered.get("mcp-session-id"), "sess-42")
        args = json.loads(call["body"])["params"]["arguments"]
        self.assertEqual(args, {"meta": {"ucp-agent": {"profile": PROFILE}},
                                "catalog": {"query": up.DEFAULT_QUERY}})

    def test_profile_endpoint_is_discovered_not_hardcoded(self):
        custom_endpoint = "https://reusecl.myshopify.com/custom/ucp/mcp/v2"
        fake = self.use_transport([{"root": REUSE_ROOT, "endpoint": custom_endpoint,
                                    "catalog": {"products": []}}])
        up.collect_from_source(REUSE_ROOT, PROFILE, "query")
        post_urls = [c["url"] for c in fake.calls if c["method"] == "POST"]
        self.assertTrue(post_urls)
        self.assertEqual(set(post_urls), {custom_endpoint})

    def test_custom_query_reaches_tools_call(self):
        fake = self.use_transport([{"root": REUSE_ROOT, "endpoint": REUSE_ENDPOINT,
                                    "catalog": {"products": []}}])
        up.collect_from_source(REUSE_ROOT, PROFILE, "Galaxy S23 FE reacondicionado")
        call = next(c for c in fake.calls
                    if c["method"] == "POST" and json.loads(c["body"])["method"] == "tools/call")
        args = json.loads(call["body"])["params"]["arguments"]
        self.assertEqual(args["catalog"]["query"], "Galaxy S23 FE reacondicionado")
        self.assertEqual(args["meta"]["ucp-agent"]["profile"], PROFILE)

    def test_sse_encoded_rpc_response_is_parsed(self):
        def router(method, url, headers, body=None):
            if method == "GET":
                return http_json({"mcp": {"endpoint": REUSE_ENDPOINT}})
            payload = json.loads(body.decode())
            if payload["method"] == "initialize":
                return up.HttpResponse(
                    200, {"content-type": "text/event-stream", "mcp-session-id": "s1"},
                    b"event: message\n"
                    + b"data: " + json.dumps({"jsonrpc": "2.0", "id": payload["id"],
                                              "result": {"protocolVersion": "2025-03-26"}}).encode()
                    + b"\n\n")
            if payload["method"] == "notifications/initialized":
                return up.HttpResponse(202, {}, b"")
            if payload["method"] == "tools/list":
                body_obj = {"jsonrpc": "2.0", "id": payload["id"],
                            "result": {"tools": [{"name": "search_catalog"}]}}
                return up.HttpResponse(200, {"content-type": "text/event-stream"},
                                       b"data: " + json.dumps(body_obj).encode() + b"\n\n")
            body_obj = {"jsonrpc": "2.0", "id": payload["id"],
                        "result": {"content": [{"type": "text",
                                                "text": json.dumps({"products": GOOD_ITEMS})}]}}
            return up.HttpResponse(200, {"content-type": "text/event-stream"},
                                   b"data: " + json.dumps(body_obj).encode() + b"\n\n")

        self.use_transport([{"root": REUSE_ROOT, "endpoint": REUSE_ENDPOINT}])
        with mock.patch.object(up, "http_request", side_effect=router):
            records = up.collect_from_source(REUSE_ROOT, PROFILE, up.DEFAULT_QUERY)
        self.assertEqual(len(records), 2)

    def test_profile_doc_without_mcp_endpoint_fails_that_source(self):
        self.use_transport([{"root": REUSE_ROOT, "endpoint": REUSE_ENDPOINT,
                             "profile_doc": {"merchant": "Reuse"},
                             "catalog": {"products": GOOD_ITEMS}}])
        with self.assertRaises(up.InvalidPayloadError):
            up.collect_from_source(REUSE_ROOT, PROFILE, up.DEFAULT_QUERY)

    def test_missing_search_catalog_tool_fails_that_source(self):
        self.use_transport([{"root": REUSE_ROOT, "endpoint": REUSE_ENDPOINT,
                             "tools": [{"name": "add_to_cart"}],
                             "catalog": {"products": GOOD_ITEMS}}])
        with self.assertRaises(up.UpdatePricesError) as ctx:
            up.collect_from_source(REUSE_ROOT, PROFILE, up.DEFAULT_QUERY)
        self.assertIn("search_catalog", str(ctx.exception))


# ---------------------------------------------------------------------------
# S2 — required profile, rejected responses, failure isolation
# ---------------------------------------------------------------------------

class MissingProfileTests(CollectorTestCase):
    def test_missing_profile_uses_public_product_json_fallback(self):
        """No profile: collector reads the store's public product JSON instead."""
        self.write_history(initial_history())
        fake = self.use_transport([{"root": REUSE_ROOT, "endpoint": REUSE_ENDPOINT}])
        os.environ.pop(up.PROFILE_ENV_VAR, None)

        with mock.patch.object(up, "collect_public_json_from_source",
                               return_value=up.normalize_catalog(
                                   {"products": GOOD_ITEMS}, REUSE_ROOT)) as fallback:
            summary = up.run_collection(str(self.history_path), sources=(REUSE_ROOT,),
                                        now=FIXED_NOW)

        self.assertEqual(summary["offer_count"], 2)
        self.assertEqual(summary["sources"],
                         {REUSE_ROOT: "ok (public product JSON)"})
        fallback.assert_called_once_with(REUSE_ROOT)
        # A missing profile means the UCP path is skipped entirely; the Fallback
        # replacement stayed mocked, so the transport saw no request at all.
        self.assertEqual(fake.calls, [])

    def test_invalid_profile_url_fails_before_network_and_leaves_history_unchanged(self):
        self.write_history(initial_history())
        before = self.read_raw()
        fake = self.use_transport([{"root": REUSE_ROOT, "endpoint": REUSE_ENDPOINT,
                                    "catalog": {"products": GOOD_ITEMS}}])
        os.environ[up.PROFILE_ENV_VAR] = "http://127.0.0.1/private-profile.json"

        with self.assertRaises(up.ProfileMissingError):
            up.run_collection(str(self.history_path), sources=(REUSE_ROOT,), now=FIXED_NOW)

        self.assertEqual(fake.calls, [])
        self.assertEqual(self.read_raw(), before)


class ShopifyFallbackTests(CollectorTestCase):
    """Covers the public product JSON path directly (no UCP profile needed)."""

    SHOPIFY_PRODUCT = {
        "id": 9055423496409,
        "title": "Samsung Galaxy S23 SM-S911U1 128GB Negro Reacondicionado",
        "handle": "53151-celular-samsung-galaxy-s23-sm-s911u1-128gb",
        "body_html": "<p><b>Estado:</b> Reacondicionado, funcional al 100%.</p>",
        "tags": ["SAMSUNG", "reacondicionado"],
        "variants": [{
            "id": 62903032381599,
            "title": "Default Title",
            "sku": "SM-S911U1",
            "price": "343190.00",
        }],
    }

    def use_product_feed(self, status: int = 200):
        """Patch the transport to serve /products.json and record every call."""
        calls: list[dict] = []

        def router(method, url, headers=None, body=None):
            calls.append({"method": method, "url": url,
                          "headers": dict(headers or {}), "body": body})
            if "/products.json" in url:
                return up.HttpResponse(
                    status, {"content-type": "application/json"},
                    json.dumps({"products": [self.SHOPIFY_PRODUCT]}).encode())
            raise AssertionError(f"unexpected fallback URL {url}")

        patcher = mock.patch.object(up, "http_request", side_effect=router)
        patcher.start()
        self.addCleanup(patcher.stop)
        return types.SimpleNamespace(calls=calls)

    def test_shopify_product_json_normalization(self):
        shapes = up.normalize_shopify_product(self.SHOPIFY_PRODUCT, REUSE_ROOT)
        records = up.normalize_catalog({"products": [shapes]}, REUSE_ROOT)
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record["price"], 343190)
        self.assertEqual(record["band"], "near_budget")
        self.assertEqual(record["model"], "s23")
        self.assertIn("Snapdragon 8 Gen 2", record["processor"])
        self.assertTrue(record["url"].startswith(
            "https://reusecl.myshopify.com/products/53151-celular"))

    def test_public_json_run_saves_offers_without_profile(self):
        self.write_history(initial_history())
        fake = self.use_product_feed()
        os.environ.pop(up.PROFILE_ENV_VAR, None)
        with redirect_stdout(StringIO()):
            summary = up.run_collection(str(self.history_path), sources=(REUSE_ROOT,),
                                        profile=None, now=FIXED_NOW)
        self.assertEqual(summary["offer_count"], 1)
        snapshot = self.read_json()["snapshots"][WEEK]
        self.assertTrue(snapshot["sources"][REUSE_ROOT].startswith("ok"))
        self.assertTrue(any(call["method"] == "GET"
                            and "/products.json?limit=250" in call["url"]
                            for call in fake.calls))


class RejectedResponseTests(CollectorTestCase):
    def test_invalid_catalog_payload_leaves_history_byte_identical(self):
        self.write_history(initial_history())
        before = self.read_raw()
        self.use_transport([{"root": REUSE_ROOT, "endpoint": REUSE_ENDPOINT,
                             "catalog": {"error": "quota exceeded"}}])

        with self.assertRaises(up.UpdatePricesError) as ctx:
            up.run_collection(str(self.history_path), sources=(REUSE_ROOT,), now=FIXED_NOW)
        self.assertIn("price history unchanged", str(ctx.exception))
        self.assertEqual(self.read_raw(), before)

    def test_non_json_catalog_text_leaves_history_unchanged(self):
        self.write_history(initial_history())
        before = self.read_raw()
        self.use_transport([{"root": REUSE_ROOT, "endpoint": REUSE_ENDPOINT,
                             "catalog": "<html>captcha</html>"}])
        with self.assertRaises(up.UpdatePricesError):
            up.run_collection(str(self.history_path), sources=(REUSE_ROOT,), now=FIXED_NOW)
        self.assertEqual(self.read_raw(), before)

    def test_all_malformed_items_leave_history_unchanged(self):
        self.write_history(initial_history())
        before = self.read_raw()
        self.use_transport([{"root": REUSE_ROOT, "endpoint": REUSE_ENDPOINT,
                             "catalog": {"products": [
                                 {"title": "Samsung Galaxy S23 Reacondicionado", "price": "abc"},
                                 {"price": "340000"},
                             ]}}])
        with self.assertRaises(up.UpdatePricesError):
            up.run_collection(str(self.history_path), sources=(REUSE_ROOT,), now=FIXED_NOW)
        self.assertEqual(self.read_raw(), before)

    def test_tool_error_response_leaves_history_unchanged(self):
        self.write_history(initial_history())
        before = self.read_raw()

        def router(method, url, headers, body=None):
            if method == "GET":
                return http_json({"mcp": {"endpoint": REUSE_ENDPOINT}})
            payload = json.loads(body.decode())
            if payload["method"] == "initialize":
                return up.HttpResponse(200, {"content-type": "application/json"},
                                       rpc_ok(payload["id"], {"protocolVersion": "2025-03-26"}))
            if payload["method"] == "notifications/initialized":
                return up.HttpResponse(202, {}, b"")
            if payload["method"] == "tools/list":
                return up.HttpResponse(200, {"content-type": "application/json"},
                                       rpc_ok(payload["id"], {"tools": [{"name": "search_catalog"}]}))
            return up.HttpResponse(200, {"content-type": "application/json"},
                                   rpc_ok(payload["id"],
                                          {"isError": True,
                                           "content": [{"type": "text", "text": "rate limited"}]}))

        self.use_transport([{"root": REUSE_ROOT, "endpoint": REUSE_ENDPOINT}])
        with mock.patch.object(up, "http_request", side_effect=router):
            with self.assertRaises(up.UpdatePricesError):
                up.run_collection(str(self.history_path), sources=(REUSE_ROOT,), now=FIXED_NOW)
        self.assertEqual(self.read_raw(), before)

    def test_corrupt_history_fails_before_network(self):
        self.write_raw_history("{ this is not json")
        before = self.read_raw()
        fake = self.use_transport([{"root": REUSE_ROOT, "endpoint": REUSE_ENDPOINT,
                                    "catalog": {"products": GOOD_ITEMS}}])
        with self.assertRaises(up.HistoryCorruptError):
            up.run_collection(str(self.history_path), sources=(REUSE_ROOT,), now=FIXED_NOW)
        self.assertEqual(fake.calls, [])
        self.assertEqual(self.read_raw(), before)

    def test_main_reports_corrupt_history_with_nonzero_exit(self):
        self.write_raw_history("[]")
        before = self.read_raw()
        self.use_transport([{"root": REUSE_ROOT, "endpoint": REUSE_ENDPOINT,
                             "catalog": {"products": GOOD_ITEMS}}])
        stdout, stderr = StringIO(), StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = up.main(["--history", str(self.history_path), "--source", REUSE_ROOT])
        self.assertEqual(code, 1)
        self.assertIn("price history", stderr.getvalue())
        self.assertEqual(self.read_raw(), before)


class FailureIsolationTests(CollectorTestCase):
    def test_one_bad_source_does_not_block_the_good_one(self):
        self.write_history(initial_history())
        fake = self.use_transport([
            {"root": REUSE_ROOT, "endpoint": REUSE_ENDPOINT,
             "catalog": {"unexpected": "shape"}},
            {"root": BOGEDA_ROOT, "endpoint": BOGEDA_ENDPOINT,
             "catalog": {"products": GOOD_BOGEDA_ITEMS}},
        ])

        summary = up.run_collection(str(self.history_path),
                                    sources=(REUSE_ROOT, BOGEDA_ROOT), now=FIXED_NOW)

        snapshot = self.read_json()["snapshots"][WEEK]
        self.assertEqual(summary["week"], WEEK)
        self.assertEqual(len(snapshot["offers"]), 2)
        self.assertTrue(all(o["source"] == BOGEDA_ROOT for o in snapshot["offers"]))
        self.assertTrue(snapshot["sources"][BOGEDA_ROOT].startswith("ok"))
        self.assertTrue(snapshot["sources"][REUSE_ROOT].startswith("error:"))
        self.assertEqual(fake.urls()[-1], BOGEDA_ENDPOINT)

    def test_all_sources_failing_leaves_history_unchanged(self):
        self.write_history(initial_history())
        before = self.read_raw()
        self.use_transport([
            {"root": REUSE_ROOT, "endpoint": REUSE_ENDPOINT, "catalog": {"error": "x"}},
            {"root": BOGEDA_ROOT, "endpoint": BOGEDA_ENDPOINT,
             "tools": [{"name": "checkout"}]},
        ])
        with self.assertRaises(up.UpdatePricesError) as ctx:
            up.run_collection(str(self.history_path),
                              sources=(REUSE_ROOT, BOGEDA_ROOT), now=FIXED_NOW)
        self.assertIn("price history unchanged", str(ctx.exception))
        self.assertEqual(self.read_raw(), before)


class AccessDenialTests(CollectorTestCase):
    def test_access_denial_stops_collection_and_leaves_history_unchanged(self):
        self.write_history(initial_history())
        before = self.read_raw()
        fake = self.use_transport([
            {"root": REUSE_ROOT, "endpoint": REUSE_ENDPOINT, "discovery_status": 403,
             "catalog": {"products": GOOD_ITEMS}},
            {"root": BOGEDA_ROOT, "endpoint": BOGEDA_ENDPOINT,
             "catalog": {"products": GOOD_ITEMS}},
        ])

        with self.assertRaises(up.AccessDeniedError):
            up.run_collection(str(self.history_path),
                              sources=(REUSE_ROOT, BOGEDA_ROOT), now=FIXED_NOW)

        self.assertEqual(self.read_raw(), before)
        self.assertFalse(any("bogedaoportunidades" in u for u in fake.urls()),
                         msg="second source must not be contacted after access denial")

    def test_rate_limit_also_stops_collection(self):
        self.write_history(initial_history())
        before = self.read_raw()
        self.use_transport([{"root": REUSE_ROOT, "endpoint": REUSE_ENDPOINT,
                             "status": 429, "catalog": {"products": GOOD_ITEMS}}])
        with self.assertRaises(up.AccessDeniedError):
            up.run_collection(str(self.history_path), sources=(REUSE_ROOT,), now=FIXED_NOW)
        self.assertEqual(self.read_raw(), before)


# ---------------------------------------------------------------------------
# S2 — dated snapshots: ISO-week idempotency, stable keys, bounded history
# ---------------------------------------------------------------------------

class SnapshotHistoryTests(CollectorTestCase):
    def run_once(self, catalog=None, now=FIXED_NOW):
        catalog = {"products": GOOD_ITEMS} if catalog is None else catalog
        self.use_transport([{"root": REUSE_ROOT, "endpoint": REUSE_ENDPOINT,
                             "catalog": catalog}])
        return up.run_collection(str(self.history_path), sources=(REUSE_ROOT,),
                                 profile=PROFILE, now=now)

    def test_snapshot_is_keyed_by_iso_week(self):
        self.write_history(initial_history())
        self.run_once()
        history = self.read_json()
        self.assertIn(WEEK, history["snapshots"])
        self.assertEqual(len(history["snapshots"]), 1)
        snapshot = history["snapshots"][WEEK]
        self.assertEqual(snapshot["collected_at"], "2026-02-09T12:00:00+00:00")
        self.assertEqual(history["last_collected_at"], "2026-02-09T12:00:00+00:00")
        self.assertEqual(history["budget"],
                         {"currency": "CLP", "within_max": 340000, "near_max": 370000})

    def test_rerun_same_week_is_byte_identical(self):
        self.write_history(initial_history())
        self.run_once()
        first = self.read_raw()
        self.run_once()
        self.assertEqual(self.read_raw(), first)
        self.assertEqual(len(self.read_json()["snapshots"][WEEK]["offers"]), 2)

    def test_stable_key_updates_price_without_duplicating(self):
        self.write_history(initial_history())
        self.run_once()
        updated = [dict(GOOD_ITEMS[0], price="330000"), dict(GOOD_ITEMS[1], price="335000")]
        self.run_once(catalog={"products": updated})
        offers = self.read_json()["snapshots"][WEEK]["offers"]
        self.assertEqual(len(offers), 2)
        by_price = {o["price"]: o for o in offers}
        self.assertIn(330000, by_price)
        self.assertEqual(by_price[330000]["band"], "within_budget")
        self.assertIn(335000, by_price)
        self.assertEqual(by_price[335000]["band"], "within_budget")

    def test_same_week_replaces_offers_that_disappeared(self):
        self.write_history(initial_history())
        self.run_once()
        self.run_once(catalog={"products": [GOOD_ITEMS[0]]})
        offers = self.read_json()["snapshots"][WEEK]["offers"]
        self.assertEqual(len(offers), 1)
        self.assertEqual(offers[0]["key"], up.stable_offer_key(
            REUSE_ROOT, GOOD_ITEMS[0]["url"], GOOD_ITEMS[0]["id"], GOOD_ITEMS[0]["title"]))

    def test_previous_weeks_are_preserved(self):
        self.seed_history(["2026-W05", "2026-W06"])
        self.run_once()
        history = self.read_json()
        self.assertEqual(set(history["snapshots"]), {"2026-W05", "2026-W06", WEEK})

    def test_history_is_bounded_to_max_weeks(self):
        weeks = [f"2023-W{w:02d}" for w in range(1, 53)] + \
                [f"2024-W{w:02d}" for w in range(1, 53)]
        self.seed_history(weeks)
        self.run_once()
        snapshots = self.read_json()["snapshots"]
        self.assertEqual(len(snapshots), up.MAX_WEEKS)
        self.assertIn(WEEK, snapshots)
        self.assertNotIn("2024-W01", snapshots)
        self.assertIn("2024-W02", snapshots)
        self.assertNotIn("2023-W52", snapshots)


class MainCliTests(CollectorTestCase):
    def freeze_clock(self):
        class FrozenDatetime:
            @classmethod
            def now(cls, tz=None):
                return FIXED_NOW

        patcher = mock.patch.object(up, "datetime", FrozenDatetime)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_successful_run_reports_summary(self):
        self.freeze_clock()
        self.write_history(initial_history())
        self.use_transport([{"root": REUSE_ROOT, "endpoint": REUSE_ENDPOINT,
                             "catalog": {"products": GOOD_ITEMS}}])
        stdout, stderr = StringIO(), StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = up.main(["--history", str(self.history_path), "--source", REUSE_ROOT,
                            "--query", "Galaxy S23"])
        self.assertEqual(code, 0)
        self.assertEqual(stderr.getvalue(), "")
        self.assertIn(f"saved 2 offer(s) for {WEEK}", stdout.getvalue())
        self.assertEqual(len(self.read_json()["snapshots"][WEEK]["offers"]), 2)

    def test_history_file_is_created_when_missing(self):
        self.freeze_clock()
        fake = self.use_transport([{"root": REUSE_ROOT, "endpoint": REUSE_ENDPOINT,
                                    "catalog": {"products": GOOD_ITEMS}}])
        stdout, stderr = StringIO(), StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = up.main(["--history", str(self.history_path), "--source", REUSE_ROOT])
        self.assertEqual(code, 0)
        self.assertTrue(self.history_path.exists())
        self.assertIn(WEEK, self.read_json()["snapshots"])
        self.assertTrue(fake.calls)

    def test_default_collection_searches_both_s23_models(self):
        products = [
            {"id": "s23", "title": "Samsung Galaxy S23 128GB Reacondicionado",
             "price": "330000", "url": "https://reusecl.myshopify.com/products/s23"},
            {"id": "fe", "title": "Samsung Galaxy S23 FE 128GB Reacondicionado",
             "price": "320000", "url": "https://reusecl.myshopify.com/products/s23-fe"},
        ]
        fake = self.use_transport([{
            "root": REUSE_ROOT,
            "endpoint": REUSE_ENDPOINT,
            "catalog": lambda args: {"products": products},
        }])
        summary = up.run_collection(str(self.history_path), sources=(REUSE_ROOT,),
                                   profile=PROFILE, now=FIXED_NOW)
        queries = [json.loads(call["body"])["params"]["arguments"]["catalog"]["query"]
                   for call in fake.calls
                   if call["method"] == "POST"
                   and json.loads(call["body"]).get("method") == "tools/call"]
        offers = self.read_json()["snapshots"][WEEK]["offers"]
        self.assertEqual(queries, ["Samsung Galaxy S23 reacondicionado",
                                   "Samsung Galaxy S23 FE reacondicionado"])
        self.assertEqual({offer["model"] for offer in offers}, {"s23", "s23_fe"})
        self.assertEqual(summary["offer_count"], 2)


# ---------------------------------------------------------------------------
# S2 — workflow, data file, and setup documentation
# ---------------------------------------------------------------------------

class WorkflowAndDocsTests(unittest.TestCase):
    def setUp(self):
        self.readme = SITE_ROOT / "README.md"
        self.data_file = SITE_ROOT / "data" / "price-history.json"
        self.agent_profile = SITE_ROOT / ".well-known" / "ucp.json"

    def test_project_remains_local_without_remote_automation(self):
        self.assertFalse((SITE_ROOT / ".github").exists(),
                         "the user directed a local-only project; no .github workflows")
        readme_text = self.readme.read_text(encoding="utf-8")
        self.assertIn("stays on this machine", readme_text)
        self.assertIn("systemd user timer", readme_text)
        self.assertIn("s23-prices.timer", readme_text)
        for banned in ("workflow_dispatch", "contents: write", "git push",
                       "GitHub Pages", "Actions variable", ".github/workflows"):
            self.assertNotIn(banned, readme_text, msg=banned)

    def test_data_file_is_valid_history(self):
        """The tracked file holds real captured weeks, not hardcoded rows."""
        history = json.loads(self.data_file.read_text(encoding="utf-8"))
        self.assertEqual(history["schema_version"], 1)
        self.assertEqual(history["budget"],
                         {"currency": "CLP", "within_max": 340000, "near_max": 370000})
        for week, snapshot in history["snapshots"].items():
            self.assertRegex(week, r"^\d{4}-W\d{2}$")
            self.assertTrue(snapshot["offers"], msg=week)
            for offer in snapshot["offers"]:
                self.assertTrue(offer["title"], msg=week)
                self.assertTrue(offer["url"].startswith("https://"), msg=offer["url"])
        self.assertIsInstance(history["last_collected_at"], str)

    def test_agent_profile_exposes_catalog_search_only(self):
        profile = json.loads(self.agent_profile.read_text(encoding="utf-8"))
        ucp = profile["ucp"]
        self.assertEqual(ucp["version"], "2026-08-25")
        self.assertEqual(set(ucp["capabilities"]), {"dev.ucp.shopping.catalog.search"})
        self.assertEqual(ucp["payment_handlers"], {})
        self.assertIn("dev.ucp.shopping", ucp["services"])

    def test_readme_documents_required_setup_and_limits(self):
        text = self.readme.read_text(encoding="utf-8")
        self.assertIn("UCP_AGENT_PROFILE_URL", text)
        self.assertIn(".well-known/ucp.json", text)
        self.assertIn("systemctl --user enable --now s23-prices.timer", text)
        self.assertIn("retries at most once", text)
        self.assertIn("340,000", text)
        self.assertIn("370,000", text)
        self.assertIn("Mercado Libre", text)
        self.assertIn("python3 -m unittest discover -s tests -v", text)
        self.assertIn("node --test tests/dashboard-core.test.cjs", text)


class RetryPolicyTests(unittest.TestCase):
    """S1: one bounded retry on transient network errors and HTTP 5xx only."""

    def setUp(self):
        self.sleep = mock.patch.object(up.time, "sleep").start()
        self.addCleanup(mock.patch.stopall)

    def once(self, results):
        calls = []

        def fake_once(method, url, headers=None, body=None):
            calls.append(url)
            result = results.pop(0)
            if isinstance(result, Exception):
                raise result
            return result

        return fake_once, calls

    def test_network_error_retries_once_then_succeeds(self):
        once, calls = self.once([up.RetryableNetworkError("urlerror: boom"),
                                 up.HttpResponse(200, {}, b"{}")])
        with mock.patch.object(up, "_http_request_once", once):
            resp = up.http_request("GET", "https://example.com")
        self.assertEqual(resp.status, 200)
        self.assertEqual(len(calls), 2)
        self.sleep.assert_called_once_with(up.RETRY_BACKOFF_SECONDS)

    def test_http_5xx_retries_once_then_succeeds(self):
        once, calls = self.once([up.HttpResponse(503, {}, b""),
                                 up.HttpResponse(200, {}, b"{}")])
        with mock.patch.object(up, "_http_request_once", once):
            resp = up.http_request("GET", "https://example.com")
        self.assertEqual(resp.status, 200)
        self.assertEqual(len(calls), 2)
        self.sleep.assert_called_once_with(up.RETRY_BACKOFF_SECONDS)

    def test_access_denied_is_never_retried(self):
        once, calls = self.once([up.HttpResponse(403, {}, b"")])
        with mock.patch.object(up, "_http_request_once", once):
            resp = up.http_request("GET", "https://example.com")
        self.assertEqual(resp.status, 403)
        self.assertEqual(len(calls), 1)
        self.sleep.assert_not_called()

    def test_rate_limit_is_never_retried(self):
        once, calls = self.once([up.HttpResponse(429, {}, b"")])
        with mock.patch.object(up, "_http_request_once", once):
            resp = up.http_request("GET", "https://example.com")
        self.assertEqual(resp.status, 429)
        self.assertEqual(len(calls), 1)
        self.sleep.assert_not_called()

    def test_gives_up_after_single_retry(self):
        once, calls = self.once([up.RetryableNetworkError("urlerror: down"),
                                 up.RetryableNetworkError("urlerror: down 2")])
        with mock.patch.object(up, "_http_request_once", once):
            with self.assertRaises(up.UpdatePricesError):
                up.http_request("GET", "https://example.com")
        self.assertEqual(len(calls), 2)
        self.assertEqual(self.sleep.call_count, 1)

    def test_client_error_4xx_is_not_retried(self):
        once, calls = self.once([up.HttpResponse(404, {}, b"")])
        with mock.patch.object(up, "_http_request_once", once):
            resp = up.http_request("GET", "https://example.com")
        self.assertEqual(resp.status, 404)
        self.assertEqual(len(calls), 1)
        self.sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()
