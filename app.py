import sqlite3
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd
import streamlit as st

# ---------------------------------------------------------
# Config
# ---------------------------------------------------------
DB_PATH = "bio_advance.db"
NEW = "➕ Add new…"
T_SALE, T_IN = "Sale", "Stock In"
T_RET_OK, T_RET_BAD = "Return (resellable)", "Return (damaged)"
T_PAY = "Payment Received"
TX_TYPES = [T_SALE, T_IN, T_RET_OK, T_RET_BAD, T_PAY]
RETURN_TYPES = (T_RET_OK, T_RET_BAD)

st.set_page_config(page_title="Bio Advance Ledger & Analytics", layout="wide")


def _ver():
    try:
        return tuple(int(x) for x in st.__version__.split(".")[:2])
    except Exception:
        return (0, 0)


STRETCH = {"width": "stretch"} if _ver() >= (1, 50) else {"use_container_width": True}


# ---------------------------------------------------------
# Database helpers
# ---------------------------------------------------------
def conn():
    c = sqlite3.connect(DB_PATH)
    c.execute("PRAGMA foreign_keys=ON")
    return c


def q(sql, params=()):
    c = conn()
    try:
        return pd.read_sql_query(sql, c, params=params)
    finally:
        c.close()


def ex(sql, params=()):
    c = conn()
    try:
        with c:
            c.execute(sql, params)
    finally:
        c.close()


def init_db():
    c = conn()
    try:
        with c:
            c.execute("""CREATE TABLE IF NOT EXISTS products(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                size TEXT NOT NULL,
                price REAL NOT NULL,
                opening_stock INTEGER NOT NULL DEFAULT 0,
                active INTEGER NOT NULL DEFAULT 1)""")
            c.execute("""CREATE TABLE IF NOT EXISTS transactions(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tx_date TEXT NOT NULL,
                created_at TEXT NOT NULL,
                product_id INTEGER REFERENCES products(id),
                tx_type TEXT NOT NULL,
                qty INTEGER NOT NULL DEFAULT 0,
                unit_price REAL NOT NULL DEFAULT 0,
                amount REAL NOT NULL DEFAULT 0,
                shop TEXT NOT NULL DEFAULT '',
                booker TEXT NOT NULL DEFAULT '',
                payment_type TEXT NOT NULL DEFAULT '',
                note TEXT NOT NULL DEFAULT '')""")
            n = c.execute("SELECT COUNT(*) FROM products").fetchone()[0]
            if n == 0:
                seed = [
                    ("BIO ADVANCE ONION SHAMPOO 850 ML", "850ml", 1400, 45, 15, "Al-Fatah Store", "Ali Raza", "Cash"),
                    ("BIO ADVANCE ALOE VERA SHAMPOO 850 ML", "850ml", 1400, 56, 20, "Shaheen Chemist", "Usman Khan", "Credit"),
                    ("BIO ADVANCE ONION SHAMPOO SMALL 130 ML", "130ml", 400, 111, 48, "Save Mart", "Ali Raza", "Cash"),
                    ("BIO ADVANCE ALOE VERA SHAMPOO SMALL 130 ML", "130ml", 400, 90, 50, "Green Valley", "Hamza Ahmed", "Cash"),
                ]
                today = date.today().isoformat()
                now = datetime.now().isoformat(timespec="seconds")
                for name, size, price, opening, sold, shop, booker, pay in seed:
                    cur = c.execute(
                        "INSERT INTO products(name,size,price,opening_stock) VALUES(?,?,?,?)",
                        (name, size, price, opening))
                    c.execute(
                        """INSERT INTO transactions(tx_date,created_at,product_id,tx_type,qty,unit_price,amount,
                            shop,booker,payment_type,note) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                        (today, now, cur.lastrowid, T_SALE, sold, price, sold * price, shop, booker, pay,
                         "Imported from original sheet"))
    finally:
        c.close()


def insert_tx(tx_date, product_id, tx_type, qty, unit_price, amount, shop, booker, pay, note):
    ex("""INSERT INTO transactions(tx_date,created_at,product_id,tx_type,qty,unit_price,amount,
          shop,booker,payment_type,note) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
       (tx_date.isoformat(), datetime.now().isoformat(timespec="seconds"), product_id, tx_type,
        int(qty), float(unit_price), round(float(amount), 2), shop, booker, pay, note))


