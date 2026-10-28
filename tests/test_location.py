"""The location importer: a stream of GPS points → "at home / driving from home
to work / at work".

Stays and trips **partition the whole day** — every minute you are either
somewhere or on the way between two places. No other source has this: they all
sample (only with the browser open, only with the Mac awake), while location is
there whenever the phone is with you.

⚠️ Raw coordinates are not stored. A named place is stored by name, an unnamed
one by an anonymous id — the page embeds the payload in full, and where you
have been does not belong there.
"""

import unittest
from unittest import mock

from umbra import places
from umbra.importers import location

T0 = 1786800000000          # some moment on 2026-08-15
MIN = 60_000

HOME = (37.3318, -122.0305)
WORK = (37.4419, -122.1430)
KNOWN = [
    {"name": "家", "lat": HOME[0], "lon": HOME[1], "radius": 150},
    {"name": "公司", "lat": WORK[0], "lon": WORK[1], "radius": 200},
]


def pt(minute, latlon, motion="stationary", accuracy=50.0):
    lat, lon = latlon
    return {"ts": T0 + minute * MIN, "lat": lat, "lon": lon,
            "accuracy": accuracy, "speed": 0, "motion": motion,
            "activity": "other", "battery": 0.8}


def moving(minute, frac, motion="driving"):
    """A point interpolated between home and work, as if commuting."""
    lat = HOME[0] + (WORK[0] - HOME[0]) * frac
    lon = HOME[1] + (WORK[1] - HOME[1]) * frac
    return pt(minute, (lat, lon), motion=motion, accuracy=30.0)


class StayTest(unittest.TestCase):
    def test_points_in_one_spot_become_one_stay(self):
        pts = [pt(i * 5, HOME) for i in range(8)]     # 35 minutes
        rows = location.to_rows(pts, KNOWN)
        stays = [r for r in rows if r[4] == "stay"]
        self.assertEqual(len(stays), 1)
        uid, ts, dur, src, kind, payload = stays[0]
        self.assertEqual(src, "location")
        self.assertEqual(payload["place"], "家")
        self.assertEqual(ts, T0)
        self.assertEqual(dur, 35 * MIN)

    def test_too_short_is_not_a_stay(self):
        """Stopping at a red light is not "arriving somewhere"."""
        rows = location.to_rows([pt(0, HOME), pt(2, HOME)], KNOWN)
        self.assertEqual([r for r in rows if r[4] == "stay"], [])

    def test_unnamed_place_gets_an_anonymous_id(self):
        far = (37.7749, -122.4194)
        rows = location.to_rows([pt(i * 5, far) for i in range(8)], KNOWN)
        stays = [r for r in rows if r[4] == "stay"]
        self.assertEqual(len(stays), 1)
        self.assertTrue(stays[0][5]["place"].startswith("p"))
        self.assertFalse(stays[0][5]["named"])

    def test_uid_is_stable(self):
        pts = [pt(i * 5, HOME) for i in range(8)]
        self.assertEqual(location.to_rows(pts, KNOWN)[0][0],
                         location.to_rows(pts, KNOWN)[0][0])


class TransitTest(unittest.TestCase):
    def setUp(self):
        self.pts = (
            [pt(i * 5, HOME) for i in range(7)]            # 0-30  at home
            + [moving(35, 0.3), moving(40, 0.6), moving(45, 0.9)]
            + [pt(50 + i * 5, WORK) for i in range(7)]     # 50-80 at work
        )
        self.rows = location.to_rows(self.pts, KNOWN)

    def test_produces_stay_transit_stay(self):
        self.assertEqual([r[4] for r in self.rows],
                         ["stay", "transit", "stay"])

    def test_transit_knows_where_from_and_to(self):
        t = [r for r in self.rows if r[4] == "transit"][0]
        self.assertEqual(t[5]["from"], "家")
        self.assertEqual(t[5]["to"], "公司")

    def test_transit_mode_comes_from_ios_not_from_speed(self):
        """motion comes from the iPhone's motion coprocessor; it is not guessed
        from speed."""
        t = [r for r in self.rows if r[4] == "transit"][0]
        self.assertEqual(t[5]["mode"], "driving")

    def test_transit_spans_the_gap_between_stays(self):
        stay1, transit, stay2 = self.rows
        self.assertEqual(transit[1], stay1[1] + stay1[2])
        self.assertEqual(transit[1] + transit[2], stay2[1])

    def test_walking_is_kept_distinct(self):
        pts = ([pt(i * 5, HOME) for i in range(7)]
               + [moving(35, 0.5, motion="walking")]
               + [pt(45 + i * 5, WORK) for i in range(7)])
        t = [r for r in location.to_rows(pts, KNOWN) if r[4] == "transit"][0]
        self.assertEqual(t[5]["mode"], "walking")


