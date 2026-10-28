import re
import sqlite3
import json
import os
from pathlib import Path
from io import BytesIO

import pandas as pd
import streamlit as st
from google import genai
from google.genai import types
import sqlglot
from sqlglot import exp

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_CSV = BASE_DIR / "data" / "sales_data.csv"
DB_PATH = BASE_DIR / "sales.db"

REQUIRED = ["Transaction_Id", "Order_date", "Product", "Category", "Region",
            "Sales_channel", "Quantity", "Unit_price", "Discount_amount", "Status"]
MONTHS = {
    "january": "01", "february": "02", "march": "03", "april": "04",
    "may": "05", "june": "06", "july": "07", "august": "08",
    "september": "09", "october": "10", "november": "11", "december": "12"
}


def read_sales_csv(file_obj):
    """Validate and clean an uploaded or bundled sales CSV."""
    df = pd.read_csv(file_obj)
    df.columns = [str(c).strip() for c in df.columns]
    missing = [c for c in REQUIRED if c not in df.columns]
    if missing:
        raise ValueError("Missing required columns: " + ", ".join(missing))

    raw_count = len(df)
    df["Order_date"] = pd.to_datetime(df["Order_date"], format="%d-%m-%Y", errors="coerce")
    for col in ["Quantity", "Unit_price", "Discount_amount"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    for col in ["Transaction_Id", "Product", "Category", "Region", "Sales_channel", "Status"]:
        df[col] = df[col].astype("string").str.strip()

    valid = (
        df["Order_date"].notna()
        & df["Product"].notna() & (df["Product"] != "")
        & df["Category"].notna() & (df["Category"] != "")
        & df["Region"].notna() & (df["Region"] != "")
        & df["Sales_channel"].notna() & (df["Sales_channel"] != "")
        & df["Quantity"].notna() & (df["Quantity"] > 0)
        & df["Unit_price"].notna() & (df["Unit_price"] >= 0)
        & df["Discount_amount"].notna() & (df["Discount_amount"] >= 0)
        & (df["Status"].str.lower() == "completed")
    )
    clean = df.loc[valid].copy()
    clean["Revenue"] = (clean["Quantity"] * clean["Unit_price"] - clean["Discount_amount"]).clip(lower=0)
    clean["Order_date"] = clean["Order_date"].dt.strftime("%Y-%m-%d")
    return clean, raw_count, int((~valid).sum())


def store_data(df, db_path=DB_PATH):
    conn = sqlite3.connect(db_path)
    try:
        df.to_sql("sales", conn, if_exists="replace", index=False)
    finally:
        conn.close()


def query_db(sql, params=(), db_path=DB_PATH):
    if not sql.lstrip().lower().startswith("select"):
        raise ValueError("Only read-only SELECT queries are allowed.")
    conn = sqlite3.connect(db_path)
    try:
        return pd.read_sql_query(sql, conn, params=params)
    finally:
        conn.close()


def normalize(text):
    return re.sub(r"[^a-z0-9\s\-]", " ", (text or "").lower()).strip()


def get_gemini_client():
    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError(
            'Gemini API key is missing. In PowerShell, set it with: '
            '$env:GEMINI_API_KEY="YOUR_API_KEY"'
        )
    return genai.Client(api_key=api_key)


def get_sales_schema(db_path=DB_PATH):
    """Discover the real columns and sample values from the currently loaded sales table."""
    conn = sqlite3.connect(db_path)
    try:
        columns = conn.execute('PRAGMA table_info("sales")').fetchall()
        if not columns:
            raise RuntimeError("No sales dataset is loaded yet. Upload a CSV first.")
        schema = []
        for _, name, dtype, *_ in columns:
            quoted = '"' + str(name).replace('"', '""') + '"'
            sample_rows = conn.execute(
                f"SELECT DISTINCT {quoted} FROM sales WHERE {quoted} IS NOT NULL LIMIT 3"
            ).fetchall()
            schema.append({
                "column": str(name),
                "type": str(dtype),
                "examples": [str(row[0])[:100] for row in sample_rows],
            })
        return schema
    finally:
        conn.close()


def validate_sales_sql(sql, db_path=DB_PATH):
    """Allow one read-only SELECT query that refers only to the loaded sales table."""
    if not isinstance(sql, str) or not sql.strip():
        raise ValueError("The AI did not generate a SQL query.")
    try:
        statements = [item for item in sqlglot.parse(sql, read="sqlite") if item is not None]
    except Exception as exc:
        raise ValueError("The generated SQL could not be parsed safely. Please retry the question.") from exc
    if len(statements) != 1 or not isinstance(statements[0], exp.Select):
        raise ValueError("Safety check blocked this query. Only one read-only SELECT query is allowed.")
    parsed = statements[0]
    tables = [table.name.lower() for table in parsed.find_all(exp.Table)]
    if not tables or any(name != "sales" for name in tables):
        raise ValueError("Safety check blocked this query because it referenced an unknown table.")
    if parsed.args.get("into") is not None:
        raise ValueError("SELECT INTO is not allowed.")
    limit_node = parsed.args.get("limit")
    if limit_node is None:
        parsed = parsed.limit(500)
    else:
        try:
            limit_value = limit_node.expression
            if isinstance(limit_value, exp.Literal) and limit_value.is_number and int(limit_value.this) > 500:
                parsed.set("limit", exp.Limit(expression=exp.Literal.number(500)))
        except Exception:
            parsed.set("limit", exp.Limit(expression=exp.Literal.number(500)))
    return parsed.sql(dialect="sqlite")


def execute_read_only_sql(sql, db_path=DB_PATH):
    safe_sql = validate_sales_sql(sql, db_path)
    conn = sqlite3.connect(db_path)
    denied_actions = {
        sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE,
        sqlite3.SQLITE_CREATE_INDEX, sqlite3.SQLITE_CREATE_TABLE,
        sqlite3.SQLITE_CREATE_TEMP_INDEX, sqlite3.SQLITE_CREATE_TEMP_TABLE,
        sqlite3.SQLITE_CREATE_TEMP_TRIGGER, sqlite3.SQLITE_CREATE_TEMP_VIEW,
        sqlite3.SQLITE_CREATE_TRIGGER, sqlite3.SQLITE_CREATE_VIEW,
        sqlite3.SQLITE_DROP_INDEX, sqlite3.SQLITE_DROP_TABLE,
        sqlite3.SQLITE_DROP_TEMP_INDEX, sqlite3.SQLITE_DROP_TEMP_TABLE,
        sqlite3.SQLITE_DROP_TEMP_TRIGGER, sqlite3.SQLITE_DROP_TEMP_VIEW,
        sqlite3.SQLITE_DROP_TRIGGER, sqlite3.SQLITE_DROP_VIEW,
        sqlite3.SQLITE_ALTER_TABLE, sqlite3.SQLITE_ATTACH, sqlite3.SQLITE_DETACH,
        sqlite3.SQLITE_PRAGMA, sqlite3.SQLITE_TRANSACTION,
    }
    def authorizer(action, arg1, arg2, db_name, trigger_name):
        if action in denied_actions:
            return sqlite3.SQLITE_DENY
        if action == sqlite3.SQLITE_FUNCTION:
            function_name = (arg2 or arg1 or "").lower()
            if function_name in {"load_extension", "writefile", "readfile"}:
                return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK
    try:
        conn.set_authorizer(authorizer)
        return pd.read_sql_query(safe_sql, conn), safe_sql
    except Exception as exc:
        raise RuntimeError(
            "The generated query could not run against this sales dataset. "
            "Try rephrasing the question or checking the uploaded column values. "
            f"Technical detail: {exc}"
        ) from exc
    finally:
        conn.set_authorizer(None)
        conn.close()


def answer_question(question, db_path=DB_PATH):
    """Use Gemini to translate a natural-language business question into safe SQL."""
    if not question or not question.strip():
        return {"error": "Please enter a question about your uploaded sales data."}
    schema = get_sales_schema(db_path)
    model_name = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
    prompt = f"""You are InsightIQ, a business data analyst and Text-to-SQL engine.
The user's validated sales CSV is stored in a SQLite table named sales.
Actual columns and examples:
{json.dumps(schema, ensure_ascii=False, indent=2)}

Question: {question}

Return JSON only with exactly these keys:
{{"status":"ok|clarify|unsupported","sql":"...","answer":"short plain-English answer or explanation","chart":"auto|bar|line|pie|none","note":"short note about filters or calculation"}}
Rules:
- Use only table sales and only columns listed in the schema.
- Generate exactly one read-only SQLite SELECT query. WITH ... SELECT is okay.
- Never generate INSERT, UPDATE, DELETE, DROP, ALTER, CREATE, PRAGMA, ATTACH, DETACH, or multiple statements.
- Do not invent columns, metrics, or data. If the CSV lacks fields needed to answer, use status unsupported, sql empty, and explain why.
- If the question is ambiguous, use status clarify, sql empty, and ask what metric or grouping is intended.
- For total sales/revenue, use the Revenue column if it exists. Do not assume a currency unless the question/data establishes one.
- For product counts, distinguish count of rows from COUNT(DISTINCT Product) as appropriate.
- Use meaningful output aliases. Use SQLite-compatible SQL.
- Use LIMIT 500 or less for detail queries; aggregate queries can use reasonable limits.
- Keep answer and note concise. Do not put markdown fences around the JSON.
"""
    try:
        client = get_gemini_client()
        response = client.models.generate_content(
            model=model_name,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.1,
                max_output_tokens=1600,
            ),
        )
    except Exception as exc:
        message = str(exc)
        if "503" in message or "UNAVAILABLE" in message or "high demand" in message.lower():
            return {"error": "Gemini is temporarily busy (503). Please wait a minute and try again. If it continues, try setting GEMINI_MODEL to another available Gemini Flash model in PowerShell."}
        if "429" in message or "RESOURCE_EXHAUSTED" in message:
            return {"error": "Gemini API rate limit or quota reached (429). Check your API usage/quota, wait, and try again."}
        return {"error": f"Gemini could not generate SQL: {message}"}

    if not getattr(response, "text", None):
        return {"error": "Gemini returned an empty response. Please retry your question."}
    try:
        ai_result = json.loads(response.text)
    except json.JSONDecodeError:
        return {"error": "Gemini returned an unreadable response. Please retry the question."}
    status = ai_result.get("status")
    if status == "clarify":
        return {"error": ai_result.get("answer") or "Could you clarify which metric or grouping you want?"}
    if status == "unsupported":
        return {"error": ai_result.get("answer") or "The uploaded data does not contain the fields needed to answer this question."}
    if status != "ok" or not ai_result.get("sql"):
        return {"error": "Gemini could not confidently interpret the question. Please try rephrasing it."}
    try:
        data, safe_sql = execute_read_only_sql(ai_result["sql"], db_path)
    except Exception as exc:
        return {"error": str(exc)}
    answer = ai_result.get("answer") or "Here are the results calculated from your uploaded sales data."
    if data.empty:
        answer = "No matching rows were found for this question. Try changing or removing a filter."
    return {
        "title": "InsightIQ analysis",
        "answer": answer,
        "data": data,
        "chart": ai_result.get("chart", "auto"),
        "note": ai_result.get("note", "Calculated from the currently uploaded, validated sales data."),
        "sql": safe_sql,
    }


