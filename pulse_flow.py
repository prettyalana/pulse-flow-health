import pandas as pd

RAW_DIR = "data/raw"

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
        
def check_for_duplicates(table):
    
   duplicates = table[table.duplicated()]
   
   return duplicates

if __name__ == "__main__":
    sfdc_current, sfdc_prior, hubspot, scorecard = load_files()
    
    sfdc_current, sfdc_prior  = truncate_ids(sfdc_current, sfdc_prior)
    hubspot = change_monthly_to_annually(hubspot)
    hubspot = rename_hubspot_columns(hubspot)

    for name, table in [("SFDC current", sfdc_current), ("SFDC prior", sfdc_prior),
                        ("HubSpot", hubspot), ("Scorecard", scorecard)]:
        print(f"\n=== {name}: {len(table)} rows")
        print(table.head())
    
    print(f"DUPLICATES")
    for name, table in [("SFDC current", sfdc_current), ("SFDC prior", sfdc_prior),
                        ("HubSpot", hubspot), ("Scorecard", scorecard)]:
        duplicates = check_for_duplicates(table)
        print(f"\n=== {name}: {len(duplicates)} exact duplicate(s)")
        print(duplicates)