"""
Credit Card Financial Dashboard - sample data generator
Outputs 3 CSVs (customers, transactions, statements) with realistic
patterns AND deliberate data-quality issues for the cleaning step.

Run from repo root:  python scripts/generate_credit_card_data.py
Needs: pandas, numpy
"""
import os
import numpy as np
import pandas as pd

os.makedirs("data/raw", exist_ok=True)

rng = np.random.default_rng(42)
N_CUST = 2000

# ---------------------------------------------------------------- customers
card_types = ["Silver", "Gold", "Platinum", "Signature"]
limit_by_card = {"Silver": 50000, "Gold": 150000, "Platinum": 300000, "Signature": 600000}
card_p = [0.40, 0.33, 0.20, 0.07]

cust = pd.DataFrame({
    "Customer_ID": [f"C{str(i).zfill(5)}" for i in range(1, N_CUST + 1)],
    "Age": rng.integers(21, 70, N_CUST),
    "Gender": rng.choice(["Male", "Female"], N_CUST, p=[0.52, 0.48]),
    "Marital_Status": rng.choice(["Married", "Single", "Divorced"], N_CUST, p=[0.55, 0.35, 0.10]),
    "Region": rng.choice(["North", "South", "East", "West"], N_CUST),
    "Card_Type": rng.choice(card_types, N_CUST, p=card_p),
})
cust["Annual_Income"] = (
    cust["Card_Type"].map({"Silver": 500000, "Gold": 1000000, "Platinum": 2000000, "Signature": 4000000})
    * rng.uniform(0.7, 1.4, N_CUST)
).round(-3)
cust["Credit_Limit"] = cust["Card_Type"].map(limit_by_card)
cust["Join_Date"] = pd.to_datetime("2019-01-01") + pd.to_timedelta(rng.integers(0, 2000, N_CUST), unit="D")

# ------------------------------------------------------------ transactions
categories = ["Travel", "Entertainment", "Utilities", "Groceries", "Dining",
              "Shopping", "Fuel", "Healthcare", "Education"]
cat_p = [0.14, 0.09, 0.10, 0.18, 0.12, 0.17, 0.08, 0.07, 0.05]
cat_scale = {"Travel": 18000, "Entertainment": 2500, "Utilities": 3000, "Groceries": 2800,
             "Dining": 1800, "Shopping": 6000, "Fuel": 2200, "Healthcare": 4500, "Education": 9000}

start, end = pd.Timestamp("2025-01-01"), pd.Timestamp("2025-12-31")
rows = []
for cid, card, limit in cust[["Customer_ID", "Card_Type", "Credit_Limit"]].itertuples(index=False):
    n = int(rng.poisson({"Silver": 14, "Gold": 20, "Platinum": 26, "Signature": 32}[card]))
    cats = rng.choice(categories, n, p=cat_p)
    mult = limit / 150000
    for c in cats:
        amt = max(100, rng.gamma(2.0, cat_scale[c] / 2) * (0.6 + 0.4 * mult))
        d = start + pd.to_timedelta(int(rng.integers(0, (end - start).days + 1)), unit="D")
        rows.append((cid, d, c, round(amt, 2), rng.choice(["Online", "POS", "Contactless"], p=[0.45, 0.35, 0.20])))
txn = pd.DataFrame(rows, columns=["Customer_ID", "Txn_Date", "Category", "Amount", "Channel"])
txn.insert(0, "Txn_ID", [f"T{str(i).zfill(7)}" for i in range(1, len(txn) + 1)])

# -------------------------------------------------------------- statements
txn["Statement_Month"] = txn["Txn_Date"].dt.to_period("M").dt.to_timestamp()
monthly = txn.groupby(["Customer_ID", "Statement_Month"], as_index=False)["Amount"].sum()
monthly = monthly.rename(columns={"Amount": "Actual_Spend"})
st = monthly.merge(cust[["Customer_ID", "Credit_Limit", "Annual_Income"]], on="Customer_ID")

# forecast = what the bank expected the customer to use (~ 30% of limit, +/- noise)
st["Forecast_Usage"] = (st["Credit_Limit"] * rng.uniform(0.20, 0.40, len(st))).round(2)
# risk profile drives payment behaviour
risk = rng.choice(["Low", "Medium", "High"], N_CUST, p=[0.60, 0.28, 0.12])
risk_map = dict(zip(cust["Customer_ID"], risk))
st["_risk"] = st["Customer_ID"].map(risk_map)
carry = st["_risk"].map({"Low": 0.02, "Medium": 0.15, "High": 0.45})
st["Outstanding_Balance"] = (st["Actual_Spend"] * (carry + rng.uniform(0, 0.2, len(st)))).round(2)
st["Min_Due"] = (st["Outstanding_Balance"] * 0.05).round(2)
st["Due_Date"] = st["Statement_Month"] + pd.offsets.MonthEnd(0) + pd.Timedelta(days=20)
late_p = st["_risk"].map({"Low": 0.04, "Medium": 0.25, "High": 0.60})
is_late = rng.random(len(st)) < late_p
delay = np.where(is_late, rng.integers(1, 45, len(st)), -rng.integers(0, 10, len(st)))
st["Payment_Date"] = st["Due_Date"] + pd.to_timedelta(delay, unit="D")
st["Paid_Amount"] = np.where(
    st["_risk"].eq("High") & (rng.random(len(st)) < 0.5), st["Min_Due"],
    (st["Actual_Spend"] - st["Outstanding_Balance"]).clip(lower=0)).round(2)
st = st.drop(columns=["_risk", "Annual_Income"])
st.insert(0, "Statement_ID", [f"S{str(i).zfill(6)}" for i in range(1, len(st) + 1)])

# ------------------------------------- inject realistic data-quality issues
def blank(df, col, frac):
    idx = df.sample(frac=frac, random_state=int(rng.integers(0, 9999))).index
    df.loc[idx, col] = np.nan

blank(cust, "Age", 0.015)
blank(cust, "Annual_Income", 0.02)
blank(txn, "Category", 0.01)
blank(st, "Payment_Date", 0.03)       # never paid / missing
cust["Gender"] = cust["Gender"].replace({"Male": "M"}).where(rng.random(N_CUST) > 0.05, "male ")   # inconsistent labels
txn["Category"] = txn["Category"].replace({"Groceries": "grocery"}).where(rng.random(len(txn)) > 0.03, "GROCERIES")
txn.loc[txn.sample(frac=0.003, random_state=1).index, "Amount"] *= -1   # bad sign values
txn = pd.concat([txn, txn.sample(frac=0.002, random_state=2)])          # duplicate rows

cust.to_csv("data/raw/customers.csv", index=False)
txn.drop(columns=["Statement_Month"]).to_csv("data/raw/transactions.csv", index=False)
st.to_csv("data/raw/statements.csv", index=False)
print(f"customers: {len(cust):,} | transactions: {len(txn):,} | statements: {len(st):,}")
