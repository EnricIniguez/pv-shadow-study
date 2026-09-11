import pandas as pd
import folium
import streamlit as st
from folium.plugins import Fullscreen
from streamlit_folium import st_folium

from solar_data import generate_annual_solar_data


st.set_page_config(page_title="PV Shadow Study", page_icon="☀️", layout="wide")

st.markdown(
    """
    <style>
        .stApp { background: #ffffff; color: #0b2942; }
        [data-testid="stSidebar"] { background: #f3f8f5; }
        [data-testid="stSidebar"] h2, h1, h2, h3 { color: #0b2942; }
        [data-testid="stMetric"] {
            background: #f3f8f5;
            border: 1px solid #d8e8df;
            border-radius: 0.75rem;
            padding: 1rem;
        }
        .stButton > button, .stDownloadButton > button {
            border-radius: 0.55rem;
            font-weight: 650;
        }
    </style>
    """,
    unsafe_allow_html=True,
)

if "latitude" not in st.session_state:
    st.session_state.latitude = 41.3874
if "longitude" not in st.session_state:
    st.session_state.longitude = 2.1686
if "pending_coordinates" in st.session_state:
    pending_latitude, pending_longitude = st.session_state.pop("pending_coordinates")
    st.session_state.latitude = pending_latitude
    st.session_state.longitude = pending_longitude

st.title("PV Shadow Study")
st.caption("Clear-sky solar data for the selected location")

with st.sidebar:
    st.header("Study location")
    latitude = st.number_input(
        "Latitude (°)", min_value=-90.0, max_value=90.0, step=0.0001, key="latitude"
    )
    longitude = st.number_input(
        "Longitude (°)", min_value=-180.0, max_value=180.0, step=0.0001, key="longitude"
    )
    year = st.number_input("Year", min_value=2000, max_value=2100, value=2026, step=1)
    resolution = st.selectbox("Time step", options=[1, 5, 15], index=1, format_func=lambda x: f"{x} min")
    ghi_threshold = st.number_input(
        "Clear-sky GHI threshold (W/m²)", min_value=0.0, max_value=1400.0, value=100.0, step=10.0
    )
    calculate = st.button("Calculate annual data", type="primary", width="stretch")

map_col, summary_col = st.columns([1.4, 1])

with map_col:
    st.subheader("Select the study location")
    st.caption("Click the satellite map to update the coordinates.")
    location_map = folium.Map(
        location=[latitude, longitude],
        zoom_start=17,
        tiles=None,
        control_scale=True,
    )
    folium.TileLayer(
        tiles="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        attr="Esri, Maxar, Earthstar Geographics, and the GIS User Community",
        name="Satellite",
        overlay=False,
    ).add_to(location_map)
    folium.Marker(
        [latitude, longitude],
        tooltip=f"{latitude:.6f}, {longitude:.6f}",
        icon=folium.Icon(color="green", icon="crosshairs", prefix="fa"),
    ).add_to(location_map)
    Fullscreen(position="topright").add_to(location_map)
    map_state = st_folium(
        location_map,
        key="study_location_map",
        height=500,
        use_container_width=True,
    )

    clicked = map_state.get("last_clicked") if map_state else None
    if clicked:
        clicked_coordinates = (round(clicked["lat"], 6), round(clicked["lng"], 6))
        if clicked_coordinates != st.session_state.get("last_processed_click"):
            st.session_state.last_processed_click = clicked_coordinates
            st.session_state.pending_coordinates = clicked_coordinates
            st.rerun()

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

    with st.expander("Preview calculated data"):
        st.dataframe(data.head(100), width="stretch")

    st.download_button(
        "Download annual CSV",
        data=data.to_csv().encode("utf-8"),
        file_name=f"solar_data_{year}_{latitude:.4f}_{longitude:.4f}_{resolution}min.csv",
        mime="text/csv",
    )
