# Credit Card Financial Dashboard: Implementation Guide

Files: `generate_credit_card_data.py` creates `customers.csv`, `transactions.csv`, `statements.csv`.
If you have the real dataset, map its columns to the names used below.

---

## 1. Data Model (star-style)

```
Dim_Customer (Customer_ID PK) ──< Fact_Transactions (Txn_ID PK)
Dim_Customer (Customer_ID PK) ──< Fact_Statements   (Statement_ID PK)
Dim_Date (Date PK) ──< Fact_Transactions[Txn_Date]
Dim_Date (Date PK) ──< Fact_Statements[Statement_Month]
```
- All relationships: one-to-many, single direction (Dim → Fact).
- Mark `Dim_Date` as a date table.
- Hide foreign keys and raw columns in report view.

---

## 2. Power Query (M)

### 2.1 Dim_Customer
```m
let
    Source = Csv.Document(File.Contents("C:\Data\customers.csv"), [Delimiter=",", Encoding=65001, QuoteStyle=QuoteStyle.Csv]),
    Promoted = Table.PromoteHeaders(Source, [PromoteAllScalars=true]),
    Typed = Table.TransformColumnTypes(Promoted, {
        {"Customer_ID", type text}, {"Age", Int64.Type}, {"Gender", type text},
        {"Marital_Status", type text}, {"Region", type text}, {"Card_Type", type text},
        {"Annual_Income", type number}, {"Credit_Limit", type number}, {"Join_Date", type date}}),
    Trimmed = Table.TransformColumns(Typed, {{"Gender", each Text.Proper(Text.Trim(_)), type text}}),
    FixGender = Table.ReplaceValue(Trimmed, "M", "Male", Replacer.ReplaceValue, {"Gender"}),
    // Missing values: impute median-style defaults, but keep a flag for transparency
    AgeFlag = Table.AddColumn(FixGender, "Age_Imputed", each [Age] = null, type logical),
    MedianAge = List.Median(List.RemoveNulls(Table.Column(AgeFlag, "Age"))),
    FillAge = Table.ReplaceValue(AgeFlag, null, MedianAge, Replacer.ReplaceValue, {"Age"}),
    MedianInc = List.Median(List.RemoveNulls(Table.Column(FillAge, "Annual_Income"))),
    FillInc = Table.ReplaceValue(FillAge, null, MedianInc, Replacer.ReplaceValue, {"Annual_Income"}),
    AgeGroup = Table.AddColumn(FillInc, "Age_Group", each
        if [Age] < 25 then "18-24" else if [Age] < 35 then "25-34" else if [Age] < 45 then "35-44"
        else if [Age] < 55 then "45-54" else "55+", type text),
    IncomeBand = Table.AddColumn(AgeGroup, "Income_Band", each
        if [Annual_Income] < 600000 then "Low" else if [Annual_Income] < 1500000 then "Middle"
        else if [Annual_Income] < 3000000 then "Upper-Middle" else "High", type text),
    Dedup = Table.Distinct(IncomeBand, {"Customer_ID"})
in
    Dedup
```

### 2.2 Fact_Transactions
```m
let
    Source = Csv.Document(File.Contents("C:\Data\transactions.csv"), [Delimiter=",", Encoding=65001, QuoteStyle=QuoteStyle.Csv]),
    Promoted = Table.PromoteHeaders(Source, [PromoteAllScalars=true]),
    Typed = Table.TransformColumnTypes(Promoted, {
        {"Txn_ID", type text}, {"Customer_ID", type text}, {"Txn_Date", type date},
        {"Category", type text}, {"Amount", type number}, {"Channel", type text}}),
    Dedup = Table.Distinct(Typed, {"Txn_ID"}),
    // Standardize category names
    Clean = Table.TransformColumns(Dedup, {{"Category", each Text.Proper(Text.Trim(_)), type text}}),
    Fix1 = Table.ReplaceValue(Clean, "Grocery", "Groceries", Replacer.ReplaceText, {"Category"}),
    // Missing category -> "Uncategorized" (kept, not dropped, so totals reconcile)
    Fix2 = Table.ReplaceValue(Fix1, null, "Uncategorized", Replacer.ReplaceValue, {"Category"}),
    // Negative amounts are sign errors in this dataset; use absolute value
    Fix3 = Table.TransformColumns(Fix2, {{"Amount", each Number.Abs(_), type number}}),
    Month = Table.AddColumn(Fix3, "Statement_Month", each Date.StartOfMonth([Txn_Date]), type date)
in
    Month
```

