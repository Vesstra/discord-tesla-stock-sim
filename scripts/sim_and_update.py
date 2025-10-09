#!/usr/bin/env python3
"""
Public JSON (docs/tesla_history.json) no longer contains meta.
Private meta persists in .data/tesla_meta.json

Features kept:
- Mean reversion, shocks (down-biased), bear regimes, weekly decay
- UB item patch + Chart.js page
"""

import os, json, math, random, datetime, requests, pathlib, sys
from typing import Any, Dict, List, Tuple

# --------- ENV / Defaults ---------
UNB_TOKEN   = os.environ.get("UNB_TOKEN")
GUILD_ID    = os.environ.get("UNB_GUILD_ID", "1219525577950888036")
ITEM_NAME   = os.environ.get("UNB_ITEM_NAME", "Tesla Stock")
ITEM_ID_OVR = os.environ.get("UNB_ITEM_ID")
PAGES_URL   = os.environ.get("PAGES_URL", "https://vesstra.github.io/discord-tesla-stock-sim/")

HISTORY_PATH = pathlib.Path("docs/tesla_history.json")   # public (no meta)
INDEX_PATH   = pathlib.Path("docs/index.html")           # public page
META_PATH    = pathlib.Path(".data/tesla_meta.json")     # private (shock/bear)

# Base model
START_PRICE = 10000.0
DRIFT       = 0.0002
VOL         = 0.03
MIN_PRICE   = 1

# Mean reversion
ANCHOR   = 1000.0
REVERT_K = 0.12

# Shocks
SHOCK_INTERVAL_RANGES: List[Tuple[int, int]] = [(2, 3), (4, 5)]
SHOCK_PCT_MIN = 0.10
SHOCK_PCT_MAX = 0.25
SHOCK_UP_PROB = 0.35

# Bear regimes
BEAR_PROB  = 0.15
BEAR_DAYS  = (2, 5)
BEAR_DRIFT = -0.002
BEAR_VOL   = 0.05

# Weekly decay
REBASE_DAY = 6
REBASE_PCT = 0.01

# Backfill
BACKFILL_DAYS = 30
# ----------------------------------

def die(msg: str, extra: str = "") -> None:
    print(f"[ERROR] {msg}")
    if extra: print(extra)
    sys.exit(1)

def ensure_paths() -> None:
    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    META_PATH.parent.mkdir(parents=True, exist_ok=True)

    # Create public history if missing (NO meta)
    if not HISTORY_PATH.exists():
        HISTORY_PATH.write_text(json.dumps({
            "symbol": "TSLA",
            "name": ITEM_NAME,
            "unit": "chips",
            "history": []
        }, indent=2), encoding="utf-8")

    # Create private meta if missing
    if not META_PATH.exists():
        META_PATH.write_text(json.dumps({
            "next_shock_in": None,
            "bear_left": 0
        }, indent=2), encoding="utf-8")

    # Create page if missing (no JSON link shown)
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

def load_json(path: pathlib.Path) -> Dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        die(f"Failed to read {path}", str(e))

def save_json(path: pathlib.Path, obj: Dict[str, Any]) -> None:
    try:
        path.write_text(json.dumps(obj, indent=2), encoding="utf-8")
    except Exception as e:
        die(f"Failed to write {path}", str(e))

def migrate_meta_if_needed(pub: Dict[str, Any], meta: Dict[str, Any]) -> (Dict[str, Any], Dict[str, Any]):
    """If old public JSON still contains 'meta', move it to private file and strip from public."""
    if "meta" in pub:
        m = pub.pop("meta") or {}
        # Only keep the fields we use
        meta.setdefault("next_shock_in", m.get("next_shock_in", None))
        meta.setdefault("bear_left", m.get("bear_left", 0))
    return pub, meta

def backfill_history(pub: Dict[str, Any], meta: Dict[str, Any]) -> None:
    hist: List[Dict[str, Any]] = pub.get("history", [])
    if len(hist) >= 2:
        if meta.get("next_shock_in") is None: meta["next_shock_in"] = draw_next_interval()
        if "bear_left" not in meta: meta["bear_left"] = 0
        return

    today = datetime.date.today()
    start = today - datetime.timedelta(days=BACKFILL_DAYS - 1)
    d = start
    price = int(round(START_PRICE))

    state = random.getstate()
    random.seed(42)
    points: List[Dict[str, Any]] = []
    for _ in range(BACKFILL_DAYS):
        if points:
            z = random.gauss(0, 1)
            step = math.exp((DRIFT - 0.5 * VOL * VOL) + VOL * z)
            price = max(MIN_PRICE, round(price * step))
        points.append({"date": d.isoformat(), "price": int(price)})
        d += datetime.timedelta(days=1)
    random.setstate(state)

    pub["history"] = points
    if meta.get("next_shock_in") is None:
        meta["next_shock_in"] = draw_next_interval()
    if "bear_left" not in meta:
        meta["bear_left"] = 0

def draw_next_interval() -> int:
    lo, hi = random.choice(SHOCK_INTERVAL_RANGES)
    return random.randint(lo, hi)