# ---------------------------------------------------------
# Data loaders
# ---------------------------------------------------------
def load_stock():
    df = q("""
        SELECT p.id, p.name, p.size, p.price, p.opening_stock,
          COALESCE(SUM(CASE WHEN t.tx_type=? THEN t.qty END),0) AS stock_in,
          COALESCE(SUM(CASE WHEN t.tx_type=? THEN t.qty END),0) AS sold,
          COALESCE(SUM(CASE WHEN t.tx_type=? THEN t.qty END),0) AS returned_ok,
          COALESCE(SUM(CASE WHEN t.tx_type=? THEN t.qty END),0) AS returned_damaged,
          COALESCE(SUM(CASE WHEN t.tx_type=? THEN t.amount END),0) AS sales_rev,
          COALESCE(SUM(CASE WHEN t.tx_type IN (?,?) THEN t.amount END),0) AS returns_val
        FROM products p LEFT JOIN transactions t ON t.product_id = p.id
        WHERE p.active = 1 GROUP BY p.id ORDER BY p.id
    """, (T_IN, T_SALE, T_RET_OK, T_RET_BAD, T_SALE, T_RET_OK, T_RET_BAD))
    df["remaining"] = df["opening_stock"] + df["stock_in"] + df["returned_ok"] - df["sold"]
    return df


def load_tx():
    df = q("""
        SELECT t.id, t.tx_date, t.created_at, t.product_id, t.tx_type, p.name AS product, p.size,
               t.qty, t.unit_price, t.amount, t.shop, t.booker, t.payment_type, t.note
        FROM transactions t LEFT JOIN products p ON p.id = t.product_id
        ORDER BY t.tx_date DESC, t.id DESC
    """)
    df["tx_date"] = pd.to_datetime(df["tx_date"])
    return df


def known_names(col):
    df = q(f"SELECT DISTINCT {col} AS n FROM transactions WHERE {col} <> '' ORDER BY {col}")
    return df["n"].tolist()


def outstanding_by_shop(tx):
    if tx.empty:
        return pd.DataFrame(columns=["Shop", "Credit Sales", "Returns Credited", "Payments Received", "Outstanding"])
    credit_sales = tx[(tx.tx_type == T_SALE) & (tx.payment_type == "Credit")].groupby("shop")["amount"].sum()
    credit_ret = tx[tx.tx_type.isin(RETURN_TYPES) & (tx.payment_type == "Credit")].groupby("shop")["amount"].sum()
    paid = tx[tx.tx_type == T_PAY].groupby("shop")["amount"].sum()
    shops = sorted(set(credit_sales.index) | set(credit_ret.index) | set(paid.index))
    out = pd.DataFrame({
        "Shop": shops,
        "Credit Sales": [credit_sales.get(s, 0.0) for s in shops],
        "Returns Credited": [credit_ret.get(s, 0.0) for s in shops],
        "Payments Received": [paid.get(s, 0.0) for s in shops],
    })
    out["Outstanding"] = out["Credit Sales"] - out["Returns Credited"] - out["Payments Received"]
    return out


# ---------------------------------------------------------
# Input helpers
# ---------------------------------------------------------
def resolve_name(key, existing):
    s = st.session_state
    sel = s.get(f"{key}_sel", "")
    raw = s.get(f"{key}_new", "") if sel == NEW else sel
    name = " ".join((raw or "").split())
    for e in existing:
        if e.lower() == name.lower():
            return e
    return name


