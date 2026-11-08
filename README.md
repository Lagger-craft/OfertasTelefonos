# S23 Precios — refurbished Galaxy S23 / S23 FE price tracker

Local static page plus a daily, standard-library-only collector that records
clearly refurbished Samsung Galaxy S23 and S23 FE offers inside the accepted
budget, and preserves them as dated snapshots for the on-page charts. The code
is published at github.com/Lagger-craft/OfertasTelefonos, but all automation
stays on this machine: no GitHub Actions, no automatic remote commits.

## Budget bands (CLP)

| Band | Range (defaults) | Meaning |
| --- | --- | --- |
| `within_budget` | up to 340,000 | through the price cap you accept |
| `near_budget` | 340,001 – 370,000 | stretch band: how far you would stretch the wallet |
| excluded | above the near cap | not recorded |

The caps are yours to set; the values above are the defaults. Pass
`--within-max CLP` and `--near-max CLP` (the stretch band must be strictly
greater than the cap), or set `S23_WITHIN_MAX` / `S23_NEAR_MAX` in the
timer's environment file. The configured caps travel into
`data/price-history.json` (`budget`), and the page reads them from there —
no page hardcoded values.

Tracked models: **Galaxy S23**, **Galaxy S23 FE**, **Galaxy S23+** and
**Galaxy S24 FE**, all clearly identified as refurbished
(`reacondicionado`, `refurbished`, `remanufactured`); items described merely
as used / seminuevo / segunda mano are excluded, and so are the S23 Ultra,
the base S24, S24+, S24 Ultra and any other non-tracked model.

## Collector

`scripts/update_prices.py` (Python 3, standard library only):

- Discovers each merchant's MCP endpoint dynamically from its public
  `/.well-known/ucp` profile, then performs an anonymous MCP `initialize`
  (`protocolVersion: 2025-03-26`), `tools/list`, and a read-only
  `tools/call` of the `search_catalog` tool only — never cart/checkout/order
  tools.
- Requests are sequential and low-rate. Each request retries at most once
  after a 60-second pause, and only for transient failures (network errors or
  HTTP 5xx); access denial or rate limiting (HTTP 401/403/429) is never
  retried and stops the run immediately without touching the history file.
- Parses the published UCP Product shape: prices come from individual
  `variants[].price` values (CLP), unavailable variants are excluded, and
  product/variant descriptions are inspected only to confirm the device and
  refurbished status. It records an Exynos/Snapdragon only when an exact model
  code identifies the variant.
- Saves snapshots into `data/price-history.json` keyed by ISO week
  (`2026-W07`) and by a stable offer key (merchant id, else normalized URL,
  else a title hash). Re-running in the same week replaces that week's current
  offer set, so disappeared offers are not left looking current; older weekly
  snapshots remain. History is bounded to the newest 52 weeks and written
  atomically (temp file + `os.replace`), so a rejected or invalid response
  cannot corrupt existing JSON. A run where every source fails exits non-zero
  and leaves the file byte-identical.

### Required setup: UCP_AGENT_PROFILE_URL

The project includes `.well-known/ucp.json`, an honest Pixel Price agent
profile that advertises **catalog search only**—no cart, checkout, order, or
payment capability. After hosting, set `UCP_AGENT_PROFILE_URL` to that exact
public HTTPS URL. The MCP request uses it as the caller identity
(`meta["ucp-agent"].profile`); the collector never borrows a merchant's
identity, invents a default profile, or stores credentials in browser code or
price history. If the variable is absent or invalid, collection fails before
network access and leaves history unchanged.

The local `.well-known/ucp.json` does not satisfy the requirement by itself:
merchants reject or ignore non-public profile URLs, and pointing the variable
at a `file://` or LAN address makes collection fail before any request. Once
that URL exists, run:

```bash
UCP_AGENT_PROFILE_URL="https://your-profile-host.example/ucp.json" \
  python3 scripts/update_prices.py
```

Optional flags: `--history PATH`, `--source SITE_ROOT` (repeatable),
`--query TEXT`, `--within-max CLP` (price cap; env `S23_WITHIN_MAX`),
`--near-max CLP` (stretch band; env `S23_NEAR_MAX`), and `--models KEYS`
(comma-separated tracked model keys, e.g. `s23,s24_fe`; env `S23_MODELS`;
default: all four tracked models). The searches still cover every tracked
model; `--models` only narrows which recorded offers are saved.

### Manufacturer support windows shown on the page

The dashboard reports how much time is left of Samsung's security updates per
tracked model (S23 / S23+ / S23 FE: security until 2028-02, 4 OS + 5 years;
S24 FE: 7 years of OS and security, security until 2031-09). The dashboard
computes the remaining months each time you open the page.

