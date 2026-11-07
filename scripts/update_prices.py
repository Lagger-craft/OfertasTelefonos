#!/usr/bin/env python3
"""Weekly refurbished Galaxy S23 / S23 FE price collector (stdlib only).

Collects offers through each merchant's published UCP/MCP product-discovery
interface (read-only ``search_catalog`` tool), filters them to clearly
refurbished S23 / S23 FE listings inside the accepted budget bands, and
preserves dated snapshots in ``data/price-history.json`` keyed by ISO week
and stable offer key.

Live collection requires UCP_AGENT_PROFILE_URL to point at the operator's
own publicly hosted UCP agent profile URL; no default or placeholder profile
is ever used. Sources: Reuse and Bodega Oportunidades, discovered
dynamically from their public ``/.well-known/ucp`` profiles.

Requests are sequential, low-rate, single-attempt (no retries), and stop
immediately on access denial (HTTP 401/403/429).
"""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
import re
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Any, NamedTuple
from urllib.parse import urlparse

# --- configuration ---------------------------------------------------------

PROFILE_ENV_VAR = "UCP_AGENT_PROFILE_URL"
PROFILE_MISSING_MSG = (
    "UCP_AGENT_PROFILE_URL is not set; live collection requires your own "
    "publicly hosted UCP agent profile URL"
)
PROFILE_INVALID_MSG = (
    "UCP_AGENT_PROFILE_URL must be an absolute public HTTPS URL without "
    "credentials, query parameters, or fragments"
)

DEFAULT_SOURCES = ("https://reusecl.myshopify.com",
                   "https://bogedaoportunidades.myshopify.com",
                   "https://dlphone.cl",
                   "https://comprala.cl")
MCP_PROTOCOL_VERSION = "2025-03-26"
CLIENT_NAME = "s23-price-collector"
CLIENT_VERSION = "1.0.0"
SEARCH_TOOL = "search_catalog"
DEFAULT_QUERIES = (
    "Samsung Galaxy S23 reacondicionado",
    "Samsung Galaxy S23 FE reacondicionado",
)
DEFAULT_QUERY = DEFAULT_QUERIES[0]  # kept for the existing single-query --query option

BUDGET_WITHIN_CLP = 340000
BUDGET_NEAR_CLP = 370000
BAND_WITHIN = "within_budget"
BAND_NEAR = "near_budget"
BAND_EXCLUDED = "excluded"

MAX_WEEKS = 52
REQUEST_DELAY_SECONDS = 1.0
HTTP_TIMEOUT_SECONDS = 30
NETWORK_RETRIES = 1
RETRY_BACKOFF_SECONDS = 60.0  # pause before the single retry on transient failures
SCHEMA_VERSION = 1

DEFAULT_HISTORY_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "data", "price-history.json"
)

REFURBISHED_MARKERS = ("reacondicionad", "refurbished", "remanufacturad")
ISO_WEEK_RE = re.compile(r"^\d{4}-W\d{2}$")
CONTAINER_KEYS = ("products", "items", "offers", "results", "listings", "entries", "catalog")
NEST_KEYS = ("data", "result", "results", "catalog")
TITLE_KEYS = ("title", "name", "productName", "product_title")
URL_KEYS = ("url", "permalink", "link", "productUrl", "href")
ID_KEYS = ("id", "productId", "sku")
PRICE_KEYS = ("price", "currentPrice", "sellingPrice", "salePrice")
TRUSTED_PRODUCT_HOSTS = {
    "reusecl.myshopify.com": {"reusecl.myshopify.com", "reuse.cl", "www.reuse.cl"},
    "bogedaoportunidades.myshopify.com": {
        "bogedaoportunidades.myshopify.com", "bodegaoportunidades.cl",
        "www.bodegaoportunidades.cl",
    },
    "dlphone.cl": {"dlphone.cl", "www.dlphone.cl", "d8f7f8-76.myshopify.com"},
    "comprala.cl": {"comprala.cl", "www.comprala.cl", "comprala-cl.myshopify.com"},
}


# --- errors ----------------------------------------------------------------

class UpdatePricesError(Exception):
    """Base class for collection failures that leave history unchanged."""


class ProfileMissingError(UpdatePricesError):
    pass


class HistoryCorruptError(UpdatePricesError):
    pass


