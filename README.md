# Paystand Business Review

Unified **deck + dashboard** for the weekly Biz Reviews (TOF Review + Booking Review),
built on live HubSpot data in BigQuery. Auto-updates each week — no manual slide rebuilding.

- **Present (deck)** = the 30-min meeting view: big numbers, RAG, decision callouts.
- **Drill-down (dashboard)** = supporting detail (watchlist, wins, full pipeline).
- Section flow mirrors the V2 frameworks; forecast logic follows the boss's MD specs.

## Run locally
```bash
pip install -r requirements.txt
# auth: either set GOOGLE_APPLICATION_CREDENTIALS to a service-account key file,
# or create .streamlit/secrets.toml from the example below.
python -m streamlit run app.py --server.port 8520
```

## Deploy to Streamlit Cloud
1. Push this folder to a GitHub repo.
2. On https://share.streamlit.io → **New app** → pick the repo, branch, and `app.py`.
3. **App → Settings → Secrets**: paste the contents of `.streamlit/secrets.toml`
   (see `.streamlit/secrets.toml.example`) — the `[gcp_service_account]` key and `app_password`.
4. Deploy. The app reads BigQuery via the service account and gates access with `app_password`.

## Notes
- **Secrets are never committed** (`.gitignore` excludes `*.json` and `secrets.toml`).
- Goals / snapshots / actions persist to local JSON today; on cloud these reset on reboot —
  move to a BigQuery table (`paystand.revops_bizreview`) for durable storage (see QUESTIONS doc).
- BigQuery service account needs **read** access to `paystand.bronze_paystand_hubspot.*`,
  `paystand.silver_paystand_hubspot.*`, and `paystand.gold_paystand_gong.*`.
