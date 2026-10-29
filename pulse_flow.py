import json
from datetime import datetime

import pandas as pd

RAW_DIR = "data/raw"
OUTPUT = "data/forecast.json"
AS_OF = "2026-10-15"            # Treat this as "today" for all date checks
QUARTER = "Q4-2026"
BOARD_TARGET = 12_000_000

# README Rule 2: pillar weights for the VP scorecard
WEIGHTS = {"Leadership_Score": 0.20, "Commercial_Score": 0.20, "Contractual_Score": 0.60}

# Expected probability for each stage, so we can catch rows where they disagree
SFDC_STAGES = {"Introduction": 0.20, "Technical Validation": 0.40,
               "Business Approval": 0.60, "Contract Negotiations": 0.80}
HUBSPOT_STAGES = {"Introduction": 0.20, "Qualified to Buy": 0.40,
                  "Decision Maker Bought-In": 0.60, "Contract Sent": 0.80}
STAGE_ORDER = list(SFDC_STAGES)   # earliest -> latest, for stage-move direction

# One shared list. Every step writes what it found here; this becomes the Data Checks tab.
audit_log = []


def log_issue(step, source, record, issue, resolution, impact=None):
    audit_log.append({"step": step, "source": source, "record": record,
                      "issue": issue, "resolution": resolution, "impact": impact})


# Step 1: Load

def load_files(raw_dir=RAW_DIR):
    sfdc_current = pd.read_csv(f"{raw_dir}/1_sfdc_pipeline_current.csv")
    sfdc_prior = pd.read_csv(f"{raw_dir}/2_sfdc_pipeline_prior_week.csv")
    hubspot = pd.read_csv(f"{raw_dir}/4_carebridge_hubspot_export.csv")

    # Real column headers are on row 4 (index 3); rows above are a title, a note, and the weights
    scorecard = pd.read_excel(
        f"{raw_dir}/3_vp_deal_scorecard.xlsx",
        sheet_name="Top 8 Scorecard",
        header=3,
    )

    return sfdc_current, sfdc_prior, hubspot, scorecard


# Step 2: Standardize

def check_id_lengths(sfdc_current, sfdc_prior):
    # Runs before truncate_ids, so the audit log shows the IDs didn't match
    for name, table in [("Salesforce current", sfdc_current), ("Salesforce prior", sfdc_prior)]:
        short_ids = table[table["Opportunity_ID"].str.len() == 15]
        if len(short_ids) > 0:
            log_issue("Standardize", name, f"{len(short_ids)} deals",
                      "Mixed 15- and 18-character Salesforce IDs (existing deals would look new)",
                      "Trimmed every ID to its first 15 characters before comparing")


def truncate_ids(sfdc_current, sfdc_prior):
    # Remove extra characters (Salesforce checksum)
    current_truncated_ids = sfdc_current["Opportunity_ID"].str[:15]
    prior_truncated_ids = sfdc_prior["Opportunity_ID"].str[:15]
    sfdc_current["Opportunity_ID"] = current_truncated_ids
    sfdc_prior["Opportunity_ID"] = prior_truncated_ids
    return sfdc_current, sfdc_prior


def change_monthly_to_annually(hubspot):
    # Copy the Amount column into the new Annual_Amount column if the Billing_Frequency is Annual
    hubspot["Annual_Amount"] = hubspot["Amount"]

    for row in hubspot.index:
        if hubspot.loc[row, "Billing_Frequency"] == "Monthly":
            annual_amount = hubspot.loc[row, "Amount"] * 12
            hubspot.loc[row, "Annual_Amount"] = annual_amount

    return hubspot


def rename_hubspot_columns(hubspot):
    renamed_headers = {"HubSpot_Deal_ID": "Opportunity_ID", "Company_Name": "Health_System", "Deal_Stage": "Stage", "Expected_Close_Date": "Close_Date", "AE_Owner": "Sales_Rep"}
    hubspot = hubspot.rename(columns=renamed_headers)
    return hubspot


def check_unknown_columns(hubspot, sfdc_current):
    # Safety check: warn if HubSpot sends a column the rename doesn't know about
    for column in hubspot.columns:
        if column not in sfdc_current.columns:
            log_issue("Standardize", "HubSpot", column,
                      "Unknown column in HubSpot export", "Kept as-is; review the column mapping")


# Step 3: Dedupe

