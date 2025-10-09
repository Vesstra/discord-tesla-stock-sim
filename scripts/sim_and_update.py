#!/usr/bin/env python3
"""
Simulate a daily price for a Discord "stock" and update the UnbelievaBoat
store item price + description. Also maintains a tiny JSON history and a
simple Chart.js page under docs/.

- Reads UNB_TOKEN from environment (required).
- Optional env overrides:
    UNB_GUILD_ID   (default: "1219525577950888036")
    UNB_ITEM_NAME  (default: "Tesla Stock")
    UNB_ITEM_ID    (optional; if set, skips name lookup)
    PAGES_URL      (default: "https://vesstra.github.io/discord-stock-sim/")

This script is designed to be idempotent per day: it appends one point per date.
"""

import os
import json
import math
import random
import datetime
import requests
import pathlib
import sys
from typing import Any, List, Dict

# ------------------ Configuration (env with sane defaults) ------------------
UNB_TOKEN   = os.environ.get("UNB_TOKEN")  # MUST be set in Actions secrets
GUILD_ID    = os.environ.get("UNB_GUILD_ID", "1219525577950888036")
ITEM_NAME   = os.environ.get("UNB_ITEM_NAME", "Tesla Stock")
ITEM_ID_OVR = os.environ.get("UNB_ITEM_ID")  # if provided, skip lookup
PAGES_URL   = os.environ.get("PAGES_URL", "https://vesstra.github.io/discord-stock-sim/")

# Files GitHub Pages will serve
HISTORY_PATH = pathlib.Path("docs/tesla_history.json")
INDEX_PATH   = pathlib.Path("docs/index.html")

# Simulation parameters (tweak freely)
START_PRICE = 1000.0      # first data point (chips)
DRIFT       = 0.0005      # ~0.05% avg daily drift
VOL         = 0.03        # 3% daily volatility
MIN_PRICE   = 1           # price floor in chips
# ---------------------------------------------------------------------------


def die(msg: str, extra: str = "") -> None:
    """Print a clear error and exit non-zero."""
    print(f"[ERROR] {msg}")
    if extra:
        print(extra)
    sys.exit(1)


def ensure_site_files() -> None:
    """Create docs/ files on first run."""
    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not HISTORY_PATH.exists():
        HISTORY_PATH.write_text(json.dumps({
            "symbol": "TSLA",
            "name": ITEM_NAME,
            "unit": "chips",
            "history": [
                {"date": datetime.date.today().isoformat(), "price": round(START_PRICE, 2)}
            ]
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
          scales: {{ y: {{ beginAtZero: false }} }},
          elements: {{ point: {{ radius: 0 }} }}
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
    return {}


def save_history(obj: Dict[str, Any]) -> None:
    try:
        HISTORY_PATH.write_text(json.dumps(obj, indent=2), encoding="utf-8")
    except Exception as e:
        die("Failed to write docs/tesla_history.json", str(e))


def simulate_next(prev_price: float) -> int:
    """Geometric Brownian motion daily step."""
    z = random.gauss(0, 1)
    step = math.exp((DRIFT - 0.5 * VOL * VOL) + VOL * z)
    price = max(MIN_PRICE, round(prev_price * step))
    return int(price)


def ub_get(url: str) -> requests.Response:
    """GET with UB auth + helpful errors."""
    try:
        r = requests.get(url, headers={"Authorization": UNB_TOKEN}, timeout=30)
    except Exception as e:
        die(f"Network error calling GET {url}", str(e))
    if r.status_code == 403:
        die("403 Forbidden from UnbelievaBoat API. Your application token is not authorized for this guild or lacks the ITEMS permission.",
            f"Endpoint: {url}\nResponse: {r.text[:400]}")
    if r.status_code == 401:
        die("401 Unauthorized from UnbelievaBoat API. Check UNB_TOKEN.", f"Endpoint: {url}")
    if not r.ok:
        die(f"GET {url} failed with {r.status_code}", r.text[:400])
    return r


def ub_patch(url: str, body: Dict[str, Any]) -> requests.Response:
    """PATCH with UB auth + helpful errors."""
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
        die("403 Forbidden on PATCH. The token may lack ITEMS permission for this guild.",
            f"Endpoint: {url}\nBody: {json.dumps(body)[:400]}\nResponse: {r.text[:400]}")
    if r.status_code == 401:
        die("401 Unauthorized on PATCH. Check UNB_TOKEN.", f"Endpoint: {url}")
    if not r.ok:
        die(f"PATCH {url} failed with {r.status_code}", r.text[:400])
    return r


def find_item_id_by_name() -> str:
    """List items and find the one matching ITEM_NAME (case-insensitive)."""
    url = f"https://unbelievaboat.com/api/v1/guilds/{GUILD_ID}/items"
    r = ub_get(url)

    # Defensive parsing: may be list, dict-with-items, or a simple string error
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
            # If the API gives strings, surface it clearly
            die("Item entry was not an object. Raw value:", repr(it))
        name = it.get("name", "")
        if name == ITEM_NAME or name.lower() == ITEM_NAME.lower():
            return str(it.get("id"))

    die(
        f'Item "{ITEM_NAME}" not found in guild {GUILD_ID}.',
        "Create it in the UnbelievaBoat dashboard Store for this server first."
    )
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
        die("UNB_TOKEN environment variable is not set.",
            "Add it as a GitHub Actions repository secret named UNB_TOKEN.")

    ensure_site_files()
    data = load_history()
    today = datetime.date.today().isoformat()

    # Append one data point per day
    if data.get("history") and data["history"][-1]["date"] == today:
        new_price = int(round(float(data["history"][-1]["price"])))
    else:
        last_price = float(data["history"][-1]["price"])
        new_price = simulate_next(last_price)
        data["history"].append({"date": today, "price": new_price})
        save_history(data)

    # Resolve item id (prefer override)
    if ITEM_ID_OVR:
        item_id = ITEM_ID_OVR
        print(f"[INFO] Using provided UNB_ITEM_ID override: {item_id}")
    else:
        print(f"[INFO] Looking up item id by name: {ITEM_NAME!r}")
        item_id = find_item_id_by_name()
        print(f"[INFO] Found item id: {item_id}")

    # Patch price/description
    patch_item_price(item_id, new_price, today)
    print(f"OK • {ITEM_NAME} → {new_price} chips ({today}) • {PAGES_URL}")


if __name__ == "__main__":
    main()