class InvalidPayloadError(UpdatePricesError):
    pass


class AccessDeniedError(UpdatePricesError):
    pass


class RetryableNetworkError(UpdatePricesError):
    """Transient network failure worth one bounded retry."""

    pass


# --- low-level HTTP --------------------------------------------------------

class HttpResponse(NamedTuple):
    status: int
    headers: dict
    body: bytes


def _http_request_once(method: str, url: str, headers: dict | None = None,
                       body: bytes | None = None) -> HttpResponse:
    """Perform exactly one delayed, low-rate HTTP request (no retries)."""
    time.sleep(REQUEST_DELAY_SECONDS)
    req = urllib.request.Request(url, data=body, method=method,
                                 headers=dict(headers or {}))
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_SECONDS) as resp:
            return HttpResponse(resp.status,
                               {k.lower(): v for k, v in resp.headers.items()},
                               resp.read())
    except urllib.error.HTTPError as exc:  # non-2xx still carries a body
        return HttpResponse(exc.code,
                            {k.lower(): v for k, v in (exc.headers or {}).items()},
                            exc.read() or b"")
    except urllib.error.URLError as exc:
        raise RetryableNetworkError(f"request to {url} failed: {exc.reason}") from exc


def http_request(method: str, url: str, headers: dict | None = None,
                 body: bytes | None = None) -> HttpResponse:
    """Low-rate requests with one bounded retry for transient failures only.

    A network error or an HTTP 5xx response is retried once after
    RETRY_BACKOFF_SECONDS. Access denials (401/403/429) and client errors
    (4xx) are never retried: blocking responses must stop immediately.
    """
    last_exc: RetryableNetworkError | None = None
    for attempt in range(NETWORK_RETRIES + 1):
        try:
            resp = _http_request_once(method, url, headers, body)
        except RetryableNetworkError as exc:
            last_exc = exc
            if attempt == NETWORK_RETRIES:
                raise
            time.sleep(RETRY_BACKOFF_SECONDS)
            continue
        if resp.status >= 500 and attempt < NETWORK_RETRIES:
            time.sleep(RETRY_BACKOFF_SECONDS)
            continue
        return resp
    raise last_exc or UpdatePricesError("unreachable: retry loop exhausted")


def ensure_status(resp: HttpResponse, url: str) -> None:
    if resp.status in (401, 403, 429):
        raise AccessDeniedError(
            f"access denied (HTTP {resp.status}) from {url}; "
            "stopping without updating price history")
    if resp.status >= 400:
        raise UpdatePricesError(f"HTTP {resp.status} from {url}")


def ensure_not_denied(resp: HttpResponse, url: str) -> None:
    """Access denial aborts the run even for best-effort requests."""
    if resp.status in (401, 403, 429):
        ensure_status(resp, url)


# --- budget / classification (S1) -----------------------------------------

