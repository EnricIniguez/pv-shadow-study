from io import BytesIO
from zipfile import ZipFile
import xml.etree.ElementTree as ET

import ezdxf
from shapely.geometry import Polygon

from export_geometry import create_dxf, create_kmz, export_features


def _fixtures():
    objects = [{
        "id": "cuboid-1", "name": "cuboid 1", "type": "Cuboid",
        "latitude": 41.3874, "longitude": 2.1686,
        "length_x_m": 20.0, "width_y_m": 10.0,
        "height_z_m": 8.0, "azimuth_deg": 90.0,
    }]
    first = Polygon([(0, 0), (30, 0), (30, 10), (0, 10)])
    second = Polygon([(20, 0), (40, 0), (40, 10), (20, 10)])
    shadow_result = {
        "solid": first.union(second), "flicker": Polygon(),
        "ghi_threshold": 100.0,
        "individual": [{
            "id": "cuboid-1", "name": "cuboid 1", "type": "Cuboid",
            "solid": first, "flicker": Polygon(),
        }],
    }
    return objects, shadow_result


def test_export_features_include_individual_and_combined_polygons():
    objects, result = _fixtures()
    features = export_features(objects, result, 41.3874, 2.1686)
    names = {name for name, _, _ in features}
    assert "cuboid_1_Object_shape_GHI_100_Wm2" in names
    assert "cuboid_1_Main_shadow_GHI_100_Wm2" in names
    assert "Envelope_Main_shadow_GHI_100_Wm2" in names
    assert "Envelope_Full_area_of_effect_GHI_100_Wm2" in names
    assert all(name == layer for name, layer, _ in features)


def test_kmz_contains_valid_polygon_placemarks():
    objects, result = _fixtures()
    features = export_features(objects, result, 41.3874, 2.1686)
    payload = create_kmz(features, 41.3874, 2.1686)
    with ZipFile(BytesIO(payload)) as archive:
        root = ET.fromstring(archive.read("doc.kml"))
    namespace = {"k": "http://www.opengis.net/kml/2.2"}
    assert root.findall(".//k:Polygon", namespace)


def test_dxf_contains_closed_polylines_on_separate_layers(tmp_path):
    objects, result = _fixtures()
    features = export_features(objects, result, 41.3874, 2.1686)
    payload, crs = create_dxf(features, 41.3874, 2.1686)
    path = tmp_path / "export.dxf"
    path.write_bytes(payload)
    document = ezdxf.readfile(path)
    polylines = list(document.modelspace().query("LWPOLYLINE"))
    assert polylines
    assert all(polyline.closed for polyline in polylines)
    assert "32631" in crs