class PrivacyTest(unittest.TestCase):
    def test_raw_coordinates_are_never_stored(self):
        rows = location.to_rows([pt(i * 5, HOME) for i in range(8)], KNOWN)
        blob = repr(rows)
        self.assertNotIn("37.33", blob)
        self.assertNotIn("-122.03", blob)

    def test_unnamed_place_id_leaks_nothing(self):
        far = (37.7749, -122.4194)
        rows = location.to_rows([pt(i * 5, far) for i in range(8)], KNOWN)
        self.assertNotIn("37.77", repr(rows))


class GapTest(unittest.TestCase):
    """"No points" means two things, and **the ends of the gap tell them apart**.

    Overland is designed not to send while stationary (Min Distance). So "no
    points" should first be read as "didn't move" — it working normally, not
    missing data.

    But it may be a flat battery or a killed app, and then you could be anywhere.
    The test is at the ends: **the same place before and after → you were there
    all along**; 50 km away after the gap → what happened between is truly
    unknown.
    """

    def test_no_points_at_the_same_place_means_he_never_left(self):
        """Ten hours at home without moving, and Overland sends nothing. That is
        not a gap; that is being at home."""
        pts = [pt(0, HOME), pt(5, HOME), pt(600, HOME), pt(605, HOME)]
        rows = location.to_rows(pts, KNOWN)
        self.assertEqual([r[4] for r in rows], ["stay"])
        self.assertEqual(rows[0][2], 605 * MIN)
        self.assertEqual(rows[0][5]["place"], "家")

    def test_an_inferred_stay_says_so(self):
        """Inferred and seen are not the same; what comes downstream is entitled
        to know."""
        pts = [pt(0, HOME), pt(5, HOME), pt(600, HOME), pt(605, HOME)]
        self.assertTrue(location.to_rows(pts, KNOWN)[0][5]["inferred"])

    def test_a_continuously_observed_stay_is_not_inferred(self):
        rows = location.to_rows([pt(i * 5, HOME) for i in range(8)], KNOWN)
        self.assertFalse(rows[0][5]["inferred"])

    def test_a_gap_that_ends_elsewhere_is_still_unknown(self):
        """Home → ten hours of silence → appears at work. When did they leave?

        Unknown. This must never be inferred as "at home until just before
        leaving" — that would be invented.
        """
        pts = [pt(0, HOME), pt(5, HOME), pt(600, WORK), pt(605, WORK)]
        rows = location.to_rows(pts, KNOWN)
        self.assertIn("gap", [r[4] for r in rows])

    def test_an_absurdly_long_silence_is_not_inferred(self):
        """Three days without a point, even with home at both ends — that looks
        more like a phone off than a stay in."""
        pts = [pt(0, HOME), pt(5, HOME), pt(3 * 24 * 60, HOME),
               pt(3 * 24 * 60 + 5, HOME)]
        rows = location.to_rows(pts, KNOWN)
        self.assertIn("gap", [r[4] for r in rows])

    def test_no_points_is_not_an_error(self):
        self.assertEqual(location.to_rows([], KNOWN), [])