def name_picker(label, key, existing):
    st.sidebar.selectbox(
        label, [""] + existing + [NEW], key=f"{key}_sel",
        format_func=lambda x: "— select —" if x == "" else x)
    if st.session_state[f"{key}_sel"] == NEW:
        st.sidebar.text_input(f"New {label.lower()}", key=f"{key}_new")


def submit_tx():
    s = st.session_state
    t = s.tx_type
    stock = load_stock()
    tx_all_master = load_tx()
    shops, bookers = known_names("shop"), known_names("booker")
    shop = resolve_name("shop", shops)
    booker = resolve_name("booker", bookers)
    tx_date = s.tx_date
    note = " ".join(s.get("tx_note", "").split())

    def fail(msg):
        s.flash = ("error", msg)

    if t == T_PAY:
        amt = float(s.tx_amount)
        if not shop or not booker:
            return fail("Shop and booker are required.")
        if amt <= 0:
            return fail("Payment amount must be greater than zero.")
        # Bug fix: use all-time transactions for checking outstanding debt
        out = outstanding_by_shop(tx_all_master)
        owed = float(out.loc[out.Shop == shop, "Outstanding"].sum()) if not out.empty else 0.0
        if amt > owed + 0.005:
            return fail(f"{shop} only owes Rs. {owed:,.0f}. Payment of Rs. {amt:,.0f} is too high.")
        insert_tx(tx_date, None, T_PAY, 0, 0, amt, shop, booker, "Cash", note)
        s.flash = ("success", f"Recorded Rs. {amt:,.0f} received from {shop}.")
        s.tx_amount = 0.0
        return

    row = stock[stock.id == s.tx_product].iloc[0]
    qty = int(s.tx_qty)
    if qty < 1:
        return fail("Quantity must be at least 1.")

    if t == T_IN:
        insert_tx(tx_date, int(row.id), T_IN, qty, row.price, 0, "", "", "", note)
        s.flash = ("success", f"Added {qty} units to {row['name']}.")
    else:
        if not shop or not booker:
            return fail("Shop and booker are required.")
        if t == T_SALE:
            if qty > row.remaining:
                return fail(f"Only {int(row.remaining)} units in stock.")
            disc = float(s.tx_disc)
            unit_price = float(row.price) * (1 - disc / 100)
            pay = s.tx_pay
            insert_tx(tx_date, int(row.id), T_SALE, qty, unit_price, qty * unit_price, shop, booker, pay,
                      note or (f"{disc:g}% discount" if disc else ""))
            s.flash = ("success", f"Sale of {qty} × {row['name']} to {shop} recorded ({pay}).")
        else:
            insert_tx(tx_date, int(row.id), t, qty, row.price, qty * row.price, shop, booker, s.tx_pay, note)
            s.flash = ("success", f"{t}: {qty} units from {shop} recorded.")


# ---------------------------------------------------------
# Start
# ---------------------------------------------------------
init_db()
st.title("🧴 Bio Advance: Stock Ledger, Booker Operations & Analytics")

stock = load_stock()
tx_all = load_tx()
names = dict(zip(stock.id, stock.name))

# ---------------------------------------------------------
# Sidebar: register transaction
# ---------------------------------------------------------
st.sidebar.header("📝 Register Transaction")
st.sidebar.selectbox("Transaction Type", TX_TYPES, key="tx_type")
t_now = st.session_state.tx_type
st.sidebar.date_input("Date", value=date.today(), max_value=date.today(), key="tx_date")

if t_now != T_PAY:
    st.sidebar.selectbox("Product", list(names.keys()), format_func=lambda i: names[i], key="tx_product")
    st.sidebar.number_input("Quantity (Units)", min_value=1, step=1, key="tx_qty")

if t_now != T_IN:
    name_picker("Shop", "shop", known_names("shop"))
    name_picker("Booker", "booker", known_names("booker"))

if t_now == T_SALE:
    st.sidebar.selectbox("Payment Type", ["Cash", "Credit"], key="tx_pay")
    st.sidebar.number_input("Discount (%)", min_value=0.0, max_value=100.0, step=1.0, key="tx_disc")
