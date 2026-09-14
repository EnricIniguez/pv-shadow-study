"""KMZ and CAD exports for PV Butterfly geometry."""

from __future__ import annotations

from io import BytesIO, StringIO
from math import floor
from zipfile import ZIP_DEFLATED, ZipFile

import ezdxf
from pyproj import CRS, Transformer
from shapely.geometry import MultiPolygon, Polygon
from shapely.ops import transform, unary_union

from shadow_geometry import local_to_latlon


def _polygons(geometry):
    if geometry is None or geometry.is_empty:
        return []
    if isinstance(geometry, Polygon):
        return [geometry]
    if isinstance(geometry, MultiPolygon):
        return list(geometry.geoms)
    return [part for part in geometry.geoms if isinstance(part, Polygon)]


def export_features(objects: list[dict], shadow_result: dict, ref_lat: float, ref_lon: float):
    """Build the two project-wide export envelopes in local EN metres."""
    threshold = float(shadow_result.get("ghi_threshold", 0.0))
    restriction = f"GHI_{threshold:g}_Wm2"

    solid = shadow_result["solid"]
    flicker = shadow_result["flicker"]
    without_flickering = f"Full_effect_without_flickering_{restriction}"
    with_flickering = f"Full_effect_with_flickering_{restriction}"
    features = [
        (without_flickering, without_flickering, solid),
        (with_flickering, with_flickering, unary_union([solid, flicker])),
    ]
    return [(name, layer, geom) for name, layer, geom in features if not geom.is_empty]


def _kml_coordinates(ring, ref_lat: float, ref_lon: float) -> str:
    values = []
    for east, north in ring.coords:
        lat, lon = local_to_latlon(east, north, ref_lat, ref_lon)
        values.append(f"{lon:.9f},{lat:.9f},0")
    return " ".join(values)


def create_kmz(features, ref_lat: float, ref_lon: float) -> bytes:
    """Return a KMZ containing every feature as one or more KML polygons."""
    from xml.etree.ElementTree import Element, SubElement, tostring

    root = Element("kml", xmlns="http://www.opengis.net/kml/2.2")
    document = SubElement(root, "Document")
    SubElement(document, "name").text = "PV Butterfly export"
    for feature_name, layer, geometry in features:
        polygons = _polygons(geometry)
        folder = SubElement(document, "Folder")
        SubElement(folder, "name").text = layer
        SubElement(folder, "open").text = "1"
        for index, polygon in enumerate(polygons, start=1):
            placemark = SubElement(folder, "Placemark")
            suffix = f" ({index})" if len(polygons) > 1 else ""
            SubElement(placemark, "name").text = feature_name + suffix
            SubElement(placemark, "description").text = f"Layer: {layer}"
            polygon_node = SubElement(placemark, "Polygon")
            SubElement(polygon_node, "tessellate").text = "1"
            outer = SubElement(polygon_node, "outerBoundaryIs")
            linear_ring = SubElement(outer, "LinearRing")
            SubElement(linear_ring, "coordinates").text = _kml_coordinates(
                polygon.exterior, ref_lat, ref_lon
            )
            for hole in polygon.interiors:
                inner = SubElement(polygon_node, "innerBoundaryIs")
                inner_ring = SubElement(inner, "LinearRing")
                SubElement(inner_ring, "coordinates").text = _kml_coordinates(
                    hole, ref_lat, ref_lon
                )
    kml = b'<?xml version="1.0" encoding="UTF-8"?>\n' + tostring(root, encoding="utf-8")
    output = BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        archive.writestr("doc.kml", kml)
    return output.getvalue()


def _utm_crs(latitude: float, longitude: float) -> CRS:
    zone = floor((longitude + 180.0) / 6.0) + 1
    epsg = (32600 if latitude >= 0 else 32700) + zone
    return CRS.from_epsg(epsg)


def create_dxf(features, ref_lat: float, ref_lon: float) -> tuple[bytes, str]:
    """Return an AutoCAD-compatible DXF with closed LWPOLYLINE boundaries in UTM."""
    utm = _utm_crs(ref_lat, ref_lon)
    transformer = Transformer.from_crs("EPSG:4326", utm, always_xy=True)

    def local_to_utm(east, north, z=None):
        lat, lon = local_to_latlon(east, north, ref_lat, ref_lon)
        return transformer.transform(lon, lat)

    document = ezdxf.new("R2010")
    document.units = ezdxf.units.M
    document.header["$PROJECTNAME"] = "PV Butterfly"
    modelspace = document.modelspace()
    for layer_index, (_, layer, _) in enumerate(features):
        if layer not in document.layers:
            document.layers.add(layer, color=(layer_index % 7) + 1)
    for feature_name, layer, geometry in features:
        projected = transform(local_to_utm, geometry)
        for polygon in _polygons(projected):
            modelspace.add_lwpolyline(
                list(polygon.exterior.coords)[:-1], close=True,
                dxfattribs={"layer": layer},
            )
            for hole in polygon.interiors:
                modelspace.add_lwpolyline(
                    list(hole.coords)[:-1], close=True,
                    dxfattribs={"layer": layer},
                )
            point = polygon.representative_point()
            modelspace.add_text(
                feature_name,
                height=max(1.0, (polygon.area ** 0.5) / 80.0),
                dxfattribs={"layer": layer},
            ).set_placement((point.x, point.y))
    stream = StringIO()
    document.write(stream)
    return stream.getvalue().encode("utf-8"), utm.to_string()