### 2.3 Fact_Statements
```m
let
    Source = Csv.Document(File.Contents("C:\Data\statements.csv"), [Delimiter=",", Encoding=65001, QuoteStyle=QuoteStyle.Csv]),
    Promoted = Table.PromoteHeaders(Source, [PromoteAllScalars=true]),
    Typed = Table.TransformColumnTypes(Promoted, {
        {"Statement_ID", type text}, {"Customer_ID", type text}, {"Statement_Month", type date},
        {"Actual_Spend", type number}, {"Credit_Limit", type number}, {"Forecast_Usage", type number},
        {"Outstanding_Balance", type number}, {"Min_Due", type number}, {"Due_Date", type date},
        {"Payment_Date", type date}, {"Paid_Amount", type number}}),
    DaysLate = Table.AddColumn(Typed, "Days_Late", each
        if [Payment_Date] = null then null else Duration.Days([Payment_Date] - [Due_Date]), Int64.Type),
    Status = Table.AddColumn(DaysLate, "Payment_Status", each
        if [Payment_Date] = null then "Unpaid"
        else if [Days_Late] <= 0 then "On-time"
        else if [Days_Late] <= 30 then "Late (1-30d)"
        else "Delinquent (30d+)", type text),
    Util = Table.AddColumn(Status, "Utilization_Pct", each
        if [Credit_Limit] = 0 then null else [Actual_Spend] / [Credit_Limit], type number),
    Variance = Table.AddColumn(Util, "Usage_Variance", each [Actual_Spend] - [Forecast_Usage], type number)
in
    Variance
```

### 2.4 Dim_Date (DAX calculated table)
```dax
Dim_Date =
ADDCOLUMNS(
    CALENDAR(DATE(2025,1,1), DATE(2025,12,31)),
    "Year", YEAR([Date]),
    "Month No", MONTH([Date]),
    "Month", FORMAT([Date], "mmm"),
    "Quarter", "Q" & QUARTER([Date]),
    "Weekday", FORMAT([Date], "ddd")
)
```
Sort `Month` by `Month No`.

### 2.5 Customer risk table (DAX calculated table)
```dax
Customer_Risk =
ADDCOLUMNS(
    VALUES(Dim_Customer[Customer_ID]),
    "Avg Util", CALCULATE(AVERAGE(Fact_Statements[Utilization_Pct])),
    "Late Rate", DIVIDE(
        CALCULATE(COUNTROWS(Fact_Statements), Fact_Statements[Payment_Status] <> "On-time"),
        CALCULATE(COUNTROWS(Fact_Statements))),
    "Risk Score",
        VAR u = CALCULATE(AVERAGE(Fact_Statements[Utilization_Pct]))
        VAR l = DIVIDE(
            CALCULATE(COUNTROWS(Fact_Statements), Fact_Statements[Payment_Status] <> "On-time"),
            CALCULATE(COUNTROWS(Fact_Statements)))
        RETURN ROUND(100 * (0.4 * MIN(u, 1) + 0.6 * COALESCE(l, 0)), 1)
)
```
Add a column: `Risk_Tier = IF([Risk Score] >= 40, "High", IF([Risk Score] >= 20, "Medium", "Low"))`.

---

## 3. DAX Measures

Create a `_Measures` table (Enter Data, blank) and put these in it.

