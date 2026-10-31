"""The place list: coordinates → "home / work / the gym".

Reverse geocoding cannot answer this — it knows what is there, not what it
means to you. Your home's coordinates come back as a street address, never as
"home". So **you name a place once and it is matched from then on**: naming
5–10 places covers 90% of the time.

⚠️ places.json sits beside the database, **never in the repository** — it holds
a home address.
"""

import os
import tempfile
import unittest
from unittest import mock

from umbra import db, places


class LocationTest(unittest.TestCase):
    def test_lives_next_to_the_database(self):
        self.assertEqual(os.path.dirname(places.PATH), db.DB_DIR)

    def test_is_outside_any_repo_checkout(self):
        """It holds a home address. Keeping it physically outside the repository
        beats relying on .gitignore."""
        self.assertNotIn("vibe_coding", places.PATH)


class HaversineTest(unittest.TestCase):
    def test_same_point_is_zero(self):
        self.assertEqual(places.meters_between(37.3, -122.0, 37.3, -122.0), 0)

    def test_known_distance(self):
        """One degree of latitude is about 111 km."""
        d = places.meters_between(37.0, -122.0, 38.0, -122.0)
        self.assertAlmostEqual(d, 111_195, delta=500)

    def test_short_distance(self):
        """0.001° of longitude is about 88 m at this latitude — the scale the
        radius works at."""
        d = places.meters_between(37.331, -122.030, 37.331, -122.029)
        self.assertAlmostEqual(d, 88, delta=10)


class MatchTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "places.json")
        places.save([
            {"name": "家", "lat": 37.3318, "lon": -122.0305, "radius": 150},
            {"name": "公司", "lat": 37.4419, "lon": -122.1430, "radius": 200},
        ], self.path)
        self.known = places.load(self.path)

    def test_exact_hit(self):
        self.assertEqual(places.match(37.3318, -122.0305, self.known), "家")

    def test_within_radius(self):
        """GPS jitters by tens of metres; that is what the radius is for."""
        self.assertEqual(places.match(37.3327, -122.0305, self.known), "家")

    def test_outside_radius_is_unknown(self):
        self.assertIsNone(places.match(37.35, -122.05, self.known))

    def test_nearest_wins_when_two_overlap(self):
        """Where two places' circles overlap, the nearer wins, not the first defined."""
        known = [
            {"name": "远", "lat": 37.3400, "lon": -122.0305, "radius": 2000},
            {"name": "近", "lat": 37.3318, "lon": -122.0305, "radius": 2000},
        ]
        self.assertEqual(places.match(37.3318, -122.0305, known), "近")

    def test_empty_table_matches_nothing(self):
        self.assertIsNone(places.match(37.3318, -122.0305, []))

    def test_default_radius_when_missing(self):
        known = [{"name": "无半径", "lat": 37.3318, "lon": -122.0305}]
        self.assertEqual(places.match(37.3318, -122.0305, known), "无半径")


class StorageTest(unittest.TestCase):
    def setUp(self):
        self.path = os.path.join(tempfile.mkdtemp(), "places.json")

    def test_roundtrip(self):
        rows = [{"name": "家", "lat": 1.0, "lon": 2.0, "radius": 100}]
        places.save(rows, self.path)
        self.assertEqual(places.load(self.path), rows)

    def test_missing_file_is_empty_not_an_error(self):
        """Nothing named yet — a normal state, not a fault."""
        self.assertEqual(places.load(self.path + ".nope"), [])

    def test_written_owner_only(self):
        """A home address; it must not be 644."""
        import stat
        places.save([{"name": "家", "lat": 1.0, "lon": 2.0}], self.path)
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o600)

    def test_atomic_write(self):
        places.save([{"name": "v1", "lat": 1.0, "lon": 2.0}], self.path)
        with mock.patch("os.replace", side_effect=OSError("boom")):
            with self.assertRaises(OSError):
                places.save([{"name": "v2", "lat": 1.0, "lon": 2.0}], self.path)
        self.assertEqual(places.load(self.path)[0]["name"], "v1")

    def test_corrupt_file_does_not_crash_the_import(self):
        with open(self.path, "w") as f:
            f.write("{ not json")
        self.assertEqual(places.load(self.path), [])


