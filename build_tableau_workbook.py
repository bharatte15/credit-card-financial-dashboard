"""
Builds CreditCard_Dashboard.twbx from customers.csv / transactions.csv / statements.csv
Run from repo root (after generate_credit_card_data.py):  python scripts/build_tableau_workbook.py     (needs pandas, numpy)
"""
import re, zipfile, os
import numpy as np
import pandas as pd
import xml.etree.ElementTree as ET

# =============================================================== 1. CLEAN
cust = pd.read_csv("data/raw/customers.csv")
txn = pd.read_csv("data/raw/transactions.csv", parse_dates=["Txn_Date"])
st = pd.read_csv("data/raw/statements.csv", parse_dates=["Statement_Month", "Due_Date", "Payment_Date"])

cust["Gender"] = (cust["Gender"].astype("string").str.strip().str.title()
                  .replace({"M": "Male"}))
cust["Age"] = cust["Age"].fillna(cust["Age"].median())
cust["Annual_Income"] = cust["Annual_Income"].fillna(cust["Annual_Income"].median())
cust["Age_Group"] = pd.cut(cust["Age"], [0, 24, 34, 44, 54, 200],
                           labels=["18-24", "25-34", "35-44", "45-54", "55+"]).astype(str)
cust["Income_Band"] = pd.cut(cust["Annual_Income"], [-1, 599999, 1499999, 2999999, 1e12],
                             labels=["Low", "Middle", "Upper-Middle", "High"]).astype(str)
dims = cust[["Customer_ID", "Card_Type", "Region", "Age_Group", "Income_Band", "Gender", "Marital_Status"]]

txn = txn.drop_duplicates("Txn_ID").copy()
txn["Category"] = (txn["Category"].astype("string").str.strip().str.title()
                   .replace({"Grocery": "Groceries"}).fillna("Uncategorized"))
txn["Amount"] = txn["Amount"].abs()
txn = txn.merge(dims.drop(columns=["Marital_Status"]), on="Customer_ID", how="left")

st["Days_Late"] = (st["Payment_Date"] - st["Due_Date"]).dt.days
st["Payment_Status"] = np.select(
    [st["Payment_Date"].isna(), st["Days_Late"] <= 0, st["Days_Late"] <= 30],
    ["Unpaid", "On-time", "Late (1-30d)"], default="Delinquent (30d+)")
st["Is_Late"] = (st["Payment_Status"] != "On-time").astype(int)
st["Is_Overspend"] = (st["Actual_Spend"] > st["Forecast_Usage"]).astype(int)
st["Usage_Variance"] = (st["Actual_Spend"] - st["Forecast_Usage"]).round(2)
risk = st.groupby("Customer_ID").apply(
    lambda g: pd.Series({"u": (g["Actual_Spend"] / g["Credit_Limit"]).mean(), "l": g["Is_Late"].mean()}),
    include_groups=False)
risk["Risk_Score"] = (100 * (0.4 * risk["u"].clip(upper=1) + 0.6 * risk["l"])).round(1)
risk["Risk_Tier"] = np.select([risk["Risk_Score"] >= 40, risk["Risk_Score"] >= 20], ["High", "Medium"], "Low")
st = st.merge(risk[["Risk_Score", "Risk_Tier"]], left_on="Customer_ID", right_index=True)
st = st.merge(dims, on="Customer_ID", how="left")

os.makedirs("data/processed", exist_ok=True)
for df, f in [(st, "cc_statements.csv"), (txn, "cc_transactions.csv")]:
    for c in df.select_dtypes("datetime").columns:
        df[c] = df[c].dt.strftime("%Y-%m-%d")
    df.to_csv(f"data/processed/{f}", index=False)

# quick facts for the write-up
print("Utilization %:", round(st["Actual_Spend"].sum() / st["Credit_Limit"].sum() * 100, 1))
print("Late+unpaid rate %:", round(st["Is_Late"].mean() * 100, 1))
print("Overspend rate %:", round(st["Is_Overspend"].mean() * 100, 1))
print("Risk tiers (customers):", risk["Risk_Tier"].value_counts().to_dict())
print("Top categories:", txn.groupby("Category")["Amount"].sum().sort_values(ascending=False).head(3).round(0).to_dict())

