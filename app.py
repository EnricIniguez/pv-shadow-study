import altair as alt
import folium
import streamlit as st
from branca.element import MacroElement
from folium.plugins import Fullscreen
from jinja2 import Template
from streamlit_folium import st_folium

from solar_data import generate_annual_solar_data, monthly_hourly_ghi_matrix


REFERENCE_YEAR = 2025


class SyncDraggedMarker(MacroElement):
    """Forward a Folium marker's drag-end position as a map click event."""

    _template = Template(
        """
        {% macro script(this, kwargs) %}
        {{ this._parent.get_name() }}.on('dragend', function(event) {
            const position = event.target.getLatLng();
            {{ this._parent._parent.get_name() }}.fire('click', {latlng: position});
        });
        {% endmacro %}
        """
    )


PAGES = ("Site", "Objects", "Shadow study", "Export")


def render_navigation(home: bool = False) -> None:
    """Render the pastel workflow navigation."""
    links = [
        ("Site", "📍", "site"),
        ("Objects", "🧊", "objects"),
        ("Shadow study", "🌤️", "shadow"),
        ("Export", "📦", "export"),
    ]
    size_class = " home" if home else ""
    items = "".join(
        f'<a class="{css_class}" href="?page={label.replace(" ", "%20")}">'
        f'<span>{icon}</span>{label}</a>'
        for label, icon, css_class in links
    )
    st.markdown(f'<nav class="pv-nav{size_class}">{items}</nav>', unsafe_allow_html=True)