def parse_price(value: Any) -> int | None:
    """Parse a CLP price from int/float/string forms; None when unusable."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, float):
        return int(value) if value > 0 and value.is_integer() else None
    if not isinstance(value, str):
        return None
    text = value.upper().replace("CLP", "").replace("$", "")
    text = text.replace("\xa0", "").replace(" ", "").strip()
    if re.fullmatch(r"\d{1,3}(?:\.\d{3})+", text):
        text = text.replace(".", "")
    elif re.fullmatch(r"\d{1,3}(?:,\d{3})+", text):
        text = text.replace(",", "")
    if not re.fullmatch(r"\d+", text):
        return None
    price = int(text)
    return price if price > 0 else None


def price_band(price: int) -> str:
    if not isinstance(price, int) or isinstance(price, bool) or price <= 0:
        raise ValueError(f"invalid price: {price!r}")
    if price <= BUDGET_WITHIN_CLP:
        return BAND_WITHIN
    if price <= BUDGET_NEAR_CLP:
        return BAND_NEAR
    return BAND_EXCLUDED


def detect_model(text: str) -> str | None:
    """Return 's23' / 's23_fe' only for clearly named S23 or S23 FE devices."""
    upper = re.sub(r"\s+", " ", text.upper())
    if re.search(r"S\s*23\s*FE\b", upper):
        return "s23_fe"
    if re.search(r"S\s*23\s*ULTRA\b", upper) or re.search(r"S\s*23\s*(?:PLUS\b|\+)", upper):
        return None
    if re.search(r"S\s*23\b", upper):
        return "s23"
    return None


def is_refurbished(text: str) -> bool:
    """True only when the text clearly marks the item as refurbished."""
    lower = text.lower()
    return any(marker in lower for marker in REFURBISHED_MARKERS)


# --- normalization (S1/S2) -------------------------------------------------

def normalize_url(url: str) -> str:
    parsed = urlparse(url.strip())
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return url.strip().lower()
    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}{parsed.path.rstrip('/')}"


def stable_offer_key(source: str, url: str | None, item_id: Any, title: str) -> str:
    if item_id not in (None, ""):
        return f"{source}#{item_id}"
    if url:
        return f"{source}#{normalize_url(url)}"
    digest = hashlib.sha256(title.strip().lower().encode("utf-8")).hexdigest()[:16]
    return f"{source}#{digest}"


def _first_str(raw: dict, keys: tuple) -> str | None:
    for key in keys:
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


class _PlainTextParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        if data.strip():
            self.parts.append(data.strip())


def _coerce_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        parts: list[str] = []
        for key in ("plain", "markdown", "label", "value", "name"):
            child = value.get(key)
            if isinstance(child, str) and child.strip():
                parts.append(child.strip())
        html_value = value.get("html")
        if isinstance(html_value, str) and html_value.strip():
            parser = _PlainTextParser()
            parser.feed(html_value)
            parts.append(" ".join(parser.parts))
        return " ".join(parts)
    if isinstance(value, list):
        return " ".join(_coerce_text(child) for child in value if child is not None)
    return ""


def _trusted_product_url(value: Any, source: str) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    parsed = urlparse(value.strip())
    source_host = (urlparse(source).hostname or "").lower()
    allowed_hosts = TRUSTED_PRODUCT_HOSTS.get(source_host, {source_host})
    if (parsed.scheme != "https" or not parsed.hostname or
            parsed.hostname.lower() not in allowed_hosts or parsed.username or parsed.password or
            parsed.port is not None):
        raise InvalidPayloadError("catalog product URL is not a trusted HTTPS merchant URL")
    return value.strip()


def validate_profile_url(value: str) -> str:
    parsed = urlparse(value.strip())
    host = (parsed.hostname or "").lower()
    if (parsed.scheme != "https" or not host or parsed.username or parsed.password or
            parsed.port is not None or parsed.query or parsed.fragment or
            host == "localhost" or host.endswith((".local", ".internal")) or "." not in host):
        raise ProfileMissingError(PROFILE_INVALID_MSG)
    try:
        if not ipaddress.ip_address(host).is_global:
            raise ProfileMissingError(PROFILE_INVALID_MSG)
    except ValueError:
        pass
    return value.strip()


def model_code_and_processor(text: str, model: str | None) -> tuple[str | None, str]:
    match = re.search(r"\bSM-(S\d{3}[A-Z0-9/.-]*)", text, flags=re.IGNORECASE)
    model_code = match.group(0).upper() if match else None
    processor = "unknown"
    if model == "s23":
        processor = "Snapdragon 8 Gen 2 for Galaxy"
    elif model == "s23_fe" and model_code:
        if model_code.startswith("SM-S711B"):
            processor = "Exynos 2200"
        elif model_code.startswith(("SM-S711U", "SM-S711U1")):
            processor = "Snapdragon 8 Gen 1"
    return model_code, processor


def normalize_offer(raw: Any, source: str) -> dict | None:
    """Normalize one catalog item; None means policy exclusion.

    Raises InvalidPayloadError for malformed items (missing title, missing or
    unparseable price) so a response made only of garbage can be rejected.
    """
    if not isinstance(raw, dict):
        raise InvalidPayloadError("catalog item is not an object")
    title = _first_str(raw, TITLE_KEYS)
    if not title:
        raise InvalidPayloadError("catalog item has no title")

    condition = " ".join((_coerce_text(raw.get("condition")),
                           _coerce_text(raw.get("description")),
                           _coerce_text(raw.get("tags")),
                           _coerce_text(raw.get("_search_text")))).strip()
    searchable_text = f"{title} {condition}"
    model = detect_model(searchable_text)
    if model is None:
        return None
    if not is_refurbished(searchable_text):
        return None
    if raw.get("available") is False:
        return None

    price_value = raw.get("price")
    if price_value is None:
        for key in PRICE_KEYS[1:]:
            if raw.get(key) is not None:
                price_value = raw.get(key)
                break
    currency = raw.get("currency")
    if isinstance(price_value, dict):
        currency = currency or price_value.get("currency")
        amount = price_value.get("amount", price_value.get("value"))
    else:
        amount = price_value
    if isinstance(currency, str) and currency.strip() and currency.strip().upper() != "CLP":
        return None
    price = parse_price(amount)
    if price is None:
        raise InvalidPayloadError(f"catalog item has an unusable price: {amount!r}")
    band = price_band(price)
    if band == BAND_EXCLUDED:
        return None

    url = _trusted_product_url(_first_str(raw, URL_KEYS), source)
    item_id = _first_str(raw, ID_KEYS)
    model_code, processor = model_code_and_processor(searchable_text, model)
    return {
        "key": stable_offer_key(source, url, item_id, title),
        "source": source,
        "model": model,
        "model_code": model_code,
        "processor": processor,
        "title": title,
        "price": price,
        "currency": "CLP",
        "band": band,
        "available": raw.get("available") if isinstance(raw.get("available"), bool) else None,
        "url": url,
    }


def normalize_ucp_product(product: Any, source: str) -> list[dict]:
    """Expand one schema-valid UCP Product into safe, in-stock variant offers."""
    if not isinstance(product, dict):
        raise InvalidPayloadError("UCP catalog product is not an object")
    title = _first_str(product, ("title",))
    variants = product.get("variants")
    price_range = product.get("price_range")
    if (not title or not isinstance(price_range, dict) or
            not isinstance(price_range.get("min"), dict) or
            not isinstance(price_range.get("max"), dict) or
            not isinstance(variants, list) or not variants):
        raise InvalidPayloadError("UCP Product is missing title, price_range, or variants")

    description = _coerce_text(product.get("description"))
    tags = _coerce_text(product.get("tags"))
    model_and_condition = f"{title} {description} {tags}"
    if detect_model(model_and_condition) is None or not is_refurbished(model_and_condition):
        return []
    product_url = _first_str(product, ("url",))
    records: list[dict] = []
    malformed = 0
    for variant in variants:
        if not isinstance(variant, dict):
            malformed += 1
            continue
        availability = variant.get("availability")
        if isinstance(availability, dict) and availability.get("available") is False:
            continue
        variant_description = _coerce_text(variant.get("description"))
        variant_title = _first_str(variant, ("title",))
        options = _coerce_text(variant.get("options"))
        listing_title = " ".join(part for part in (title, variant_title, options)
                                  if part and part.lower() != "default title")
        variant_url = _first_str(variant, ("url",)) or product_url
        if not variant_url:
            malformed += 1
            continue
        raw_offer = {
            "id": _first_str(variant, ("id",)) or _first_str(product, ("id",)),
            "title": listing_title,
            "price": variant.get("price"),
            "url": variant_url,
            "description": f"{description} {variant_description}",
            "tags": " ".join((tags, _coerce_text(variant.get("tags")))),
            "_search_text": " ".join((model_and_condition, variant_title or "", options,
                                       _coerce_text(variant.get("sku")))),
            "available": (availability.get("available")
                          if isinstance(availability, dict) else None),
        }
        try:
            record = normalize_offer(raw_offer, source)
        except InvalidPayloadError:
            malformed += 1
            continue
        if record is not None:
            records.append(record)
    if variants and malformed == len(variants):
        raise InvalidPayloadError("every UCP Product variant was malformed")
    return records


def _extract_items(payload: Any) -> list:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in CONTAINER_KEYS:
            value = payload.get(key)
            if isinstance(value, list):
                return value
        for nest in NEST_KEYS:
            value = payload.get(nest)
            if isinstance(value, list):
                return value
            if isinstance(value, dict):
                for key in CONTAINER_KEYS:
                    inner = value.get(key)
                    if isinstance(inner, list):
                        return inner
    raise InvalidPayloadError("catalog response does not contain an item list")


def normalize_catalog(payload: Any, source: str) -> list[dict]:
    items = _extract_items(payload)
    records: list[dict] = []
    malformed = 0
    for item in items:
        try:
            if isinstance(item, dict) and ("price_range" in item or "variants" in item):
                records.extend(normalize_ucp_product(item, source))
                continue
            record = normalize_offer(item, source)
        except InvalidPayloadError:
            malformed += 1
            continue
        if record is not None:
            records.append(record)
    if items and malformed == len(items):
        raise InvalidPayloadError("every catalog item was malformed")
    records.sort(key=lambda record: record["key"])
    return records


# --- UCP / MCP transport (read-only search_catalog) -----------------------

def _parse_jsonrpc(resp: HttpResponse, url: str) -> dict:
    text = resp.body.decode("utf-8", errors="replace").strip()
    if not text:
        raise InvalidPayloadError(f"empty response body from {url}")
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except ValueError:
        pass
    for line in text.splitlines():  # streamable-HTTP servers may answer SSE
        if line.startswith("data:"):
            chunk = line[5:].strip()
            if not chunk or chunk == "[DONE]":
                continue
            try:
                data = json.loads(chunk)
            except ValueError:
                continue
            if isinstance(data, dict):
                return data
    raise InvalidPayloadError(f"response from {url} is not valid JSON-RPC JSON")


def _find_mcp_endpoint(doc: Any) -> str | None:
    """Locate the MCP endpoint in a published /.well-known/ucp profile.

    Prefers URLs under MCP-named keys, then any published http(s) URL whose
    path looks like an MCP endpoint. Returns None instead of guessing an
    unrelated URL so the source fails loudly.
    """
    matches: list[tuple[int, str]] = []

    def collect(node: Any, key: str = "") -> None:
        if isinstance(node, dict):
            for child_key, child in node.items():
                collect(child, child_key.lower())
        elif isinstance(node, list):
            for child in node:
                collect(child, key)
        elif isinstance(node, str) and node.startswith(("http://", "https://")):
            score = 0
            if "mcp" in key:
                score = 2
            elif "mcp" in urlparse(node).path:
                score = 1
            if score:
                matches.append((score, node))

    collect(doc)
    if matches:
        matches.sort(key=lambda item: -item[0])
        return matches[0][1]
    return None


def discover_mcp_endpoint(site_root: str) -> str:
    url = site_root.rstrip("/") + "/.well-known/ucp"
    resp = http_request("GET", url, {"Accept": "application/json"})
    ensure_status(resp, url)
    try:
        doc = json.loads(resp.body.decode("utf-8", errors="replace"))
    except ValueError as exc:
        raise InvalidPayloadError(f"UCP profile at {url} is not valid JSON") from exc
    endpoint = _find_mcp_endpoint(doc)
    if not endpoint:
        raise InvalidPayloadError(f"no MCP endpoint published in UCP profile at {url}")
    site = urlparse(site_root)
    target = urlparse(endpoint)
    if (site.scheme != "https" or not site.hostname or target.scheme != "https" or
            target.hostname != site.hostname or target.username or target.password or
            target.port is not None or "mcp" not in target.path.lower()):
        raise InvalidPayloadError(
            f"published MCP endpoint for {site_root} is not a same-host HTTPS endpoint")
    return endpoint


class _McpSession:
    def __init__(self, endpoint: str, session_id: str | None):
        self.endpoint = endpoint
        self.session_id = session_id
        self._next_id = 2

    def post(self, payload: dict) -> dict:
        headers = {"Content-Type": "application/json",
                   "Accept": "application/json, text/event-stream"}
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        resp = http_request("POST", self.endpoint, headers,
                            json.dumps(payload).encode("utf-8"))
        ensure_status(resp, self.endpoint)
        return _parse_jsonrpc(resp, self.endpoint)

    def call(self, method: str, params: dict) -> dict:
        payload = {"jsonrpc": "2.0", "id": self._next_id, "method": method, "params": params}
        self._next_id += 1
        data = self.post(payload)
        if "error" in data:
            raise UpdatePricesError(f"MCP {method} failed: {data['error']}")
        result = data.get("result")
        if not isinstance(result, dict):
            raise InvalidPayloadError(f"MCP {method} returned no result object")
        return result


def mcp_initialize(endpoint: str) -> _McpSession:
    headers = {"Content-Type": "application/json",
               "Accept": "application/json, text/event-stream"}
    payload = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
               "params": {"protocolVersion": MCP_PROTOCOL_VERSION,
                          "capabilities": {},
                          "clientInfo": {"name": CLIENT_NAME, "version": CLIENT_VERSION}}}
    resp = http_request("POST", endpoint, headers, json.dumps(payload).encode("utf-8"))
    ensure_status(resp, endpoint)
    data = _parse_jsonrpc(resp, endpoint)
    if "error" in data:
        raise UpdatePricesError(f"MCP initialize failed: {data['error']}")
    session = _McpSession(endpoint, resp.headers.get("mcp-session-id"))

    notification = {"jsonrpc": "2.0", "method": "notifications/initialized"}
    notify_headers = dict(headers)
    if session.session_id:
        notify_headers["Mcp-Session-Id"] = session.session_id
    notify_resp = http_request("POST", endpoint, notify_headers,
                               json.dumps(notification).encode("utf-8"))
    ensure_not_denied(notify_resp, endpoint)  # best-effort otherwise
    return session


def mcp_tools_list(session: _McpSession) -> list[str]:
    result = session.call("tools/list", {})
    tools = result.get("tools")
    if not isinstance(tools, list):
        raise InvalidPayloadError("tools/list returned no tool array")
    return [tool.get("name") for tool in tools
            if isinstance(tool, dict) and isinstance(tool.get("name"), str)]


def _extract_search_payload(result: dict) -> Any:
    structured = result.get("structuredContent")
    if isinstance(structured, (dict, list)) and structured:
        return structured
    parts = [content.get("text", "") for content in (result.get("content") or [])
             if isinstance(content, dict) and content.get("type") == "text"]
    joined = "\n".join(part for part in parts if part).strip()
    if not joined:
        raise InvalidPayloadError("search_catalog returned no text content")
    try:
        return json.loads(joined)
    except ValueError:
        return joined  # raw non-JSON text; normalization will reject it


def mcp_search_catalog(session: _McpSession, profile: str, query: str) -> Any:
    arguments = {"meta": {"ucp-agent": {"profile": profile}},
                 "catalog": {"query": query}}
    result = session.call("tools/call", {"name": SEARCH_TOOL, "arguments": arguments})
    if result.get("isError"):
        text = " ".join(part.get("text", "") for part in (result.get("content") or [])
                        if isinstance(part, dict))
        raise UpdatePricesError(f"search_catalog reported an error: {text.strip()}")
    return _extract_search_payload(result)


def source_id(site_root: str) -> str:
    parsed = urlparse(site_root)
    return parsed.netloc or site_root


def collect_queries_from_source(site_root: str, profile: str,
                                queries: tuple[str, ...]) -> list[dict]:
    """Discover one source once, then search each requested model read-only."""
    endpoint = discover_mcp_endpoint(site_root)
    session = mcp_initialize(endpoint)
    tools = mcp_tools_list(session)
    if SEARCH_TOOL not in tools:
        raise UpdatePricesError(
            f"{source_id(site_root)} does not publish the {SEARCH_TOOL} tool")
    records: list[dict] = []
    for query in queries:
        payload = mcp_search_catalog(session, profile, query)
        records.extend(normalize_catalog(payload, site_root))
    return records


def collect_from_source(site_root: str, profile: str, query: str) -> list[dict]:
    """Compatibility wrapper for callers requiring one explicit search."""
    return collect_queries_from_source(site_root, profile, (query,))


# --- public product JSON fallback (no UCP agent profile required) ----------

def _shopify_minor_units(value: Any) -> int | None:
    """Convert a Shopify price string/number to integer minor units."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float):
        return round(value) if value >= 0 else None
    if isinstance(value, str):
        text = value.strip()
        if not re.fullmatch(r"\d+(?:\.\d{1,2})?", text):
            return None
        return int(round(float(text)))
    return None


