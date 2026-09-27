"""Unit tests for actions/earth_intel.py — the keyless Earth-intelligence tool.

SAFETY: no test touches the network and none touches the user's real config.
Every request is served by a fake, and the home-location writes are patched out
of `earth_intel` itself — a test suite that can overwrite the file holding the
user's API keys is a test suite that gets deleted.

The interesting assertions here are the honest ones: that a feed which is empty,
unreachable or malformed produces a sentence, and that no coordinate is ever
invented. The orbital arithmetic is checked against the real ISS elements rather
than against its own output, because a test that only proves the code agrees
with itself proves nothing.
"""
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from actions import earth_intel as ei


def _resp(payload=None, status=200, text=None, raises_json=False):
    """A fake requests.Response good enough for both the JSON and text paths."""
    class FakeResp:
        status_code = status

        def json(self):
            if raises_json:
                raise ValueError("not json")
            return payload

        @property
        def text(self):
            if text is not None:
                return text
            return ""

    return FakeResp()


# A real ISS element set (CelesTrak, 2026 day 269). Real values, so the derived
# orbit can be checked against the published orbit instead of against itself.
ISS_NAME = "ISS (ZARYA)"
ISS_L1 = "1 25544U 98067A   26269.85154941  .00007609  00000+0  14781-3 0  9997"
ISS_L2 = "2 25544  51.6312 156.9417 0007058 191.1476 168.9357 15.48656208587512"
ISS_TLE = f"{ISS_NAME}\n{ISS_L1}\n{ISS_L2}\n"


class DistanceTest(unittest.TestCase):
    def test_known_distances(self):
        # Delhi → Mumbai is ~1150 km; London → Paris ~344 km.
        self.assertAlmostEqual(ei.haversine_km(28.6139, 77.2090, 19.0760, 72.8777),
                               1153, delta=15)
        self.assertAlmostEqual(ei.haversine_km(51.5074, -0.1278, 48.8566, 2.3522),
                               344, delta=6)

    def test_same_point_is_zero(self):
        self.assertEqual(ei.haversine_km(10.0, 20.0, 10.0, 20.0), 0.0)

    def test_antipodal_is_half_the_circumference(self):
        self.assertAlmostEqual(ei.haversine_km(0.0, 0.0, 0.0, 180.0),
                               20015, delta=20)


class LatLonParseTest(unittest.TestCase):
    def test_accepts_comma_and_space_forms(self):
        self.assertEqual(ei.parse_latlon("28.61, 77.21"), (28.61, 77.21))
        self.assertEqual(ei.parse_latlon("28.61 77.21"), (28.61, 77.21))
        self.assertEqual(ei.parse_latlon("-33.87, 151.21"), (-33.87, 151.21))

    def test_rejects_out_of_range_rather_than_guessing(self):
        # 91 is not a latitude: better no answer than a point in the ocean.
        self.assertIsNone(ei.parse_latlon("91.0, 10.0"))
        self.assertIsNone(ei.parse_latlon("10.0, 181.0"))

    def test_rejects_garbage_and_partial_pairs(self):
        self.assertIsNone(ei.parse_latlon("Delhi"))
        self.assertIsNone(ei.parse_latlon("28.61"))
        self.assertIsNone(ei.parse_latlon(""))


class HttpTest(unittest.TestCase):
    def test_http_error_becomes_a_sentence(self):
        with mock.patch.object(ei.requests, "get", return_value=_resp(status=503)):
            data, err = ei._get_json("https://example.invalid/x")
        self.assertIsNone(data)
        self.assertIn("503", err)

    def test_network_failure_becomes_a_sentence_not_an_exception(self):
        import requests as _rq
        with mock.patch.object(ei.requests, "get",
                               side_effect=_rq.ConnectionError("refused")):
            data, err = ei._get_json("https://example.invalid/x")
        self.assertIsNone(data)
        self.assertIn("connection", err.lower())

    def test_unreadable_body_is_reported(self):
        with mock.patch.object(ei.requests, "get",
                               return_value=_resp(raises_json=True)):
            data, err = ei._get_json("https://example.invalid/x")
        self.assertIsNone(data)
        self.assertIn("readable", err.lower())

    def test_get_text_returns_the_raw_body(self):
        # CelesTrak answers plain text; running that through .json() is a
        # guaranteed parse failure, which is why this path exists.
        with mock.patch.object(ei.requests, "get",
                               return_value=_resp(status=200, text="1 abc\n2 def\n")):
            text, err = ei._get_text("https://example.invalid/tle")
        self.assertEqual(err, "")
        self.assertIn("1 abc", text)

    def test_network_error_messages_are_specific(self):
        for text, want in (("timed out", "timed out"),
                           ("Name or service not known", "internet"),
                           ("[SSL: CERTIFICATE_VERIFY_FAILED]", "verified")):
            self.assertIn(want, ei._explain_network_error(Exception(text)).lower())