# =============================================================== 2. TWB
TYPE = {"string": ("129", "Count"), "integer": ("20", "Sum"), "real": ("5", "Sum"), "date": ("133", "Year")}
def col_types(df):
    out = []
    for c in df.columns:
        if c in ("Statement_Month", "Due_Date", "Payment_Date", "Txn_Date"): t = "date"
        elif pd.api.types.is_integer_dtype(df[c]): t = "integer"
        elif pd.api.types.is_float_dtype(df[c]): t = "real"
        else: t = "string"
        out.append((c, t))
    return out

ST_COLS = col_types(pd.read_csv("data/processed/cc_statements.csv"))
TX_COLS = col_types(pd.read_csv("data/processed/cc_transactions.csv"))

CALCS = {
    "stmts": [
        ("Calculation_1001", "Utilization %", "SUM([Actual_Spend])/SUM([Credit_Limit])", "p0.0%"),
        ("Calculation_1002", "Late/Unpaid Rate %", "SUM([Is_Late])/COUNT([Statement_ID])", "p0.0%"),
        ("Calculation_1003", "Usage Variance %", "(SUM([Actual_Spend])-SUM([Forecast_Usage]))/SUM([Forecast_Usage])", "p0.0%"),
        ("Calculation_1004", "Overspend Rate %", "SUM([Is_Overspend])/COUNT([Statement_ID])", "p0.0%"),
    ],
    "txns": [
        ("Calculation_2001", "Avg Txn Value", "AVG([Amount])", "n#,##0"),
        ("Calculation_2002", "Spend per Customer", "SUM([Amount])/COUNTD([Customer_ID])", "n#,##0"),
    ],
}
DS = {
    "stmts": dict(name="federated.cc_statements", caption="Statements", file="cc_statements.csv", cols=ST_COLS),
    "txns": dict(name="federated.cc_transactions", caption="Transactions", file="cc_transactions.csv", cols=TX_COLS),
}

def field_info(key):
    info = {}
    for n, t in DS[key]["cols"]:
        if t in ("integer", "real"): info[n] = (t, "measure", "quantitative", None, None, None)
        elif t == "date": info[n] = (t, "dimension", "ordinal", None, None, None)
        else: info[n] = (t, "dimension", "nominal", None, None, None)
    for cid, cap, f, fmt in CALCS[key]:
        info[cid] = ("real", "measure", "quantitative", f, cap, fmt)
    return info
INFO = {k: field_info(k) for k in DS}

def S(parent, tag, **attrs): return ET.SubElement(parent, tag, {k: str(v) for k, v in attrs.items()})

wb = ET.Element("workbook", {"original-version": "18.1", "source-build": "2023.1.0 (20231.23.0509.1121)",
                             "source-platform": "win", "version": "18.1",
                             "xmlns:user": "http://www.tableausoftware.com/xml/user"})
dss = S(wb, "datasources")
for key, d in DS.items():
    ds = S(dss, "datasource", caption=d["caption"], inline="true", name=d["name"], version="18.1")
    conn = S(ds, "connection", **{"class": "federated"})
    ncs = S(conn, "named-connections")
    nc = S(ncs, "named-connection", caption="Data", name="textscan.1")
    S(nc, "connection", **{"class": "textscan", "directory": "Data", "filename": d["file"], "password": "", "server": ""})
    rel = S(conn, "relation", connection="textscan.1", name=d["file"], table="[%s]" % d["file"].replace(".", "#"), type="table")
    cs = S(rel, "columns", **{"character-set": "UTF-8", "header": "yes", "locale": "en_US", "separator": ","})
    for i, (n, t) in enumerate(d["cols"]):
        S(cs, "column", datatype=t, name=n, ordinal=i)
    mdr = S(conn, "metadata-records")
    for i, (n, t) in enumerate(d["cols"]):
        r = S(mdr, "metadata-record", **{"class": "column"})
        rt, agg = TYPE[t]
        S(r, "remote-name").text = n
        S(r, "remote-type").text = rt
        S(r, "local-name").text = "[%s]" % n
        S(r, "parent-name").text = "[%s]" % d["file"]
        S(r, "remote-alias").text = n
        S(r, "ordinal").text = str(i)
        S(r, "local-type").text = t
        S(r, "aggregation").text = agg
        S(r, "contains-null").text = "true"
    S(ds, "aliases", enabled="yes")
    for cid, cap, f, fmt in CALCS[key]:
        c = S(ds, "column", caption=cap, datatype="real", name="[%s]" % cid, role="measure", type="quantitative")
        c.set("default-format", fmt)
        S(c, "calculation", **{"class": "tableau", "formula": f})