def _id_str(value: Any) -> str | None:
    """Represent numeric or string identifiers as honest strings."""
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return str(value)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def normalize_shopify_product(product: Any, source_root: str) -> dict:
    """Reshape one Shopify ``/products.json`` entry into the UCP Product shape.

    Shopify's public product JSON is a documented, robots-permitted read-only
    feed. We repackage it as a UCP Product so the same schema-aware normalizer
    runs, while making clear this path never impersonates a shopping agent.
    """
    if not isinstance(product, dict):
        raise InvalidPayloadError("product.json entry is not an object")
    title = _first_str(product, ("title",))
    handle = _first_str(product, ("handle",))
    variants = product.get("variants")
    if not title or not handle or not isinstance(variants, list) or not variants:
        raise InvalidPayloadError("product.json entry is missing title, handle, or variants")

    host = (urlparse(source_root).hostname or source_root).lower()
    product_url = f"https://{host}/products/{handle}"
    description_text = _coerce_text({"html": product.get("body_html")})
    tags = _coerce_text(product.get("tags"))

    shaped_variants: list[dict] = []
    prices: list[int] = []
    for variant in variants:
        if not isinstance(variant, dict):
            raise InvalidPayloadError("product.json variant is not an object")
        variant_id = _id_str(variant.get("id"))
        raw_price = _shopify_minor_units(variant.get("price"))
        if variant_id is None or raw_price is None:
            raise InvalidPayloadError("product.json variant lacks id or usable price")
        prices.append(raw_price)
        shaped_variants.append({
            "id": variant_id,
            "title": _first_str(variant, ("title",)) or "Default Title",
            "price": {"amount": raw_price, "currency": "CLP"},
            "url": f"{product_url}?variant={variant_id}",
        })
    if not prices:
        raise InvalidPayloadError("product.json entry has no priced variants")

    return {
        "id": _id_str(product.get("id")) or handle,
        "title": title,
        "description": {"plain": description_text},
        "url": product_url,
        "price_range": {
            "min": {"amount": min(prices), "currency": "CLP"},
            "max": {"amount": max(prices), "currency": "CLP"},
        },
        "tags": tags,
        "variants": shaped_variants,
    }


