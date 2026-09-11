import altair as alt
import folium
import plotly.graph_objects as go
import streamlit as st
import streamlit.components.v1 as components
from html import escape
import uuid
from branca.element import MacroElement
from folium.plugins import Fullscreen
from jinja2 import Template
from streamlit_folium import st_folium

from object_geometry import (
    bearing_from_coordinates,
    cuboid_dimension_midpoints,
    cuboid_footprint_latlon,
    cuboid_vertices,
    footprint_center,
)
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


class LiveMoveHandle(MacroElement):
    """Move a footprint live and report the final centre to Streamlit."""

    _template = Template(
        """
        {% macro script(this, kwargs) %}
        {
        const moveMarker = {{ this.marker.get_name() }};
        const movePolygon = {{ this.polygon.get_name() }};
        const originMarker = {{ this.origin_marker.get_name() }};
        const moveMetadata = document.createElement('span');
        moveMetadata.innerText = 'MOVE||{{ this.object_id }}';
        moveMarker._popup = L.popup({autoClose: false, closeOnClick: false})
            .setContent(moveMetadata);
        let moveStart = null;
        let polygonStart = null;
        let originStart = null;
        moveMarker.on('dragstart', function() {
            moveStart = moveMarker.getLatLng();
            polygonStart = movePolygon.getLatLngs()[0].map(
                point => L.latLng(point.lat, point.lng)
            );
            originStart = originMarker.getLatLng();
        });
        moveMarker.on('drag', function() {
            const current = moveMarker.getLatLng();
            const latitudeDelta = current.lat - moveStart.lat;
            const longitudeDelta = current.lng - moveStart.lng;
            movePolygon.setLatLngs([polygonStart.map(
                point => L.latLng(
                    point.lat + latitudeDelta,
                    point.lng + longitudeDelta
                )
            )]);
            originMarker.setLatLng(L.latLng(
                originStart.lat + latitudeDelta,
                originStart.lng + longitudeDelta
            ));
        });
        moveMarker.on('dragend', function() {
            moveMarker.fire('click', {
                latlng: moveMarker.getLatLng(),
                sourceTarget: moveMarker
            });
        });
        }
        {% endmacro %}
        """
    )

    def __init__(self, marker, polygon, origin_marker, object_id):
        super().__init__()
        self.marker = marker
        self.polygon = polygon
        self.origin_marker = origin_marker
        self.object_id = object_id