class TleTest(unittest.TestCase):
    def test_parses_a_three_line_element_set(self):
        tles = ei._parse_tles(ISS_TLE)
        self.assertEqual(len(tles), 1)
        self.assertEqual(tles[0]["name"], ISS_NAME)
        self.assertEqual(tles[0]["norad"], "25544")
        self.assertEqual(tles[0]["line1"], ISS_L1)

    def test_tolerates_blank_lines_and_headers(self):
        messy = f"# CelesTrak elements\n\n\n{ISS_NAME}\n{ISS_L1}\n{ISS_L2}\n\n"
        tles = ei._parse_tles(messy)
        self.assertEqual(len(tles), 1)
        self.assertEqual(tles[0]["name"], ISS_NAME)

    def test_rubbish_input_yields_no_objects_rather_than_raising(self):
        for junk in ("", "not a tle at all", "<html>error</html>"):
            self.assertEqual(ei._parse_tles(junk), [])

    def test_iss_orbit_matches_the_real_iss(self):
        facts = ei._orbit_facts(ISS_L1, ISS_L2)
        # Published ISS figures: ~92-93 min period, ~410-430 km, 51.6 deg.
        self.assertAlmostEqual(facts["period_min"], 92.98, delta=0.5)
        self.assertTrue(410 <= facts["alt_km"] <= 435, facts["alt_km"])
        self.assertAlmostEqual(facts["inclination"], 51.63, delta=0.2)
        self.assertLessEqual(facts["ecc"], 0.01)

    def test_bad_elements_return_none(self):
        self.assertIsNone(ei._orbit_facts("", ""))
        self.assertIsNone(ei._orbit_facts("x" * 70, "y" * 70))


class ResolvePlaceTest(unittest.TestCase):
    def test_explicit_coordinates_win(self):
        lat, lon, label, err = ei._resolve_place({"lat": 10.0, "lon": 20.0})
        self.assertEqual((lat, lon), (10.0, 20.0))
        self.assertEqual(err, "")

    def test_coordinates_given_inside_place(self):
        lat, lon, _label, err = ei._resolve_place({"place": "10.0, 20.0"})
        self.assertEqual((lat, lon), (10.0, 20.0))
        self.assertEqual(err, "")

    def test_out_of_range_coordinates_are_refused(self):
        _lat, _lon, _label, err = ei._resolve_place({"lat": 500.0, "lon": 20.0})
        self.assertIn("out of range", err)

    def test_a_named_place_is_geocoded(self):
        payload = {"features": [{"geometry": {"coordinates": [77.2, 28.6]},
                                 "properties": {"name": "Delhi", "country": "India"}}]}
        with mock.patch.object(ei, "_get_json", return_value=(payload, "")):
            lat, lon, label, err = ei._resolve_place({"place": "Delhi"})
        self.assertAlmostEqual(lat, 28.6)
        self.assertAlmostEqual(lon, 77.2)
        self.assertIn("Delhi", label)
        self.assertEqual(err, "")

    def test_an_unknown_place_says_so(self):
        with mock.patch.object(ei, "_get_json", return_value=({"features": []}, "")):
            lat, _lon, _label, err = ei._resolve_place({"place": "Nowhereville"})
        self.assertIsNone(lat)
        self.assertIn("Nowhereville", err)

    def test_falls_back_to_the_remembered_location(self):
        with mock.patch.object(ei, "get_home_location",
                               return_value={"lat": 1.5, "lon": 2.5, "label": "home"}):
            lat, lon, label, err = ei._resolve_place({})
        self.assertEqual((lat, lon), (1.5, 2.5))
        self.assertEqual(label, "home")
        self.assertEqual(err, "")

    def test_no_location_at_all_asks_instead_of_inventing_one(self):
        with mock.patch.object(ei, "get_home_location", return_value={}):
            lat, _lon, _label, err = ei._resolve_place({})
        self.assertIsNone(lat)
        self.assertIn("do not know where you are", err)