```dax
-- Spend
Total Spend = SUM(Fact_Transactions[Amount])
Txn Count = COUNTROWS(Fact_Transactions)
Avg Txn Value = DIVIDE([Total Spend], [Txn Count])
Active Customers = DISTINCTCOUNT(Fact_Transactions[Customer_ID])
Spend per Customer = DIVIDE([Total Spend], [Active Customers])
Spend LY = CALCULATE([Total Spend], SAMEPERIODLASTYEAR(Dim_Date[Date]))
Spend MoM % =
VAR prev = CALCULATE([Total Spend], DATEADD(Dim_Date[Date], -1, MONTH))
RETURN DIVIDE([Total Spend] - prev, prev)
Category Share % =
DIVIDE([Total Spend], CALCULATE([Total Spend], ALL(Fact_Transactions[Category])))

-- Credit usage vs forecast
Total Credit Limit = SUM(Fact_Statements[Credit_Limit])
Actual Usage = SUM(Fact_Statements[Actual_Spend])
Forecast Usage = SUM(Fact_Statements[Forecast_Usage])
Usage Variance = [Actual Usage] - [Forecast Usage]
Usage Variance % = DIVIDE([Usage Variance], [Forecast Usage])
Credit Utilization % = DIVIDE([Actual Usage], [Total Credit Limit])
Overspend Accounts =
COUNTROWS(FILTER(Fact_Statements, Fact_Statements[Actual_Spend] > Fact_Statements[Forecast_Usage]))
Overspend Rate % = DIVIDE([Overspend Accounts], COUNTROWS(Fact_Statements))
High Utilization Customers =
COUNTROWS(FILTER(VALUES(Dim_Customer[Customer_ID]),
    CALCULATE(AVERAGE(Fact_Statements[Utilization_Pct])) > 0.30))

-- Payment behaviour
Statements = COUNTROWS(Fact_Statements)
On-time Payments = CALCULATE([Statements], Fact_Statements[Payment_Status] = "On-time")
Late Payments = CALCULATE([Statements], Fact_Statements[Payment_Status] IN {"Late (1-30d)", "Delinquent (30d+)"})
Unpaid Statements = CALCULATE([Statements], Fact_Statements[Payment_Status] = "Unpaid")
On-time Rate % = DIVIDE([On-time Payments], [Statements])
Late Rate % = DIVIDE([Late Payments], [Statements])
Avg Days Late = CALCULATE(AVERAGE(Fact_Statements[Days_Late]), Fact_Statements[Days_Late] > 0)
Payment Coverage % = DIVIDE(SUM(Fact_Statements[Paid_Amount]), [Actual Usage])
Min-Due-Only Rate % =
DIVIDE(
    COUNTROWS(FILTER(Fact_Statements, Fact_Statements[Paid_Amount] <= Fact_Statements[Min_Due] * 1.01)),
    [Statements])

-- Credit risk
Total Outstanding = SUM(Fact_Statements[Outstanding_Balance])
Outstanding % of Limit = DIVIDE([Total Outstanding], [Total Credit Limit])
Delinquent Outstanding =
CALCULATE([Total Outstanding], Fact_Statements[Payment_Status] = "Delinquent (30d+)")
Delinquent Exposure % = DIVIDE([Delinquent Outstanding], [Total Outstanding])
High Risk Customers = CALCULATE(COUNTROWS(Customer_Risk), Customer_Risk[Risk_Tier] = "High")
High Risk % = DIVIDE([High Risk Customers], COUNTROWS(Customer_Risk))

-- Dynamic titles / insights
Insight Top Category =
VAR t = TOPN(1, VALUES(Fact_Transactions[Category]), [Total Spend], DESC)
RETURN "Top category: " & MAXX(t, Fact_Transactions[Category]) & " (" &
       FORMAT(CALCULATE([Category Share %], t), "0.0%") & " of spend)"

-- KPI colour (conditional formatting by rules)
Late Rate Colour = IF([Late Rate %] > 0.2, "#C0392B", IF([Late Rate %] > 0.1, "#E67E22", "#27AE60"))
```

---

## 4. Dashboard Layout (4 pages + tooltip)

**Global navigation:** a left rail of page-navigator buttons (Home | Spend | Credit Usage | Payments & Risk), Bookmarks for "Reset filters", and a shared slicer panel (Year, Quarter, Card Type, Region, Age Group, Income Band) synced across all pages. Use consistent colours: teal = healthy, amber = watch, red = risk.

### Page 1: Executive Overview
| Zone | Visual | Fields |
|---|---|---|
| Top | 6 KPI cards | Total Spend, Active Customers, Credit Utilization %, On-time Rate %, Total Outstanding, High Risk % |
| Mid-left | Line + column | Total Spend by month, Spend MoM % |
| Mid-right | Donut (≤6 slices, group the rest) | Spend by Category |
| Bottom | Smart narrative or dynamic-title text | `Insight Top Category` |

### Page 2: Spending Behaviour
- Clustered bar: Category × Card Type (Total Spend)
- Matrix with heatmap formatting: Category (rows) × Age Group (cols), value = Spend per Customer
- Stacked column: Channel mix by month
- Treemap: Spend by Region → Category
- Decomposition tree: Total Spend by Card Type > Income Band > Category

### Page 3: Credit Usage vs Forecast
- Gauge/KPI: Credit Utilization % (target 30%)
- Line chart with two lines: Actual Usage vs Forecast Usage by month, plus the Usage Variance % bars
- Scatter: X = Credit_Limit, Y = Avg Utilization %, size = Total Outstanding, colour = Card Type
- Table with data bars: Top 20 customers by Usage Variance
- Histogram (binned Utilization_Pct): shows the share of customers above 30%, 50%, 80%

### Page 4: Payments & Credit Risk
- 100% stacked column: Payment_Status by month
- Funnel/bar: On-time → Late → Delinquent → Unpaid
- Scatter: Avg Util vs Late Rate per customer, coloured by Risk_Tier (the "danger zone" quadrant is top-right)
- Matrix: Risk_Tier × Card Type (customer count, outstanding)
- Drill-through page: Customer 360 (right-click any customer ID → spend by category, payment timeline, risk score)