class LiveRotateHandle(MacroElement):
    """Rotate a footprint live about its insertion corner."""

    _template = Template(
        """
        {% macro script(this, kwargs) %}
        {
        const rotateMarker = {{ this.marker.get_name() }};
        const rotatePolygon = {{ this.polygon.get_name() }};
        const centreMarker = {{ this.centre_marker.get_name() }};
        const lengthX = {{ this.length_x }};
        const widthY = {{ this.width_y }};
        const rotateMetadata = document.createElement('span');
        rotateMetadata.innerText = 'ROTATE||{{ this.object_id }}';
        rotateMarker._popup = L.popup({autoClose: false, closeOnClick: false})
            .setContent(rotateMetadata);
        let fixedOrigin = null;

        function destination(origin, distance, bearingDegrees) {
            const radius = 6378137.0;
            const angularDistance = distance / radius;
            const bearing = bearingDegrees * Math.PI / 180;
            const latitude1 = origin.lat * Math.PI / 180;
            const longitude1 = origin.lng * Math.PI / 180;
            const latitude2 = Math.asin(
                Math.sin(latitude1) * Math.cos(angularDistance) +
                Math.cos(latitude1) * Math.sin(angularDistance) * Math.cos(bearing)
            );
            const longitude2 = longitude1 + Math.atan2(
                Math.sin(bearing) * Math.sin(angularDistance) * Math.cos(latitude1),
                Math.cos(angularDistance) - Math.sin(latitude1) * Math.sin(latitude2)
            );
            return L.latLng(
                latitude2 * 180 / Math.PI,
                longitude2 * 180 / Math.PI
            );
        }

        function bearing(origin, target) {
            const latitude1 = origin.lat * Math.PI / 180;
            const latitude2 = target.lat * Math.PI / 180;
            const longitudeDelta = (target.lng - origin.lng) * Math.PI / 180;
            const east = Math.sin(longitudeDelta) * Math.cos(latitude2);
            const north = (
                Math.cos(latitude1) * Math.sin(latitude2) -
                Math.sin(latitude1) * Math.cos(latitude2) * Math.cos(longitudeDelta)
            );
            return (Math.atan2(east, north) * 180 / Math.PI + 360) % 360;
        }

        rotateMarker.on('dragstart', function() {
            fixedOrigin = rotatePolygon.getLatLngs()[0][0];
        });
        rotateMarker.on('drag', function() {
            const azimuth = bearing(fixedOrigin, rotateMarker.getLatLng());
            const pointX = destination(fixedOrigin, lengthX, azimuth);
            const pointY = destination(fixedOrigin, widthY, azimuth - 90);
            const pointXY = destination(pointX, widthY, azimuth - 90);
            const corners = [fixedOrigin, pointX, pointXY, pointY];
            rotatePolygon.setLatLngs([corners]);
            centreMarker.setLatLng(L.latLng(
                corners.reduce((sum, point) => sum + point.lat, 0) / 4,
                corners.reduce((sum, point) => sum + point.lng, 0) / 4
            ));
        });
        rotateMarker.on('dragend', function() {
            rotateMarker.fire('click', {
                latlng: rotateMarker.getLatLng(),
                sourceTarget: rotateMarker
            });
        });
        }
        {% endmacro %}
        """
    )

    def __init__(self, marker, polygon, centre_marker, length_x, width_y, object_id):
        super().__init__()
        self.marker = marker
        self.polygon = polygon
        self.centre_marker = centre_marker
        self.length_x = float(length_x)
        self.width_y = float(width_y)
        self.object_id = object_id


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
        status = "✓" if completed else ""
        content = (
            f'<span class="nav-icon">{icon}</span><span class="nav-label">{label}</span>'
            f'<span class="nav-status{" completed" if completed else ""}" '
            f'title="{"Completed" if completed else "Not completed"}">{status}</span>'
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
    st.session_state.completion_notice = "Site Creation"


def next_object_name(object_type: str) -> str:
    """Return the next unused sequential default name for an object type."""
    prefix = object_type.lower()
    numbers = []
    for item in st.session_state.get("objects", []):
        name = item.get("name", "").lower()
        if name.startswith(f"{prefix} "):
            try:
                numbers.append(int(name.removeprefix(f"{prefix} ")))
            except ValueError:
                pass
    counters = st.session_state.setdefault("object_name_counters", {})
    next_number = max(counters.get(object_type, 0), max(numbers, default=0)) + 1
    counters[object_type] = next_number
    return f"{object_type.lower()} {next_number}"


def open_object_form(item: dict | None = None, index: int | None = None) -> None:
    """Initialise the editor for a new or saved object."""
    is_new = item is None
    item = item or {}
    object_type = item.get("type", "Cuboid")
    st.session_state.object_form_visible = True
    st.session_state.editing_object_index = index
    st.session_state.object_type = object_type
    st.session_state.object_name = (
        next_object_name(object_type) if is_new else item["name"]
    )
    st.session_state.cuboid_x = float(item.get("length_x_m", 20.0))
    st.session_state.cuboid_y = float(item.get("width_y_m", 10.0))
    st.session_state.cuboid_z = float(item.get("height_z_m", 8.0))
    st.session_state.object_latitude = float(item.get("latitude", st.session_state.latitude))
    st.session_state.object_longitude = float(item.get("longitude", st.session_state.longitude))
    st.session_state.object_azimuth = float(item.get("azimuth_deg", 90.0))


def render_cuboid_preview(vertices, dimensions) -> None:
    """Render a dimensioned cuboid with its insertion point and global axes."""
    faces = [
        (0, 1, 2), (0, 2, 3), (4, 6, 5), (4, 7, 6),
        (0, 4, 5), (0, 5, 1), (1, 5, 6), (1, 6, 2),
        (2, 6, 7), (2, 7, 3), (3, 7, 4), (3, 4, 0),
    ]
    figure = go.Figure()
    figure.add_trace(
        go.Mesh3d(
            x=vertices[:, 0], y=vertices[:, 1], z=vertices[:, 2],
            i=[face[0] for face in faces],
            j=[face[1] for face in faces],
            k=[face[2] for face in faces],
            color="#9fc8ba", opacity=0.62, flatshading=True,
            hoverinfo="skip", showlegend=False,
        )
    )
    edges = [
        (0, 1), (1, 2), (2, 3), (3, 0),
        (4, 5), (5, 6), (6, 7), (7, 4),
        (0, 4), (1, 5), (2, 6), (3, 7),
    ]
    for start, end in edges:
        figure.add_trace(
            go.Scatter3d(
                x=[vertices[start, 0], vertices[end, 0]],
                y=[vertices[start, 1], vertices[end, 1]],
                z=[vertices[start, 2], vertices[end, 2]],
                mode="lines", line=dict(color="#254f63", width=4),
                hoverinfo="skip", showlegend=False,
            )
        )

    origin = vertices[0]
    span = max(dimensions) * 0.42
    axes = [
        ("X · East", "#d55e5e", (span, 0, 0)),
        ("Y · North", "#3a9565", (0, span, 0)),
        ("Z · Up", "#4d78b8", (0, 0, span)),
    ]
    for label, colour, delta in axes:
        end = origin + delta
        figure.add_trace(
            go.Scatter3d(
                x=[origin[0], end[0]], y=[origin[1], end[1]],
                z=[origin[2], end[2]], mode="lines+text",
                line=dict(color=colour, width=7), text=[None, label],
                textposition="top center", showlegend=False,
                hoverinfo="skip",
            )
        )
    figure.add_trace(
        go.Scatter3d(
            x=[origin[0]], y=[origin[1]], z=[origin[2]],
            mode="markers+text", marker=dict(size=8, color="#c84848"),
            text=["ORIGIN"], textposition="bottom center",
            name="Insertion corner",
        )
    )

    midpoints = cuboid_dimension_midpoints(vertices)
    labels = [
        (midpoints["x"], f"X = {dimensions[0]:g} m"),
        (midpoints["y"], f"Y = {dimensions[1]:g} m"),
        (midpoints["z"], f"Z = {dimensions[2]:g} m"),
    ]
    figure.add_trace(
        go.Scatter3d(
            x=[item[0][0] for item in labels],
            y=[item[0][1] for item in labels],
            z=[item[0][2] for item in labels],
            mode="text", text=[item[1] for item in labels],
            textfont=dict(size=13, color="#16324a"),
            hoverinfo="skip", showlegend=False,
        )
    )
    figure.update_layout(
        height=610, margin=dict(l=0, r=0, t=15, b=0),
        paper_bgcolor="rgba(255,255,255,0.76)",
        scene=dict(
            xaxis_title="X · East (m)", yaxis_title="Y · North (m)",
            zaxis_title="Height (m)", aspectmode="data",
            camera=dict(eye=dict(x=1.45, y=1.55, z=1.15)),
        ),
        showlegend=False,
    )
    st.plotly_chart(figure, width="stretch", config={"displaylogo": False})


def render_completion_notice() -> None:
    """Show a one-time completion toast and play a short confirmation tone."""
    completed_page = st.session_state.pop("completion_notice", None)
    if not completed_page:
        return
    st.toast(f"{completed_page} has been completed", icon="✅")
    components.html(
        """
        <script>
        (() => {
            try {
                const AudioContext = window.AudioContext || window.webkitAudioContext;
                const context = new AudioContext();
                const oscillator = context.createOscillator();
                const gain = context.createGain();
                oscillator.type = "sine";
                oscillator.frequency.setValueAtTime(660, context.currentTime);
                oscillator.frequency.exponentialRampToValueAtTime(880, context.currentTime + 0.14);
                gain.gain.setValueAtTime(0.055, context.currentTime);
                gain.gain.exponentialRampToValueAtTime(0.001, context.currentTime + 0.2);
                oscillator.connect(gain);
                gain.connect(context.destination);
                oscillator.start();
                oscillator.stop(context.currentTime + 0.2);
            } catch (error) {
                // The visual confirmation remains available if a browser blocks audio.
            }
        })();
        </script>
        """,
        height=0,
    )


st.set_page_config(page_title="PV Butterfly", page_icon="🦋", layout="wide")
st.markdown(
    """
    <style>
        .stApp {
            color: #16324a;
            background-color: #fffdf9;
            background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 800 620'%3E%3Cg fill='%23dccbb5' fill-opacity='.10' stroke='%23cbb79d' stroke-opacity='.14' stroke-width='4' stroke-linejoin='round'%3E%3Cpath d='M386 304 C338 164 196 76 82 137 C86 252 188 319 354 326 C244 342 184 410 199 501 C268 519 340 436 388 355 Z'/%3E%3Cpath d='M414 304 C462 164 604 76 718 137 C714 252 612 319 446 326 C556 342 616 410 601 501 C532 519 460 436 412 355 Z'/%3E%3Cpath d='M398 278 C383 322 385 442 400 487 C415 442 417 322 402 278 Z'/%3E%3C/g%3E%3Cg fill='none' stroke='%23cbb79d' stroke-opacity='.13' stroke-width='4' stroke-linecap='round'%3E%3Cpath d='M393 281 C365 224 337 184 307 153 M407 281 C435 224 463 184 493 153'/%3E%3Cpath d='M361 322 C267 287 176 225 104 151 M439 322 C533 287 624 225 696 151'/%3E%3C/g%3E%3C/svg%3E");
            background-position: center center;
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
            min-width: 1.15rem;
            min-height: 1.15rem;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            font-size: .82rem;
            font-weight: 800;
        }
        .pv-nav .nav-status.completed {
            color: #ffffff;
            background: #43a66b;
            border-radius: 50%;
        }
        .pv-nav .site { background: #cfe8dc; }
        .pv-nav .objects { background: #d9e5f2; }
        .pv-nav .shadow { background: #f7dfb9; }
        .pv-nav .export { background: #eadcf0; }
        .pv-nav .export.disabled {
            color: #a1a7ad !important;
            background: #e5e7e9;
            border-color: #d5d8da;
            box-shadow: none;
            cursor: not-allowed;
        }
        .pv-nav .export.disabled .nav-label,
        .pv-nav .export.disabled .nav-icon {
            color: #a1a7ad !important;
            filter: grayscale(1);
            opacity: .62;
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

render_completion_notice()


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
        st.session_state.pop("site_data", None)
        st.rerun()

    data = st.session_state.get("site_data")
    if calculate:
        with st.spinner("Calculating solar position and clear-sky irradiance…"):
            data = generate_annual_solar_data(
                latitude=latitude, longitude=longitude,
                year=REFERENCE_YEAR, interval_minutes=SITE_INTERVAL_MINUTES,
                ghi_threshold=float(ghi_threshold),
            )
        st.session_state.site_data = data
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

elif active_page == "Object Generation":
    render_navigation()
    st.title("Object Generation")
    st.caption("Define and preview the objects that will cast shadows")
    if "objects" not in st.session_state:
        st.session_state.objects = []
    if "object_map_revision" not in st.session_state:
        st.session_state.object_map_revision = 0

    action_col, count_col = st.columns([1, 3])
    with action_col:
        if st.button("Create new object", type="primary", width="stretch"):
            open_object_form()
            st.rerun()
    with count_col:
        object_count = len(st.session_state.objects)
        st.caption(f"{object_count} saved object{'s' if object_count != 1 else ''}")

    if st.session_state.get("object_form_visible", False):
        st.divider()
        editing_index = st.session_state.get("editing_object_index")
        st.subheader("Edit object" if editing_index is not None else "New object")
        object_type = st.selectbox(
            "Object type", ["Cuboid", "Wind turbine"], key="object_type"
        )
        st.text_input("Object name", key="object_name")

        if object_type == "Wind turbine":
            st.info("Wind-turbine geometry will be added in the next development step.")
            if st.button("Cancel"):
                st.session_state.object_form_visible = False
                st.rerun()
        else:
            controls, preview = st.columns([0.82, 1.65], gap="large")
            with controls:
                st.markdown("##### Dimensions")
                cuboid_x = st.number_input(
                    "X dimension (m)", min_value=0.01,
                    step=0.5, key="cuboid_x"
                )
                cuboid_y = st.number_input(
                    "Y dimension (m)", min_value=0.01,
                    step=0.5, key="cuboid_y"
                )
                cuboid_z = st.number_input(
                    "Z height (m)", min_value=0.01,
                    step=0.5, key="cuboid_z"
                )
                st.markdown("##### Origin position")
                object_latitude = st.number_input(
                    "Origin latitude (°)", min_value=-90.0, max_value=90.0,
                    step=0.00001, format="%.5f", key="object_latitude"
                )
                object_longitude = st.number_input(
                    "Origin longitude (°)", min_value=-180.0, max_value=180.0,
                    step=0.00001, format="%.5f", key="object_longitude"
                )
                azimuth = st.number_input(
                    "Local X-axis azimuth (°)", min_value=0.0,
                    max_value=359.99, step=1.0,
                    key="object_azimuth",
                    help="Clockwise from North. At 90°, the cuboid's local X-axis points East."
                )
                st.caption(
                    "Latitude and longitude define the highlighted insertion corner. "
                    "The terrain is horizontal at Z = 0 and the cuboid rotates around this origin."
                )

                save_col, cancel_col = st.columns(2)
                save_clicked = save_col.button(
                    "Save object", type="primary", width="stretch"
                )
                cancel_clicked = cancel_col.button("Cancel", width="stretch")

            vertices = cuboid_vertices(
                cuboid_x, cuboid_y, cuboid_z,
                azimuth=azimuth,
            )
            with preview:
                st.subheader("Live 3D preview")
                render_cuboid_preview(vertices, (cuboid_x, cuboid_y, cuboid_z))

            if cancel_clicked:
                st.session_state.object_form_visible = False
                st.rerun()
            if save_clicked:
                clean_name = st.session_state.object_name.strip()
                if not clean_name:
                    st.error("Enter an object name before saving.")
                elif any(
                    existing["name"].casefold() == clean_name.casefold()
                    and index != editing_index
                    for index, existing in enumerate(st.session_state.objects)
                ):
                    st.error("Object names must be unique.")
                else:
                    saved_object = {
                        "id": (
                            st.session_state.objects[editing_index]["id"]
                            if editing_index is not None else str(uuid.uuid4())
                        ),
                        "name": clean_name,
                        "type": "Cuboid",
                        "length_x_m": float(cuboid_x),
                        "width_y_m": float(cuboid_y),
                        "height_z_m": float(cuboid_z),
                        "latitude": float(object_latitude),
                        "longitude": float(object_longitude),
                        "azimuth_deg": float(azimuth),
                    }
                    was_complete = bool(st.session_state.objects)
                    if editing_index is None:
                        st.session_state.objects.append(saved_object)
                    else:
                        st.session_state.objects[editing_index] = saved_object
                    st.session_state.object_map_revision += 1
                    st.session_state.object_completed = True
                    st.session_state.object_form_visible = False
                    st.session_state.editing_object_index = None
                    if not was_complete:
                        st.session_state.completion_notice = "Object Generation"
                    st.rerun()

    if st.session_state.objects:
        st.divider()
        list_col, map_col = st.columns([0.9, 1.55], gap="large")
        with list_col:
            st.subheader("Saved objects")
            for index, item in enumerate(st.session_state.objects):
                with st.container(border=True):
                    st.markdown(f"**{item['name']}** · {item['type']}")
                    st.caption(
                        f"{item['length_x_m']:g} × {item['width_y_m']:g} × "
                        f"{item['height_z_m']:g} m · Azimuth {item['azimuth_deg']:g}°"
                    )
                    st.caption(
                        f"Origin: {item['latitude']:.5f}, {item['longitude']:.5f}"
                    )
                    edit_col, delete_col = st.columns(2)
                    if edit_col.button("Edit", key=f"edit_{item['id']}", width="stretch"):
                        open_object_form(item, index)
                        st.rerun()
                    if delete_col.button(
                        "Delete", key=f"delete_{item['id']}", width="stretch"
                    ):
                        st.session_state.objects.pop(index)
                        st.session_state.object_completed = bool(st.session_state.objects)
                        st.session_state.object_map_revision += 1
                        st.rerun()

        with map_col:
            st.subheader("Object locations")
            centre_latitude = sum(item["latitude"] for item in st.session_state.objects) / len(st.session_state.objects)
            centre_longitude = sum(item["longitude"] for item in st.session_state.objects) / len(st.session_state.objects)
            object_map = folium.Map(
                location=[centre_latitude, centre_longitude],
                zoom_start=18, tiles=None, control_scale=True,
            )
            folium.TileLayer(
                tiles="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
                attr="Esri, Maxar, Earthstar Geographics, and the GIS User Community",
                name="Satellite", overlay=False,
            ).add_to(object_map)
            all_footprint_points = []
            for item in st.session_state.objects:
                footprint = cuboid_footprint_latlon(
                    item["latitude"], item["longitude"],
                    item["length_x_m"], item["width_y_m"],
                    item["azimuth_deg"],
                )
                all_footprint_points.extend(footprint)
                centre = footprint_center(footprint)
                safe_name = escape(item["name"])
                object_polygon = folium.Polygon(
                    locations=footprint,
                    color="#1f6f55", weight=3,
                    fill=True, fill_color="#9fc8ba", fill_opacity=0.48,
                    tooltip=safe_name,
                )
                object_polygon.add_to(object_map)
                move_handle = folium.Marker(
                    centre,
                    draggable=True,
                    tooltip=safe_name,
                    icon=folium.DivIcon(
                        icon_size=(34, 34), icon_anchor=(17, 17),
                        html=(
                            '<div style="width:34px;height:34px;cursor:move">'
                            '<div style="width:30px;height:30px;border-radius:50%;'
                            'background:#fffdf9;border:2px solid #1f6f55;color:#1f6f55;'
                            'display:flex;align-items:center;justify-content:center;'
                            'font-size:20px;font-weight:800;box-shadow:0 2px 7px #0004">✥</div></div>'
                        ),
                    ),
                )
                rotation_handle = folium.Marker(
                    [item["latitude"], item["longitude"]],
                    draggable=True,
                    tooltip=safe_name,
                    icon=folium.DivIcon(
                        icon_size=(28, 28), icon_anchor=(14, 14),
                        html=(
                            '<div style="width:26px;height:26px;border-radius:50%;'
                            'background:#fff7ed;border:2px solid #b47b45;color:#9b642f;'
                            'display:flex;align-items:center;justify-content:center;'
                            'font-size:20px;font-weight:800;box-shadow:0 2px 7px #0004;'
                            'cursor:url(&quot;data:image/svg+xml,%3Csvg xmlns=\'http://www.w3.org/2000/svg\' '
                            'width=\'28\' height=\'28\'%3E%3Ctext x=\'2\' y=\'23\' font-size=\'24\' '
                            'fill=\'%2316324a\'%3E%E2%86%BB%3C/text%3E%3C/svg%3E&quot;) 14 14,grab">↻</div>'
                        ),
                    ),
                )
                move_handle.add_to(object_map)
                rotation_handle.add_to(object_map)
                object_map.add_child(
                    LiveMoveHandle(
                        move_handle, object_polygon, rotation_handle, item["id"]
                    )
                )
                object_map.add_child(
                    LiveRotateHandle(
                        rotation_handle,
                        object_polygon,
                        move_handle,
                        item["length_x_m"],
                        item["width_y_m"],
                        item["id"],
                    )
                )
            if all_footprint_points:
                object_map.fit_bounds(all_footprint_points, padding=(35, 35))
            Fullscreen(position="topright").add_to(object_map)
            object_map_state = st_folium(
                object_map,
                key=f"saved_objects_map_{st.session_state.object_map_revision}",
                height=560, use_container_width=True,
                returned_objects=[
                    "last_object_clicked",
                    "last_object_clicked_popup",
                ],
            )
            handle_position = (
                object_map_state.get("last_object_clicked")
                if object_map_state else None
            )
            handle_metadata = (
                object_map_state.get("last_object_clicked_popup")
                if object_map_state else None
            )
            if handle_position and handle_metadata and "||" in handle_metadata:
                action_label, object_id = handle_metadata.split("||", 1)
                event_signature = (
                    action_label,
                    object_id,
                    round(handle_position["lat"], 7),
                    round(handle_position["lng"], 7),
                )
                if event_signature != st.session_state.get("last_object_map_event"):
                    st.session_state.last_object_map_event = event_signature
                    selected = next(
                        (
                            item for item in st.session_state.objects
                            if item["id"] == object_id
                        ),
                        None,
                    )
                    if selected is not None and action_label == "MOVE":
                        old_footprint = cuboid_footprint_latlon(
                            selected["latitude"], selected["longitude"],
                            selected["length_x_m"], selected["width_y_m"],
                            selected["azimuth_deg"],
                        )
                        old_centre = footprint_center(old_footprint)
                        selected["latitude"] += handle_position["lat"] - old_centre[0]
                        selected["longitude"] += handle_position["lng"] - old_centre[1]
                        st.session_state.object_map_revision += 1
                        st.rerun()
                    if selected is not None and action_label == "ROTATE":
                        displacement = abs(handle_position["lat"] - selected["latitude"]) + abs(
                            handle_position["lng"] - selected["longitude"]
                        )
                        if displacement > 1e-8:
                            selected["azimuth_deg"] = bearing_from_coordinates(
                                selected["latitude"], selected["longitude"],
                                handle_position["lat"], handle_position["lng"],
                            )
                            st.session_state.object_map_revision += 1
                            st.rerun()

elif active_page in ("Shadow Study", "Export Results"):
    render_navigation()
    st.title(active_page)
    placeholder_text = {
        "Shadow Study": "Shadow calculations will be added after the object definition.",
        "Export Results": "KMZ and DWG export options will be added in a later step.",
    }
    st.info(placeholder_text[active_page])