class AircraftTest(unittest.TestCase):
    def _payload(self):
        return {"ac": [
            {"flight": "FAR1   ", "t": "B738", "lat": 28.9, "lon": 77.2,
             "alt_baro": 30000, "gs": 400.0, "track": 90.0, "dst": 40.0},
            {"flight": "NEAR1  ", "t": "A320", "lat": 28.64, "lon": 77.21,
             "alt_baro": "ground", "gs": 12.0, "dst": 2.0},
        ]}

    def test_sorted_by_distance_and_speaks_the_numbers(self):
        with mock.patch.object(ei, "_get_json", return_value=(self._payload(), "")):
            out = ei._aircraft({"place": "28.61, 77.21", "radius_km": 100})
        self.assertIn("NEAR1", out)
        self.assertIn("FAR1", out)
        self.assertLess(out.index("NEAR1"), out.index("FAR1"))
        # alt_baro is the literal string "ground" for a taxiing aircraft
        self.assertIn("on the ground", out)
        self.assertIn("30,000 ft", out)
        self.assertIn("heading east", out)

    def test_respects_the_row_limit(self):
        payload = {"ac": [dict(f, flight=f"P{i}") for i, f in enumerate(
            [{"lat": 28.6, "lon": 77.2, "dst": float(i)} for i in range(30)])]}
        with mock.patch.object(ei, "_get_json", return_value=(payload, "")):
            out = ei._aircraft({"place": "28.61, 77.21", "limit": 3})
        self.assertEqual(out.count("•"), 3)

    def test_empty_sky_is_reported_not_padded(self):
        with mock.patch.object(ei, "_get_json", return_value=({"ac": []}, "")):
            out = ei._aircraft({"place": "28.61, 77.21"})
        self.assertIn("No aircraft", out)

    def test_feed_failure_is_a_sentence(self):
        with mock.patch.object(ei, "_get_json", return_value=(None, "the request timed out")):
            out = ei._aircraft({"place": "28.61, 77.21"})
        self.assertIn("timed out", out)

    def test_radius_is_clamped_to_the_provider_ceiling(self):
        seen = {}

        def _capture(url, timeout=ei._TIMEOUT):
            seen["url"] = url
            return {"ac": []}, ""

        with mock.patch.object(ei, "_get_json", side_effect=_capture):
            ei._aircraft({"place": "28.61, 77.21", "radius_km": 99999})
        # last path segment is the radius in nautical miles, capped at 250
        self.assertTrue(seen["url"].endswith("/250"), seen["url"])