def drop_exact_duplicates(table, source):
    duplicates = table[table.duplicated()]

    for row in duplicates.index:
        log_issue("Dedupe", source,
                  f"{duplicates.loc[row, 'Opportunity_ID']} {duplicates.loc[row, 'Health_System']}",
                  "Exact duplicate row",
                  "Removed the extra copy",
                  f"Prevented ${duplicates.loc[row, 'Annual_Amount']:,.0f} double count")

    if len(duplicates) == 0:
        log_issue("Dedupe", source, "All rows", "Checked for exact duplicates", "None found")

    return table.drop_duplicates()


def resolve_conflicting_ids(table, source):
    # Same ID twice with different values: keep the most recent row (lowest Days_In_Stage).
    # The older row is saved in older_rows so the dashboard can offer a toggle.
    older_rows = {}
    repeated = table[table.duplicated(subset=["Opportunity_ID"], keep=False)]

    for deal_id in repeated["Opportunity_ID"].unique():
        rows = table[table["Opportunity_ID"] == deal_id]
        keep_row = rows["Days_In_Stage"].idxmin()

        for row in rows.index:
            if row != keep_row:
                log_issue("Dedupe", source, f"{deal_id} {rows.loc[row, 'Health_System']}",
                          f"Same ID twice: {rows.loc[row, 'Stage']} ${rows.loc[row, 'Amount']:,.0f} "
                          f"({rows.loc[row, 'Days_In_Stage']} days in stage) vs "
                          f"{rows.loc[keep_row, 'Stage']} ${rows.loc[keep_row, 'Amount']:,.0f} "
                          f"({rows.loc[keep_row, 'Days_In_Stage']} days in stage)",
                          "Kept the most recently updated row; older row available as a toggle",
                          "Judgment call")
                older_rows[deal_id] = {"stage": rows.loc[row, "Stage"],
                                       "stage_probability": float(rows.loc[row, "Stage_Probability"]),
                                       "annual_acv": float(rows.loc[row, "Annual_Amount"])}
                table = table.drop(row)

    return table, older_rows


def remove_cross_system_duplicates(sfdc_current, hubspot):
    # Same deal in Salesforce and HubSpot. The IDs are different, so match on business details.
    for s_row in sfdc_current.index:
        for h_row in hubspot.index:
            same_deal = (
                sfdc_current.loc[s_row, "Health_System"].strip().lower() == hubspot.loc[h_row, "Health_System"].strip().lower()
                and sfdc_current.loc[s_row, "Product"] == hubspot.loc[h_row, "Product"]
                and sfdc_current.loc[s_row, "Close_Date"] == hubspot.loc[h_row, "Close_Date"]
                and sfdc_current.loc[s_row, "Annual_Amount"] == hubspot.loc[h_row, "Annual_Amount"]
            )
            if same_deal:
                expected_value = sfdc_current.loc[s_row, "Annual_Amount"] * sfdc_current.loc[s_row, "Stage_Probability"]
                log_issue("Dedupe", "Salesforce + HubSpot",
                          f"{sfdc_current.loc[s_row, 'Opportunity_ID']} / {hubspot.loc[h_row, 'Opportunity_ID']} "
                          f"{sfdc_current.loc[s_row, 'Health_System']}",
                          f"Same deal in both systems (HubSpot shows ${hubspot.loc[h_row, 'Amount']:,.0f}/month "
                          f"= ${hubspot.loc[h_row, 'Annual_Amount']:,.0f}/year)",
                          "Counted once, as a CareBridge deal (kept the HubSpot record)",
                          f"Prevented ${expected_value:,.0f} expected-value double count")
                sfdc_current = sfdc_current.drop(s_row)
                break   # found its match, move on to the next Salesforce deal

    return sfdc_current


# Step 4: Validate

def validate(table, source, stages):
    for row in table.index:
        deal = f"{table.loc[row, 'Opportunity_ID']} {table.loc[row, 'Health_System']}"
        stage = table.loc[row, "Stage"]
        probability = table.loc[row, "Stage_Probability"]

        # Lost deals shouldn't have any chance to close
        if stage == "Closed Lost" and probability > 0:
            log_issue("Validate", source, deal,
                      f"Closed Lost deal still shows {probability:.0%} probability",
                      "Treated as lost; excluded from the forecast")

        # Probability should match the stage
        elif stage in stages and abs(probability - stages[stage]) > 0.001:
            log_issue("Validate", source, deal,
                      f"{stage} should be {stages[stage]:.0%}, export shows {probability:.0%}",
                      "Used the export's probability; review with the deal owner")

        # Open Q4 deals whose close date already passed
        if table.loc[row, "Fiscal_Period"] == QUARTER and stage != "Closed Lost" and table.loc[row, "Close_Date"] < AS_OF:
            log_issue("Validate", source, deal,
                      f"Still open, but close date {table.loc[row, 'Close_Date']} already passed",
                      "Included by default; dashboard toggle can exclude past-due deals",
                      f"${table.loc[row, 'Annual_Amount'] * probability:,.0f} expected value at stake")


