# discord-stock-sim

Self-contained **Discord stock simulator** for UnbelievaBoat store items.

- **GitHub Pages site:** renders a line chart for the simulated price.
- **Daily GitHub Action:** simulates the next price and **PATCH**es the UnbelievaBoat item price/description.
- **Pre-wired for:**  
  - GitHub Pages at `https://vesstra.github.io/discord-stock-sim/`  
  - Guild ID: `1219525577950888036`  
  - Item name: `Tesla Stock`

---

## Quick start

1. Create a **public** repo named `discord-stock-sim` and push this content.
2. In the repo, go to **Settings → Pages**:
   - Source: **Deploy from branch**
   - Branch: **main**, Folder: **/docs**
3. Add a secret for the UnbelievaBoat API token:
   - **Settings → Secrets and variables → Actions → New repository secret**
   - Name: `UNB_TOKEN`
   - Value: *(your UnbelievaBoat Application Token)*
4. Ensure your UnbelievaBoat bot is in your server and your UB application is authorized for that guild.
5. Trigger the workflow manually:
   - **Actions → Simulate & Update Tesla Stock → Run workflow**

It will:
- Create/refresh `docs/tesla_history.json` and `docs/index.html` (if missing)
- PATCH the **Tesla Stock** item price in your guild
- Commit the updated site files, which GitHub Pages publishes

---

## Files

- `scripts/sim_and_update.py` — simulator + updater (edit VOL/DRIFT for different behavior)
- `.github/workflows/sim.yml` — runs **23:00 UTC** (06:00 ICT) daily and on manual trigger
- `docs/index.html` — simple Chart.js viewer
- `docs/tesla_history.json` — initial seed; updated daily

---

## Configuration

These are **hard-coded** for your convenience. Change in `scripts/sim_and_update.py` if needed:

- `GUILD_ID = "1219525577950888036"`
- `ITEM_NAME = "Tesla Stock"`
- `PAGES_URL = "https://vesstra.github.io/discord-stock-sim/"`

Runtime **environment variable** required (as GitHub Actions secret):
- `UNB_TOKEN` — your UnbelievaBoat Application Token (use as-is in the `Authorization` header; no Bearer prefix).

---

## Local test (optional)

```bash
python -m venv .venv && . .venv/bin/activate
pip install requests
export UNB_TOKEN=YOUR_UB_TOKEN_HERE
python scripts/sim_and_update.py
```

If successful, you should see `OK • Tesla Stock → <price> chips (YYYY-MM-DD) • https://vesstra.github.io/discord-stock-sim/`

---

## Notes

- The role/actions system of UnbelievaBoat is not used here; we only update **store item price/description** via the official API.
- Pricing model = **geometric Brownian motion** with configurable drift/volatility.
- Rate limits: this job runs once daily—well within safe bounds.
- For multiple stocks, duplicate the JSON + extend the script (loop over a list of items).