elif t_now in RETURN_TYPES:
    st.sidebar.selectbox("Settlement", ["Cash", "Credit"], key="tx_pay",
                         help="Cash = refunded in cash. Credit = deducted from the shop's outstanding balance.")
elif t_now == T_PAY:
    st.sidebar.number_input("Amount Received (Rs.)", min_value=0.0, step=100.0, key="tx_amount")

st.sidebar.text_input("Note (optional)", key="tx_note")
st.sidebar.button("Submit Transaction", on_click=submit_tx, type="primary")

flash = st.session_state.pop("flash", None)
if flash:
    (st.sidebar.success if flash[0] == "success" else st.sidebar.error)(flash[1])

with st.sidebar.expander("⚙️ Manage Products"):
    p_name = st.text_input("New product name", key="np_name")
    p_size = st.text_input("Size (e.g. 250ml)", key="np_size")
    p_price = st.number_input("Price (Rs.)", min_value=1.0, step=50.0, key="np_price")
    p_open = st.number_input("Opening stock", min_value=0, step=1, key="np_open")
    if st.button("Add product"):
        nm = " ".join(p_name.split()).upper()
        if not nm or not p_size.strip():
            st.error("Name and size are required.")
        elif nm in [n.upper() for n in names.values()]:
            st.error("That product already exists.")
        else:
            ex("INSERT INTO products(name,size,price,opening_stock) VALUES(?,?,?,?)",
               (nm, p_size.strip(), p_price, int(p_open)))
            st.rerun()
    st.markdown("---")
    if names:
        up_id = st.selectbox("Update price for", list(names.keys()), format_func=lambda i: names[i], key="up_id")
        new_price = st.number_input("New price (Rs.)", min_value=1.0, step=50.0, key="up_price")
        if st.button("Update price"):
            ex("UPDATE products SET price=? WHERE id=?", (new_price, int(up_id)))
            st.success("Updated. Past sales keep the price they were sold at.")
            st.rerun()

LOW_STOCK = st.sidebar.number_input("Low-stock alert at (units)", min_value=0, value=20, step=5)

# ---------------------------------------------------------
# Report period (applies to sales / shop / analytics views)
# ---------------------------------------------------------
default_start = tx_all["tx_date"].min().date() if not tx_all.empty else date.today()
period = st.date_input("📅 Report period", value=(default_start, date.today()), key="period")
if isinstance(period, (tuple, list)) and len(period) == 2:
    p_start, p_end = period
else:
    p_start = p_end = period[0] if isinstance(period, (tuple, list)) else period
mask = (tx_all["tx_date"].dt.date >= p_start) & (tx_all["tx_date"].dt.date <= p_end)
tx = tx_all[mask]
sales_period = tx[tx.tx_type == T_SALE]
sales_all = tx_all[tx_all.tx_type == T_SALE]

tab_ledger, tab_log, tab_shop, tab_credit, tab_an = st.tabs([
    "📋 Stock Ledger", "🧾 Transactions", "🏪 Shop & Booker", "💳 Credit / Outstanding", "📊 Analytics"
])

# ---------------------------------------------------------
# Ledger
# ---------------------------------------------------------
with tab_ledger:
    st.caption("Stock is all-time: opening + stock in + resellable returns − sales. Damaged returns do not go back on the shelf.")
    led = pd.DataFrame({
        "Product": stock["name"],
        "Size": stock["size"],
        "Price (Rs.)": stock["price"],
        "Opening Stock": stock["opening_stock"],
        "Stock In": stock["stock_in"],
        "Units Sold": stock["sold"],
        "Returned (Resellable)": stock["returned_ok"],
        "Returned (Damaged)": stock["returned_damaged"],
        "Remaining Stock": stock["remaining"],
        "Sales Revenue (Rs.)": stock["sales_rev"],
        "Returns Value (Rs.)": stock["returns_val"],
        "Remaining Stock Value (Rs.)": stock["remaining"] * stock["price"],
        "Status": np.where(stock["remaining"] <= LOW_STOCK, "⚠️ Low", "OK"),
    })
    m1, m2, m3 = st.columns(3)
    m1.metric("Net Sales Revenue", f"Rs. {(stock.sales_rev.sum() - stock.returns_val.sum()):,.0f}")
    m2.metric("Stock Value on Shelf", f"Rs. {(stock.remaining * stock.price).sum():,.0f}")
    m3.metric("Products Low on Stock", int((stock.remaining <= LOW_STOCK).sum()))
    st.dataframe(led, hide_index=True, **STRETCH)
    st.download_button("📥 Export Ledger to CSV", led.to_csv(index=False).encode("utf-8"),
                       "Bio_Advance_Inventory_Ledger.csv", "text/csv")