# Step 5: Classify

def combine_active_deals(sfdc_current, hubspot):
    # Stack both systems into one table (this is why we renamed the HubSpot columns)
    sfdc_current["Source"] = "Salesforce"
    hubspot["Source"] = "HubSpot"
    all_deals = pd.concat([sfdc_current, hubspot], ignore_index=True)

    is_active = (all_deals["Fiscal_Period"] == QUARTER) & (all_deals["Stage"] != "Closed Lost")

    not_active = all_deals[~is_active]
    for row in not_active.index:
        reason = "Closed Lost" if not_active.loc[row, "Stage"] == "Closed Lost" else not_active.loc[row, "Fiscal_Period"]
        log_issue("Classify", not_active.loc[row, "Source"],
                  f"{not_active.loc[row, 'Opportunity_ID']} {not_active.loc[row, 'Health_System']}",
                  f"Not an active {QUARTER} deal ({reason})", "Excluded from the Q4 forecast")

    active_deals = all_deals[is_active].copy()
    active_deals["Segment"] = "Core"
    active_deals.loc[active_deals["Product"] == "CareBridge", "Segment"] = "CareBridge"
    return active_deals


# Step 6: Top 8

def calculate_gpa(leadership, commercial, contractual):
    # Round first: 0.2*4 + 0.2*3 + 0.6*3 is 3.1999... in Python, which would drop a 75% deal to 50%
    return round(0.20 * leadership + 0.20 * commercial + 0.60 * contractual, 2)


def gpa_to_probability(gpa):
    if gpa >= 3.20:
        return 0.75
    if gpa >= 2.40:
        return 0.50
    if gpa >= 1.60:
        return 0.30
    return 0.10


def match_scorecard(scorecard, active_deals):
    # Connect each scorecard row to a real deal: by ID if the VP filled it in,
    # otherwise by name + product, with the sales rep as a tiebreaker
    salesforce_deals = active_deals[active_deals["Source"] == "Salesforce"]
    matches = {}

    for row in scorecard.index:
        name = scorecard.loc[row, "Hospital_Shorthand_Name"]
        scorecard_id = scorecard.loc[row, "Opportunity_ID_15"]
        label = f"Rank {scorecard.loc[row, 'Rank']} {name}"

        if pd.notna(scorecard_id):
            candidates = salesforce_deals[salesforce_deals["Opportunity_ID"] == scorecard_id]
        else:
            candidates = salesforce_deals[
                salesforce_deals["Health_System"].str.lower().str.contains(name.lower(), regex=False)
                & (salesforce_deals["Product"] == scorecard.loc[row, "Product"])
            ]
            if len(candidates) > 1:
                options = ", ".join(candidates["Health_System"])
                candidates = candidates[candidates["Sales_Rep"] == scorecard.loc[row, "Sales_Rep"]]
                log_issue("Top 8", "VP Scorecard", label,
                          f"Blank ID, and the name matches several deals ({options})",
                          f"Matched on sales rep {scorecard.loc[row, 'Sales_Rep']}")
            else:
                log_issue("Top 8", "VP Scorecard", label, "Opportunity ID left blank",
                          "Matched by name, product and rep")

        if len(candidates) != 1:
            log_issue("Top 8", "VP Scorecard", label,
                      "Could not match to exactly one active Q4 deal", "Left out of the Top 8; needs review")
            continue

        matches[row] = candidates["Opportunity_ID"].iloc[0]

    return matches


