import altair as alt
from copy import deepcopy
import folium
import hashlib
import json
from math import asin, cos, radians, sin, sqrt
import numpy as np
import plotly.graph_objects as go
import streamlit as st
import streamlit.components.v1 as components
from html import escape
import uuid
from branca.element import MacroElement
from folium.plugins import Fullscreen
from jinja2 import Template
from streamlit_folium import st_folium

from export_geometry import create_dxf, create_kmz, export_features

from object_geometry import (
    bearing_from_coordinates,
    circle_bounds_latlon,
    cuboid_dimension_midpoints,
    cuboid_footprint_latlon,
    cuboid_vertices,
    footprint_center,
)
from solar_data import generate_annual_solar_data, monthly_hourly_ghi_matrix
from shadow_geometry import (
    annual_shadow_envelopes,
    geometry_to_latlon_rings,
    soften_envelope_boundary,
)


REFERENCE_YEAR = 2025
SITE_INTERVAL_MINUTES = 15
SHADOW_INTERVAL_MINUTES = 1
DISTANT_OBJECT_THRESHOLD_KM = 50.0


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


class LiveTurbineMoveHandle(MacroElement):
    """Move a turbine's mast and rotor reference live on the map."""

    _template = Template(
        """
        {% macro script(this, kwargs) %}
        {
        const moveMarker = {{ this.marker.get_name() }};
        const mastCircle = {{ this.mast_circle.get_name() }};
        const rotorCircle = {{ this.rotor_circle.get_name() }};
        const moveMetadata = document.createElement('span');
        moveMetadata.innerText = 'MOVE||{{ this.object_id }}';
        moveMarker._popup = L.popup({autoClose: false, closeOnClick: false})
            .setContent(moveMetadata);
        moveMarker.on('drag', function() {
            const current = moveMarker.getLatLng();
            mastCircle.setLatLng(current);
            rotorCircle.setLatLng(current);
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

    def __init__(self, marker, mast_circle, rotor_circle, object_id):
        super().__init__()
        self.marker = marker
        self.mast_circle = mast_circle
        self.rotor_circle = rotor_circle
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


def navigate_to(page: str) -> None:
    """Navigate without reloading the browser, preserving the active session."""
    if page == "Home":
        st.query_params.clear()
    else:
        st.query_params["page"] = page


def committed_site_coordinates() -> tuple[float, float]:
    """Return the immutable coordinates used by calculations after Site completion."""
    return (
        float(st.session_state.get("site_latitude", st.session_state.latitude)),
        float(st.session_state.get("site_longitude", st.session_state.longitude)),
    )


def begin_site_location_edit() -> None:
    """Explicitly unlock the site coordinates for intentional modification."""
    st.session_state.site_completed = False
    st.session_state.site_location_editing = True
    st.session_state.pop("site_data", None)
    invalidate_shadow_study()


def distance_from_site_km(latitude: float, longitude: float) -> float:
    """Return great-circle distance from the study site to an object."""
    site_latitude, site_longitude = committed_site_coordinates()
    latitude_delta = radians(latitude - site_latitude)
    longitude_delta = radians(longitude - site_longitude)
    value = (
        sin(latitude_delta / 2) ** 2
        + cos(radians(site_latitude))
        * cos(radians(latitude))
        * sin(longitude_delta / 2) ** 2
    )
    return 2 * 6_371.0088 * asin(sqrt(value))


def distance_acknowledgement_signature(latitude: float, longitude: float) -> str:
    """Bind a distance acknowledgement to both site and object coordinates."""
    values = (
        round(committed_site_coordinates()[0], 5),
        round(committed_site_coordinates()[1], 5),
        round(float(latitude), 5),
        round(float(longitude), 5),
    )
    return hashlib.sha256(repr(values).encode("utf-8")).hexdigest()[:16]


def object_distance_is_accepted(item: dict) -> bool:
    """Return whether an object is near the site or its distance was accepted."""
    if distance_from_site_km(item["latitude"], item["longitude"]) <= DISTANT_OBJECT_THRESHOLD_KM:
        return True
    expected = distance_acknowledgement_signature(item["latitude"], item["longitude"])
    return item.get("distance_acknowledgement") == expected


def accept_saved_object_distance(object_id: str) -> None:
    """Acknowledge the current distance of one already-saved object."""
    for item in st.session_state.get("objects", []):
        if item["id"] == object_id:
            item["distance_acknowledgement"] = distance_acknowledgement_signature(
                item["latitude"], item["longitude"]
            )
            st.session_state.object_completed = all(
                object_distance_is_accepted(candidate)
                for candidate in st.session_state.get("objects", [])
            )
            invalidate_shadow_study()
            return


def require_distance_acceptance(
    save_clicked: bool, latitude: float, longitude: float, form_nonce: int
) -> tuple[bool, str | None]:
    """Pause a save until the user explicitly accepts a distance over 50 km."""
    distance_km = distance_from_site_km(latitude, longitude)
    if distance_km <= DISTANT_OBJECT_THRESHOLD_KM:
        st.session_state.pop("pending_distance_warning", None)
        return save_clicked, None

    signature = distance_acknowledgement_signature(latitude, longitude)
    editing_id = st.session_state.get("editing_object_id")
    existing = next(
        (item for item in st.session_state.get("objects", []) if item["id"] == editing_id),
        None,
    )
    if existing and existing.get("distance_acknowledgement") == signature:
        return save_clicked, signature
    if save_clicked:
        st.session_state.pending_distance_warning = signature
        st.toast(
            f"Object is {distance_km:,.1f} km from the study site.", icon="⚠️"
        )
    if st.session_state.get("pending_distance_warning") != signature:
        return False, None

    st.error(
        f"This object is {distance_km:,.1f} km from the Site Creation coordinates, "
        f"exceeding the {DISTANT_OBJECT_THRESHOLD_KM:g} km warning threshold. "
        "Confirm that these coordinates are intentional before continuing."
    )
    accept_col, cancel_col = st.columns(2)
    accepted = accept_col.button(
        "Accept distance and save", type="primary", width="stretch",
        key=f"accept_distance_{form_nonce}",
    )
    if cancel_col.button(
        "Review coordinates", width="stretch", key=f"review_distance_{form_nonce}"
    ):
        st.session_state.pop("pending_distance_warning", None)
        st.rerun()
    if accepted:
        st.session_state.pop("pending_distance_warning", None)
        return True, signature
    return False, None


def render_navigation(home: bool = False) -> None:
    """Render session-preserving workflow navigation."""
    if not home:
        st.button(
            "⌂  Main menu", key=f"main_menu_{st.query_params.get('page', 'home')}",
            on_click=navigate_to, args=("Home",),
        )
    site_complete = st.session_state.get("site_completed", False)
    object_complete = st.session_state.get("object_completed", False)
    shadow_complete = st.session_state.get("shadow_completed", False)
    export_unlocked = site_complete and object_complete and shadow_complete
    links = [
        ("Site Creation", "📍", "site", site_complete, True),
        ("Object Generation", "🧊", "objects", object_complete, True),
        ("Shadow Study", "🌤️", "shadow", shadow_complete, True),
        # Export is an available action, not a workflow step to complete.
        ("Export Results", "📦", "export", False, export_unlocked),
    ]
    columns = st.columns(2 if home else 4, gap="medium")
    key_context = "home" if home else str(st.query_params.get("page", "workspace"))
    for index, (label, icon, css_class, completed, enabled) in enumerate(links):
        status = "  ✓" if completed else ""
        columns[index % len(columns)].button(
            f"{icon}  {label}{status}",
            key=f"nav_{css_class}_{key_context}",
            disabled=not enabled,
            type="primary" if completed else "secondary",
            width="stretch",
            on_click=navigate_to,
            args=(label,),
        )


def mark_site_complete() -> None:
    """Store the exact site inputs used by the calculation."""
    st.session_state.site_calculation_signature = (
        round(float(st.session_state.latitude), 5),
        round(float(st.session_state.longitude), 5),
    )
    st.session_state.site_latitude = float(st.session_state.latitude)
    st.session_state.site_longitude = float(st.session_state.longitude)
    st.session_state.site_location_editing = False
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
    # Count saved objects only. Opening a form and switching its type must not
    # consume a number from either object's independent sequence.
    next_number = max(numbers, default=0) + 1
    return f"{object_type.lower()} {next_number}"


def open_object_form(item: dict | None = None, index: int | None = None) -> None:
    """Initialise the editor for a new or saved object."""
    is_new = item is None
    item = item or {}
    object_type = item.get("type", "Cuboid")
    st.session_state.object_form_visible = True
    st.session_state.object_form_nonce = st.session_state.get("object_form_nonce", 0) + 1
    st.session_state.editing_object_index = index
    st.session_state.editing_object_id = None if is_new else item["id"]
    st.session_state.object_type = object_type
    st.session_state.new_object_type_last = object_type
    st.session_state.object_name = (
        next_object_name(object_type) if is_new else item["name"]
    )
    st.session_state.form_cuboid_x = float(item.get("length_x_m", 20.0))
    st.session_state.form_cuboid_y = float(item.get("width_y_m", 10.0))
    st.session_state.form_cuboid_z = float(item.get("height_z_m", 8.0))
    site_latitude, site_longitude = committed_site_coordinates()
    st.session_state.form_object_latitude = float(item.get("latitude", site_latitude))
    st.session_state.form_object_longitude = float(item.get("longitude", site_longitude))
    st.session_state.form_object_azimuth = float(item.get("azimuth_deg", 90.0))
    st.session_state.form_mast_radius = float(item.get("mast_radius_m", 3.0))
    st.session_state.form_mast_height = float(item.get("mast_height_m", 120.0))
    st.session_state.form_blade_length = float(item.get("blade_length_m", 70.0))


def edit_saved_object(object_id: str) -> None:
    """Open the requested object by stable ID rather than list position."""
    for index, item in enumerate(st.session_state.get("objects", [])):
        if item["id"] == object_id:
            open_object_form(item, index)
            return


def delete_saved_object(object_id: str) -> None:
    """Delete exactly one requested object while preserving every other object."""
    st.session_state.objects = [
        item for item in st.session_state.get("objects", [])
        if item["id"] != object_id
    ]
    if st.session_state.get("editing_object_id") == object_id:
        st.session_state.object_form_visible = False
        st.session_state.editing_object_index = None
        st.session_state.editing_object_id = None
    st.session_state.object_completed = bool(st.session_state.objects)
    st.session_state.object_map_revision = (
        st.session_state.get("object_map_revision", 0) + 1
    )
    invalidate_shadow_study()


def copy_saved_object(object_id: str) -> None:
    """Create an independent copy of one saved object."""
    source = next(
        (
            item for item in st.session_state.get("objects", [])
            if item["id"] == object_id
        ),
        None,
    )
    if source is None:
        return

    copied_object = deepcopy(source)
    copied_object["id"] = str(uuid.uuid4())
    copied_object["name"] = next_object_name(source["type"])
    st.session_state.objects.append(copied_object)
    st.session_state.object_completed = all(
        object_distance_is_accepted(item)
        for item in st.session_state.objects
    )
    st.session_state.object_map_revision = (
        st.session_state.get("object_map_revision", 0) + 1
    )
    invalidate_shadow_study()


def invalidate_shadow_study() -> None:
    """Invalidate results whenever site or object geometry changes."""
    st.session_state.shadow_completed = False
    st.session_state.pop("shadow_result", None)
    st.session_state.pop("shadow_result_signature", None)


def reset_project() -> None:
    """Clear the current study and return to a clean main menu."""
    st.session_state.clear()
    st.query_params.clear()


def shadow_study_signature(interval_minutes: int, ghi_threshold: float) -> str:
    """Return a stable signature of every input affecting the shadow result."""
    payload = {
        "site": st.session_state.get("site_calculation_signature"),
        "objects": st.session_state.get("objects", []),
        "interval_minutes": interval_minutes,
        "ghi_threshold": ghi_threshold,
        "result_schema": 2,
    }
    encoded = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:16]


def add_shadow_geometry_to_map(
    map_object, geometry, reference_latitude: float, reference_longitude: float,
    colour: str, fill_colour: str, tooltip: str,
) -> list[tuple[float, float]]:
    """Add a Shapely polygon or multipolygon to a Folium map."""
    bounds = []
    for exterior, holes in geometry_to_latlon_rings(
        geometry, reference_latitude, reference_longitude
    ):
        rings = [exterior, *holes]
        folium.Polygon(
            locations=rings, color=colour, weight=2,
            fill=True, fill_color=fill_colour, fill_opacity=0.34,
            tooltip=tooltip,
        ).add_to(map_object)
        bounds.extend(exterior)
    return bounds


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


def render_wind_turbine_preview(
    mast_radius: float, mast_height: float, blade_length: float
) -> None:
    """Render a north-facing, three-blade turbine centred on its mast."""
    figure = go.Figure()
    theta = np.linspace(0, 2 * np.pi, 36)
    z_levels = np.array([0.0, mast_height])
    theta_grid, z_grid = np.meshgrid(theta, z_levels)
    radius_grid = mast_radius * (1 - 0.38 * z_grid / mast_height)
    figure.add_trace(go.Surface(
        x=radius_grid * np.cos(theta_grid),
        y=radius_grid * np.sin(theta_grid), z=z_grid,
        colorscale=[[0, "#d7e6df"], [1, "#9fc8ba"]],
        showscale=False, hoverinfo="skip", opacity=0.95,
    ))

    hub_radius = max(mast_radius * 0.8, blade_length * 0.025)
    u, v = np.mgrid[0:2*np.pi:22j, 0:np.pi:12j]
    figure.add_trace(go.Surface(
        x=hub_radius * np.cos(u) * np.sin(v),
        y=hub_radius * np.sin(u) * np.sin(v),
        z=mast_height + hub_radius * np.cos(v),
        colorscale=[[0, "#315c70"], [1, "#547f8c"]],
        showscale=False, hoverinfo="skip",
    ))

    root = hub_radius * 0.8
    for angle_deg in (90, 210, 330):
        angle = np.radians(angle_deg)
        radial = np.array([np.cos(angle), 0.0, np.sin(angle)])
        tangent = np.array([-np.sin(angle), 0.0, np.cos(angle)])
        root_point = np.array([0.0, 0.0, mast_height]) + radial * root
        tip_point = np.array([0.0, 0.0, mast_height]) + radial * blade_length
        width = max(blade_length * 0.055, mast_radius * 0.5)
        blade = np.array([
            root_point - tangent * width,
            root_point + tangent * width,
            tip_point + tangent * width * 0.08,
            tip_point - tangent * width * 0.08,
        ])
        figure.add_trace(go.Mesh3d(
            x=blade[:, 0], y=blade[:, 1], z=blade[:, 2],
            i=[0, 0], j=[1, 2], k=[2, 3], color="#e8eee9",
            flatshading=True, hoverinfo="skip", showlegend=False,
        ))

    north_span = max(blade_length, mast_height) * 0.28
    figure.add_trace(go.Scatter3d(
        x=[0, 0], y=[0, north_span], z=[mast_height, mast_height],
        mode="lines+text", line=dict(color="#3a9565", width=7),
        text=[None, "North · rotor direction"], textposition="top center",
        hoverinfo="skip", showlegend=False,
    ))
    figure.add_trace(go.Scatter3d(
        x=[0], y=[0], z=[0], mode="markers+text",
        marker=dict(size=8, color="#c84848"), text=["ORIGIN · MAST CENTRE"],
        textposition="bottom center", hoverinfo="skip", showlegend=False,
    ))
    upper_height = mast_height + blade_length
    figure.add_trace(go.Scatter3d(
        x=[0, blade_length * 0.52, 0], y=[0, 0, 0],
        z=[mast_height * 0.5, mast_height, upper_height], mode="text",
        text=[f"Mast = {mast_height:g} m", f"Blade = {blade_length:g} m",
              f"Upper tip = {upper_height:g} m"],
        textfont=dict(size=13, color="#16324a"), hoverinfo="skip",
        showlegend=False,
    ))
    horizontal_extent = blade_length * 1.12
    figure.update_layout(
        height=650, margin=dict(l=0, r=0, t=15, b=0),
        paper_bgcolor="rgba(255,255,255,0.76)", showlegend=False,
        scene=dict(
            xaxis_title="X · East (m)", yaxis_title="Y · North (m)",
            zaxis_title="Height (m)", aspectmode="data",
            camera=dict(eye=dict(x=1.55, y=1.7, z=1.05)),
            xaxis=dict(range=[-horizontal_extent, horizontal_extent]),
            yaxis=dict(range=[-blade_length * 0.38, blade_length * 0.45]),
            zaxis=dict(range=[0, upper_height * 1.08]),
        ),
    )
    st.plotly_chart(figure, width="stretch", config={"displaylogo": False})


def render_completion_notice() -> None:
    """Show a one-time completion toast and play a short confirmation tone."""
    completed_page = st.session_state.pop("completion_notice", None)
    export_ready = st.session_state.pop("export_ready_notice", False)
    if not completed_page and not export_ready:
        return
    if completed_page:
        st.toast(f"{completed_page} has been completed", icon="✅")
    if export_ready:
        st.toast("Results may now be exported", icon="📦")
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
        [class*="st-key-nav_site_"] button { background: #cfe8dc !important; }
        [class*="st-key-nav_objects_"] button { background: #d9e5f2 !important; }
        [class*="st-key-nav_shadow_"] button { background: #f7dfb9 !important; }
        [class*="st-key-nav_export_"] button { background: #eadcf0 !important; }
        [class*="st-key-nav_export_"] button:disabled {
            color: #9ba1a7 !important;
            background: #e5e7e9 !important;
            border-color: #d5d8da !important;
            opacity: .68;
        }
        [class*="st-key-nav_"][class*="_home"] button {
            min-height: 7rem;
            font-size: 1.18rem;
            font-weight: 700;
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
    if not st.session_state.get("site_completed", False):
        st.session_state.latitude = pending_latitude
        st.session_state.longitude = pending_longitude
if (
    st.session_state.get("site_completed", False)
    and not st.session_state.get("site_location_editing", False)
    and "site_latitude" in st.session_state
):
    # Restore the committed coordinates before any page widgets or calculations run.
    st.session_state.latitude = float(st.session_state.site_latitude)
    st.session_state.longitude = float(st.session_state.site_longitude)


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

    new_project_col, spacer_col = st.columns([1, 3])
    with new_project_col:
        if st.button("New project", type="secondary", width="stretch"):
            st.session_state.confirm_new_project = True
    if st.session_state.get("confirm_new_project", False):
        st.warning(
            "Start a new project? This will clear the current site, objects, "
            "shadow results and completion status."
        )
        confirm_col, cancel_col, _ = st.columns([1, 1, 2])
        if confirm_col.button(
            "Clear and start", type="primary", width="stretch",
            on_click=reset_project,
        ):
            st.rerun()
        if cancel_col.button("Cancel", width="stretch"):
            st.session_state.confirm_new_project = False
            st.rerun()

    with st.expander("Instructions"):
        st.caption("Instructions will be added as the workflow is developed.")

elif active_page == "Site Creation":
    render_navigation()
    st.title("Site Creation")
    st.caption("Representative annual clear-sky study for the selected location")

    site_locked = (
        st.session_state.get("site_completed", False)
        and not st.session_state.get("site_location_editing", False)
    )
    with st.sidebar:
        st.header("Study location")
        latitude = st.number_input(
            "Latitude (°)", min_value=-90.0, max_value=90.0,
            step=0.00001, format="%.5f", key="latitude", disabled=site_locked,
        )
        longitude = st.number_input(
            "Longitude (°)", min_value=-180.0, max_value=180.0,
            step=0.00001, format="%.5f", key="longitude", disabled=site_locked,
        )
        calculate = st.button(
            "Calculate annual data", type="primary", width="stretch",
            disabled=site_locked,
        )
        if site_locked:
            st.success("Site coordinates locked for this study.")
            st.button(
                "Change site location", width="stretch",
                on_click=begin_site_location_edit,
            )

    site_signature = (
        round(float(latitude), 5),
        round(float(longitude), 5),
    )
    previous_signature = st.session_state.get("site_calculation_signature")
    if previous_signature is not None and previous_signature != site_signature:
        st.session_state.site_completed = False
        st.session_state.site_calculation_signature = None
        st.session_state.pop("site_data", None)
        invalidate_shadow_study()
        st.rerun()

    data = st.session_state.get("site_data")
    if calculate:
        with st.spinner("Calculating solar position and clear-sky irradiance…"):
            data = generate_annual_solar_data(
                latitude=latitude, longitude=longitude,
                year=REFERENCE_YEAR, interval_minutes=SITE_INTERVAL_MINUTES,
                ghi_threshold=0.0,
            )
        st.session_state.site_data = data
        st.session_state.site_completed = True
        st.session_state.site_calculation_signature = site_signature
        st.session_state.site_latitude = float(latitude)
        st.session_state.site_longitude = float(longitude)
        st.session_state.site_location_editing = False
        st.session_state.completion_notice = "Site Creation"
        st.rerun()

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
            tooltip=(
                "Site location locked" if site_locked
                else "Drag to fine-tune the study location"
            ),
            icon=folium.Icon(color="green", icon="crosshairs", prefix="fa"),
            draggable=not site_locked,
        )
        location_marker.add_child(SyncDraggedMarker())
        location_marker.add_to(location_map)
        Fullscreen(position="topright").add_to(location_map)
        map_state = st_folium(
            location_map, key="study_location_map",
            height=500, use_container_width=True
        )
        clicked = map_state.get("last_clicked") if map_state else None
        if clicked and not site_locked:
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
            st.metric("Annual clear-sky GHI", f"{annual_ghi:,.1f} kWh/m²")
            st.metric("Maximum clear-sky GHI", f"{data['ghi'].max():,.1f} W/m²")

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

    unaccepted_distant_objects = [
        item for item in st.session_state.objects
        if not object_distance_is_accepted(item)
    ]
    if unaccepted_distant_objects:
        st.session_state.object_completed = False
        st.error(
            "One or more objects are over 50 km from the study site. "
            "Accept each distance before continuing to Shadow Study."
        )
        for item in unaccepted_distant_objects:
            distance_km = distance_from_site_km(item["latitude"], item["longitude"])
            st.button(
                f"Accept {item['name']} at {distance_km:,.1f} km",
                key=f"accept_saved_distance_{item['id']}",
                on_click=accept_saved_object_distance, args=(item["id"],),
            )

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
        form_nonce = st.session_state.get("object_form_nonce", 0)
        object_type = st.selectbox(
            "Object type", ["Cuboid", "Wind turbine"], key="object_type",
            disabled=editing_index is not None,
        )
        if (
            editing_index is None
            and object_type != st.session_state.get("new_object_type_last")
        ):
            st.session_state.new_object_type_last = object_type
            st.session_state.object_name = next_object_name(object_type)
        st.text_input("Object name", key="object_name")

        if object_type == "Wind turbine":
            controls, preview = st.columns([0.82, 1.65], gap="large")
            with controls:
                st.markdown("##### Turbine dimensions")
                mast_radius = st.number_input(
                    "Mast radius (m)", min_value=0.05, step=0.1,
                    value=st.session_state.form_mast_radius,
                    key=f"turbine_mast_radius_{form_nonce}",
                )
                mast_height = st.number_input(
                    "Mast height / hub height (m)", min_value=0.1,
                    step=1.0, value=st.session_state.form_mast_height,
                    key=f"turbine_mast_height_{form_nonce}",
                )
                blade_length = st.number_input(
                    "Blade length (m)", min_value=0.1, step=1.0,
                    value=st.session_state.form_blade_length,
                    key=f"turbine_blade_length_{form_nonce}",
                )
                upper_tip_height = mast_height + blade_length
                st.metric("Calculated upper blade-tip height", f"{upper_tip_height:g} m")
                st.markdown("##### Mast-centre position")
                object_latitude = st.number_input(
                    "Origin latitude (°)", min_value=-90.0, max_value=90.0,
                    step=0.00001, format="%.5f",
                    value=st.session_state.form_object_latitude,
                    key=f"turbine_latitude_{form_nonce}",
                )
                object_longitude = st.number_input(
                    "Origin longitude (°)", min_value=-180.0, max_value=180.0,
                    step=0.00001, format="%.5f",
                    value=st.session_state.form_object_longitude,
                    key=f"turbine_longitude_{form_nonce}",
                )
                st.caption(
                    "The coordinates define the centre of the mast at ground level. "
                    "The three-blade rotor always faces North; no azimuth is required."
                )
                save_col, cancel_col = st.columns(2)
                save_clicked = save_col.button(
                    "Save object", type="primary", width="stretch"
                )
                cancel_clicked = cancel_col.button("Cancel", width="stretch")

            with preview:
                st.subheader("Live 3D preview")
                render_wind_turbine_preview(mast_radius, mast_height, blade_length)

            if cancel_clicked:
                st.session_state.object_form_visible = False
                st.session_state.editing_object_index = None
                st.session_state.editing_object_id = None
                st.rerun()
            save_authorized, distance_acknowledgement = require_distance_acceptance(
                save_clicked, object_latitude, object_longitude, form_nonce
            )
            if save_authorized:
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
                        "id": st.session_state.get("editing_object_id") or str(uuid.uuid4()),
                        "name": clean_name,
                        "type": "Wind turbine",
                        "mast_radius_m": float(mast_radius),
                        "mast_height_m": float(mast_height),
                        "blade_length_m": float(blade_length),
                        "upper_tip_height_m": float(upper_tip_height),
                        "latitude": float(object_latitude),
                        "longitude": float(object_longitude),
                        "distance_acknowledgement": distance_acknowledgement,
                    }
                    was_complete = bool(st.session_state.objects)
                    editing_object_id = st.session_state.get("editing_object_id")
                    if editing_object_id is None:
                        st.session_state.objects.append(saved_object)
                    else:
                        st.session_state.objects = [
                            saved_object if item["id"] == editing_object_id else item
                            for item in st.session_state.objects
                        ]
                    st.session_state.object_map_revision += 1
                    invalidate_shadow_study()
                    st.session_state.object_completed = True
                    st.session_state.object_form_visible = False
                    st.session_state.editing_object_index = None
                    st.session_state.editing_object_id = None
                    if not was_complete:
                        st.session_state.completion_notice = "Object Generation"
                    st.rerun()
        else:
            controls, preview = st.columns([0.82, 1.65], gap="large")
            with controls:
                st.markdown("##### Dimensions")
                cuboid_x = st.number_input(
                    "X dimension (m)", min_value=0.01,
                    step=0.5, value=st.session_state.form_cuboid_x,
                    key=f"cuboid_x_{form_nonce}",
                )
                cuboid_y = st.number_input(
                    "Y dimension (m)", min_value=0.01,
                    step=0.5, value=st.session_state.form_cuboid_y,
                    key=f"cuboid_y_{form_nonce}",
                )
                cuboid_z = st.number_input(
                    "Z height (m)", min_value=0.01,
                    step=0.5, value=st.session_state.form_cuboid_z,
                    key=f"cuboid_z_{form_nonce}",
                )
                st.markdown("##### Origin position")
                object_latitude = st.number_input(
                    "Origin latitude (°)", min_value=-90.0, max_value=90.0,
                    step=0.00001, format="%.5f",
                    value=st.session_state.form_object_latitude,
                    key=f"cuboid_latitude_{form_nonce}",
                )
                object_longitude = st.number_input(
                    "Origin longitude (°)", min_value=-180.0, max_value=180.0,
                    step=0.00001, format="%.5f",
                    value=st.session_state.form_object_longitude,
                    key=f"cuboid_longitude_{form_nonce}",
                )
                azimuth = st.number_input(
                    "Local X-axis azimuth (°)", min_value=0.0,
                    max_value=359.99, step=1.0,
                    value=st.session_state.form_object_azimuth,
                    key=f"cuboid_azimuth_{form_nonce}",
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
                st.session_state.editing_object_index = None
                st.session_state.editing_object_id = None
                st.rerun()
            save_authorized, distance_acknowledgement = require_distance_acceptance(
                save_clicked, object_latitude, object_longitude, form_nonce
            )
            if save_authorized:
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
                        "id": st.session_state.get("editing_object_id") or str(uuid.uuid4()),
                        "name": clean_name,
                        "type": "Cuboid",
                        "length_x_m": float(cuboid_x),
                        "width_y_m": float(cuboid_y),
                        "height_z_m": float(cuboid_z),
                        "latitude": float(object_latitude),
                        "longitude": float(object_longitude),
                        "azimuth_deg": float(azimuth),
                        "distance_acknowledgement": distance_acknowledgement,
                    }
                    was_complete = bool(st.session_state.objects)
                    editing_object_id = st.session_state.get("editing_object_id")
                    if editing_object_id is None:
                        st.session_state.objects.append(saved_object)
                    else:
                        st.session_state.objects = [
                            saved_object if item["id"] == editing_object_id else item
                            for item in st.session_state.objects
                        ]
                    st.session_state.object_map_revision += 1
                    invalidate_shadow_study()
                    st.session_state.object_completed = True
                    st.session_state.object_form_visible = False
                    st.session_state.editing_object_index = None
                    st.session_state.editing_object_id = None
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
                    if item["type"] == "Wind turbine":
                        st.caption(
                            f"Mast radius {item['mast_radius_m']:g} m · "
                            f"Hub {item['mast_height_m']:g} m · "
                            f"Blade {item['blade_length_m']:g} m · "
                            f"Upper tip {item['upper_tip_height_m']:g} m"
                        )
                    else:
                        st.caption(
                            f"{item['length_x_m']:g} × {item['width_y_m']:g} × "
                            f"{item['height_z_m']:g} m · Azimuth {item['azimuth_deg']:g}°"
                        )
                    st.caption(
                        f"Origin: {item['latitude']:.5f}, {item['longitude']:.5f}"
                    )
                    edit_col, copy_col, delete_col = st.columns(3)
                    edit_col.button(
                        "Edit", key=f"edit_{item['id']}", width="stretch",
                        on_click=edit_saved_object, args=(item["id"],),
                    )
                    copy_col.button(
                        "Copy", key=f"copy_{item['id']}", width="stretch",
                        on_click=copy_saved_object, args=(item["id"],),
                    )
                    delete_col.button(
                        "Delete", key=f"delete_{item['id']}", width="stretch",
                        on_click=delete_saved_object, args=(item["id"],),
                    )

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
                safe_name = escape(item["name"])
                if item["type"] == "Wind turbine":
                    centre = (item["latitude"], item["longitude"])
                    all_footprint_points.extend(circle_bounds_latlon(
                        item["latitude"], item["longitude"], item["blade_length_m"]
                    ))
                    rotor_circle = folium.Circle(
                        location=centre, radius=item["blade_length_m"],
                        color="#547f8c", weight=2, dash_array="7 6",
                        fill=True, fill_color="#dbe8e2", fill_opacity=0.18,
                        tooltip=safe_name,
                    )
                    mast_circle = folium.Circle(
                        location=centre, radius=item["mast_radius_m"],
                        color="#1f6f55", weight=3,
                        fill=True, fill_color="#9fc8ba", fill_opacity=0.72,
                        tooltip=safe_name,
                    )
                    rotor_circle.add_to(object_map)
                    mast_circle.add_to(object_map)
                    move_handle = folium.Marker(
                        centre, draggable=True, tooltip=safe_name,
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
                    move_handle.add_to(object_map)
                    object_map.add_child(LiveTurbineMoveHandle(
                        move_handle, mast_circle, rotor_circle, item["id"]
                    ))
                    continue

                footprint = cuboid_footprint_latlon(
                    item["latitude"], item["longitude"],
                    item["length_x_m"], item["width_y_m"],
                    item["azimuth_deg"],
                )
                all_footprint_points.extend(footprint)
                centre = footprint_center(footprint)
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
                        if selected["type"] == "Wind turbine":
                            selected["latitude"] = handle_position["lat"]
                            selected["longitude"] = handle_position["lng"]
                        else:
                            old_footprint = cuboid_footprint_latlon(
                                selected["latitude"], selected["longitude"],
                                selected["length_x_m"], selected["width_y_m"],
                                selected["azimuth_deg"],
                            )
                            old_centre = footprint_center(old_footprint)
                            selected["latitude"] += handle_position["lat"] - old_centre[0]
                            selected["longitude"] += handle_position["lng"] - old_centre[1]
                        selected["distance_acknowledgement"] = None
                        st.session_state.object_completed = object_distance_is_accepted(selected)
                        st.session_state.object_map_revision += 1
                        invalidate_shadow_study()
                        st.rerun()
                    if (
                        selected is not None
                        and selected["type"] == "Cuboid"
                        and action_label == "ROTATE"
                    ):
                        displacement = abs(handle_position["lat"] - selected["latitude"]) + abs(
                            handle_position["lng"] - selected["longitude"]
                        )
                        if displacement > 1e-8:
                            selected["azimuth_deg"] = bearing_from_coordinates(
                                selected["latitude"], selected["longitude"],
                                handle_position["lat"], handle_position["lng"],
                            )
                            st.session_state.object_map_revision += 1
                            invalidate_shadow_study()
                            st.rerun()

elif active_page == "Shadow Study":
    render_navigation()
    st.title("Shadow Study")
    st.caption(
        "Annual clear-sky shadow envelope for the objects defined in this project"
    )

    objects = st.session_state.get("objects", [])
    site_ready = st.session_state.get("site_completed", False)
    study_latitude, study_longitude = committed_site_coordinates()
    unaccepted_distant_objects = [
        item for item in objects if not object_distance_is_accepted(item)
    ]
    object_ready = bool(objects) and not unaccepted_distant_objects
    if not site_ready:
        st.warning("Complete and calculate Site Creation before running the shadow study.")
    if not object_ready:
        if not objects:
            st.warning("Create at least one object before running the shadow study.")
        else:
            names = ", ".join(item["name"] for item in unaccepted_distant_objects)
            st.error(
                f"Return to Object Generation and accept the over-50-km warning for: {names}."
            )

    control_col, explanation_col = st.columns([0.72, 2.1], gap="large")
    with control_col:
        interval_minutes = SHADOW_INTERVAL_MINUTES
        st.markdown("**Shadow time step: 1 minute**")
        ghi_threshold = st.number_input(
            "Clear-sky GHI threshold (W/m²)", min_value=0.0,
            max_value=1400.0, value=100.0, step=10.0,
            key="shadow_ghi_threshold",
        )
        st.caption(
            "Shadow solar position, clear-sky GHI, affected hours and boundary geometry "
            "are all evaluated from an independent one-minute pvlib series."
        )
        calculate_shadow = st.button(
            "Calculate shadow area", type="primary", width="stretch",
            disabled=not (site_ready and object_ready),
        )
    with explanation_col:
        st.markdown(
            "**Area interpretation**  \n"
            "The main shadow layer contains cuboids and turbine masts. The separate "
            "flicker-risk layer treats each turbine rotor as a continuously swept disc. "
            "It indicates potential blade flicker and is not a full exclusion area.  \n\n"
            "Every boundary point defining each 3D object is projected for every valid "
            "sun position. The map displays only the resulting external envelope, not "
            "the individual timestamp polygons or projected points."
        )

    current_signature = shadow_study_signature(interval_minutes, float(ghi_threshold))
    if st.session_state.get("shadow_result_signature") != current_signature:
        st.session_state.pop("shadow_result", None)
        st.session_state.shadow_completed = False

    if calculate_shadow:
        with st.spinner(
            f"Calculating the annual envelope at {interval_minutes}-minute resolution…"
        ):
            solar_data = generate_annual_solar_data(
                latitude=study_latitude,
                longitude=study_longitude,
                year=REFERENCE_YEAR,
                interval_minutes=int(interval_minutes),
                ghi_threshold=float(ghi_threshold),
            )
            solid_shadow, flicker_risk, relevant_steps, individual_results = annual_shadow_envelopes(
                objects,
                solar_data,
                study_latitude,
                study_longitude,
                boundary_solar_data=solar_data,
                return_individual=True,
            )
            solid_display = soften_envelope_boundary(
                solid_shadow, int(interval_minutes), flicker=False
            )
            flicker_display = soften_envelope_boundary(
                flicker_risk, int(interval_minutes), flicker=True
            )
            production_mask = (
                (solar_data["ghi"] > 0.0)
                & (solar_data["apparent_elevation"] > 0.0)
            )
            affected_mask = production_mask & (solar_data["ghi"] < float(ghi_threshold))
            production_hours = float(production_mask.sum()) * interval_minutes / 60
            affected_hours = float(affected_mask.sum()) * interval_minutes / 60
            affected_share = (
                affected_hours / production_hours * 100.0
                if production_hours > 0 else 0.0
            )
        st.session_state.shadow_result = {
            "solid": solid_shadow,
            "flicker": flicker_risk,
            "individual": individual_results,
            "solid_display": solid_display,
            "flicker_display": flicker_display,
            "relevant_steps": relevant_steps,
            "interval_minutes": int(interval_minutes),
            "ghi_threshold": float(ghi_threshold),
            "affected_hours": affected_hours,
            "affected_share": affected_share,
        }
        st.session_state.shadow_result_signature = current_signature
        was_complete = st.session_state.get("shadow_completed", False)
        st.session_state.shadow_completed = True
        if not was_complete:
            st.session_state.completion_notice = "Shadow Study"
            st.session_state.export_ready_notice = True
        st.rerun()

    if objects:
        st.divider()
        st.subheader("Objects and annual area of effect")
        reference_latitude = study_latitude
        reference_longitude = study_longitude
        shadow_map = folium.Map(
            location=[reference_latitude, reference_longitude],
            zoom_start=17, tiles=None, control_scale=True,
        )
        folium.TileLayer(
            tiles="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
            attr="Esri, Maxar, Earthstar Geographics, and the GIS User Community",
            name="Satellite", overlay=False,
        ).add_to(shadow_map)
        map_bounds = []
        result = st.session_state.get("shadow_result")
        if result is not None:
            map_bounds.extend(add_shadow_geometry_to_map(
                shadow_map, result.get("solid_display", result["solid"]),
                reference_latitude, reference_longitude,
                "#153c5a", "#315c70", "Annual main shadow area",
            ))
            map_bounds.extend(add_shadow_geometry_to_map(
                shadow_map, result.get("flicker_display", result["flicker"]),
                reference_latitude, reference_longitude,
                "#c88732", "#edc77c", "Potential turbine flicker-risk area",
            ))

        for item in objects:
            safe_name = escape(item["name"])
            if item["type"] == "Wind turbine":
                centre = (item["latitude"], item["longitude"])
                rotor_bounds = circle_bounds_latlon(
                    item["latitude"], item["longitude"], item["blade_length_m"]
                )
                # With the rotor facing North, its true top-down projection is
                # an East-West diameter centred on the mast.
                folium.PolyLine(
                    locations=[rotor_bounds[2], rotor_bounds[3]],
                    color="#76558f", weight=7, opacity=0.72,
                    tooltip=f"{safe_name} · horizontal rotor projection",
                ).add_to(shadow_map)
                folium.Circle(
                    centre, radius=item["mast_radius_m"], color="#5f3d78",
                    weight=3, fill=True, fill_color="#c9b5d8", fill_opacity=0.9,
                    tooltip=safe_name,
                ).add_to(shadow_map)
                map_bounds.extend(rotor_bounds)
            else:
                footprint = cuboid_footprint_latlon(
                    item["latitude"], item["longitude"], item["length_x_m"],
                    item["width_y_m"], item["azimuth_deg"],
                )
                folium.Polygon(
                    footprint, color="#1f6f55", weight=3, fill=True,
                    fill_color="#9fc8ba", fill_opacity=0.72, tooltip=safe_name,
                ).add_to(shadow_map)
                map_bounds.extend(footprint)
        if map_bounds:
            shadow_map.fit_bounds(map_bounds, padding=(30, 30))
        Fullscreen(position="topright").add_to(shadow_map)
        st_folium(
            shadow_map,
            key=f"shadow_result_map_{st.session_state.get('shadow_result_signature', 'preview')}",
            height=650, use_container_width=True, returned_objects=[],
        )

        if result is not None:
            metric_one, metric_two, metric_three, metric_four = st.columns(4)
            metric_one.metric("Main shadow envelope", f"{result['solid'].area / 10_000:,.2f} ha")
            metric_two.metric("Additional flicker-risk area", f"{result['flicker'].area / 10_000:,.2f} ha")
            metric_three.metric(
                "Affected production hours", f"{result['affected_hours']:,.1f} h",
                help="Production hours with clear-sky GHI below the selected threshold.",
            )
            metric_four.metric(
                "Affected production hours (%)", f"{result['affected_share']:.1f}%",
                help="Affected hours divided by all positive-GHI production hours.",
            )
            st.caption(
                f"Calculated using clear-sky GHI ≥ {result['ghi_threshold']:g} W/m² "
                f"at {result['interval_minutes']}-minute intervals "
                f"({result['relevant_steps']:,} relevant time steps). Flat terrain assumed."
            )
            st.caption(
                "The annual edge connects equivalent first, highest-sun and last "
                "shadow limits calculated at one-minute resolution between consecutive "
                "solar days. Evening and following-morning limits are never connected."
            )

elif active_page == "Export Results":
    render_navigation()
    st.title("Export Results")
    st.caption(
        "Export every object and its annual area of effect separately, together "
        "with overlap-safe combined project boundaries."
    )
    result = st.session_state.get("shadow_result")
    objects = st.session_state.get("objects", [])
    if result is None or not result.get("individual"):
        st.warning(
            "Run Shadow Study again to prepare the individual geometries required for export."
        )
    else:
        reference_latitude, reference_longitude = committed_site_coordinates()
        features = export_features(
            objects, result, reference_latitude, reference_longitude
        )
        individual_main = sum(
            1 for name, _, _ in features
            if not name.startswith("Envelope_") and "_Main_shadow_" in name
        )
        individual_flicker = sum(
            1 for name, _, _ in features
            if not name.startswith("Envelope_") and "_Flicker_risk_" in name
        )
        st.success(
            f"Ready: {len(objects)} object footprints, {individual_main} individual "
            f"main-shadow areas and {individual_flicker} individual flicker-risk areas."
        )

        kmz_data = create_kmz(features, reference_latitude, reference_longitude)
        dxf_data, projected_crs = create_dxf(
            features, reference_latitude, reference_longitude
        )
        kmz_col, cad_col = st.columns(2, gap="large")
        with kmz_col:
            st.subheader("KMZ polygons")
            st.write(
                "WGS84 polygons grouped by feature name for Google Earth and GIS software."
            )
            st.download_button(
                "Download KMZ",
                data=kmz_data,
                file_name="pv_butterfly_results.kmz",
                mime="application/vnd.google-earth.kmz",
                type="primary",
                width="stretch",
            )
        with cad_col:
            st.subheader("CAD polylines")
            st.write(
                f"Closed, metre-based polylines on separate layers in {projected_crs}."
            )
            st.download_button(
                "Download DXF",
                data=dxf_data,
                file_name="pv_butterfly_results.dxf",
                mime="application/dxf",
                type="primary",
                width="stretch",
            )
            st.caption(
                "DXF opens directly in AutoCAD and can be saved as DWG. Native DWG "
                "generation requires a licensed external conversion service, which is "
                "not included in Streamlit Community Cloud."
            )

        st.markdown("**Included geometry**")
        st.markdown(
            "- One footprint for each saved object\n"
            "- One main-shadow boundary for each object\n"
            "- One additional flicker-risk boundary for each wind turbine\n"
            "- Combined main shadow, combined flicker risk, and combined full area of effect\n\n"
            "Overlapping polygons are unioned in every combined layer, so shared area is not duplicated."
        )