# ---------------------------------------------------------
# Transaction log + undo
# ---------------------------------------------------------
with tab_log:
    st.caption(f"Showing {len(tx)} transactions in the selected period.")
    view = tx.drop(columns=["product_id", "created_at"]).copy()
    view["tx_date"] = view["tx_date"].dt.date
    st.dataframe(view, hide_index=True, **STRETCH)
    st.download_button("📥 Export Transactions to CSV", view.to_csv(index=False).encode("utf-8"),
                       "Bio_Advance_Transactions.csv", "text/csv")

    st.markdown("#### ↩️ Void a transaction")
    recent = tx_all.head(50)
    if recent.empty:
        st.info("No transactions yet.")
    else:
        def lbl(i):
            r = recent[recent.id == i].iloc[0]
            what = r["product"] if pd.notna(r["product"]) else "payment"
            return f"#{r.id} · {r.tx_date.date()} · {r.tx_type} · {what} · qty {r.qty} · {r.shop or '-'}"
        void_id = st.selectbox("Pick one of the 50 most recent", recent.id.tolist(), format_func=lbl)
        if st.button("Delete selected transaction"):
            r = recent[recent.id == void_id].iloc[0]
            blocked = None
            if r.tx_type in (T_IN, T_RET_OK):
                rem = stock.loc[stock.id == r.product_id, "remaining"]
                if not rem.empty and rem.iloc[0] - r.qty < 0:
                    blocked = "Deleting this would make stock negative (units were already sold)."
            if blocked:
                st.error(blocked)
            else:
                ex("DELETE FROM transactions WHERE id=?", (int(void_id),))
                st.rerun()

# ---------------------------------------------------------
# Shop & booker performance
# ---------------------------------------------------------
with tab_shop:
    if sales_period.empty:
        st.info("No sales in this period.")
    else:
        shop_sum = (sales_period.groupby(["shop", "booker", "payment_type"])
                    .agg(Units=("qty", "sum"), Revenue=("amount", "sum"), Orders=("id", "count"))
                    .reset_index().rename(columns={"shop": "Shop", "booker": "Booker", "payment_type": "Payment"}))
        st.dataframe(shop_sum, hide_index=True, **STRETCH)
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**Revenue by booker**")
            st.bar_chart(sales_period.groupby("booker")["amount"].sum())
        with c2:
            st.markdown("**Revenue by shop**")
            st.bar_chart(sales_period.groupby("shop")["amount"].sum())

# ---------------------------------------------------------
# Credit / outstanding
# ---------------------------------------------------------
with tab_credit:
    out = outstanding_by_shop(tx_all)
    if out.empty:
        st.info("No credit activity yet.")
    else:
        c1, c2 = st.columns(2)
        c1.metric("Total Outstanding", f"Rs. {out.Outstanding.sum():,.0f}")
        c2.metric("Shops with Balance", int((out.Outstanding > 0.005).sum()))
        st.dataframe(out.sort_values("Outstanding", ascending=False), hide_index=True, **STRETCH)
        st.caption("Outstanding = credit sales − credit-note returns − payments received.")
        st.markdown("**Payment history**")
        ph = tx_all[tx_all.tx_type == T_PAY][["tx_date", "shop", "booker", "amount", "note"]].copy()
        ph["tx_date"] = ph["tx_date"].dt.date
        st.dataframe(ph, hide_index=True, **STRETCH)