### Tooltip page
Mini card with Spend, Utilization %, Late Rate % for whatever the user hovers over.

**Navigation tips:** use bookmarks, drill-through, and a "How to use" info button on Page 1. Keep each page to 8 visuals or fewer. Add alt text and set tab order.

---

## 5. Insight Checklist (to fill from your data)

Run the dashboard, then write findings in this format: **observation → evidence → so what**.

1. Which 3 categories drive the most spend, and does the mix differ by card type or age?
2. What % of statements exceed forecast usage, and in which segments?
3. How many customers run above 30% utilization consistently?
4. Is the late-payment rate trending up or down by month?
5. What share of customers pay only the minimum due?
6. Do high-utilization customers also pay late? (Check the Page 4 scatter.)
7. What share of outstanding balance sits with delinquent accounts?

---

## 6. Recommendations Template (actionable, tied to metrics)

| # | Finding | Action | Owner | KPI to track |
|---|---|---|---|---|
| 1 | High-risk tier (high utilization + late rate) holds a disproportionate share of outstanding balance | Early-warning outreach at 60% utilization; offer instalment conversion before delinquency | Collections / Risk | Delinquent Exposure %, High Risk % |
| 2 | Min-due-only payers concentrate in specific cards or segments | Targeted nudges, auto-pay incentives, interest-cost transparency in statements | Marketing / CX | Min-Due-Only Rate %, On-time Rate % |
| 3 | Low-utilization, always-on-time customers (low risk) | Limit increase offers and premium-card upgrade campaigns | Product | Utilization %, Spend per Customer |
| 4 | Top spend categories (e.g. Travel, Shopping) | Co-branded offers and category cashback with merchant partners | Partnerships | Category Share %, Avg Txn Value |
| 5 | Actual usage consistently above forecast in certain segments | Recalibrate forecasting models and credit-limit policy by segment | Credit Policy | Usage Variance % |
| 6 | Unpaid or missing payment records | Fix data capture and add a reconciliation check | Data / Ops | Unpaid Statements |

---

## 7. Presentation Narrative (7-8 minutes)

1. **Hook (30s):** "Credit cards are the bank's most profitable and most volatile product. Where is the growth, and where is the risk?"
2. **Data and method (45s):** 3 sources, cleaning steps (deduped, standardized, imputed, flagged), star schema.
3. **Spend story (90s):** Page 1 → Page 2. Who spends, on what, where.
4. **Forecast vs reality (90s):** Page 3. Where customers overshoot, and which segments.
5. **Payment and risk story (120s):** Page 4. The danger-zone scatter; drill through to one real customer to show the 360 view.
6. **Recommendations (90s):** top 3 from the table above with expected KPI impact.
7. **Close (30s):** What the dashboard lets the business monitor weekly, and next steps (predictive default model, live refresh through a Fabric Dataflow Gen2).

**Data integrity talking points:** row counts reconciled before and after cleaning (state the numbers), duplicates removed, sign errors corrected, imputed fields flagged (`Age_Imputed`), missing categories kept as "Uncategorized" so totals tie back to the source.

---

## 8. Tableau Equivalents (if the evaluation requires Tableau)

| Power BI | Tableau |
|---|---|
| Power Query steps | Tableau Prep flow, or Data Interpreter and calculated fields |
| `Total Spend` | `SUM([Amount])` |
| `Credit Utilization %` | `SUM([Actual_Spend]) / SUM([Credit_Limit])` |
| `Late Rate %` | `SUM(IF [Payment_Status]<>"On-time" THEN 1 ELSE 0 END) / COUNT([Statement_ID])` |
| `Usage Variance %` | `(SUM([Actual_Spend]) - SUM([Forecast_Usage])) / SUM([Forecast_Usage])` |
| Spend MoM % | Quick table calc: *Percent Difference From*, computed along Month |
| Category Share % | Quick table calc: *Percent of Total*, computed along Category |
| Customer risk score | Fixed LOD: `{FIXED [Customer_ID] : AVG([Utilization_Pct])}` combined with a late-rate LOD |
| Bookmarks and slicers | Dashboard actions (filter, highlight) + parameters |
| Drill-through | Dashboard action → "Go to Sheet" or a Customer 360 sheet |
| Decomposition tree | Hierarchies + drill-down on the Card Type > Income Band > Category hierarchy |

Layout carries over 1:1: use the same 4 dashboards in a Tableau story.