class QuakesTest(unittest.TestCase):
    def _feed(self):
        return {"features": [
            {"properties": {"mag": 5.4, "place": "Somewhere", "time": 1_700_000_000_000,
                            "tsunami": 1},
             "geometry": {"coordinates": [77.2, 28.6]}},
            {"properties": {"mag": 3.1, "place": "Far away", "time": 1_700_000_000_000,
                            "tsunami": 0},
             "geometry": {"coordinates": [10.0, 10.0]}},
            {"properties": {"mag": None, "place": "Broken row"},
             "geometry": {"coordinates": [1.0, 1.0]}},
        ]}

    def test_global_answer_ranks_by_magnitude_and_skips_broken_rows(self):
        with mock.patch.object(ei, "_get_json", return_value=(self._feed(), "")):
            out = ei._quakes({"magnitude": "4.5", "period": "day"})
        self.assertIn("magnitude 5.4", out)
        self.assertNotIn("Broken row", out)
        self.assertIn("tsunami warning", out)

    def test_near_me_filters_by_distance(self):
        # The fixture's first event sits ~1.5 km from this point; the second is
        # on the other side of the planet and must not appear.
        with mock.patch.object(ei, "_get_json", return_value=(self._feed(), "")):
            out = ei._quakes({"place": "28.61, 77.21", "radius_km": 500,
                              "magnitude": "2.5"})
        self.assertIn("Somewhere", out)
        self.assertIn("1.5 km away", out)
        self.assertNotIn("Far away", out)

    def test_no_matches_near_me_is_stated_plainly(self):
        # A point far from everything in the feed. The honest answer is "none",
        # never the nearest event on Earth presented as if it were local.
        with mock.patch.object(ei, "_get_json", return_value=(self._feed(), "")):
            out = ei._quakes({"place": "40.0, -100.0", "radius_km": 500,
                              "magnitude": "2.5"})
        self.assertIn("No magnitude", out)
        self.assertIn("within", out)
        self.assertNotIn("magnitude 5.4", out)

    def test_bad_enum_values_fall_back_instead_of_failing(self):
        seen = {}

        def _capture(url, timeout=ei._TIMEOUT):
            seen["url"] = url
            return {"features": []}, ""

        with mock.patch.object(ei, "_get_json", side_effect=_capture):
            ei._quakes({"magnitude": "9.9", "period": "fortnight"})
        self.assertIn("2.5_day", seen["url"])


class EventsTest(unittest.TestCase):
    def _feed(self):
        return {"events": [{
            "title": "Tropical Storm Test",
            "categories": [{"title": "Severe Storms"}],
            "geometry": [{"date": "2026-09-25T03:00:00Z", "coordinates": [-22.1, 13.2]}],
        }]}

    def test_renders_title_category_and_position(self):
        with mock.patch.object(ei, "_get_json", return_value=(self._feed(), "")):
            out = ei._events({})
        self.assertIn("Tropical Storm Test", out)
        self.assertIn("Severe Storms", out)
        self.assertIn("13.2°N", out)
        self.assertIn("22.1°W", out)

    def test_empty_feed_is_reported(self):
        with mock.patch.object(ei, "_get_json", return_value=({"events": []}, "")):
            out = ei._events({})
        self.assertIn("No open natural events", out)

    def test_a_bad_date_reads_as_unknown_not_as_now(self):
        self.assertEqual(ei._iso_to_ms("not-a-date"), 0.0)
        self.assertEqual(ei._ago(0.0), "time unknown")


class SatellitesTest(unittest.TestCase):
    def test_lists_catalogue_objects_with_real_orbits(self):
        with mock.patch.object(ei, "_get_text", return_value=(ISS_TLE, "")):
            out = ei._satellites({"group": "stations"})
        self.assertIn("ISS (ZARYA)", out)
        self.assertIn("km up", out)
        self.assertIn("min orbit", out)
        self.assertIn("inclination", out)

    def test_name_filter(self):
        with mock.patch.object(ei, "_get_text", return_value=(ISS_TLE, "")):
            hit = ei._satellites({"name": "zarya"})
            miss = ei._satellites({"name": "definitely-not-a-satellite"})
        self.assertIn("ISS (ZARYA)", hit)
        self.assertIn("No satellite", miss)

    def test_unknown_group_falls_back_to_stations(self):
        seen = {}

        def _capture(url, timeout=ei._TIMEOUT):
            seen["url"] = url
            return ISS_TLE, ""

        with mock.patch.object(ei, "_get_text", side_effect=_capture):
            ei._satellites({"group": "../../etc/passwd"})
        self.assertIn("GROUP=stations", seen["url"])

    def test_catalogue_failure_is_a_sentence(self):
        with mock.patch.object(ei, "_get_text", return_value=(None, "no internet")):
            out = ei._satellites({})
        self.assertIn("no internet", out)

    def test_missing_sgp4_is_disclosed_not_hidden(self):
        with mock.patch.object(ei, "_get_text", return_value=(ISS_TLE, "")), \
             mock.patch.object(ei, "_sgp4_available", return_value=False):
            out = ei._satellites({})
        self.assertIn("sgp4", out)