class SamePlaceDespiteMovingTest(unittest.TestCase):
    """Walking around a big place ≠ leaving it — say, a beach.

        near the car park
          ⋯ a long silence (in the water) ⋯
        by the shore, several hundred metres from the last point, motion=stationary

    By STAY_RADIUS_M (150 m), that distance means "changed place", so the whole
    visit reads as a gap. But both points lie inside that named place's 600 m radius — a beach, a
    campus, a mall are meant to be walked around; **the place's radius is its
    real scale**.
    """

    BEACH = {"name": "海滩", "lat": 36.800000, "lon": -121.900000, "radius": 600}
    KNOWN = KNOWN + [BEACH]
    A = (36.796489, -121.899357)          # the car park, about 400 m from the centre
    B = (36.803510, -121.900643)          # the shore, about 390 m from the centre, 789 m from A

    def test_moving_within_one_named_place_is_not_leaving(self):
        pts = [pt(0, self.A), pt(5, self.A),
               pt(160, self.B, accuracy=5.0), pt(165, self.B, accuracy=5.0)]
        rows = location.to_rows(pts, self.KNOWN)
        self.assertNotIn("gap", [r[4] for r in rows])
        stays = [r for r in rows if r[4] == "stay"]
        self.assertEqual(len(stays), 1)
        self.assertEqual(stays[0][5]["place"], "海滩")
        self.assertEqual(stays[0][2], 165 * MIN)

    def test_two_different_named_places_is_still_leaving(self):
        """Home → silence → work. That really was leaving; both being named does
        not join them."""
        pts = [pt(0, HOME), pt(5, HOME), pt(600, WORK), pt(605, WORK)]
        self.assertIn("gap", [r[4] for r in location.to_rows(pts, self.KNOWN)])

    def test_two_unnamed_spots_far_apart_is_still_unknown(self):
        """Unnamed, there is no basis for "this is one place"; only distance counts."""
        x, y = (35.0, -120.0), (35.006, -120.0)      # about 670 m apart
        pts = [pt(0, x), pt(5, x), pt(600, y), pt(605, y)]
        self.assertIn("gap", [r[4] for r in location.to_rows(pts, self.KNOWN)])


class SamePlaceTransitTest(unittest.TestCase):
    """"Travelling" from a place to itself is not a trip. Real output (2026-08-16):

        08:35 → 11:10   154 min  beach
        11:10 → 11:14     4 min  beach → beach     <- this row
        11:14 → 11:31    16 min  beach

    500 m of walking on the beach cut it into two stays with a "trip" between.
    The numbers are right, but "beach → beach" says nothing, and one visit became
    three rows — the lane looks like leaving and coming back. It should be one
    176-minute stay.
    """

    BEACH = {"name": "海滩", "lat": 36.800000, "lon": -121.900000, "radius": 600}
    KNOWN = KNOWN + [BEACH]
    A = (36.796489, -121.899357)
    B = (36.803510, -121.900643)

    def _rows(self):
        pts = ([pt(i * 5, self.A, accuracy=5.0) for i in range(4)]     # 0-15
               + [pt(20 + i * 5, self.B, accuracy=5.0) for i in range(4)])  # 20-35
        return location.to_rows(pts, self.KNOWN)

    def test_it_collapses_into_one_stay(self):
        self.assertEqual([r[4] for r in self._rows()], ["stay"])

    def test_the_stay_spans_the_whole_thing(self):
        row = self._rows()[0]
        self.assertEqual(row[1], T0)
        self.assertEqual(row[2], 35 * MIN)
        self.assertEqual(row[5]["place"], "海滩")

    def test_the_observed_points_are_summed(self):
        """Merging must not lose evidence — points is "how many I actually saw"."""
        self.assertEqual(self._rows()[0][5]["points"], 8)

    def test_being_inferred_survives_the_merge(self):
        """If one part before merging was inferred, the mark must survive.

        "Inferred" versus "measured" is one of this source's most important
        outputs — lose it, and a stay filled in from silence passes for one
        backed by observation.
        """
        pts = ([pt(0, self.A, accuracy=5.0), pt(5, self.A, accuracy=5.0)]
               + [pt(200, self.A, accuracy=5.0),          # 195 minutes of silence
                  pt(205, self.A, accuracy=5.0)]
               + [pt(210 + i * 5, self.B, accuracy=5.0) for i in range(3)])
        rows = location.to_rows(pts, self.KNOWN)
        self.assertEqual([r[4] for r in rows], ["stay"])
        self.assertTrue(rows[0][5]["inferred"], "merging swallowed the inferred mark")

    def test_a_real_journey_is_left_alone(self):
        """Home → work: different start and end, a real trip; never merged."""
        pts = ([pt(i * 5, HOME) for i in range(7)]
               + [moving(35, 0.3), moving(40, 0.6), moving(45, 0.9)]
               + [pt(50 + i * 5, WORK) for i in range(7)])
        self.assertEqual([r[4] for r in location.to_rows(pts, self.KNOWN)],
                         ["stay", "transit", "stay"])

    def test_two_unnamed_spots_are_not_merged(self):
        """Unnamed, there is no basis for "the same place". Two anonymous ids that
        differ are different."""
        x, y = (35.0, -120.0), (35.01, -120.0)     # about 1,100 m apart
        pts = ([pt(i * 5, x, accuracy=5.0) for i in range(4)]
               + [pt(20 + i * 5, y, accuracy=5.0) for i in range(4)])
        self.assertIn("transit", [r[4] for r in location.to_rows(pts, self.KNOWN)])


