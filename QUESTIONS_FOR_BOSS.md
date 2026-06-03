# Biz Review App — Questions / Decisions for Boss Meeting

Draft is live (TOF Review + Booking Review, both with visuals, navy theme, decision callouts).
This doc tracks (1) what's resolved, (2) open decisions, and (3) data gaps for RevOps.

_Last updated: 2026-06-03._

---

## 0. Recently resolved (since first draft)
- **Forecast now reads from HubSpot (source of truth).** Commit / Best Case / Pipeline / Not
  Forecasted come from `hs_manual_forecast_category`; weighted = Σ(ACV × `hs_deal_stage_probability`).
  The system's deterministic call is shown as the *challenge* (Keep / Upgrade / Downgrade / Remove /
  Escalate) per your MD verbs. **No more invented 0.9/0.6/0.3 weights.**
- **SQL-Held fixed** to require the meeting actually happened (`meeting_happened_ = 'Yes'`), not just
  a scheduled `discovery_call_date`.
- **Sage Future / import carve-out** (see C8 — big one).
- **WoW / Trends / Forecast Movement are live now** — backfilled ~9 weeks of weekly snapshots by
  reconstructing date-stamped metrics, instead of waiting weeks to accrue.
- **Pipeline coverage ratio** added to the Exec Forecast (open pipeline ÷ gap-to-plan, benchmark ≥3×).
- **Forecast-hygiene flag** added (see A1).

---

## A. Forecast methodology (highest priority — affects the number)
1. **Forecast hygiene is weak — confirm this is the story to tell.** Of **472 open deals**, only
   **292 carry a rep forecast category**, and just **7 are Commit / 32 Best Case** — **$4.49M of open
   ACV sits in OMIT/uncategorized** (not a forecast call). The app surfaces this. Is driving forecast
   discipline a goal of the Booking Review? (If so, we should make "% categorized" a tracked metric.)
2. **Weighted forecast** now uses HubSpot's own `hs_deal_stage_probability`. Good enough, or do you
   have a different probability/win-rate model you'd rather apply?
3. **ARR definition.** Spec lists both Paystand `arr` (new-biz only) and Deal Total `total_arr`
   (includes Teampay AP). TOF funnel uses `arr`; Wins uses `total_arr`. Which is the single
   "Bookings ARR" for the plan?
4. **ACV** = `total_ar_ap_acv` (AR + AP combined). Confirmed correct?

