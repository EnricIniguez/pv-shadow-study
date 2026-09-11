import pandas as pd
import streamlit as st

from solar_data import generate_annual_solar_data


st.set_page_config(page_title="PV Shadow Study", page_icon="☀️", layout="wide")

st.title("PV Shadow Study")
st.caption("Clear-sky solar data for the selected location")

with st.sidebar:
    st.header("Study location")
    latitude = st.number_input(
        "Latitude (°)", min_value=-90.0, max_value=90.0, value=41.3874, step=0.0001
    )
    longitude = st.number_input(
        "Longitude (°)", min_value=-180.0, max_value=180.0, value=2.1686, step=0.0001
    )
    year = st.number_input("Year", min_value=2000, max_value=2100, value=2026, step=1)
    resolution = st.selectbox("Time step", options=[1, 5, 15], index=1, format_func=lambda x: f"{x} min")
    ghi_threshold = st.number_input(
        "Clear-sky GHI threshold (W/m²)", min_value=0.0, max_value=1400.0, value=100.0, step=10.0
    )
    calculate = st.button("Calculate annual data", type="primary", width="stretch")

map_col, summary_col = st.columns([1.4, 1])

with map_col:
    st.subheader("Location")
    location_df = pd.DataFrame({"lat": [latitude], "lon": [longitude]})
    st.map(location_df, latitude="lat", longitude="lon", zoom=10, height=360)

with summary_col:
    st.subheader("Current study")
    st.metric("Coordinates", f"{latitude:.4f}°, {longitude:.4f}°")
    st.metric("Resolution", f"{resolution} minute{'s' if resolution != 1 else ''}")
    expected_rows = int((366 if pd.Timestamp(year=year, month=12, day=31).is_leap_year else 365) * 24 * 60 / resolution)
    st.metric("Annual timestamps", f"{expected_rows:,}")

if calculate:
    with st.spinner("Calculating solar position and clear-sky irradiance…"):
        data = generate_annual_solar_data(
            latitude=latitude,
            longitude=longitude,
            year=int(year),
            interval_minutes=int(resolution),
            ghi_threshold=float(ghi_threshold),
        )

    relevant = data[data["above_ghi_threshold"]]
    daylight = data[data["apparent_elevation"] > 0]

    st.subheader("Annual result")
    a, b, c = st.columns(3)
    a.metric("Daylight timestamps", f"{len(daylight):,}")
    b.metric("Above GHI threshold", f"{len(relevant):,}")
    c.metric("Equivalent selected hours", f"{len(relevant) * resolution / 60:,.1f} h")

    monthly = relevant["ghi"].resample("MS").count() * resolution / 60
    monthly.index = monthly.index.strftime("%b")
    st.bar_chart(monthly, x_label="Month", y_label="Hours above threshold")

    with st.expander("Preview calculated data"):
        st.dataframe(data.head(100), width="stretch")

    st.download_button(
        "Download annual CSV",
        data=data.to_csv().encode("utf-8"),
        file_name=f"solar_data_{year}_{latitude:.4f}_{longitude:.4f}_{resolution}min.csv",
        mime="text/csv",
    )