class AnonymousIdTest(unittest.TestCase):
    """An unnamed place stores no coordinates but a stable anonymous id.

    You can still see "the same unnamed place, three times", and the page never
    shows the coordinates of where you went. Raw coordinates stay in your own
    database and places.json.
    """

    def test_same_place_same_id(self):
        a = places.anon_id(37.331812, -122.030581)
        b = places.anon_id(37.331834, -122.030522)     # a few metres away
        self.assertEqual(a, b)

    def test_different_places_different_ids(self):
        a = places.anon_id(37.3318, -122.0305)
        b = places.anon_id(37.4419, -122.1430)
        self.assertNotEqual(a, b)

    def test_id_leaks_no_coordinates(self):
        i = places.anon_id(37.3318, -122.0305)
        self.assertNotIn("37.3", i)
        self.assertNotIn("122.0", i)

    def test_id_is_short(self):
        self.assertLessEqual(len(places.anon_id(37.3318, -122.0305)), 12)


class RivalsTest(unittest.TestCase):
    """match() returns the nearest, but "nearest" is not "certain".

    Real data (2026-08-15): within a gym's 120 m radius were three other places,
    at 73, 91 and 91 m. A stay's centre carries tens of metres of error itself,
    and at that scale "the nearest" is a guess. Umbra says "don't know" elsewhere
    (gap rows), and should here too — whatever comes downstream (analysis, the
    page, cross-checking with WHOOP) is entitled to know how far the label holds.
    """

    GYM = (37.600000, -121.600000)
    KNOWN = [
        {"name": "健身房", "lat": GYM[0], "lon": GYM[1], "radius": 120},
        {"name": "咖啡店", "lat": 37.600654, "lon": -121.600625, "radius": 120},
        {"name": "家", "lat": 37.596102, "lon": -121.618476, "radius": 150},
    ]

    def test_a_clear_match_has_no_rivals(self):
        """Home is 1.6 km from the other two; nothing to hesitate over."""
        self.assertEqual(places.rivals(37.596102, -121.618476, self.KNOWN), [])

    def test_a_resolvable_gap_is_not_ambiguity(self):
        """At the gym's centre the coffee shop is 91 m off — that is **not**

        ambiguous. Measured median accuracy was 7 m (2026-08-16, 39 points),
        and a stay averages to a centre within 3–5 m. 91 m is twenty times that,
        clearly distinguishable. Reporting it as ambiguous would hang a useless
        "or maybe" on every label, drowning what really deserves attention.
        """
        self.assertEqual(places.rivals(*self.GYM, self.KNOWN), [])

    def test_near_equidistant_places_are_rivals(self):
        """Exactly between the gym and the coffee shop — genuinely ambiguous.

        No winner or rival is asserted: a few metres either way flips it, a coin
        toss. What is asserted is that Umbra **names both**.
        """
        mid = (37.600327, -121.600313)
        named = {places.match(*mid, self.KNOWN)} | set(places.rivals(*mid, self.KNOWN))
        self.assertEqual(named, {"健身房", "咖啡店"})

    def test_the_winner_is_not_its_own_rival(self):
        self.assertNotIn("健身房", places.rivals(*self.GYM, self.KNOWN))

    def test_nothing_in_range_has_no_rivals(self):
        self.assertEqual(places.rivals(37.0, -122.0, self.KNOWN), [])

    def test_a_far_enough_second_place_is_not_a_rival(self):
        """Far enough apart is not hesitation, or every label carries a string of
        "or maybe"."""
        near = [{"name": "A", "lat": 37.5, "lon": -122.0, "radius": 300},
                {"name": "B", "lat": 37.5018, "lon": -122.0, "radius": 300}]
        self.assertEqual(places.rivals(37.5, -122.0, near), [])   # B is 200 m away

    def test_match_still_returns_a_single_name(self):
        """rivals() is extra information and must not change match()'s contract
        — location.py depends on it."""
        self.assertEqual(places.match(*self.GYM, self.KNOWN), "健身房")


if __name__ == "__main__":
    unittest.main()
