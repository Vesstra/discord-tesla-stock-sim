#!/usr/bin/env python3
"""
Simulate a daily price for a Discord "stock" and update the UnbelievaBoat
store item price + description. Also maintains JSON history + a Chart.js page.

First run: if history has < 2 points, this script BACKFILLS the last 30 days
so your chart shows a nice line right away.

Env vars:
  UNB_TOKEN     (required)  — UnbelievaBoat Application Token
  UNB_GUILD_ID  (optional)  — defaults to 1219525577950888036
  UNB_ITEM_NAME (optional)  — defaults to "Tesla Stock"
  UNB_ITEM_ID   (optional)  — if set, skip lookup by name
  PAGES_URL     (optional)  — defaults to https://vesstra.github.io/discord-tesla-stock-sim/
"""

import os, json, math, random, datetime, requests, pathlib, sys
from typing import Any, Dict, List

# ------------------ Configuration ------------------
UNB_TOKEN   = os.environ.get("UNB_TOKEN")  # MUST be set in Actions secrets
GUILD_ID    = os.environ.get("UNB_GUILD_ID", "1219525577950888036")
ITEM_NAME   = os.environ.get("UNB_ITEM_NAME", "Tesla Stock")
ITEM_ID_OVR = os.environ.get("UNB_ITEM_ID")  # if provided, skip lookup
PAGES_URL   = os.environ.get("PAGES_URL", "https://vesstra.github.io/discord-tesla-stock-sim/")

HISTORY_PATH = pathlib.Path("docs/tesla_history.json")
INDEX_PATH   = pathlib.Path("docs/index.html")

# Price simulation
START_PRICE = 1000.0
DRIFT       = 0.0005     # ~0.05% avg daily drift
VOL         = 0.03       # 3% daily volatility
MIN_PRICE   = 1
BACKFILL_DAYS = 30       # initial history length if missing
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
            "history": []  # we'll populate below
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
          elements: {{ point: {{ radius: 2 }} }},   // visible points
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

def simulate_next(prev_price: float) -> int:
    z = random.gauss(0, 1)
    step = math.exp((DRIFT - 0.5 * VOL * VOL) + VOL * z)
    price = max(MIN_PRICE, round(prev_price * step))
    return int(price)

def backfill_history(data: Dict[str, Any]) -> None:
    """If there are <2 points, generate BACKFILL_DAYS ending today."""
    hist: List[Dict[str, Any]] = data.get("history", [])
    if len(hist) >= 2:
        return
    # Seed with START_PRICE and roll forward
    today = datetime.date.today()
    points: List[Dict[str, Any]] = []
    price = int(round(START_PRICE))
    start_date = today - datetime.timedelta(days=BACKFILL_DAYS - 1)
    d = start_date
    random.seed(42)  # deterministic backfill
    for _ in range(BACKFILL_DAYS):
        if points:
            price = simulate_next(price)
        points.append({"date": d.isoformat(), "price": price})
        d += datetime.timedelta(days=1)
    data["history"] = points

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

def patch_item_price(item_id: str, new_price: int, date_str: str) -> None:
    url = f"https://unbelievaboat.com/api/v1/guilds/{GUILD_ID}/items/{item_id}"
    body = {
        "price": int(new_price),
        "description": f"{ITEM_NAME} • {int(new_price)} chips • Updated {date_str} • Chart: {PAGES_URL}"
    }
    ub_patch(url, body)

def main() -> None:
    if not UNB_TOKEN:
        die("UNB_TOKEN is not set. Add it as a GitHub Actions secret named UNB_TOKEN.")

    ensure_site_files()
    data = load_history()

    # Backfill if necessary to make the chart meaningful
    backfill_history(data)

    today = datetime.date.today().isoformat()
    if data["history"] and data["history"][-1]["date"] == today:
        new_price = int(round(float(data["history"][-1]["price"])))
    else:
        last_price = float(data["history"][-1]["price"])
        new_price = simulate_next(last_price)
        data["history"].append({"date": today, "price": new_price})
    save_history(data)

    # Resolve item id (prefer override)
    if ITEM_ID_OVR:
        item_id = ITEM_ID_OVR
        print(f"[INFO] Using UNB_ITEM_ID override: {item_id}")
    else:
        print(f"[INFO] Looking up item id for: {ITEM_NAME!r}")
        item_id = find_item_id_by_name()
        print(f"[INFO] Found item id: {item_id}")

    patch_item_price(item_id, new_price, today)
    print(f"OK • {ITEM_NAME} → {new_price} chips ({today}) • {PAGES_URL}")

if __name__ == "__main__":
    main()