def score_top8(scorecard, active_deals, matches):
    active_deals["Is_Top8"] = False
    active_deals["Top8"] = None

    for row, deal_id in matches.items():
        leadership = int(scorecard.loc[row, "Leadership_Score"])
        commercial = int(scorecard.loc[row, "Commercial_Score"])
        contractual = int(scorecard.loc[row, "Contractual_Score"])
        gpa = calculate_gpa(leadership, commercial, contractual)
        probability = gpa_to_probability(gpa)
        vp_probability = float(scorecard.loc[row, "Win_Probability"])
        name = scorecard.loc[row, "Hospital_Shorthand_Name"]
        deal_row = active_deals.index[active_deals["Opportunity_ID"] == deal_id][0]

        # The VP typed a probability that doesn't match the formula
        if abs(vp_probability - probability) > 0.001:
            difference = active_deals.loc[deal_row, "Annual_Amount"] * (vp_probability - probability)
            log_issue("Top 8", "VP Scorecard", f"{deal_id} {name}",
                      f"Win probability typed as {vp_probability:.0%}, but GPA {gpa:.2f} maps to {probability:.0%}",
                      "Used the formula; dashboard toggle can honor the VP override",
                      f"${difference:+,.0f} expected value if honored")

        active_deals.loc[deal_row, "Is_Top8"] = True
        active_deals.at[deal_row, "Top8"] = {
            "rank": int(scorecard.loc[row, "Rank"]), "shorthand": name,
            "leadership": leadership, "commercial": commercial, "contractual": contractual,
            "gpa": gpa, "probability": probability, "vp_probability": vp_probability,
            "notes": scorecard.loc[row, "VP_Notes"],
        }

    return active_deals


# Step 7: Compare weeks

def compare_weeks(sfdc_current, sfdc_prior):
    movements = {"new": [], "slipped": [], "closed_lost": [], "stage_moves": [], "acv_changes": []}
    prior_ids = list(sfdc_prior["Opportunity_ID"])

    for row in sfdc_current.index:
        deal_id = sfdc_current.loc[row, "Opportunity_ID"]
        deal = {"id": deal_id, "health_system": sfdc_current.loc[row, "Health_System"],
                "product": sfdc_current.loc[row, "Product"], "sales_rep": sfdc_current.loc[row, "Sales_Rep"],
                "stage": sfdc_current.loc[row, "Stage"], "annual_acv": float(sfdc_current.loc[row, "Annual_Amount"])}

        # 1. New: not in last week's file
        if deal_id not in prior_ids:
            movements["new"].append(deal)
            continue

        last_week = sfdc_prior[sfdc_prior["Opportunity_ID"] == deal_id].iloc[0]
        active_q4 = sfdc_current.loc[row, "Fiscal_Period"] == QUARTER and sfdc_current.loc[row, "Stage"] != "Closed Lost"

        # 2. Slipped: was Q4 last week, isn't now
        if last_week["Fiscal_Period"] == QUARTER and sfdc_current.loc[row, "Fiscal_Period"] != QUARTER:
            movements["slipped"].append({**deal, "to_period": sfdc_current.loc[row, "Fiscal_Period"],
                                         "from_close": last_week["Close_Date"], "to_close": sfdc_current.loc[row, "Close_Date"]})

        # 3. Closed lost: was open last week
        if last_week["Stage"] != "Closed Lost" and sfdc_current.loc[row, "Stage"] == "Closed Lost":
            movements["closed_lost"].append({**deal, "from_stage": last_week["Stage"]})

        # 4. Stage moves on active Q4 deals
        if active_q4 and last_week["Stage"] != sfdc_current.loc[row, "Stage"]:
            if STAGE_ORDER.index(sfdc_current.loc[row, "Stage"]) > STAGE_ORDER.index(last_week["Stage"]):
                direction = "advanced"
            else:
                direction = "regressed"
            movements["stage_moves"].append({**deal, "from_stage": last_week["Stage"], "direction": direction})

        # 5. Amount changes on active Q4 deals
        if active_q4 and last_week["Annual_Amount"] != sfdc_current.loc[row, "Annual_Amount"]:
            movements["acv_changes"].append({**deal, "from_acv": float(last_week["Annual_Amount"]),
                                             "delta": float(sfdc_current.loc[row, "Annual_Amount"] - last_week["Annual_Amount"])})

    # A deal that vanished from one week to the next is worth a look
    for deal_id in prior_ids:
        if deal_id not in list(sfdc_current["Opportunity_ID"]):
            log_issue("Compare weeks", "Salesforce", deal_id,
                      "In last week's export but missing this week", "Review: deleted or merged?")

    return movements


# Step 8: Output