## B. Metric definitions (confirm)
5. **SQL-Booked** = `createdate` (spec's "SQL meeting booked date"). OK, or use date-entered-SQL-stage?
6. **SQL-Held** = `discovery_call_date` in-period **AND `meeting_happened_ = 'Yes'`**.
   Current QTD: **Held = 575** (Apr 307 / May 246 / Jun 22), vs **SQL-Booked 633, SAL 290**.
   (Your report showed Apr 293 / May 245 / Jun 24 — a ~14-deal April gap remains; send me your report's
   exact "held" filter and I'll match it precisely.)
7. **SAL** = `sal_date`. OK?
8. **Open pipeline excludes SQL stage** (→ ~472 open deals). Confirmed by spec — good.

## C. Data hygiene (I already applied; confirm)
9. **"Sage Future" / import carve-out (IMPORTANT — confirm).** The `IMPORT` source label mixes two
   very different things:
   - **Real new-business leads** (e.g., the **Sage Future 2026** event batch — 275 deals, Sage Intacct,
     marketing-sourced) that we *want* in the funnel, and
   - **~2,392 bulk-imported renewals** ("…(Monthly Renewal)") that were creating **$16M of phantom
     wins**.
   Old rule dropped both. **New rule:** exclude `IMPORT`/`INTEGRATION` **unless the deal has a real
   discovery call booked** (`discovery_call_date IS NOT NULL`). Renewals have a discovery call in only
   **1 of 2,392** cases, so this keeps Sage Future and drops the renewal junk cleanly.
   Result: funnel now matches your held numbers; bookings stay clean at **30 wins / $647K ACV / $533K ARR**.
   **Confirm this carve-out is the behavior you want.**
10. **Test deals + Teampay migrations excluded.** Confirm the exclusion list.

## D. CRM data gaps (RevOps cleanup — these limit the report)
11. **`dealtype` is unreliable** — **all** deals (incl. the 2,392 renewals) are tagged `newbusiness`.
    This means we **cannot split new-logo vs install-base/expansion** today, so the strategic question
    *"Are we winning the NetSuite install base?"* can't be answered from data. Recommend a clean
    new-business / expansion / renewal tag.
12. **Champion** (`champion_identified_`) is **100% null** — MEDDPICC scored out of 5, not 6.
    Recommend adding/using a Champion field.
13. **`partner_influence_level`** and **`partner_motion_type`** don't exist in HubSpot, but the spec
    calls for them. `partner_sourced_` / `registered_by_partner` are 0% filled; only `channel_influence`
    (~20%) is populated. Recommend standardizing partner attribution fields.
14. **`closed_won_reason`** is ~90% blank/unstructured ("goodness", "awesomeness"). "Why we won" needs
    Gong, not CRM. Recommend standardizing to the 11 closed-won categories in your SKILL doc.
15. **No `hs_last_sales_activity_date`** — using `notes_last_contacted` for the 14-day stale flag. OK
    as a proxy? (Gong last-call date would be far better — see E.)
16. **Close-date-change count** (the property you're building) — once live I'll add a "repeated
    close-date push" risk flag (it's in the spec's risk rules) and a slippage view.
17. **Global FX** — no field exists for the strategic priority #6. Needs a definition.
18. **Signal vs noise** — no flag to separate incentive/promo/one-time spikes from core pipeline
    (V2 explicitly wants this). Needs a campaign/promo tag.
19. **Hygiene reality check:** **256 of 472 open deals flag Red** (89 Yellow, 127 Green), mostly from
    no logged activity in 14d / no next step / single-threaded. Real risk picture or a CRM-logging gap?
    Either way it's a strong exec talking point.

## E. Gong + AI (pending your meeting — you said wait)
20. Gong is a rich warehouse (transcripts, sentiment, MEDDPICC deltas, conversation signals), but
    **deal-level linkage is partial** today: `int_gong__call_deal_linkage` is contact-level only;
    `ml_sentiment_deal_trajectory` joins to ~60–70 open deals. We surface Gong sentiment where available.
21. **Your plan (agreed):** your Claude agent — with access to Gong + HubSpot (and Metabase APIs) —
    becomes the **queryable qualitative brain**: ask-anything in the dashboard + auto-narrative per
    section, grounded in the two MD specs. Runs server-side so anyone can use it. **Needs:** API key +
    a reliable call→deal bridge so Gong activity (last call, #calls, multi-threading) can power the risk
    flags for *all* deals.

## F. Build / deploy decisions
22. **Goals vs. Snapshots:**
    - *Goals* = inputtable targets in the sidebar (per quarter/market/metric), local JSON today.
    - *Snapshots* = weekly frozen actuals for WoW / Forecast Movement (now backfilled + capturing).
    - **Both should move to a BigQuery table** so they persist and everyone sees the same numbers on
      Streamlit Cloud (local files reset on cloud). Need: OK to create `paystand.revops_bizreview` +
      write access.
23. **Deploy** = Streamlit + password; you'll make the GitHub repo. Also need the real **HubSpot
    portal id** to activate deal deep-links (placeholder now).
24. **Pods** = HubSpot `primary_team_name`; owners with no team fall back to rep name. Confirm pod defs.

## G. Inputs I need from you
25. **Current-quarter goals** per market (NetSuite/Sage/Dynamics/Other) × metric (SQL-Booked, SQL-Held,
    SAL, Pipeline ARR, Bookings ARR) — to populate attainment/RAG/coverage.
26. **Your report's exact "meetings held" definition** — to close the ~14-deal April gap.