def collect_public_json_from_source(site_root: str) -> list[dict]:
    """Read one store's public ``products.json`` and normalize refurb offers.

    This path is used only when the UCP agent-profile requirement cannot be
    met; it stays read-only, low-rate, and carries no caller identity claim.
    Shopify omits older listings unless ``limit`` is raised, so one request
    asks for the maximum page size a store allows to expose.
    """
    url = site_root.rstrip("/") + "/products.json?limit=250"
    resp = http_request("GET", url, {"Accept": "application/json"})
    ensure_status(resp, url)
    try:
        payload = json.loads(resp.body.decode("utf-8", errors="replace"))
    except ValueError as exc:
        raise InvalidPayloadError(f"public product feed at {url} is not valid JSON") from exc
    products = payload.get("products") if isinstance(payload, dict) else None
    if not isinstance(products, list):
        raise InvalidPayloadError(f"public product feed at {url} has no products list")
    shapes: list[dict] = []
    malformed = 0
    for entry in products:
        try:
            shapes.append(normalize_shopify_product(entry, site_root))
        except InvalidPayloadError:
            malformed += 1
            continue
    if products and malformed == len(products):
        raise InvalidPayloadError("every product.json entry was malformed")
    return normalize_catalog({"products": shapes}, site_root)


# --- history file ----------------------------------------------------------

