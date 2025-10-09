import os, json, math, random, datetime, requests, pathlib

# ------------------ Fixed config for YOUR setup ------------------
GUILD_ID   = "1219525577950888036"
ITEM_NAME  = "Tesla Stock"
PAGES_URL  = "https://vesstra.github.io/discord-stock-sim/"
UNB_TOKEN  = os.environ["UNB_TOKEN"]   # set in GitHub Actions > Secrets

# Files GitHub Pages will serve
HISTORY_PATH = pathlib.Path("docs/tesla_history.json")
INDEX_PATH   = pathlib.Path("docs/index.html")

# Simulation parameters (tweak freely)
START_PRICE = 1000.0     # first data point (chips)
DRIFT       = 0.0005     # ~0.05% avg daily drift
VOL         = 0.03       # 3% daily volatility (raise/lower for wilder/tamer)
MIN_PRICE   = 1          # price floor in chips
# ----------------------------------------------------------------

def ensure_site_files():
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

def load_history():
    return json.loads(HISTORY_PATH.read_text(encoding="utf-8"))

def save_history(obj):
    HISTORY_PATH.write_text(json.dumps(obj, indent=2), encoding="utf-8")

def simulate_next(prev_price: float) -> int:
    # Geometric Brownian step: S_{t+1} = S_t * exp( (μ - 0.5σ^2) + σ * Z )
    z = random.gauss(0, 1)
    step = math.exp((DRIFT - 0.5 * VOL * VOL) + VOL * z)
    price = max(MIN_PRICE, round(prev_price * step))
    return int(price)

def find_item_id_by_name() -> str:
    url = f"https://unbelievaboat.com/api/v1/guilds/{GUILD_ID}/items"
    r = requests.get(url, headers={"Authorization": UNB_TOKEN}, timeout=30)
    r.raise_for_status()
    for it in r.json():
        n = it.get("name","")
        if n == ITEM_NAME or n.lower() == ITEM_NAME.lower():
            return it["id"]
    raise SystemExit(f'Item "{ITEM_NAME}" not found in guild {GUILD_ID}. Create it in the store first.')

def patch_item_price(item_id: str, new_price: int, date_str: str):
    url = f"https://unbelievaboat.com/api/v1/guilds/{GUILD_ID}/items/{item_id}"
    body = {
        "price": new_price,
        "description": f"{ITEM_NAME} • {new_price} chips • Updated {date_str} • Chart: {PAGES_URL}"
    }
    r = requests.patch(url,
        headers={"Authorization": UNB_TOKEN, "Content-Type":"application/json"},
        data=json.dumps(body),
        timeout=30)
    if not r.ok:
        raise SystemExit(f"PATCH {r.status_code}: {r.text}")

def main():
    ensure_site_files()
    data = load_history()
    today = datetime.date.today().isoformat()

    if data["history"] and data["history"][-1]["date"] == today:
        new_price = int(round(float(data["history"][-1]["price"])))
    else:
        last_price = float(data["history"][-1]["price"])
        new_price  = simulate_next(last_price)
        data["history"].append({"date": today, "price": new_price})
        save_history(data)

    item_id = find_item_id_by_name()
    patch_item_price(item_id, new_price, today)
    print(f"OK • {ITEM_NAME} → {new_price} chips ({today}) • {PAGES_URL}")

if __name__ == "__main__":
    main()