def main():
    st.set_page_config(page_title="InsightIQ", page_icon="📊", layout="centered")

    st.markdown("""
    <style>
    
/* Light theme: question input */
div[data-testid="stTextInput"] input {
    background-color: #F1F5F9 !important;
    color: #243B53 !important;
    border: 1px solid #D4DEE8 !important;
    border-radius: 10px !important;
    caret-color: #087E8B !important;
}

div[data-testid="stTextInput"] input:focus {
    background-color: #FFFFFF !important;
    color: #243B53 !important;
    border-color: #087E8B !important;
    box-shadow: 0 0 0 1px #087E8B !important;
}

/* Light theme: supporting figures table */
[data-testid="stDataFrame"] {
    background-color: #FFFFFF !important;
    color-scheme: light !important;
    border: 1px solid #DCE5EE !important;
    border-radius: 10px !important;
    overflow: hidden !important;
}

[data-testid="stDataFrame"] iframe {
    background-color: #FFFFFF !important;
}

[data-testid="stTable"] {
    background-color: #FFFFFF !important;
    color: #243B53 !important;
}

[data-testid="stTable"] table {
    background-color: #FFFFFF !important;
    color: #243B53 !important;
}

[data-testid="stTable"] th,
[data-testid="stTable"] td {
    background-color: #FFFFFF !important;
    color: #243B53 !important;
    border-color: #DCE5EE !important;
}

/* Light toolbar for the data table */
[data-testid="stDataFrame"] button {
    background-color: #FFFFFF !important;
    color: #087E8B !important;
}

    :root { color-scheme: light !important; }
    html, body, .stApp, [data-testid="stAppViewContainer"],
    [data-testid="stMain"], [data-testid="stMainBlockContainer"] {
        background: #F7F9FC !important;
        color: #172B3A !important;
    }
    .block-container { max-width: 1000px; padding-top: 1rem; padding-bottom: 2.5rem; }

    /* Typography: avoid Streamlit's dark-theme low-contrast text */
    [data-testid="stMarkdownContainer"], [data-testid="stMarkdownContainer"] *,
    [data-testid="stCaptionContainer"], [data-testid="stCaptionContainer"] *,
    label, p, li, h1, h2, h3, h4, span, small {
        color: #243B53;
    }
    [data-testid="stCaptionContainer"], [data-testid="stCaptionContainer"] * { color: #60758A !important; }
    h1, h2, h3, h4 { color: #153451 !important; }

    .hero, .hero * { color: #FFFFFF !important; }
    .hero {
        background: #153451 !important; color: #FFFFFF !important;
        padding: 1.25rem 1.5rem; border-radius: 16px; margin-bottom: 1rem;
        display: block; overflow: visible; min-height: 112px; box-sizing: border-box;
    }
    .hero h1 { color: #FFFFFF !important; font-size: 2.1rem; line-height: 1.25; margin: 0; padding: 0; visibility: visible; opacity: 1; }
    .hero p { color: #EAF2F8 !important; margin: .45rem 0 0; font-size: 1rem; line-height: 1.5; }
    .panel {
        background: #FFFFFF !important; border: 1px solid #DCE5EE !important;
        border-radius: 14px; padding: 1.1rem 1.2rem; margin: .8rem 0;
    }

   /* Main CSV uploader — white background with teal accents */
[data-testid="stFileUploader"],
[data-testid="stFileUploaderDropzone"] {
    background: #FFFFFF !important;
    background-color: #FFFFFF !important;
    border: 1px dashed #BFE5E6 !important;
    border-radius: 12px !important;
    color: #243B53 !important;
}

/* Keep uploader text readable */
[data-testid="stFileUploader"] *,
[data-testid="stFileUploaderDropzone"] * {
    color: #243B53 !important;
}

/* Browse files button */
[data-testid="stFileUploaderDropzone"] button,
[data-testid="stFileUploader"] button {
    background: #FFFFFF !important;
    background-color: #FFFFFF !important;
    color: #087E8B !important;
    border: 1px solid #087E8B !important;
    border-radius: 8px !important;
}

/* Keep button text teal */
[data-testid="stFileUploaderDropzone"] button *,
[data-testid="stFileUploader"] button * {
    color: #087E8B !important;
}

/* Keep background white when hovering */
[data-testid="stFileUploaderDropzone"]:hover,
[data-testid="stFileUploaderDropzone"]:focus-within {
    background: #FFFFFF !important;
    background-color: #FFFFFF !important;
    border-color: #BFE5E6 !important;
}

    /* Text inputs and dropdowns */
    div[data-testid="stTextInput"] input,
    div[data-testid="stSelectbox"] div[data-baseweb="select"],
    div[data-testid="stSelectbox"] [role="combobox"] {
        background: #F1F5F9 !important; color: #243B53 !important;
        border: 1px solid #D4DEE8 !important; border-radius: 10px !important;
    }
    div[data-testid="stTextInput"] input { min-height: 3.2rem; }
    div[data-testid="stTextInput"] input::placeholder { color: #71869A !important; opacity: 1 !important; }
    div[data-testid="stSelectbox"] *, div[data-baseweb="popover"] * { color: #243B53 !important; }

    /* Primary action */
    .stButton button {
        background: #087E8B !important; color: #FFFFFF !important;
        border: 1px solid #087E8B !important; border-radius: 10px !important;
        font-weight: 600 !important; min-height: 2.8rem;
    }
    .stButton button * { color: #FFFFFF !important; }
    .stButton button[kind="primary"] {
        background: #087E8B !important; border: 1px solid #087E8B !important;
        color: #FFFFFF !important; border-radius: 10px !important; font-weight: 700 !important;
    }
    .stButton button[kind="primary"] *, .stButton button[kind="primary"] p {
        color: #FFFFFF !important;
    }
    .stButton button:hover { filter: brightness(.97); }

    /* KPI cards, only shown after data is selected */
    [data-testid="stMetric"] {
        background: #FFFFFF !important; border: 1px solid #DCE5EE !important;
        border-radius: 12px !important; padding: .9rem !important;
    }
    [data-testid="stMetricLabel"], [data-testid="stMetricLabel"] *,
    [data-testid="stMetricValue"], [data-testid="stMetricValue"] * {
        color: #153451 !important;
    }
    .answer {
        background: #FFFFFF !important; border: 1px solid #DCE5EE !important;
        border-left: 5px solid #087E8B !important; border-radius: 12px;
        padding: 1rem 1.1rem; color: #243B53 !important; line-height: 1.6;
    }
    .answer * { color: #243B53 !important; }
    [data-testid="stDataFrame"], [data-testid="stTable"] { background: #FFFFFF !important; }
    /* Fix clipped InsightIQ header */
/* Fix all four corners of the InsightIQ header */
div[data-testid="stMarkdown"]:has(.hero) {
    border-radius: 22px !important;
    overflow: visible !important;
}

.hero {
    display: block !important;
    width: 100% !important;
    margin: 24px 0 28px !important;
    padding: 36px 30px 28px !important;
    background: #153451 !important;
    border-radius: 32px !important;
    box-sizing: border-box !important;
    overflow: hidden !important;
    clip-path: none !important;
}
.hero h1 {
    margin: 0 0 12px !important;
    padding: 0 !important;
    color: #fff !important;
    line-height: 1.4 !important;
}

.hero p {
    margin: 0 !important;
    color: #fff !important;
}
/* Supporting figures: pale teal result table */
.insight-results {
    width: 100%;
    border-collapse: separate;
    border-spacing: 0;
    border: 1px solid #BFE5E6;
    border-radius: 10px;
    overflow: hidden;
    background: #E0F2F2;
    color: #243B53;
}

.insight-results th,
.insight-results td {
    background: #E0F2F2 !important;
    color: #243B53 !important;
    padding: 12px 14px;
    text-align: left;
    border-bottom: 1px solid #BFE5E6;
}

.insight-results th {
    font-weight: 600;
}

.insight-results tr:last-child td {
    border-bottom: none;
}

/* Match question input to the pale teal theme */
div[data-testid="stTextInput"] input {
    background-color: #E0F2F2 !important;
    color: #243B53 !important;
    border: 1px solid #BFE5E6 !important;
    caret-color: #087E8B !important;
}

div[data-testid="stTextInput"] input:focus {
    background-color: #E0F2F2 !important;
    color: #243B53 !important;
    border-color: #087E8B !important;
}



/* Download results button */
.stDownloadButton button {
    background-color: #E0F2F2 !important;
    color: #243B53 !important;
    border: 1px solid #BFE5E6 !important;
}

.stDownloadButton button p,
.stDownloadButton button span {
    color: #243B53 !important;
}

/* InsightIQ uploader — white background */
[data-testid="stFileUploader"] {
    background: #FFFFFF !important;
    color: #243B53 !important;
}

[data-testid="stFileUploaderDropzone"] {
    background: #FFFFFF !important;
    background-color: #FFFFFF !important;
    border: 1px dashed #BFE5E6 !important;
    border-radius: 12px !important;
}

/* Keep the drop zone white on hover and while focused */
[data-testid="stFileUploaderDropzone"]:hover,
[data-testid="stFileUploaderDropzone"]:focus-within {
    background: #FFFFFF !important;
    background-color: #FFFFFF !important;
}

/* Uploaded file chip: target the actual chip and its descendants */
[data-testid="stFileUploaderFile"] {
    background: #FFFFFF !important;
    background-color: #FFFFFF !important;
    border: 1px solid #BFE5E6 !important;
    border-radius: 8px !important;
    color: #243B53 !important;
}

[data-testid="stFileUploaderFile"] > div {
    background: #FFFFFF !important;
    background-color: #FFFFFF !important;
}

[data-testid="stFileUploaderFile"] * {
    color: #243B53 !important;
}

/* Upload button */
[data-testid="stFileUploader"] button {
    background: #FFFFFF !important;
    color: #087E8B !important;
    border: 1px solid #BFE5E6 !important;
}

[data-testid="stFileUploader"] button * {
    color: #087E8B !important;
}
/* Force uploaded-file chip to use a white theme */
[data-testid="stFileUploaderFile"],
[data-testid="stFileUploaderFile"] > div,
[data-testid="stFileUploaderFile"] [data-testid],
[data-testid="stFileUploaderFile"] section {
    background: #FFFFFF !important;
    background-color: #FFFFFF !important;
    color: #243B53 !important;
    border: 1px solid #BFE5E6 !important;
    border-radius: 8px !important;
    box-shadow: none !important;
}

/* Uploaded filename and icons */
[data-testid="stFileUploaderFile"] *,
[data-testid="stFileUploaderFile"] svg {
    color: #243B53 !important;
    fill: #243B53 !important;
}

/* Upload area */
[data-testid="stFileUploaderDropzone"] {
    background: #FFFFFF !important;
    background-color: #FFFFFF !important;
}



    </style>
    """, unsafe_allow_html=True)

    st.markdown(
        '<div class="hero"><h1>InsightIQ</h1>'
        '<p>Upload a sales CSV and ask questions about your business data.</p></div>',
        unsafe_allow_html=True
    )

    st.markdown("### Upload your sales data")
    uploaded = st.file_uploader(
        "Upload a sales CSV",
        type=["csv"],
        help="Your CSV should include transaction ID, order date, product, category, region, sales channel, quantity, unit price, discount amount and status.",
        label_visibility="visible"
    )

    if uploaded is None:
        st.markdown(
            '<div class="panel"><h3>Welcome to InsightIQ</h3>'
            '<p>Upload a sales CSV to begin. Your business metrics, answers, and charts will appear here after the file is validated.</p></div>',
            unsafe_allow_html=True
        )
        st.stop()

    try:
        uploaded.seek(0)
        df, raw_count, excluded_count = read_sales_csv(uploaded)
        source_name = uploaded.name
    except Exception as exc:
        st.error(f"Couldn't load this CSV: {exc}")
        st.info("Please check that your file contains the required sales columns and dates in DD-MM-YYYY format. No sample data has been substituted.")
        st.stop()

    if df is None or df.empty:
        st.warning("The file was read, but no valid completed sales rows were found. Please check the data.")
        st.stop()

    store_data(df)
    overview = query_db(
        "SELECT ROUND(COALESCE(SUM(Revenue),0),2) AS revenue, COUNT(*) AS txns, "
        "COUNT(DISTINCT Product) AS products, COUNT(DISTINCT Region) AS regions FROM sales"
    )
    r = overview.iloc[0]
    st.caption(f"Dataset: **{source_name}** · {raw_count} rows read · {excluded_count} rows excluded during validation")
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Revenue", f"₹{float(r['revenue'] or 0):,.0f}")
    k2.metric("Transactions", f"{int(r['txns'])}")
    k3.metric("Products", f"{int(r['products'])}")
    k4.metric("Regions", f"{int(r['regions'])}")

    st.markdown("### Ask a question about your business data")
    st.caption("Use your own words. Answers are calculated from the uploaded CSV.")
    question = st.text_input(
        "Your question",
        placeholder="e.g. Which product generated the highest revenue?",
        key="business_question"
    )
    if st.button("Analyze question", type="primary", use_container_width=True):
        try:
            result = answer_question(question)
        except Exception as exc:
            st.error("I couldn't calculate that answer from the uploaded data. Please try a question about total revenue, discounts, units sold, or a product/region/month breakdown.")
            st.caption(f"Technical detail: {type(exc).__name__}: {exc}")
            st.stop()
        if result.get("error"):
            st.warning(result["error"])
        else:
            st.markdown("### Answer")
            st.markdown(
                f'<div class="answer"><b>{result["answer"]}</b><br>'
                f'<span>{result.get("note", "")}</span></div>',
                unsafe_allow_html=True
            )
            if result.get("sql"):
                with st.expander("View generated SQL"):
                    st.code(result["sql"], language="sql")
            data = result.get("data")
            chart = result.get("chart")
            if data is not None and not data.empty:
                if chart and len(data.columns) >= 2:
                    st.markdown("### Visualization")
                    plot_df = data.set_index(data.columns[0])
                    numeric_col = data.columns[1]
                    if chart == "line":
                        st.line_chart(plot_df[numeric_col], use_container_width=True)
                    else:
                        st.bar_chart(plot_df[numeric_col], use_container_width=True)
                st.markdown("### Supporting figures")
                st.markdown(
    data.to_html(index=False, border=0, classes="insight-results"),
    unsafe_allow_html=True
)
                st.download_button(
                    "Download results CSV",
                    data=data.to_csv(index=False).encode("utf-8"),
                    file_name="insightiq_results.csv",
                    mime="text/csv"
                )



if __name__ == "__main__":
    main()