class DepartedDuringSilenceTest(unittest.TestCase):
    """Leaving during a silence — a real case (2026-08-16).

        18:39  at home (2.7 m from home)
          ⋯ 772 minutes of silence ⋯
        07:31  already 28 km away, motion=driving

    Comparing only the ends says "changed place, discard it all", throwing away
    12 hours 21 minutes of real stay. But the clue is in the data: the end point
    says driving — they **left partway through**, not "whereabouts unknown".

    The departure is worked out by **upper bound**: 28 km ÷ the speed ceiling =
    the shortest trip, which gives the latest possible departure. The physics is
    clear — you cannot drive faster, so you cannot have left later. The direction
    is known: it can overstate the stay, never understate it.
    """

    FAR = (HOME[0] + 0.252, HOME[1])            # about 28 km due north

    def _rows(self, motion="driving", at=777):
        pts = [pt(0, HOME), pt(5, HOME),
               pt(at, self.FAR, motion=motion, accuracy=45.0)]
        return location.to_rows(pts, KNOWN)

    def test_the_stay_survives(self):
        """A long stay at home must not be discarded because the gap ended on
        the road."""
        stays = [r for r in self._rows() if r[4] == "stay"]
        self.assertEqual(len(stays), 1)
        self.assertEqual(stays[0][5]["place"], "家")
        self.assertGreater(stays[0][2], 700 * MIN)

    def test_it_ends_at_the_latest_possible_departure(self):
        """28 km ÷ 40 m/s ≈ an 11.7-minute trip → the stay runs to 777 − 11.7 ≈ 765."""
        stay = [r for r in self._rows() if r[4] == "stay"][0]
        expected = T0 + (777 - 28053 / location.DEFAULT_MAX_SPEED_MPS / 60) * MIN
        self.assertAlmostEqual(stay[1] + stay[2], expected, delta=90_000)

    def test_it_is_marked_inferred(self):
        self.assertTrue([r for r in self._rows() if r[4] == "stay"][0][5]["inferred"])

    def test_the_estimated_departure_is_not_counted_as_an_observed_point(self):
        """The worked-out moment is not an observation. points counts what was
        actually seen."""
        self.assertEqual([r for r in self._rows() if r[4] == "stay"][0][5]["points"], 2)

    def test_a_settled_point_elsewhere_is_still_unknown(self):
        """The end point is stationary — they may have arrived hours ago, and the
        upper bound says nothing."""
        self.assertIn("gap", [r[4] for r in self._rows(motion="stationary")])

    def test_walking_uses_a_walking_speed(self):
        """500 m on foot must not be costed at 144 km/h as 12 seconds."""
        near = (HOME[0] + 0.0045, HOME[1])       # about 500 m
        pts = [pt(0, HOME), pt(5, HOME),
               pt(200, near, motion="walking", accuracy=20.0)]
        stay = [r for r in location.to_rows(pts, KNOWN) if r[4] == "stay"][0]
        # 500 m ÷ 2.5 m/s = 200 s ≈ 3.3 minutes, not 12 seconds
        self.assertLess(stay[1] + stay[2], T0 + (200 - 2) * MIN)