### Scheduled collection (daily systemd user timer)

Keep the project running locally with a lightweight daily systemd user timer,
so a failed attempt no longer loses the week: re-running on another day of the
same week replaces that week's current offer set. Install the units from
`systemd/` (edit `WorkingDirectory=` in `s23-prices.service` if you move the
project):

```bash
cp systemd/s23-prices.service systemd/s23-prices.timer ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now s23-prices.timer
```

The timer runs `scripts/update_prices.py` every day at 09:45
(`Persistent=true`, so a missed run happens on the next boot). Put
`UCP_AGENT_PROFILE_URL=...` in `~/.config/s23precios/env` (that file stays
outside the project) to use the supported UCP interface; without it the
collectors use their robots-permitted public products.json fallback. Inspect
runs with `journalctl --user -u s23-prices.service` and the schedule with
`systemctl --user list-timers s23-prices.timer`.

Never point `UCP_AGENT_PROFILE_URL` at a local file or private address; the
collector rejects those before making any request.

## Sources and current limitations

- Primary path: for each of **Reuse**, **Bodega Oportunidades**,
  **DL Phone** (dlphone.cl) and **Comprala** (comprala.cl), the collector
  uses the merchant's supported UCP/MCP interface (via its
  `/.well-known/ucp` profile and the read-only `search_catalog` tool
  declared in
  `https://ucp.dev/2026-08-25/services/shopping/mcp.openrpc.json`). That path
  requires a truthful, publicly hosted caller profile URL (see above).
- DL Phone and Comprala publish genuine UCP profiles (MCP endpoints
  `https://d8f7f8-76.myshopify.com/api/ucp/mcp` and
  `https://comprala-cl.myshopify.com/api/ucp/mcp`); their robots.txt permits
  the public product feed and UCP discovery, and product URLs across both
  each store domain and its Shopify backend domain are trusted.
- gsmpro.cl was ruled out for now: it did not respond to robots/ucp probes.
  Falabella, Paris and Knasta are marketplace aggregators intentionally not
  integrated (no robots-permitted public product feed and their own terms).
- Fallback path (no profile configured): the collector reads the same store's
  public Shopify `products.json` feed, which robots.txt explicitly permits.
  It stays read-only, low-rate, and never pretends to be a shopping agent.
- **Mercado Libre** is not used: its official API's anonymous search
  returned 403 and would require an app token.
- The first real snapshot (2026-W45) was collected through the fallback path on
  this machine. When a public UCP agent-profile URL becomes available, rerun
  with `UCP_AGENT_PROFILE_URL` set to switch to the supported UCP interface.
- Access denied (HTTP 401/403/429) or a malformed feed stops the run and
  leaves `data/price-history.json` untouched.
- Profile discovery handles common JSON shapes (MCP endpoint under MCP-named
  keys or a published `.../mcp` URL). If a merchant changes its profile
  schema, that source fails loudly and is recorded as an error in the
  snapshot while other sources still run.

## Local preview

Serve the folder over HTTP so the browser can fetch the JSON history file:

```bash
./start-website.sh
```

That one-liner starts `python3 -m http.server 8000` and opens
`http://localhost:8000/`. Opening `index.html` directly as `file://` blocks
the data fetch in most browsers, and the page explains this when detected.

## Tests

Run from the `S23Precios` directory:

```bash
python3 -m unittest discover -s tests -v
node --test tests/dashboard-core.test.cjs
```

Python tests cover S1/S2: refurbished classification, exact budget boundaries,
UCP request parsing and variant normalization, Shopify `products.json`
repackaging and its fallback run, processor mapping, trusted links, failure
isolation, access-denial stops, and idempotent bounded weekly history. Node
tests cover the dashboard's budget bands, latest-snapshot selection, current
offers, price histogram, trend series, processor labeling, and model price
comparisons. All automated tests use fixtures and make no network calls.

## Data format

```json
{
  "schema_version": 1,
  "budget": {"currency": "CLP", "within_max": 340000, "near_max": 370000},
  "last_collected_at": "2026-02-09T12:00:00+00:00",
  "snapshots": {
    "2026-W07": {
      "collected_at": "2026-02-09T12:00:00+00:00",
      "sources": {"https://reusecl.myshopify.com": "ok"},
      "offers": [
        {"key": "...", "source": "...", "model": "s23_fe", "model_code": "SM-S711B",
         "processor": "Exynos 2200", "title": "...", "price": 340000,
         "currency": "CLP", "band": "within_budget", "available": true, "url": "..."}
      ]
    }
  }
}
```