# ---------------------------------------------------------
# Analytics (including ML Price Sensitivity)
# ---------------------------------------------------------
with tab_an:
    sub_var, sub_fc, sub_price = st.tabs(["📉 Variance by Size", "🔮 Demand & Reorder", "💲 Price Sensitivity & ML"])

    with sub_var:
        if sales_period.empty:
            st.info("No sales in this period.")
        else:
            daily = sales_period.groupby(["tx_date", "size"])["qty"].sum().reset_index()
            vs = daily.groupby("size")["qty"].agg(
                Days_With_Sales="count", Mean_Daily_Units="mean", Std_Dev="std", Variance="var").round(2).reset_index()
            vs = vs.fillna("n/a (need ≥ 2 days)")
            c1, c2 = st.columns([1, 2])
            c1.dataframe(vs, hide_index=True)
            c2.bar_chart(daily.groupby("size")["qty"].mean())

    with sub_fc:
        if stock.empty:
            st.info("No products.")
        else:
            pid = st.selectbox("Product", stock.id.tolist(), format_func=lambda i: names[i], key="fc_pid")
            remaining = int(stock.loc[stock.id == pid, "remaining"].iloc[0])
            sp = sales_all[sales_all.product_id == pid]
            if sp.empty:
                st.info("No sales recorded for this product yet.")
            else:
                s_daily = sp.groupby("tx_date")["qty"].sum()
                idx = pd.date_range(s_daily.index.min(), max(s_daily.index.max(), pd.Timestamp(date.today())))
                y = s_daily.reindex(idx, fill_value=0).astype(float)
                n = len(y)
                recent_avg = y.tail(14).mean()
                c1, c2, c3 = st.columns(3)
                c1.metric("Days of data", n)
                c2.metric("Avg daily units (last 14d)", f"{recent_avg:.1f}")
                c3.metric("Days of stock left", f"{remaining / recent_avg:.0f}" if recent_avg > 0 else "∞")

                if n >= 14:
                    x = np.arange(n)
                    slope, intercept = np.polyfit(x, y.values, 1)
                    fit = slope * x + intercept
                    fut_idx = pd.date_range(idx[-1] + timedelta(days=1), periods=7)
                    fut = np.clip(slope * np.arange(n, n + 7) + intercept, 0, None)
                    chart = pd.DataFrame({
                        "Actual": pd.concat([y, pd.Series(np.nan, index=fut_idx)]),
                        "Trend / Forecast": np.concatenate([np.clip(fit, 0, None), fut]),
                    }, index=idx.append(fut_idx))
                    st.line_chart(chart)

    with sub_price:
        st.markdown("Estimates how units sold respond to **selling price** per bottle size.")
        shown = False
        for size, grp in sales_all.groupby("size"):
            shown = True
            d = grp.groupby("tx_date").agg(units=("qty", "sum"), amt=("amount", "sum"))
            d["price"] = d["amt"] / d["units"]
            d = d[(d.units > 0) & (d.price > 0)]  # Bug fix: prevent log(0) errors
            distinct = d["price"].round(0).nunique()
            if len(d) < 5 or distinct < 2:
                st.info(f"**{size}**: Need slightly more distinct price points/sales days for elasticities, but tracking is active!")
                continue
            b, a = np.polyfit(np.log(d["price"]), np.log(d["units"]), 1)
            fit = a + b * np.log(d["price"])
            ss_tot = ((np.log(d["units"]) - np.log(d["units"]).mean()) ** 2).sum()
            r2 = 1 - ((np.log(d["units"]) - fit) ** 2).sum() / ss_tot if ss_tot > 0 else float("nan")
            st.success(f"**{size}**: Elasticity ≈ {b:.2f} (R² {r2:.2f}, n={len(d)}). A 1% price rise shifts units by about that many %.")
        if not shown:
            st.info("No sales yet to compute price sensitivity.")