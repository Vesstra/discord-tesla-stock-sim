#!/usr/bin/env python3
"""
Simulate a daily price for a Discord "stock" and update the UnbelievaBoat
store item price + description. Maintains JSON history + a Chart.js page.

Now with SHOCK DAYS:
- A countdown (stored in JSON) triggers a shock every few days.
- After each shock, the next interval is drawn randomly from one of two ranges.

Env vars:
  UNB_TOKEN     (required)  — UnbelievaBoat Application Token
  UNB_GUILD_ID  (optional)  — defaults to 1219525577950888036
  UNB_ITEM_NAME (optional)  — defaults to "Tesla Stock"
  UNB_ITEM_ID   (optional)  — if set, skip lookup by name
  PAGES_URL     (optional)  — defaults to https://vesstra.github.io/discord-tesla-stock-sim/
"""

import os, json, math, random, datetime, requests, pathlib, sys
from typing import Any, Dict, List, Tuple

# ------------------ Configuration ------------------
UNB_TOKEN   = os.environ.get("UNB_TOKEN")  # MUST be set in Actions secrets
GUILD_ID    = os.environ.get("UNB_GUILD_ID", "1219525577950888036")
ITEM_NAME   = os.environ.get("UNB_ITEM_NAME", "Tesla Stock")
ITEM_ID_OVR = os.environ.get("UNB_ITEM_ID")  # if provided, skip lookup
PAGES_URL   = os.environ.get("PAGES_URL", "https://vesstra.github.io/discord-tesla-stock-sim/")

HISTORY_PATH = pathlib.Path("docs/tesla_history.json")
INDEX_PATH   = pathlib.Path("docs/index.html")

# Base (non-shock) price model: geometric Brownian motion
START_PRICE = 1000.0
DRIFT       = 0.0005     # ~+0.05% avg daily drift
VOL         = 0.03       # ~3% daily volatility
MIN_PRICE   = 1

# First-run chart backfill
BACKFILL_DAYS = 30

# -------- Shock configuration --------
# After each shock, we randomly choose the next interval from ONE of these ranges:
SHOCK_INTERVAL_RANGES: List[Tuple[int, int]] = [(2, 3), (4, 5)]  # your 2–3 OR 4–5 days
# Size of a shock (percent of price, absolute value). Actual sign is random ±1.
SHOCK_PCT_MIN = 0.06   # 6%
SHOCK_PCT_MAX = 0.15   # 15%
# Bias toward up or down on shocks (0.5 = fair coin)
SHOCK_UP_PROB = 0.5
# -------------------------------------

# ----------------------------------------------------

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
                "next_shock_in": None  # set on first run
            },
            "history": []  # we’ll backfill below
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

def simulate_step(prev_price: float) -> int:
    """One non-shock day using GBM."""
    z = random.gauss(0, 1)
    step = math.exp((DRIFT - 0.5 * VOL * VOL) + VOL * z)
    price = max(MIN_PRICE, round(prev_price * step))
    return int(price)

def draw_next_interval() -> int:
    """Pick one of the ranges at random, then pick an integer day count within it."""
    lo, hi = random.choice(SHOCK_INTERVAL_RANGES)
    return random.randint(lo, hi)

def apply_shock(price: int) -> Tuple[int, float, int]:
    """
    Apply a shock of ±X% where X is uniform in [SHOCK_PCT_MIN, SHOCK_PCT_MAX].
    Returns: (new_price, pct_signed, sign) where sign is +1 or -1.
    """
    pct = random.uniform(SHOCK_PCT_MIN, SHOCK_PCT_MAX)
    sign = +1 if random.random() < SHOCK_UP_PROB else -1
    new_price = int(max(MIN_PRICE, round(price * (1.0 + sign * pct))))
    return new_price, (sign * pct), sign

def backfill_history(data: Dict[str, Any]) -> None:
    """If <2 points exist, generate BACKFILL_DAYS so the chart has a line."""
    hist: List[Dict[str, Any]] = data.get("history", [])
    meta = data.setdefault("meta", {})
    if len(hist) >= 2:
        if meta.get("next_shock_in") is None:
            meta["next_shock_in"] = draw_next_interval()
        return

    today = datetime.date.today()
    points: List[Dict[str, Any]] = []
    price = int(round(START_PRICE))
    start_date = today - datetime.timedelta(days=BACKFILL_DAYS - 1)
    d = start_date

    # Deterministic backfill for a nice looking first curve
    state = random.getstate()
    random.seed(42)
    for _ in range(BACKFILL_DAYS):
        if points:
            price = simulate_step(price)
        points.append({"date": d.isoformat(), "price": price})
        d += datetime.timedelta(days=1)
    random.setstate(state)

    data["history"] = points
    # Initialize a first countdown
    data.setdefault("meta", {})["next_shock_in"] = draw_next_interval()

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

def patch_item_price(item_id: str, new_price: int, date_str: str, shock_note: str) -> None:
    url = f"https://unbelievaboat.com/api/v1/guilds/{GUILD_ID}/items/{item_id}"
    desc = f"{ITEM_NAME} • {int(new_price)} chips • Updated {date_str} • Chart: {PAGES_URL}"
    if shock_note:
        desc = f"{desc} • ⚡ {shock_note}"
    body = {"price": int(new_price), "description": desc}
    ub_patch(url, body)

def main() -> None:
    if not UNB_TOKEN:
        die("UNB_TOKEN is not set. Add it as a GitHub Actions secret named UNB_TOKEN.")

    ensure_site_files()
    data = load_history()
    backfill_history(data)

    hist: List[Dict[str, Any]] = data["history"]
    meta: Dict[str, Any] = data.setdefault("meta", {})
    if meta.get("next_shock_in") is None:
        meta["next_shock_in"] = draw_next_interval()

    today = datetime.date.today().isoformat()
    shock_note = ""

    # Base next price
    if hist and hist[-1]["date"] == today:
        price = int(round(float(hist[-1]["price"])))
    else:
        price = simulate_step(float(hist[-1]["price"]))

    # Shock logic
    nsi = int(meta.get("next_shock_in", 0))
    if nsi <= 0:
        # Apply a shock today
        new_price, pct_signed, sign = apply_shock(price)
        price = new_price
        # Human readable note e.g., "+8.3%" or "−10.2%"
        pct_display = f"{pct_signed*100:+.1f}%"
        shock_note = f"Shock {pct_display}"
        # Reset countdown
        meta["next_shock_in"] = draw_next_interval()
    else:
        # Countdown ticks
        meta["next_shock_in"] = nsi - 1

    # Append today if not already recorded
    if not hist or hist[-1]["date"] != today:
        hist.append({"date": today, "price": int(price)})

    save_history(data)

    # Resolve item ID and patch
    if ITEM_ID_OVR:
        item_id = ITEM_ID_OVR
        print(f"[INFO] Using UNB_ITEM_ID override: {item_id}")
    else:
        print(f"[INFO] Looking up item id for: {ITEM_NAME!r}")
        item_id = find_item_id_by_name()
        print(f"[INFO] Found item id: {item_id}")

    patch_item_price(item_id, int(price), today, shock_note)
    print(f"OK • {ITEM_NAME} → {int(price)} chips ({today}) • {PAGES_URL} "
          f"• next shock in {meta['next_shock_in']} day(s){' • ⚡' if shock_note else ''}")

if __name__ == "__main__":
    main()