# ---- helpers to describe shelf fields
def dim(f): return (f, "None", "none:%s:nk" % f, "nominal")
def msum(f): return (f, "Sum", "sum:%s:qk" % f, "quantitative")
def mcnt(f): return (f, "Count", "cnt:%s:qk" % f, "quantitative")
def mtrunc(f): return (f, "Month-Trunc", "tmn:%s:qk" % f, "quantitative")
def calc(cid): return (cid, "User", "usr:%s:qk" % cid, "quantitative")

def worksheet(wss, name, key, rows, cols, mark, color=None, detail=None):
    d, info = DS[key], INFO[key]
    ws = S(wss, "worksheet", name=name)
    table = S(ws, "table")
    view = S(table, "view")
    vds = S(view, "datasources"); S(vds, "datasource", caption=d["caption"], name=d["name"])
    shelf = [x for x in (rows + cols + ([color] if color else []) + ([detail] if detail else []))]
    used_cols = []
    for f, *_ in shelf:
        used_cols.append(f)
        formula = info[f][3]
        if formula:
            used_cols += re.findall(r"\[([^\]]+)\]", formula)
    dep = S(view, "datasource-dependencies", datasource=d["name"])
    seen = set()
    for f in used_cols:
        if f in seen: continue
        seen.add(f)
        dt, role, typ, formula, cap, fmt = info[f]
        attrs = dict(datatype=dt, name="[%s]" % f, role=role, type=typ)
        if cap: attrs["caption"] = cap
        c = S(dep, "column", **attrs)
        if formula: S(c, "calculation", **{"class": "tableau", "formula": formula})
    seen_i = set()
    for f, deriv, iname, typ in shelf:
        if iname in seen_i: continue
        seen_i.add(iname)
        S(dep, "column-instance", column="[%s]" % f, derivation=deriv, name="[%s]" % iname, pivot="key", type=typ)
    S(view, "aggregation", value="true")
    S(table, "style")
    panes = S(table, "panes")
    pane = S(panes, "pane", **{"selection-relaxation-option": "selection-relaxation-allow"})
    pv = S(pane, "view"); S(pv, "breakdown", value="auto")
    S(pane, "mark", **{"class": mark})
    if color or detail:
        enc = S(pane, "encodings")
        if detail: S(enc, "lod", column="[%s].[%s]" % (d["name"], detail[2]))
        if color: S(enc, "color", column="[%s].[%s]" % (d["name"], color[2]))
    ref = lambda items: " + ".join("[%s].[%s]" % (d["name"], i[2]) for i in items)
    r, c_ = ref(rows), ref(cols)
    S(table, "rows").text = "(%s)" % r if len(rows) > 1 else r
    S(table, "cols").text = c_
    return ws

wss = S(wb, "worksheets")
SHEETS = {}
def add(name, *a, **k):
    worksheet(wss, name, *a, **k); SHEETS[name] = a[0]

