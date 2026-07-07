"""
Winbond Competitive Price Tracker - Streamlit Dashboard
---------------------------------------------------------
Reads prices.csv (updated daily by the GitHub Actions workflow) and
displays it as a live, shareable dashboard: summary metrics, a price
trend chart you can filter by density, and the latest day's full
data table.

DEPLOYMENT (Streamlit Community Cloud):
1. Make sure this file (app.py) lives in the SAME GitHub repo as
   prices.csv and track_prices.py.
2. Go to share.streamlit.io, sign in with GitHub, click "New app".
3. Pick this repo, branch "main", and set the main file path to
   "app.py".
4. Deploy. Streamlit Cloud will re-read prices.csv automatically
   every time the app is opened/refreshed, so it always shows
   whatever GitHub Actions most recently committed.

LOCAL TESTING (optional, before deploying):
    pip install streamlit pandas plotly
    streamlit run app.py
"""

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

CSV_PATH = "prices.csv"

MANUFACTURER_COLORS = {
    "Winbond": "#2a78d6",
    "Macronix": "#1baf7a",
    "GigaDevice": "#eda100",
    "ISSI": "#4a3aa7",
}

FLAG_THRESHOLD_PCT = 3.0

st.set_page_config(page_title="Winbond Price Tracker", layout="wide")


@st.cache_data(ttl=300)  # re-read the file at most every 5 minutes
def load_data():
    df = pd.read_csv(CSV_PATH)
    df["date"] = pd.to_datetime(df["date"])
    if "pct_change_vs_last_pull" in df.columns:
        df["pct_change_vs_last_pull"] = pd.to_numeric(
            df["pct_change_vs_last_pull"], errors="coerce"
        )
    return df


def main():
    st.title("Winbond competitive price tracker")
    st.caption("NOR and NAND flash - vs. Macronix, GigaDevice, ISSI - updated daily via DigiKey")

    try:
        df = load_data()
    except FileNotFoundError:
        st.error(f"Could not find {CSV_PATH}. Make sure it's in the same repo as this app.")
        return

    if df.empty:
        st.warning("prices.csv exists but has no rows yet.")
        return

    # ---------------------------------------------------------------
    # Summary metrics
    # ---------------------------------------------------------------
    latest_date = df["date"].max()
    days_collected = df["date"].nunique()
    parts_tracked = df["part_number"].nunique()
    manufacturers = df["manufacturer"].nunique()

    flagged_count = 0
    if "pct_change_vs_last_pull" in df.columns:
        flagged_count = int(
            (df["pct_change_vs_last_pull"].abs() >= FLAG_THRESHOLD_PCT).sum()
        )

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Parts tracked", parts_tracked)
    col2.metric("Manufacturers", manufacturers)
    col3.metric("Days collected", days_collected)
    col4.metric("Flagged moves (>3%)", flagged_count)

    st.divider()

    # ---------------------------------------------------------------
    # Trend chart, filterable by density/type
    # ---------------------------------------------------------------
    st.subheader("Price trend over time")

    density_options = (
        df[["density", "type"]]
        .drop_duplicates()
        .sort_values(["type", "density"])
        .apply(lambda r: f"{r['density']} {r['type']}", axis=1)
        .tolist()
    )
    selected = st.selectbox("Density / type", density_options)
    sel_density, sel_type = selected.rsplit(" ", 1)

    subset = df[(df["density"] == sel_density) & (df["type"] == sel_type)].copy()
    subset = subset.sort_values("date")

    fig = go.Figure()
    for manufacturer in subset["manufacturer"].unique():
        m_data = subset[subset["manufacturer"] == manufacturer]
        fig.add_trace(
            go.Scatter(
                x=m_data["date"],
                y=m_data["price_usd"],
                mode="lines+markers",
                name=manufacturer,
                line=dict(color=MANUFACTURER_COLORS.get(manufacturer, "#888780"), width=2),
                marker=dict(size=7),
            )
        )

    fig.update_layout(
        height=380,
        margin=dict(l=10, r=10, t=10, b=10),
        yaxis_title="Price (USD)",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
        hovermode="x unified",
    )
    st.plotly_chart(fig, use_container_width=True)

    st.divider()

    # ---------------------------------------------------------------
    # Latest snapshot table
    # ---------------------------------------------------------------
    st.subheader(f"Latest pull - {latest_date.strftime('%Y-%m-%d')}")

    latest = df[df["date"] == latest_date].copy()
    latest = latest.sort_values(["type", "density", "manufacturer"])
    display_cols = ["density", "type", "manufacturer", "part_number", "price_usd"]
    if "pct_change_vs_last_pull" in latest.columns:
        display_cols.append("pct_change_vs_last_pull")

    st.dataframe(
        latest[display_cols].rename(
            columns={
                "price_usd": "Price (USD)",
                "pct_change_vs_last_pull": "% change vs last pull",
                "part_number": "Part number",
                "density": "Density",
                "type": "Type",
                "manufacturer": "Manufacturer",
            }
        ),
        use_container_width=True,
        hide_index=True,
    )

    st.caption(
        "Data source: DigiKey public distributor pricing (US site, qty-1 tier). "
        "This is a proxy for competitive positioning, not OEM contract or spot market pricing."
    )


if __name__ == "__main__":
    main()