class AccuracyTest(unittest.TestCase):
    def test_wildly_inaccurate_points_are_dropped(self):
        """A point accurate to kilometres would drag the stay into the next block."""
        pts = ([pt(i * 5, HOME) for i in range(8)]
               + [pt(20, (37.4, -122.1), accuracy=5000.0)])
        rows = location.to_rows(pts, KNOWN)
        self.assertEqual(len([r for r in rows if r[4] == "stay"]), 1)


class TeleportOutlierTest(unittest.TestCase):
    """A teleport: jumping 1.3 km away and back within a second.

    Real data (2026-08-16 13:19):

        13:14:06   at a café
        13:19:57   1.3 km away, claiming 64 m accuracy
        13:19:58   at the café

    Accurate enough (64 m < TRUST_ACCURACY_M), so it was allowed to split the
    cluster — and six minutes at the café became two one-second stays, both
    shorter than MIN_STAY_MS, and the stay vanished.

    **How accurate it claims to be and whether it is right are different.** The
    test looks at three points: remove the middle and the ends join → the middle
    one is bad. Looking only backwards blames good points (the previous point to
    it was 3.8 m/s, entirely normal; what is impossible is from it to the next).

    This cannot catch a flight: every leg is fast, and removing any one point
    still leaves the ends apart.
    """

    NEAR = (37.200000, -121.700000)
    FAR = (37.187965, -121.699466)          # about 1.3 km due south

    def _pts(self):
        return [pt(0, self.NEAR, accuracy=5.0),
                pt(0.02, self.NEAR, accuracy=28.0),
                pt(6, self.FAR, accuracy=64.0),          # the teleport
                pt(6.02, self.NEAR, accuracy=99.0)]

    def test_the_teleport_is_dropped(self):
        kept = location.usable(self._pts())
        self.assertEqual(len(kept), 3)
        for p in kept:
            self.assertLess(places.meters_between(p["lat"], p["lon"], *self.NEAR),
                            500)

    def test_the_stay_survives(self):
        known = KNOWN + [{"name": "咖啡店", "lat": self.NEAR[0],
                          "lon": self.NEAR[1], "radius": 100}]
        stays = [r for r in location.to_rows(self._pts(), known)
                 if r[4] == "stay"]
        self.assertEqual(len(stays), 1, "the teleport split the stay")
        self.assertEqual(stays[0][5]["place"], "咖啡店")

    def test_normal_driving_is_kept(self):
        """Driving through at 60 km/h, not one point may be lost."""
        pts = [pt(i, (HOME[0] + i * 0.009, HOME[1]), motion="driving",
                  accuracy=10.0) for i in range(6)]   # about 1 km a minute
        self.assertEqual(len(location.usable(pts)), 6)

    def test_sustained_fast_travel_is_kept(self):
        """Flying: every leg fast, no single removal joins the ends — not a teleport."""
        pts = [pt(i, (HOME[0] + i * 2.0, HOME[1]), accuracy=50.0)
               for i in range(5)]            # about 220 km a minute
        self.assertEqual(len(location.usable(pts)), 5)

    def test_fast_but_possible_is_not_a_teleport(self):
        """180 km/h is a fast train or a motorway, not a fault.

        The three-point test is strong enough that lowering the ceiling to 10 m/s
        turns no existing test red — sustained movement is protected by it. This
        pins what the constant **means**: "impossible" is faster than any
        vehicle, not faster than walking.
        """
        far = (HOME[0] + 0.027, HOME[1])          # about 3 km
        pts = [pt(0, HOME, accuracy=10.0),
               pt(1, far, accuracy=10.0),          # 3km / 60s = 50 m/s
               pt(2, HOME, accuracy=10.0)]
        self.assertEqual(len(location.usable(pts)), 3)

    def test_the_endpoints_are_never_dropped(self):
        """The first and last points have no triple to judge by; no guessing."""
        pts = [pt(0, self.FAR, accuracy=50.0), pt(1, self.NEAR, accuracy=5.0),
               pt(2, self.NEAR, accuracy=5.0)]
        self.assertEqual(len(location.usable(pts)), 3)

    def test_a_dropped_point_does_not_poison_the_next_decision(self):
        """Compare against **the last point kept**, not the previous one in the

        sequence. When GPS and Wi-Fi fixes alternate, good and bad interleave
        (seen: two points a second apart, 477 m different). Measured against a
        bad point just thrown away, the next good one would be thrown away too.
        """
        pts = [pt(0, self.NEAR, accuracy=5.0),
               pt(0.01, self.FAR, accuracy=60.0),      # bad
               pt(0.02, self.NEAR, accuracy=5.0),      # good, between two bad ones
               pt(0.03, self.FAR, accuracy=60.0),      # bad
               pt(1, self.NEAR, accuracy=5.0)]
        kept = location.usable(pts)
        near = sum(1 for p in kept
                   if places.meters_between(p["lat"], p["lon"], *self.NEAR) < 500)
        self.assertEqual(near, 3, "the good point between two bad ones was thrown out")

    def test_points_are_returned_in_time_order(self):
        kept = location.usable(list(reversed(self._pts())))
        self.assertEqual([p["ts"] for p in kept], sorted(p["ts"] for p in kept))


