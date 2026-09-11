import altair as alt
import folium
import streamlit as st
from branca.element import MacroElement
from folium.plugins import Fullscreen
from jinja2 import Template
from streamlit_folium import st_folium

from solar_data import generate_annual_solar_data, monthly_hourly_ghi_matrix


REFERENCE_YEAR = 2025
SITE_INTERVAL_MINUTES = 15


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


PAGES = ("Site Creation", "Object Generation", "Shadow Study", "Export Results")


def render_navigation(home: bool = False) -> None:
    """Render the pastel workflow navigation."""
    if not home:
        st.markdown(
            '<a class="main-menu" href="?" target="_self">⌂&nbsp;&nbsp;Main menu</a>',
            unsafe_allow_html=True,
        )
    site_complete = st.session_state.get("site_completed", False)
    object_complete = st.session_state.get("object_completed", False)
    shadow_complete = st.session_state.get("shadow_completed", False)
    export_unlocked = site_complete and object_complete and shadow_complete
    links = [
        ("Site Creation", "📍", "site", site_complete, True),
        ("Object Generation", "🧊", "objects", object_complete, True),
        ("Shadow Study", "🌤️", "shadow", shadow_complete, True),
        ("Export Results", "📦", "export", export_unlocked, export_unlocked),
    ]
    size_class = " home" if home else ""
    items = []
    for label, icon, css_class, completed, enabled in links:
        status = "😊" if completed else "☹️"
        content = (
            f'<span class="nav-icon">{icon}</span><span class="nav-label">{label}</span>'
            f'<span class="nav-status" title="{"Completed" if completed else "Not completed"}">{status}</span>'
        )
        if enabled:
            items.append(
                f'<a class="{css_class}" href="?page={label.replace(" ", "%20")}" '
                f'target="_self">{content}</a>'
            )
        else:
            items.append(f'<div class="{css_class} disabled">{content}</div>')
    st.markdown(
        f'<nav class="pv-nav{size_class}">{"".join(items)}</nav>',
        unsafe_allow_html=True,
    )


def mark_site_complete() -> None:
    """Store the exact site inputs used by the calculation."""
    st.session_state.site_calculation_signature = (
        round(float(st.session_state.latitude), 5),
        round(float(st.session_state.longitude), 5),
        float(st.session_state.ghi_threshold),
    )
    st.session_state.site_completed = True


