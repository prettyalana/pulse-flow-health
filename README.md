# PulseFlow Health · Q4-2026 Forecast

Replaces the Monday 3-hour spreadsheet fire drill with a repeatable pipeline and a live dashboard.

**Baseline result:** $13,182,400 expected Q4 sales, **+$1,182,400** vs. the $12,000,000 board target.

| | |
|---|---|
| Top 8 strategic deals | $7,840,000 |
| Longtail · Core Salesforce | $4,350,000 |
| Longtail · Acquired CareBridge | $992,400 |

## How it works

```
data/raw/ (4 exports) → pulse_flow.py → data/forecast.json → index.html + app.js
```

- **`pulse_flow.py`** (pandas) cleans and reconciles the 4 files, computes the baseline, and writes `forecast.json` with every deal, the week-over-week movements, and the audit log.
- **`app.js`** reads that JSON and does the what-if math in the browser, so score changes, promotions and toggles update the banner instantly.
- The JSON file is the contract between the two halves. The dashboard is static, so it can be hosted on GitHub Pages.

## Run it

```bash
pip install -r requirements.txt
python pulse_flow.py              # prints the totals and writes data/forecast.json
python -m http.server             # then open http://localhost:8000
```

### A new week's data drop

Add the new exports into `data/raw/` with the same file names and rerun `python pulse_flow.py`. To keep weeks separate, put them in their own folder and change the first line of the main block to `load_files("data/week_2")`. Nothing is hardcoded to this week's deals, so new duplicates, overrides and blank IDs are caught by the same rules.

## Pipeline steps

1. **Load** the 4 files (the scorecard's real headers are on row 4).
2. **Standardize:** trim Salesforce IDs to 15 characters, convert HubSpot monthly amounts to annual (×12), and map HubSpot column names onto the Salesforce schema.
3. **Dedupe:** exact copies, same ID with different values, and the same deal in both systems.
4. **Validate:** Closed Lost deals with a nonzero probability, stage/probability mismatches, past-due close dates.
5. **Classify:** stack Salesforce + HubSpot, keep active Q4-2026 deals, tag Core vs. CareBridge.
6. **Top 8:** match the VP scorecard (by ID, or by name + product + rep when blank), recalculate GPA (rounded to 2 decimals before the tier lookup), and map it to win probability.
7. **Compare weeks:** new, slipped to Q1-2027, closed lost, stage moves, ACV changes.
8. **Output** `forecast.json` and print the totals.

## Data issues caught

| Issue | Resolution |
|---|---|
| Prior-week export uses 15-character IDs for 6 deals; current week and the scorecard differ | Trimmed all IDs to 15 characters. Without this, 6 existing deals look "new." |
| Cascadia Crest (L014) exported twice | Removed the copy ($400K ACV double count) |
| HubSpot HS-9002 Solaris Transitions exported twice | Removed the copy ($240K ACV double count) |
| Caledonia (L008) appears twice with different stage and amount | Kept the row with the lowest Days_In_Stage (Contract Negotiations, $550K). Older row is a dashboard toggle (−$200K). |
| Aethelgard Post-Acute is in Salesforce (M001, $180K annual) **and** HubSpot (HS-9001, $15K/month) | Matched on name, product, close date and annual amount; counted once as CareBridge ($144K expected-value double count prevented) |
| HubSpot monthly amounts | ×12 before any summing |
| VP scorecard rows 5–8 have no Opportunity ID | Matched by name + product, with sales rep as the tiebreaker |
| "Borealis Health" matches two PulseOR deals | Rep (Clara Oswald) resolves it to Main Campus (T006), consistent with the VP note |
| Ironwood: VP typed 75%, but GPA 2.40 maps to 50% | Used the formula; toggle to honor the override (+$550K) |
| GPA tier boundaries (Solaris 3.20, Zephyr/Ironwood 2.40) | Rounded before comparing, so floating-point error can't drop a tier |
| 4 open deals with close dates before 2026-10-15 (Hyperion, Obsidian, Valkyrie, Zephyr Senior Care) | Included by default; toggle to exclude (−$812K) |
| Closed Lost deals with 40% / 30% probability (Coral Reef, HS-9013) | Excluded as lost |
| Q1-2027 deals in both systems | Excluded from Q4 |

Cross-checks that passed: the VP notes for Solaris (+$400K this week) and Borealis (regressed from Business Approval) match the Salesforce week-over-week changes.

## Assumptions and open questions

- **Caledonia:** lower Days_In_Stage = more recent record. I'd confirm with Salesforce admins why two rows exported.
- **Aethelgard Post-Acute:** HubSpot treated as the system of record for CareBridge deals until the migration is finished.
- **VP overrides:** the formula wins by default, since the README defines it; executives can flip the toggle live.
- **Past-due deals:** counted by default so the baseline matches the README's rules; flagged for the reps to update.
- Week-over-week is Salesforce only, per the README.
- Dashboard filters (product, rep) narrow the tables; the banner always shows the full forecast so the variance to target stays meaningful.

## Next steps at scale

- Schedule the pipeline (e.g. a GitHub Action on each new data drop) so Monday's numbers are ready before the call.
- Pull directly from the Salesforce and HubSpot APIs instead of CSV exports.
- Add tests for each cleanup rule using small fixture files.