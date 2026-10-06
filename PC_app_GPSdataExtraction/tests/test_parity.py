"""
Byte-for-byte parity with the Android app. The hashes are of the files the Kotlin
Exporters wrote (Asia/Ho_Chi_Minh) for the same fixtures: the replay of a real video's
OCR log, and the cleaned real GPX / KMZ route exports. Any difference in parsing,
cleaning or writing changes a hash.
"""
import hashlib

import pytest

from conftest import RESOURCES, resource
from dashgps.core import cleaner, routefiles
from dashgps.core.exporters import Exporters
from dashgps.core.profiles import VIETMAP_M1_FRONT
from dashgps.core.timezone import Zone
from test_routefiles import load_ocr_log

ANDROID_SHA256 = {
    "replay.csv": "88b482167a1af679590ac97f8eed322adf2d4a7106f9721e8cb626047a68dd2f",
    "replay.gpx": "3e581b1b68ad4c3bdb5af0b551ccd99107006570b99aa9f65cba4832541a32d3",
    "replay.kml": "85a01ba0688d8cbe2e9598a92708737676522728da2ec6c31c1fe95c0c88020e",
    "replay_ge.kml": "ffd6cc0e10749a405e48f1aaa9bd087e2ae4ed8094596df55524b23ad138fec7",
    "replay_ocr_log.csv": "5142da4a28df62b40f6843345b89d8339c139b516c4f6ba9a32ec4d1cf347544",
    "replay_summary.txt": "950f485e99c16a6aaa6717d29bc611fced83023e290e88561f96b8dfabf92e29",
    "route_vietmap_front_route_noisy_gpx.csv": "cb726bac0e4f65f5dac10889ae356221f380657d5bc667e752df8e0e0075ba95",
    "route_vietmap_front_route_noisy_gpx.gpx": "e7357a444a87d2613aaa4f354fcfe0f81e30098107772afaf920782e7da808c9",
    "route_vietmap_front_route_noisy_gpx.kml": "41d3fcd51a2992f60df8b850adc3b5a53a589b93dd36b4ad2283526277cbd7a2",
    "route_vietmap_front_route_noisy_gpx_ge.kml": "7faf693b154790b0b184e4def4772b676b66a778c7b37215a5ea04d8310350d6",
    "route_vietmap_front_route_noisy_gpx_ocr_log.csv": "c0ffb2ac93a1d6d0063d921d7306f8744f76015589704d35e40ddef33856c6fb",
    "route_vietmap_front_route_noisy_gpx_summary.txt": "1164186de7071b758d7990f30539342a2a409ad99c215cab0406de777d89c145",
    "route_vietmap_front_route_noisy_kmz.csv": "96c714056e0c72df16a160c2b3943796714afd66ecab37815c7157f08b31e5ff",
    "route_vietmap_front_route_noisy_kmz.gpx": "59431491d974a71493b355693e3327aa3e5ca3006eb243e970770fc4b380be76",
    "route_vietmap_front_route_noisy_kmz.kml": "145482b36c51dda2686dcf9da506fb93760f6c66ba6fed61141f67b9c9114350",
    "route_vietmap_front_route_noisy_kmz_ge.kml": "145482b36c51dda2686dcf9da506fb93760f6c66ba6fed61141f67b9c9114350",
    "route_vietmap_front_route_noisy_kmz_ocr_log.csv": "f448048886a50010a3efa8a6a69d2e2bde842ba03534f35618ef0cbb999cac2d",
    "route_vietmap_front_route_noisy_kmz_summary.txt": "950f23873f4092a4c651b22c219b1d735e24a73ac37f422f729baee583266a1a",
}


def _outputs():
    zone = Zone("Asia/Ho_Chi_Minh")
    ex = Exporters(zone)
    out = {}

    def dump(name, t, timed=True):
        out[f"{name}.kml"] = ex.kml(t, name, False, timed)
        out[f"{name}_ge.kml"] = ex.kml(t, name, True, timed)
        out[f"{name}.gpx"] = ex.gpx(t, name, timed)
        out[f"{name}.csv"] = ex.csv(t)
        out[f"{name}_ocr_log.csv"] = ex.ocr_log(t)
        out[f"{name}_summary.txt"] = ex.summary(t, name, timed)

    dump("replay", cleaner.clean(load_ocr_log(RESOURCES / "vietmap_m1_front_ocr_log.csv"), VIETMAP_M1_FRONT))
    for n in ("vietmap_front_route_noisy.gpx", "vietmap_front_route_noisy.kmz"):
        c = cleaner.clean_route(routefiles.read(n, resource(n), zone))
        dump("route_" + n.replace(".", "_"), c.track, c.route.has_time)
    return out


@pytest.fixture(scope="module")
def outputs():
    return _outputs()


@pytest.mark.parametrize("name", sorted(ANDROID_SHA256))
def test_output_is_byte_identical_to_android(outputs, name):
    assert hashlib.sha256(outputs[name].encode("utf-8")).hexdigest() == ANDROID_SHA256[name]