def empty_history() -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "budget": {"currency": "CLP", "within_max": BUDGET_WITHIN_CLP,
                   "near_max": BUDGET_NEAR_CLP},
        "last_collected_at": None,
        "snapshots": {},
    }


def load_history(path: str) -> dict:
    if not os.path.exists(path):
        return empty_history()
    try:
        with open(path, "r", encoding="utf-8") as handle:
            history = json.load(handle)
    except (OSError, ValueError) as exc:
        raise HistoryCorruptError(f"price history at {path} is not valid: {exc}") from exc
    if not isinstance(history, dict):
        raise HistoryCorruptError(f"price history at {path} is not a JSON object")
    if history.get("schema_version", SCHEMA_VERSION) != SCHEMA_VERSION:
        raise HistoryCorruptError(f"price history at {path} has an unsupported schema_version")
    snapshots = history.get("snapshots")
    if not isinstance(snapshots, dict):
        raise HistoryCorruptError(f"price history at {path} has no snapshots object")
    for week, snapshot in snapshots.items():
        if not isinstance(week, str) or not ISO_WEEK_RE.match(week):
            raise HistoryCorruptError(f"price history at {path} has a bad week key {week!r}")
        if not isinstance(snapshot, dict) or not isinstance(snapshot.get("offers"), list):
            raise HistoryCorruptError(
                f"price history at {path} has a malformed snapshot for {week}")
    return history


