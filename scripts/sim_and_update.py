#!/usr/bin/env python3
"""
Stocks sim with:
- Mean reversion to an anchor (punishes bubbles)
- Random shock/spike days every few days (down-biased)
- Bear regimes (temporary higher vol + negative drift)
- Weekly decay/rebase (holding cost)
- JSON-persisted meta so behavior carries across days
- Patches UnbelievaBoat store item price/description
- Maintains Chart.js page + JSON under docs/

Env:
  UNB_TOKEN      (required)  — UnbelievaBoat Application Token
  UNB_GUILD_ID   (optional)  — defaults to 1219525577950888036
  UNB_ITEM_NAME  (optional)  — defaults to "Tesla Stock"
  UNB_ITEM_ID    (optional)  — if set, skip name lookup
  PAGES_URL      (optional)  — defaults to https://vesstra.github.io/discord-tesla-stock-sim/
"""

import os, json, math, random, datetime, requests, pathlib, sys
from typing import Any, Dict, List, Tuple

# ------------------ Required / Defaults ------------------
UNB_TOKEN   = os.environ.get("UNB_TOKEN")  # MUST be set in Actions secrets
GUILD_ID    = os.environ.get("UNB_GUILD_ID", "1219525577950888036")
ITEM_NAME   = os.environ.get("UNB_ITEM_NAME", "Tesla Stock")
ITEM_ID_OVR = os.environ.get("UNB_ITEM_ID")  # if provided, skip lookup
PAGES_URL   = os.environ.get("PAGES_URL", "https://vesstra.github.io/discord-tesla-stock-sim/")

HISTORY_PATH = pathlib.Path("docs/tesla_history.json")
INDEX_PATH   = pathlib.Path("docs/index.html")

# ------------------ Base model (calmer daily) ------------------
START_PRICE = 1000.0
DRIFT       = 0.0002   # ~+0.02% avg/day (small upward bias)
VOL         = 0.03     # ~3% daily vol
MIN_PRICE   = 1

# Mean reversion
ANCHOR   = 1000.0      # long-run "fair value" (can drift over time if you want)
REVERT_K = 0.12        # pull strength: 0.05–0.20 is typical

# ------------------ Shocks (discourage "just hold") ------------------
# Shock every few days: pick one of the ranges randomly each time
SHOCK_INTERVAL_RANGES: List[Tuple[int, int]] = [(2, 3), (4, 5)]
SHOCK_PCT_MIN = 0.10   # 10%
SHOCK_PCT_MAX = 0.25   # 25%
SHOCK_UP_PROB = 0.35   # 35% up / 65% down (down bias)

# ------------------ Bear regimes ------------------
BEAR_PROB  = 0.15           # chance to enter bear on any day (when not already in one)
BEAR_DAYS  = (2, 5)         # length range (inclusive)
BEAR_DRIFT = -0.002         # -0.2%/day during bear
BEAR_VOL   = 0.05           # 5% daily vol during bear

# ------------------ Weekly decay / rebase ------------------
REBASE_DAY = 6              # Sunday (Mon=0 .. Sun=6)
REBASE_PCT = 0.01           # 1% weekly holding cost

# ------------------ Backfill on first run ------------------
BACKFILL_DAYS = 30          # seed more days so chart draws a line immediately

# -----------------------------------------------------------

def die(msg: str, extra: str = "") -> None:
    print(f"[ERROR] {msg}")
    if extra:
        print(extra)
    sys.exit(1)