class RememberLocationTest(unittest.TestCase):
    def test_locate_returns_the_place_without_writing_config(self):
        payload = {"features": [{"geometry": {"coordinates": [77.2, 28.6]},
                                 "properties": {"name": "Delhi", "country": "India"}}]}
        with mock.patch.object(ei, "_get_json", return_value=(payload, "")), \
             mock.patch.object(ei, "save_home_location") as saved:
            out = ei._locate({"place": "Delhi", "remember": False})
        self.assertIn("Delhi", out)
        saved.assert_not_called()

    def test_locate_remembers_when_asked(self):
        payload = {"features": [{"geometry": {"coordinates": [77.2, 28.6]},
                                 "properties": {"name": "Delhi", "country": "India"}}]}
        with mock.patch.object(ei, "_get_json", return_value=(payload, "")), \
             mock.patch.object(ei, "save_home_location") as saved:
            out = ei._locate({"place": "Delhi"})
        saved.assert_called_once()
        self.assertIn("Saved", out)

    def test_locate_reports_the_saved_position(self):
        with mock.patch.object(ei, "get_home_location",
                               return_value={"lat": 1.0, "lon": 2.0, "label": "home"}):
            out = ei._locate({})
        self.assertIn("home", out)


class DispatchTest(unittest.TestCase):
    def test_aliases_route_to_the_right_action(self):
        for raw, want in (("planes", "aircraft"), ("earthquake", "quakes"),
                          ("fires", "events"), ("orbit", "satellites"),
                          ("where", "locate"), ("QUAKES", "quakes"),
                          ("natural-events", "natural_events")):
            self.assertEqual(ei.resolve_action(raw), want)

    def test_missing_action_asks(self):
        out = ei.earth_intel({})
        self.assertIn("aircraft", out)

    def test_unknown_action_lists_the_valid_ones(self):
        out = ei.earth_intel({"action": "frobnicate"})
        self.assertIn("Unknown", out)
        for name in ("aircraft", "quakes", "events", "satellites", "locate"):
            self.assertIn(name, out)

    def test_a_failing_action_returns_a_speakable_string(self):
        # A tool result must never be a traceback.
        with mock.patch.object(ei, "_quakes", side_effect=RuntimeError("boom")):
            out = ei.earth_intel({"action": "quakes"})
        self.assertIsInstance(out, str)
        self.assertIn("could not complete", out)

    def test_every_action_survives_total_feed_failure(self):
        # No network at all: each action must still answer.
        with mock.patch.object(ei, "_get_json", return_value=(None, "no internet")), \
             mock.patch.object(ei, "_get_text", return_value=(None, "no internet")), \
             mock.patch.object(ei, "get_home_location",
                               return_value={"lat": 1.0, "lon": 2.0, "label": "home"}):
            for action in ("aircraft", "quakes", "events", "satellites"):
                out = ei.earth_intel({"action": action})
                self.assertIsInstance(out, str)
                self.assertGreater(len(out), 10)


class ToolDeclarationTest(unittest.TestCase):
    def test_declaration_shape(self):
        tool = ei.TOOL
        self.assertEqual(tool["name"], "earth_intel")
        self.assertTrue(callable(tool["handler"]))
        self.assertEqual(tool["parameters"]["type"], "OBJECT")
        props = tool["parameters"]["properties"]
        self.assertIn("action", props)
        self.assertEqual(tool["parameters"]["required"], ["action"])
        self.assertEqual(props["action"]["type"], "STRING")
        self.assertGreaterEqual(len(props["action"]["enum"]), 5)

    def test_every_action_has_a_handler_and_an_enum_entry(self):
        props = ei.TOOL["parameters"]["properties"]
        for action in props["action"]["enum"]:
            self.assertIn(action, ei._ACTION_ALIASES.values(),
                          f"{action} has no dispatch alias")

    def test_no_duplicate_keys_in_the_schema(self):
        import ast
        src = Path(ei.__file__).read_text(encoding="utf-8")
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.Dict):
                keys = [k.value for k in node.keys if isinstance(k, ast.Constant)]
                self.assertEqual(len(keys), len(set(keys)),
                                 f"duplicate dict key near line {node.lineno}")


if __name__ == "__main__":
    unittest.main()