def calculate_totals(active_deals):
    # Expected value = annual amount x chance to close
    active_deals["Expected_Value"] = active_deals["Annual_Amount"] * active_deals["Stage_Probability"]
    for row in active_deals.index:
        if active_deals.loc[row, "Is_Top8"]:
            active_deals.loc[row, "Expected_Value"] = active_deals.loc[row, "Annual_Amount"] * active_deals.at[row, "Top8"]["probability"]

    top8 = active_deals[active_deals["Is_Top8"]]
    longtail = active_deals[~active_deals["Is_Top8"]]

    totals = {
        "top8": float(top8["Expected_Value"].sum()),
        "core_longtail": float(longtail[longtail["Segment"] == "Core"]["Expected_Value"].sum()),
        "carebridge_longtail": float(longtail[longtail["Segment"] == "CareBridge"]["Expected_Value"].sum()),
    }
    totals["total"] = totals["top8"] + totals["core_longtail"] + totals["carebridge_longtail"]
    totals["variance"] = totals["total"] - BOARD_TARGET
    return totals


def write_forecast(active_deals, older_rows, movements, totals, output=OUTPUT):
    deals = []
    for row in active_deals.index:
        deal_id = active_deals.loc[row, "Opportunity_ID"]
        deals.append({
            "id": deal_id,
            "source": active_deals.loc[row, "Source"],
            "segment": active_deals.loc[row, "Segment"],
            "health_system": active_deals.loc[row, "Health_System"],
            "product": active_deals.loc[row, "Product"],
            "stage": active_deals.loc[row, "Stage"],
            "stage_probability": float(active_deals.loc[row, "Stage_Probability"]),
            "annual_acv": float(active_deals.loc[row, "Annual_Amount"]),
            "billing": active_deals.loc[row, "Billing_Frequency"],
            "close_date": active_deals.loc[row, "Close_Date"],
            "sales_rep": active_deals.loc[row, "Sales_Rep"],
            "past_due": bool(active_deals.loc[row, "Close_Date"] < AS_OF),
            "top8": active_deals.at[row, "Top8"],
            "alternate": older_rows.get(deal_id),
        })

    forecast = {
        "meta": {"quarter": QUARTER, "as_of": AS_OF, "board_target": BOARD_TARGET,
                 "generated_at": datetime.now().isoformat(timespec="seconds")},
        "baseline": totals,
        "deals": deals,
        "movements": movements,
        "audit_log": audit_log,
    }
    with open(output, "w") as file:
        json.dump(forecast, file, indent=2, default=str)


if __name__ == "__main__":
    # Step 1: Load
    sfdc_current, sfdc_prior, hubspot, scorecard = load_files()

    # Step 2: Standardize
    check_id_lengths(sfdc_current, sfdc_prior)
    sfdc_current, sfdc_prior = truncate_ids(sfdc_current, sfdc_prior)
    sfdc_current = change_monthly_to_annually(sfdc_current)   # Salesforce is all Annual, but this gives it an Annual_Amount column too
    sfdc_prior = change_monthly_to_annually(sfdc_prior)
    hubspot = change_monthly_to_annually(hubspot)
    hubspot = rename_hubspot_columns(hubspot)
    check_unknown_columns(hubspot, sfdc_current)

    # Step 3: Dedupe
    sfdc_current = drop_exact_duplicates(sfdc_current, "Salesforce current")
    sfdc_prior = drop_exact_duplicates(sfdc_prior, "Salesforce prior")
    hubspot = drop_exact_duplicates(hubspot, "HubSpot")
    sfdc_current, older_rows = resolve_conflicting_ids(sfdc_current, "Salesforce current")
    sfdc_for_compare = sfdc_current.copy()   # week-over-week only looks at Salesforce
    sfdc_current = remove_cross_system_duplicates(sfdc_current, hubspot)

    # Step 4: Validate
    validate(sfdc_current, "Salesforce current", SFDC_STAGES)
    validate(hubspot, "HubSpot", HUBSPOT_STAGES)

    # Step 5: Classify
    active_deals = combine_active_deals(sfdc_current, hubspot)

    # Step 6: Top 8
    matches = match_scorecard(scorecard, active_deals)
    active_deals = score_top8(scorecard, active_deals, matches)

    # Step 7: Compare weeks
    movements = compare_weeks(sfdc_for_compare, sfdc_prior)

    # Step 8: Output
    totals = calculate_totals(active_deals)
    write_forecast(active_deals, older_rows, movements, totals)

    print(f"Top 8 expected value:     ${totals['top8']:,.0f}")
    print(f"Core longtail:            ${totals['core_longtail']:,.0f}")
    print(f"CareBridge longtail:      ${totals['carebridge_longtail']:,.0f}")
    print(f"Total expected Q4 sales:  ${totals['total']:,.0f}")
    print(f"Variance to target:       ${totals['variance']:+,.0f}")
    print(f"Audit log entries: {len(audit_log)}")