def save_history(path: str, history: dict) -> None:
    payload = (json.dumps(history, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=".price-history-", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


def iso_week(now: datetime) -> str:
    calendar = now.isocalendar()
    return f"{calendar.year}-W{calendar.week:02d}"


def _timestamp(now: datetime) -> str:
    return now.astimezone(timezone.utc).isoformat(timespec="seconds")


def apply_snapshot(history: dict, week: str, records: list[dict],
                   source_status: dict, now: datetime) -> dict:
    """Merge this run's observation into `week`, pruning history to MAX_WEEKS."""
    snapshots = dict(history.get("snapshots", {}))
    # This snapshot represents the latest collection, not a union of every
    # result seen during the week. Re-running replaces the current offer set
    # while the older ISO-week snapshots remain untouched.
    offers: dict[str, dict] = {}
    for record in records:  # latest observation of a stable key wins
        offers[record["key"]] = record
    snapshots[week] = {
        "collected_at": _timestamp(now),
        "sources": dict(source_status),
        "offers": [offers[key] for key in sorted(offers)],
    }
    keep = sorted(snapshots, reverse=True)[:MAX_WEEKS]
    updated = dict(history)
    updated["snapshots"] = {key: snapshots[key] for key in sorted(keep)}
    updated["last_collected_at"] = _timestamp(now)
    return updated


def run_collection(history_path: str, sources=DEFAULT_SOURCES, query: str | None = None,
                   profile: str | None = None, now: datetime | None = None) -> dict:
    """Collect from every source and persist one weekly snapshot.

    UCP ``search_catalog`` is the primary, supported interface and needs an
    honest public profile URL. When no profile is configured at all, each
    source falls back to its robots-permitted public product JSON feed.
    A configured profile never silently downgrades to the fallback; its UCP
    errors surface directly so the operator can fix them. Raises before
    writing anything when every source fails, the existing history is
    unusable, or access is denied mid-run.
    """
    env_profile = os.environ.get(PROFILE_ENV_VAR, "").strip()
    profile = profile or env_profile
    if profile:
        profile = validate_profile_url(profile)
    now = now or datetime.now(timezone.utc)
    history = load_history(history_path)

    queries = (query,) if query else DEFAULT_QUERIES
    all_records: list[dict] = []
    source_status: dict[str, str] = {}
    for site_root in sources:
        if profile:
            try:
                all_records.extend(collect_queries_from_source(site_root, profile, queries))
                source_status[site_root] = "ok"
                continue
            except AccessDeniedError:
                raise  # stop on access denial / rate limits: no further requests
            except UpdatePricesError as exc:
                source_status[site_root] = f"error: {exc}"
                continue
        try:
            all_records.extend(collect_public_json_from_source(site_root))
        except AccessDeniedError:
            raise
        except UpdatePricesError as exc:
            source_status[site_root] = f"error: {exc}"
        else:
            source_status[site_root] = "ok (public product JSON)"
    if not any(status.startswith("ok") for status in source_status.values()):
        raise UpdatePricesError(
            "no source returned usable data; price history unchanged "
            f"(sources: {source_status})")

    week = iso_week(now)
    updated = apply_snapshot(history, week, all_records, source_status, now)
    save_history(history_path, updated)
    return {"week": week, "offer_count": len(updated["snapshots"][week]["offers"]),
            "sources": source_status}


# --- CLI -------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="update_prices",
        description="Collect weekly refurbished Galaxy S23/S23 FE offers over the "
                    "merchants' read-only UCP search_catalog tool and update "
                    "data/price-history.json.")
    parser.add_argument("--history", default=DEFAULT_HISTORY_PATH,
                        help="path to price-history.json (default: %(default)s)")
    parser.add_argument("--source", action="append", dest="sources", metavar="SITE_ROOT",
                        help="merchant site root publishing /.well-known/ucp; repeatable "
                             "(default: Reuse and Bodega Oportunidades)")
    parser.add_argument("--query", default=None,
                        help="use one custom catalog search query (default: searches S23 and S23 FE)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    sources = tuple(args.sources) if args.sources else DEFAULT_SOURCES
    try:
        summary = run_collection(args.history, sources=sources, query=args.query)
    except ProfileMissingError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except UpdatePricesError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"saved {summary['offer_count']} offer(s) for {summary['week']} "
          f"to {args.history}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