def simulate_step(prev_price: float, mu: float, sigma: float) -> int:
    z = random.gauss(0, 1)
    gap = math.log(max(1e-9, ANCHOR)) - math.log(max(1e-9, prev_price))
    drift_term = (mu - 0.5 * sigma * sigma) + REVERT_K * gap
    step = math.exp(drift_term + sigma * z)
    return int(max(MIN_PRICE, round(prev_price * step)))

def apply_shock(price: int) -> (int, float):
    pct = random.uniform(SHOCK_PCT_MIN, SHOCK_PCT_MAX)
    sign = +1 if random.random() < SHOCK_UP_PROB else -1
    new_price = int(max(MIN_PRICE, round(price * (1 + sign * pct))))
    return new_price, sign * pct

# --------- UB helpers ----------
def ub_get(url: str) -> requests.Response:
    try:
        r = requests.get(url, headers={"Authorization": UNB_TOKEN}, timeout=30)
    except Exception as e:
        die(f"Network error GET {url}", str(e))
    if r.status_code in (401, 403):
        die(f"{r.status_code} from UB API", f"{url}\n{r.text[:400]}")
    if not r.ok:
        die(f"GET {url} failed {r.status_code}", r.text[:400])
    return r

def ub_patch(url: str, body: Dict[str, Any]) -> requests.Response:
    try:
        r = requests.patch(url, headers={"Authorization": UNB_TOKEN, "Content-Type": "application/json"},
                           data=json.dumps(body), timeout=30)
    except Exception as e:
        die(f"Network error PATCH {url}", str(e))
    if r.status_code in (401, 403):
        die(f"{r.status_code} on PATCH", f"{url}\n{r.text[:400]}")
    if not r.ok:
        die(f"PATCH {url} failed {r.status_code}", r.text[:400])
    return r

def find_item_id_by_name() -> str:
    url = f"https://unbelievaboat.com/api/v1/guilds/{GUILD_ID}/items"
    r = ub_get(url)
    data = r.json()
    items = data["items"] if isinstance(data, dict) and "items" in data else data
    if not isinstance(items, list): die("Unexpected items payload", repr(items))
    for it in items:
        if isinstance(it, dict) and it.get("name", "").lower() == ITEM_NAME.lower():
            return str(it.get("id"))
    die(f'Item "{ITEM_NAME}" not found in guild {GUILD_ID}.')
    return ""

def patch_item_price(item_id: str, new_price: int, date_str: str, notes: List[str]) -> None:
    url = f"https://unbelievaboat.com/api/v1/guilds/{GUILD_ID}/items/{item_id}"
    suffix = " • ".join(notes) if notes else ""
    desc = f"{ITEM_NAME} • {int(new_price)} chips • Updated {date_str} • Chart: {PAGES_URL}"
    if suffix: desc = f"{desc} • {suffix}"
    ub_patch(url, {"price": int(new_price), "description": desc})

def main() -> None:
    if not UNB_TOKEN: die("UNB_TOKEN not set.")

    ensure_paths()
    pub = load_json(HISTORY_PATH)
    meta = load_json(META_PATH)

    # Migrate any legacy public meta → private
    pub, meta = migrate_meta_if_needed(pub, meta)

    backfill_history(pub, meta)

    hist: List[Dict[str, Any]] = pub["history"]
    today = datetime.date.today()
    today_str = today.isoformat()
    notes: List[str] = []

    # Choose regime
    if meta.get("bear_left", 0) > 0:
        mu, sigma = BEAR_DRIFT, BEAR_VOL
        meta["bear_left"] -= 1
        notes.append("🐻 bear regime")
    else:
        if random.random() < BEAR_PROB:
            meta["bear_left"] = random.randint(*BEAR_DAYS)
            mu, sigma = BEAR_DRIFT, BEAR_VOL
            notes.append("🐻 bear regime (new)")
        else:
            mu, sigma = DRIFT, VOL

    # Base step
    if hist and hist[-1]["date"] == today_str:
        price = int(hist[-1]["price"])
    else:
        price = simulate_step(float(hist[-1]["price"]), mu, sigma)

    # Shock?
    nsi = int(meta.get("next_shock_in", 0) or 0)
    if nsi <= 0:
        price, pct = apply_shock(price)
        notes.append(f"⚡ shock {pct*100:+.1f}%")
        meta["next_shock_in"] = draw_next_interval()
    else:
        meta["next_shock_in"] = nsi - 1

    # Weekly decay
    if today.weekday() == REBASE_DAY:
        before = price
        price = int(max(MIN_PRICE, round(price * (1 - REBASE_PCT))))
        if price != before:
            notes.append(f"⤵️ weekly rebase {-(REBASE_PCT*100):.1f}%")

    if not hist or hist[-1]["date"] != today_str:
        hist.append({"date": today_str, "price": int(price)})

    # Save public + private separately
    save_json(HISTORY_PATH, pub)
    save_json(META_PATH, meta)

    # UB patch
    item_id = ITEM_ID_OVR or find_item_id_by_name()
    patch_item_price(item_id, int(price), today_str, notes)
    print(f"OK • {ITEM_NAME} → {int(price)} • next shock in {meta['next_shock_in']} • notes: {', '.join(notes) if notes else '—'}")

if __name__ == "__main__":
    main()