st.set_page_config(page_title="PV Butterfly", page_icon="🦋", layout="wide")
st.markdown(
    """
    <style>
        .stApp {
            color: #16324a;
            background-color: #fffdf9;
            background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 600 500'%3E%3Cg fill='%23d8c3a5' fill-opacity='.13'%3E%3Cellipse cx='180' cy='175' rx='125' ry='155' transform='rotate(-28 180 175)'/%3E%3Cellipse cx='420' cy='175' rx='125' ry='155' transform='rotate(28 420 175)'/%3E%3Cellipse cx='205' cy='355' rx='90' ry='115' transform='rotate(24 205 355)'/%3E%3Cellipse cx='395' cy='355' rx='90' ry='115' transform='rotate(-24 395 355)'/%3E%3Cellipse cx='300' cy='270' rx='23' ry='175'/%3E%3C/g%3E%3C/svg%3E");
            background-position: center 58%;
            background-repeat: no-repeat;
            background-size: min(56vw, 680px);
            background-attachment: fixed;
        }
        [data-testid="stSidebar"] { background: #f3f8f5; }
        [data-testid="stMetric"] {
            background: #f3f8f5; border: 1px solid #d8e8df;
            border-radius: 0.75rem; padding: 1rem;
        }
        .stButton > button, .stDownloadButton > button {
            border-radius: 0.65rem; font-weight: 650;
        }
        .pv-nav {
            display: grid;
            grid-template-columns: repeat(4, minmax(0, 1fr));
            gap: .75rem;
            margin: .5rem 0 1.5rem;
        }
        .pv-nav a {
            min-height: 3.4rem;
            padding: .75rem;
            border-radius: .8rem;
            display: flex;
            align-items: center;
            justify-content: center;
            gap: .55rem;
            color: #16324a !important;
            font-weight: 700;
            text-decoration: none !important;
            border: 1px solid rgba(22, 50, 74, .12);
            box-shadow: 0 3px 12px rgba(22, 50, 74, .06);
            transition: transform .15s ease, box-shadow .15s ease;
        }
        .pv-nav a:hover {
            transform: translateY(-2px);
            box-shadow: 0 6px 18px rgba(22, 50, 74, .12);
        }
        .pv-nav.home {
            grid-template-columns: repeat(2, minmax(0, 1fr));
        }
        .pv-nav.home a {
            min-height: 8rem;
            font-size: 1.3rem;
        }
        .pv-nav a span { font-size: 1.35em; }
        .pv-nav .site { background: #cfe8dc; }
        .pv-nav .objects { background: #d9e5f2; }
        .pv-nav .shadow { background: #f7dfb9; }
        .pv-nav .export { background: #eadcf0; }
        @media (max-width: 720px) {
            .pv-nav, .pv-nav.home { grid-template-columns: repeat(2, minmax(0, 1fr)); }
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


active_page = st.query_params.get("page", "Home")
if active_page not in (*PAGES, "Home"):
    active_page = "Home"


if active_page == "Home":
    st.title("PV Butterfly")
    st.caption("Select a study area to begin")
    render_navigation(home=True)

    with st.expander("Instructions"):
        st.caption("Instructions will be added as the workflow is developed.")

elif active_page == "Site":
    render_navigation()
    st.title("Site")
    st.caption("Representative annual clear-sky study for the selected location")

    with st.sidebar:
        st.header("Study location")
        latitude = st.number_input(
            "Latitude (°)", min_value=-90.0, max_value=90.0,
            step=0.00001, format="%.5f", key="latitude"
        )
        longitude = st.number_input(
            "Longitude (°)", min_value=-180.0, max_value=180.0,
            step=0.00001, format="%.5f", key="longitude"
        )
        resolution = st.selectbox(
            "Time step", options=[1, 5, 15], index=1,
            format_func=lambda value: f"{value} min"
        )
        ghi_threshold = st.number_input(
            "Clear-sky GHI threshold (W/m²)", min_value=0.0,
            max_value=1400.0, value=100.0, step=10.0
        )
        calculate = st.button("Calculate annual data", type="primary", width="stretch")

    map_col, summary_col = st.columns([1.4, 1])
    with map_col:
        st.subheader("Select the study location")
        st.caption("Click the map to reposition the marker, or drag it for fine adjustment.")
        location_map = folium.Map(
            location=[latitude, longitude], zoom_start=17,
            tiles=None, control_scale=True
        )
        folium.TileLayer(
            tiles="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
            attr="Esri, Maxar, Earthstar Geographics, and the GIS User Community",
            name="Satellite", overlay=False,
        ).add_to(location_map)
        location_marker = folium.Marker(
            [latitude, longitude],
            tooltip="Drag to fine-tune the study location",
            icon=folium.Icon(color="green", icon="crosshairs", prefix="fa"),
            draggable=True,
        )
        location_marker.add_child(SyncDraggedMarker())
        location_marker.add_to(location_map)
        Fullscreen(position="topright").add_to(location_map)
        map_state = st_folium(
            location_map, key="study_location_map",
            height=500, use_container_width=True
        )
        clicked = map_state.get("last_clicked") if map_state else None
        if clicked:
            clicked_coordinates = (round(clicked["lat"], 6), round(clicked["lng"], 6))
            if clicked_coordinates != st.session_state.get("last_processed_click"):
                st.session_state.last_processed_click = clicked_coordinates
                st.session_state.pending_coordinates = clicked_coordinates
                st.rerun()

    with summary_col:
        st.subheader("Current site")
        st.metric("Coordinates", f"{latitude:.5f}°, {longitude:.5f}°")
        st.metric("Resolution", f"{resolution} minute{'s' if resolution != 1 else ''}")

    if calculate:
        with st.spinner("Calculating solar position and clear-sky irradiance…"):
            data = generate_annual_solar_data(
                latitude=latitude, longitude=longitude,
                year=REFERENCE_YEAR, interval_minutes=int(resolution),
                ghi_threshold=float(ghi_threshold),
            )

        annual_ghi = data["ghi"].sum() * float(resolution) / 60 / 1000
        st.subheader("Site parameters")
        st.dataframe(
            {
                "Parameter": ["Annual clear-sky GHI", "Maximum clear-sky GHI"],
                "Value": [
                    f"{annual_ghi:,.1f} kWh/m²",
                    f"{data['ghi'].max():,.1f} W/m²",
                ],
            },
            hide_index=True, width="stretch",
        )

        st.subheader("Average clear-sky GHI by month and hour")
        ghi_matrix, timezone_name, utc_offset = monthly_hourly_ghi_matrix(
            data, latitude, longitude
        )
        heatmap_data = (
            ghi_matrix.rename_axis("Month").reset_index()
            .melt(id_vars="Month", var_name="Hour", value_name="Average GHI")
        )
        heatmap = (
            alt.Chart(heatmap_data).mark_rect().encode(
                x=alt.X("Hour:O", title="Hour of day", sort=list(range(24))),
                y=alt.Y("Month:N", title=None, sort=list(ghi_matrix.index)),
                color=alt.Color(
                    "Average GHI:Q", title="GHI (W/m²)",
                    scale=alt.Scale(
                        range=["#f4faf6", "#9bd4b1", "#1e8e5a", "#0b2942"]
                    ),
                ),
                tooltip=[
                    alt.Tooltip("Month:N"),
                    alt.Tooltip("Hour:O", title="Local standard hour"),
                    alt.Tooltip("Average GHI:Q", title="Average GHI", format=".1f"),
                ],
            ).properties(height=360)
        )
        st.altair_chart(heatmap, width="stretch")

        offset_label = f"UTC{utc_offset:+g}"
        st.caption(
            f"Time basis: local standard time ({timezone_name}, {offset_label}). "
            "Daylight saving time is not considered."
        )

else:
    render_navigation()
    st.title(active_page)
    placeholder_text = {
        "Objects": "Object definition will be added in the next development step.",
        "Shadow study": "Shadow calculations will be added after the object definition.",
        "Export": "KMZ and DWG export options will be added in a later step.",
    }
    st.info(placeholder_text[active_page])