st.set_page_config(page_title="PV Butterfly", page_icon="🦋", layout="wide")
st.markdown(
    """
    <style>
        .stApp {
            color: #16324a;
            background-color: #fffdf9;
            background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 800 650'%3E%3Cg stroke='%23806a55' stroke-width='5' stroke-linejoin='round'%3E%3Cpath d='M382 304 C323 154 186 70 75 118 C57 236 156 320 354 337 C237 340 171 390 177 474 C218 502 280 477 363 370 C329 491 273 549 209 588 C294 565 361 495 390 386 Z' fill='%23e6d7c3'/%3E%3Cpath d='M418 304 C477 154 614 70 725 118 C743 236 644 320 446 337 C563 340 629 390 623 474 C582 502 520 477 437 370 C471 491 527 549 591 588 C506 565 439 495 410 386 Z' fill='%23e6d7c3'/%3E%3Cpath d='M354 299 C304 194 213 128 113 145 C141 230 221 286 354 318 Z M446 299 C496 194 587 128 687 145 C659 230 579 286 446 318 Z' fill='%23806a55'/%3E%3Cellipse cx='277' cy='391' rx='65' ry='48' transform='rotate(-18 277 391)' fill='%23a98d70'/%3E%3Cellipse cx='523' cy='391' rx='65' ry='48' transform='rotate(18 523 391)' fill='%23a98d70'/%3E%3Cpath d='M248 462 C276 423 314 411 344 421 C326 477 289 509 244 515 Z M552 462 C524 423 486 411 456 421 C474 477 511 509 556 515 Z' fill='%23806a55'/%3E%3Cellipse cx='216' cy='438' rx='23' ry='27' fill='%23806a55'/%3E%3Cellipse cx='584' cy='438' rx='23' ry='27' fill='%23806a55'/%3E%3Cpath d='M400 289 C379 315 380 477 400 526 C420 477 421 315 400 289 Z' fill='%2364503f'/%3E%3Ccircle cx='400' cy='275' r='18' fill='%2364503f'/%3E%3Cpath d='M392 267 C369 218 340 181 321 148 M408 267 C431 218 460 181 479 148' fill='none' stroke-linecap='round'/%3E%3C/g%3E%3C/svg%3E");
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
        .main-menu {
            display: inline-flex;
            align-items: center;
            margin: 0 0 .7rem;
            padding: .5rem .9rem;
            border-radius: 999px;
            color: #16324a !important;
            background: rgba(255, 253, 249, .88);
            border: 1px solid #d8c3a5;
            font-weight: 700;
            text-decoration: none !important;
            box-shadow: 0 2px 8px rgba(22, 50, 74, .06);
        }
        .main-menu:hover { background: #f2e7d8; }
        .pv-nav {
            display: grid;
            grid-template-columns: repeat(4, minmax(0, 1fr));
            gap: .75rem;
            margin: .5rem 0 1.5rem;
        }
        .pv-nav a, .pv-nav > div {
            min-height: 3.4rem;
            padding: .75rem;
            border-radius: .8rem;
            display: flex;
            position: relative;
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
        .pv-nav.home a, .pv-nav.home > div {
            min-height: 8rem;
            font-size: 1.3rem;
        }
        .pv-nav .nav-icon { font-size: 1.35em; }
        .pv-nav .nav-status {
            position: absolute;
            right: .55rem;
            bottom: .4rem;
            font-size: .9rem;
        }
        .pv-nav .site { background: #cfe8dc; }
        .pv-nav .objects { background: #d9e5f2; }
        .pv-nav .shadow { background: #f7dfb9; }
        .pv-nav .export { background: #eadcf0; }
        .pv-nav .export.disabled {
            color: #798087;
            background: #e5e7e9;
            border-color: #d5d8da;
            box-shadow: none;
            cursor: not-allowed;
        }
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
if active_page == "Export Results" and not all(
    st.session_state.get(key, False)
    for key in ("site_completed", "object_completed", "shadow_completed")
):
    active_page = "Home"


if active_page == "Home":
    st.title("PV Butterfly")
    st.caption("Select a study area to begin")
    render_navigation(home=True)

    with st.expander("Instructions"):
        st.caption("Instructions will be added as the workflow is developed.")

elif active_page == "Site Creation":
    render_navigation()
    st.title("Site Creation")
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
        st.caption("Site-data time step: 15 minutes")
        ghi_threshold = st.number_input(
            "Clear-sky GHI threshold (W/m²)", min_value=0.0,
            max_value=1400.0, value=100.0, step=10.0, key="ghi_threshold"
        )
        calculate = st.button(
            "Calculate annual data", type="primary", width="stretch",
            on_click=mark_site_complete
        )

    site_signature = (
        round(float(latitude), 5),
        round(float(longitude), 5),
        float(ghi_threshold),
    )
    previous_signature = st.session_state.get("site_calculation_signature")
    if previous_signature is not None and previous_signature != site_signature:
        st.session_state.site_completed = False
        st.session_state.site_calculation_signature = None
        st.rerun()

    data = None
    if calculate:
        with st.spinner("Calculating solar position and clear-sky irradiance…"):
            data = generate_annual_solar_data(
                latitude=latitude, longitude=longitude,
                year=REFERENCE_YEAR, interval_minutes=SITE_INTERVAL_MINUTES,
                ghi_threshold=float(ghi_threshold),
            )
        st.session_state.site_completed = True
        st.session_state.site_calculation_signature = site_signature

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
        st.subheader("Site parameters")
        if data is None:
            st.info("Calculate the annual data to display the site parameters.")
        else:
            annual_ghi = data["ghi"].sum() * SITE_INTERVAL_MINUTES / 60 / 1000
            production_mask = data["ghi"] > 0
            affected_mask = production_mask & (data["ghi"] < float(ghi_threshold))
            production_hours = production_mask.sum() * SITE_INTERVAL_MINUTES / 60
            affected_hours = affected_mask.sum() * SITE_INTERVAL_MINUTES / 60
            affected_share = (
                affected_hours / production_hours * 100
                if production_hours > 0
                else 0.0
            )
            st.metric("Annual clear-sky GHI", f"{annual_ghi:,.1f} kWh/m²")
            st.metric("Maximum clear-sky GHI", f"{data['ghi'].max():,.1f} W/m²")
            st.metric(
                "Affected production hours",
                f"{affected_hours:,.1f} h",
                help="Hours with clear-sky GHI above 0 W/m² but below the selected threshold.",
            )
            st.metric(
                "Affected production hours (%)",
                f"{affected_share:.1f}%",
                help="Affected hours as a percentage of all hours with clear-sky GHI above 0 W/m².",
            )

    if data is not None:
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
        "Object Generation": "Object definition will be added in the next development step.",
        "Shadow Study": "Shadow calculations will be added after the object definition.",
        "Export Results": "KMZ and DWG export options will be added in a later step.",
    }
    st.info(placeholder_text[active_page])