class WeightedCentreTest(unittest.TestCase):
    """Points of different accuracy must not get the same vote.

    Real data (2026-08-16, 39 points): median accuracy 7 m, with a tail at
    89/95/109 m. A plain mean lets one 109 m point drag the centre of five 7 m
    points by nearly 20 m.
    The statistically right way is inverse-variance weighting — weight ∝
    1/accuracy², so a 7 m point weighs 240 times a 109 m one.
    """

    def test_a_precise_point_outweighs_a_vague_one(self):
        """One 5 m and one 500 m point: the centre lands almost entirely on the 5 m."""
        a, b = (37.5, -122.0), (37.51, -122.0)      # about 1,100 m apart
        lat, lon = location._centre([
            {"lat": a[0], "lon": a[1], "accuracy": 5.0, "ts": T0},
            {"lat": b[0], "lon": b[1], "accuracy": 500.0, "ts": T0 + 1},
        ])
        self.assertLess(places.meters_between(lat, lon, *a), 10)

    def test_equal_accuracy_degenerates_to_a_plain_average(self):
        """Equal accuracy must favour neither — or a new bug has been introduced."""
        lat, lon = location._centre([
            {"lat": 37.0, "lon": -122.0, "accuracy": 20.0, "ts": T0},
            {"lat": 37.2, "lon": -122.0, "accuracy": 20.0, "ts": T0 + 1},
        ])
        self.assertAlmostEqual(lat, 37.1, places=6)

    def test_missing_accuracy_is_not_infinite_confidence(self):
        """Real data has points with accuracy 0 or missing.

        1/0² = ∞ would let that one point take all the weight — "unknown accuracy"
        read as "infinite accuracy", exactly backwards.
        """
        lat, lon = location._centre([
            {"lat": 37.0, "lon": -122.0, "accuracy": 0, "ts": T0},
            {"lat": 37.2, "lon": -122.0, "accuracy": 10.0, "ts": T0 + 1},
        ])
        self.assertTrue(37.0 < lat < 37.2, f"the weight collapsed onto one point: {lat}")

    def test_unknown_accuracy_weighs_less_than_a_known_good_fix(self):
        """No reported accuracy = "unknown", and must weigh clearly less than a
        point reporting 10 m.

        The test above only checks "not collapsed onto one point", which is too
        loose — an assumed accuracy of 0.1 m would pass. This pins the direction:
        not knowing should say less.
        """
        lat, _ = location._centre([
            {"lat": 37.0, "lon": -122.0, "ts": T0},               # no accuracy field at all
            {"lat": 37.2, "lon": -122.0, "accuracy": 10.0, "ts": T0 + 1},
        ])
        self.assertGreater(lat, 37.15, "a point of unknown accuracy was treated as a good one")

    def test_impossible_precision_is_capped(self):
        """A point claiming 0.001 m is a data error, not a miracle.

        Uncapped, it outweighs a 5 m point twenty-five million times — one bad
        point decides where the whole stay was. Capped at 5 m the two weigh the
        same and the centre falls between them.
        """
        lat, _ = location._centre([
            {"lat": 37.0, "lon": -122.0, "accuracy": 0.001, "ts": T0},
            {"lat": 37.2, "lon": -122.0, "accuracy": 5.0, "ts": T0 + 1},
        ])
        self.assertAlmostEqual(lat, 37.1, places=6)

    def test_a_barely_usable_point_does_not_extend_a_stay(self):
        """A 300 m point must not stretch "at home" from 35 minutes to 65.

        Too vague to split the cluster (that protection is right) — and exactly
        for that reason it is **absorbed into the cluster**, pushing the stay's
        end half an hour later. Such points have to go one step earlier, which is
        why MAX_ACCURACY_M exists.
        """
        pts = ([pt(i * 5, HOME) for i in range(8)]            # 0-35 at home
               + [pt(65, (37.3400, -122.0305), accuracy=300.0)])
        stays = [r for r in location.to_rows(pts, KNOWN) if r[4] == "stay"]
        self.assertEqual(len(stays), 1)
        self.assertEqual(stays[0][2], 35 * MIN, "a vague point stretched the stay")

    def test_a_vague_point_does_not_split_a_stay(self):
        """A 200 m point "300 m off" says nothing — that is its error.

        Let it split the cluster and a stay at home becomes two, with a transit
        conjured between them.
        """
        pts = ([pt(i * 5, HOME) for i in range(4)]
               + [pt(20, (37.3345, -122.0305), accuracy=200.0)]   # 300 m off
               + [pt(20 + i * 5, HOME) for i in range(1, 5)])
        stays = [r for r in location.to_rows(pts, KNOWN) if r[4] == "stay"]
        self.assertEqual(len(stays), 1, "one vague point split the stay")
        self.assertEqual(stays[0][5]["place"], "家")


class CollectTest(unittest.TestCase):
    def test_no_token_means_no_data_not_a_crash(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertEqual(location.collect(), [])

    def test_registered(self):
        from umbra.importers import ALL, SOURCE_OF
        self.assertIn("location", ALL)
        self.assertEqual(SOURCE_OF["location"], "location")

    def test_collect_takes_since_ms_first(self):
        import inspect
        self.assertEqual(
            list(inspect.signature(location.collect).parameters)[0], "since_ms")

    def test_fetches_incrementally(self):
        seen = []

        def fake_get(url, headers=None, timeout=60, **kw):
            seen.append(url)
            return {"points": []}

        with mock.patch.dict("os.environ", {"UMBRA_LOCATION_TOKEN": "t",
                                            "UMBRA_LOCATION_URL": "https://x.test"}):
            with mock.patch.object(location.net, "get_json", side_effect=fake_get):
                location.collect(since_ms=12345)
        self.assertIn("since=12345", seen[0])


if __name__ == "__main__":
    unittest.main()