def ensure_site_files() -> None:
    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not HISTORY_PATH.exists():
        HISTORY_PATH.write_text(json.dumps({
            "symbol": "TSLA",
            "name": ITEM_NAME,
            "unit": "chips",
            "meta": {
                "next_shock_in": None,
                "bear_left": 0
            },
            "history": []
        }, indent=2), encoding="utf-8")

    if not INDEX_PATH.exists():
        INDEX_PATH.write_text(f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>{ITEM_NAME} — Chips</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
  <style>
    body {{ font-family: system-ui, sans-serif; margin: 24px; background:#0b0b10; color:#eaeaea; }}
    .card {{ max-width: 900px; margin:auto; padding:20px; background:#151523; border-radius:16px; box-shadow:0 8px 24px rgba(0,0,0,.35); }}
    h1 {{ margin-top:0; font-weight:700; }}
    .muted {{ color:#a0a0b0; font-size:14px; }}
    canvas {{ width:100%; height:420px; }}
    a {{ color:#8ab4ff; }}
  </style>
</head>
<body>
  <div class="card">
    <h1>{ITEM_NAME} <span class="muted">(chips)</span></h1>
    <div id="price" class="muted">loading…</div>
    <canvas id="chart"></canvas>
    <p class="muted">Data auto-updates daily. JSON: <a href="tesla_history.json">tesla_history.json</a></p>
  </div>
  <script>
    async function run() {{
      const r = await fetch('tesla_history.json?ts=' + Date.now());
      const data = await r.json();
      const labels = data.history.map(p => p.date);
      const prices = data.history.map(p => p.price);
      document.getElementById('price').textContent =
        "Latest: " + prices.at(-1) + " chips (" + labels.at(-1) + ")";
      new Chart(document.getElementById('chart'), {{
        type: 'line',
        data: {{ labels, datasets: [{{ label: '{ITEM_NAME}', data: prices }}] }},
        options: {{
          responsive: true,
          elements: {{ point: {{ radius: 2 }} }},
          tension: 0.25,
          scales: {{ y: {{ beginAtZero: false }} }}
        }}
      }});
    }}
    run();
  </script>
</body>
</html>
""", encoding="utf-8")

def load_history() -> Dict[str, Any]:
    try:
        return json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        die("Failed to read docs/tesla_history.json", str(e))

def save_history(obj: Dict[str, Any]) -> None:
    try:
        HISTORY_PATH.write_text(json.dumps(obj, indent=2), encoding="utf-8")
    except Exception as e:
        die("Failed to write docs/tesla_history.json", str(e))

def draw_next_interval() -> int:
    lo, hi = random.choice(SHOCK_INTERVAL_RANGES)
    return random.randint(lo, hi)

def backfill_history(data: Dict[str, Any]) -> None:
    hist: List[Dict[str, Any]] = data.get("history", [])
    meta = data.setdefault("meta", {"next_shock_in": None, "bear_left": 0})
    if len(hist) >= 2:
        if meta.get("next_shock_in") is None:
            meta["next_shock_in"] = draw_next_interval()
        if "bear_left" not in meta:
            meta["bear_left"] = 0
        return

    today = datetime.date.today()
    start_date = today - datetime.timedelta(days=BACKFILL_DAYS - 1)
    d = start_date
    price = int(round(START_PRICE))

    # deterministic backfill for a nice initial curve
    state = random.getstate()
    random.seed(42)
    points: List[Dict[str, Any]] = []
    for _ in range(BACKFILL_DAYS):
        if points:
            price = max(MIN_PRICE, round(price * math.exp((DRIFT - 0.5 * VOL * VOL) + VOL * random.gauss(0, 1))))
        points.append({"date": d.isoformat(), "price": int(price)})
        d += datetime.timedelta(days=1)
    random.setstate(state)

    data["history"] = points
    meta["next_shock_in"] = draw_next_interval()
    meta["bear_left"] = 0

def simulate_step(prev_price: float, mu: float, sigma: float) -> int:
    """
    Mean-reverting GBM step:
      log S_{t+1} - log S_t = (mu - 0.5*sigma^2 + k*(log A - log S_t)) + sigma*Z
    """
    z = random.gauss(0, 1)
    gap = math.log(max(1e-9, ANCHOR)) - math.log(max(1e-9, prev_price))
    drift_term = (mu - 0.5 * sigma * sigma) + REVERT_K * gap
    step = math.exp(drift_term + sigma * z)
    price = max(MIN_PRICE, round(prev_price * step))
    return int(price)

def apply_shock(price: int) -> (int, float):
    pct = random.uniform(SHOCK_PCT_MIN, SHOCK_PCT_MAX)
    sign = +1 if random.random() < SHOCK_UP_PROB else -1
    new_price = int(max(MIN_PRICE, round(price * (1.0 + sign * pct))))
    return new_price, sign * pct  # signed percent

def ub_get(url: str) -> requests.Response:
    try:
        r = requests.get(url, headers={"Authorization": UNB_TOKEN}, timeout=30)
    except Exception as e:
        die(f"Network error calling GET {url}", str(e))
    if r.status_code == 403:
        die("403 Forbidden from UB API. Token not authorized for this guild or lacks ITEMS permission.",
            f"Endpoint: {url}\nResponse: {r.text[:400]}")
    if r.status_code == 401:
        die("401 Unauthorized from UB API. Check UNB_TOKEN.", f"Endpoint: {url}")
    if not r.ok:
        die(f"GET {url} failed with {r.status_code}", r.text[:400])
    return r

def ub_patch(url: str, body: Dict[str, Any]) -> requests.Response:
    try:
        r = requests.patch(
            url,
            headers={"Authorization": UNB_TOKEN, "Content-Type": "application/json"},
            data=json.dumps(body),
            timeout=30
        )
    except Exception as e:
        die(f"Network error calling PATCH {url}", str(e))
    if r.status_code == 403:
        die("403 Forbidden on PATCH. Token may lack ITEMS permission for this guild.",
            f"Endpoint: {url}\nBody: {json.dumps(body)[:400]}\nResponse: {r.text[:400]}")
    if r.status_code == 401:
        die("401 Unauthorized on PATCH. Check UNB_TOKEN.", f"Endpoint: {url}")
    if not r.ok:
        die(f"PATCH {url} failed with {r.status_code}", r.text[:400])
    return r

def find_item_id_by_name() -> str:
    url = f"https://unbelievaboat.com/api/v1/guilds/{GUILD_ID}/items"
    r = ub_get(url)
    try:
        data = r.json()
    except Exception:
        die("Unexpected non-JSON response from UB when listing items.", r.text[:400])

    if isinstance(data, dict) and "items" in data:
        items = data["items"]
    elif isinstance(data, list):
        items = data
    else:
        die("Unexpected payload when listing items. Are you authorized for this guild?", repr(data))

    if not isinstance(items, list):
        die("Items payload is not a list.", repr(items))

    for it in items:
        if not isinstance(it, dict):
            die("Item entry was not an object. Raw value:", repr(it))
        name = it.get("name", "")
        if name == ITEM_NAME or name.lower() == ITEM_NAME.lower():
            return str(it.get("id"))

    die(f'Item "{ITEM_NAME}" not found in guild {GUILD_ID}.',
        "Create it in the UB dashboard Store for this server first.")
    return ""

def patch_item_price(item_id: str, new_price: int, date_str: str, notes: List[str]) -> None:
    url = f"https://unbelievaboat.com/api/v1/guilds/{GUILD_ID}/items/{item_id}"
    suffix = " • ".join(notes) if notes else ""
    desc = f"{ITEM_NAME} • {int(new_price)} chips • Updated {date_str} • Chart: {PAGES_URL}"
    if suffix:
        desc = f"{desc} • {suffix}"
    body = {"price": int(new_price), "description": desc}
    ub_patch(url, body)

def main() -> None:
    if not UNB_TOKEN:
        die("UNB_TOKEN is not set. Add it as a GitHub Actions secret named UNB_TOKEN.")

    ensure_site_files()
    data = load_history()
    backfill_history(data)

    hist: List[Dict[str, Any]] = data["history"]
    meta: Dict[str, Any] = data.setdefault("meta", {"next_shock_in": None, "bear_left": 0})
    if meta.get("next_shock_in") is None:
        meta["next_shock_in"] = draw_next_interval()
    if "bear_left" not in meta:
        meta["bear_left"] = 0

    today = datetime.date.today()
    today_str = today.isoformat()
    notes: List[str] = []

    # Choose regime params
    if meta["bear_left"] > 0:
        mu, sigma = BEAR_DRIFT, BEAR_VOL
        meta["bear_left"] -= 1
        notes.append("🐻 bear regime")
    else:
        # chance to enter a new bear regime
        if random.random() < BEAR_PROB:
            meta["bear_left"] = random.randint(*BEAR_DAYS)
            mu, sigma = BEAR_DRIFT, BEAR_VOL
            notes.append("🐻 bear regime (new)")
        else:
            mu, sigma = DRIFT, VOL

    # Base move (mean-reverting)
    if hist and hist[-1]["date"] == today_str:
        price = int(hist[-1]["price"])
    else:
        price = simulate_step(float(hist[-1]["price"]), mu, sigma)

    # Shock day?
    nsi = int(meta.get("next_shock_in", 0))
    if nsi <= 0:
        price, pct_signed = apply_shock(price)
        notes.append(f"⚡ shock {pct_signed*100:+.1f}%")
        meta["next_shock_in"] = draw_next_interval()
    else:
        meta["next_shock_in"] = nsi - 1

    # Weekly rebase / decay (holding cost) — Sunday by default
    if today.weekday() == REBASE_DAY:
        before = price
        price = int(max(MIN_PRICE, round(price * (1 - REBASE_PCT))))
        if price != before:
            notes.append(f"⤵️ weekly rebase {-(REBASE_PCT*100):.1f}%")

    # Append today's point if not already there
    if not hist or hist[-1]["date"] != today_str:
        hist.append({"date": today_str, "price": int(price)})

    save_history(data)

    # Resolve item ID and patch
    if ITEM_ID_OVR:
        item_id = ITEM_ID_OVR
        print(f"[INFO] Using UNB_ITEM_ID override: {item_id}")
    else:
        print(f"[INFO] Looking up item id for: {ITEM_NAME!r}")
        item_id = find_item_id_by_name()
        print(f"[INFO] Found item id: {item_id}")

    patch_item_price(item_id, int(price), today_str, notes)
    print(f"OK • {ITEM_NAME} → {int(price)} chips ({today_str}) • next shock in {meta['next_shock_in']} day(s) • notes: {', '.join(notes) if notes else '—'}")

if __name__ == "__main__":
    main()