# Dashboard 1: Spend & Behaviour
add("Monthly Spend", "txns", [msum("Amount")], [mtrunc("Txn_Date")], "Line")
add("Spend by Category", "txns", [dim("Category")], [msum("Amount")], "Bar")
add("Category x Age Group", "txns", [dim("Category")], [dim("Age_Group")], "Square", color=msum("Amount"))
add("Spend by Card Type", "txns", [dim("Card_Type")], [msum("Amount")], "Bar", color=dim("Channel"))
# Dashboard 2: Credit usage, payments and risk
add("Actual vs Forecast Usage", "stmts", [msum("Actual_Spend"), msum("Forecast_Usage")], [mtrunc("Statement_Month")], "Line")
add("Utilization by Card Type", "stmts", [dim("Card_Type")], [calc("Calculation_1001")], "Bar")
add("Payment Status by Month", "stmts", [mcnt("Statement_ID")], [mtrunc("Statement_Month")], "Bar", color=dim("Payment_Status"))
add("Late Rate by Card Type", "stmts", [dim("Card_Type")], [calc("Calculation_1002")], "Bar")
add("Risk Scatter", "stmts", [calc("Calculation_1002")], [calc("Calculation_1001")], "Circle",
    color=dim("Risk_Tier"), detail=dim("Customer_ID"))

# ---- dashboards
DASH = {
    "Spend & Behaviour": [["Monthly Spend", "Spend by Category"], ["Category x Age Group", "Spend by Card Type"]],
    "Credit & Risk": [["Actual vs Forecast Usage", "Utilization by Card Type"],
                      ["Payment Status by Month", "Late Rate by Card Type"], ["Risk Scatter"]],
}
dbs = S(wb, "dashboards")
zid = [10]
def nid(): zid[0] += 1; return zid[0]
for dname, grid in DASH.items():
    db = S(dbs, "dashboard", name=dname)
    S(db, "style")
    S(db, "size", maxheight=900, maxwidth=1200, minheight=900, minwidth=1200, **{"sizing-mode": "fixed"})
    zs = S(db, "zones")
    outer = S(zs, "zone", h=100000, id=nid(), **{"type-v2": "layout-basic"}, w=100000, x=0, y=0)
    vert = S(outer, "zone", h=100000, id=nid(), param="vert", **{"type-v2": "layout-flow"}, w=100000, x=0, y=0)
    rh = 100000 // len(grid)
    for ri, row in enumerate(grid):
        hz = S(vert, "zone", h=rh, id=nid(), param="horz", **{"type-v2": "layout-flow"}, w=100000, x=0, y=ri * rh)
        cw = 100000 // len(row)
        for ci, sh in enumerate(row):
            S(hz, "zone", h=rh, id=nid(), name=sh, w=cw, x=ci * cw, y=ri * rh)

# ---- windows
wins = S(wb, "windows", **{"source-height": 30})
for sh in SHEETS:
    w = S(wins, "window", **{"class": "worksheet", "name": sh})
    cards = S(w, "cards")
    left = S(cards, "edge", name="left")
    strip = S(left, "strip", size=160)
    for t in ("pages", "filters", "marks"): S(strip, "card", type=t)
    top = S(cards, "edge", name="top")
    for t, sz in (("columns", 2147483647), ("rows", 2147483647), ("title", 31)):
        S(S(top, "strip", size=sz), "card", type=t)
for i, (dname, grid) in enumerate(DASH.items()):
    w = S(wins, "window", **{"class": "dashboard", "name": dname})
    if i == 0: w.set("maximized", "true")
    vps = S(w, "viewpoints")
    for row in grid:
        for sh in row: S(vps, "viewpoint", name=sh)
    S(w, "active", id=-1)

OUT = "CreditCard_Dashboard"
ET.indent(wb)
ET.ElementTree(wb).write(f"{OUT}.twb", encoding="utf-8", xml_declaration=True)
ET.parse(f"{OUT}.twb")  # well-formedness check

os.makedirs("tableau", exist_ok=True)
with zipfile.ZipFile(f"tableau/{OUT}.twbx", "w", zipfile.ZIP_DEFLATED) as z:
    z.write(f"{OUT}.twb")
    for f in ("cc_statements.csv", "cc_transactions.csv"):
        z.write(f"data/processed/{f}", f"Data/{f}")
os.remove(f"{OUT}.twb")
print("Built", f"tableau/{OUT}.twbx", "-", len(SHEETS), "sheets,", len(DASH), "dashboards")
