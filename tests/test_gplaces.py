"""Google Places (New) — coordinates ⇄ places.

Places only, **not the Geocoding API**: that one lives on maps.googleapis.com,
an older web service that accepts only API keys, not service accounts
(measured: REQUEST_DENIED). Places lives on places.googleapis.com, a standard
Cloud API
with the whole IAM model. Whether a Google API accepts a service account can
be told from its domain.

And Places covers both directions in one API, so Geocoding is redundant:
    coordinates → place   searchNearby (with formattedAddress)
    place → coordinates   searchText

⚠️ Each call hands a coordinate to a third party. The caller decides whether to
   call — scripts/places.py calls only for **unnamed** clusters; home and work
   are never sent.
"""

import unittest
from unittest import mock

from umbra import gplaces

SA = {"type": "service_account", "project_id": "proj-1",
      "client_email": "bot@proj-1.iam.gserviceaccount.com",
      "private_key": "-----BEGIN PRIVATE KEY-----\nx\n-----END PRIVATE KEY-----\n"}

NEARBY_RESP = {"places": [
    {"displayName": {"text": "Riverside Community Center"}, "formattedAddress": "100 Example Ave",
     "primaryTypeDisplayName": {"text": "Community Center"},
     "types": ["gym", "point_of_interest"]},
    {"displayName": {"text": "Starbucks"}, "formattedAddress": "1 Main St",
     "primaryTypeDisplayName": {"text": "Coffee Shop"}, "types": ["cafe"]},
]}

TEXT_RESP = {"places": [
    {"displayName": {"text": "Parking lot"},
     "formattedAddress": "1 Example Rd, Springfield, CA 90000, USA",
     "location": {"latitude": 36.796489, "longitude": -121.899357}},
]}


def _patched(resp):
    """Wire up credentials and the network; return (url, body, headers) of the call."""
    seen = {}

    def fake_post(url, body, headers=None, timeout=60):
        seen.update(url=url, body=body, headers=headers or {})
        return resp

    ctx = mock.patch.multiple(
        gplaces.google_auth,
        credentials=mock.DEFAULT, ensure_access_token=mock.DEFAULT)
    return seen, ctx, fake_post


class UnconfiguredTest(unittest.TestCase):
    """No Google configured is "not set up yet", not a fault — it must never stop
    the import."""

    def test_nearby_returns_nothing(self):
        with mock.patch.object(gplaces.google_auth, "credentials",
                               return_value=None):
            self.assertEqual(gplaces.nearby(37.0, -122.0), [])

    def test_search_returns_nothing(self):
        with mock.patch.object(gplaces.google_auth, "credentials",
                               return_value=None):
            self.assertIsNone(gplaces.search("某个地址"))

    def test_it_never_reaches_the_network(self):
        with mock.patch.object(gplaces.google_auth, "credentials",
                               return_value=None):
            with mock.patch.object(gplaces.net, "post_json") as post:
                gplaces.nearby(37.0, -122.0)
                gplaces.search("x")
        self.assertFalse(post.called, "a request went out without credentials")


class NearbyTest(unittest.TestCase):
    def setUp(self):
        self.seen, _, fake = _patched(NEARBY_RESP)
        with mock.patch.object(gplaces.google_auth, "credentials",
                               return_value=SA), \
             mock.patch.object(gplaces.google_auth, "ensure_access_token",
                               return_value="tok-1"), \
             mock.patch.object(gplaces.net, "post_json", side_effect=fake):
            self.out = gplaces.nearby(37.600000, -121.600000, radius=120)

    def test_it_returns_readable_names(self):
        self.assertEqual([p["name"] for p in self.out], ["Riverside Community Center", "Starbucks"])

    def test_it_keeps_the_type(self):
        """types is the column that matters — gym / cafe is activity in itself,
        and says far more than a street number."""
        self.assertEqual(self.out[0]["type"], "Community Center")
        self.assertIn("gym", self.out[0]["types"])

    def test_it_asks_the_right_circle(self):
        c = self.seen["body"]["locationRestriction"]["circle"]
        self.assertAlmostEqual(c["center"]["latitude"], 37.600000)
        self.assertEqual(c["radius"], 120)

    def test_the_billing_project_is_named(self):
        """Places uses standard Cloud authentication; without this header it 403s."""
        self.assertEqual(self.seen["headers"]["X-Goog-User-Project"], "proj-1")
        self.assertEqual(self.seen["headers"]["Authorization"], "Bearer tok-1")

    def test_the_field_mask_only_asks_for_what_we_use(self):
        """FieldMask decides the billing tier. One more field can move the whole
        request up a tier, and asking for what you don't use is paying for nothing."""
        mask = self.seen["headers"]["X-Goog-FieldMask"]
        self.assertNotIn("places.photos", mask)
        self.assertNotIn("places.reviews", mask)
        self.assertNotIn("*", mask)


class SearchTest(unittest.TestCase):
    def _run(self, resp):
        seen, _, fake = _patched(resp)
        with mock.patch.object(gplaces.google_auth, "credentials",
                               return_value=SA), \
             mock.patch.object(gplaces.google_auth, "ensure_access_token",
                               return_value="tok-1"), \
             mock.patch.object(gplaces.net, "post_json", side_effect=fake):
            return gplaces.search("1 Example Rd, Springfield, CA 90000"), seen

    def test_it_returns_coordinates(self):
        got, _ = self._run(TEXT_RESP)
        self.assertAlmostEqual(got["lat"], 36.796489)
        self.assertAlmostEqual(got["lon"], -121.899357)
        self.assertEqual(got["name"], "Parking lot")

    def test_the_query_is_sent(self):
        _, seen = self._run(TEXT_RESP)
        self.assertIn("Example Rd", seen["body"]["textQuery"])

    def test_no_match_is_none_not_a_crash(self):
        got, _ = self._run({})
        self.assertIsNone(got)


class FailureTest(unittest.TestCase):
    """Location is one source among many. Google failing must not sink the import."""

    def test_a_network_error_yields_nothing(self):
        with mock.patch.object(gplaces.google_auth, "credentials",
                               return_value=SA), \
             mock.patch.object(gplaces.google_auth, "ensure_access_token",
                               return_value="tok-1"), \
             mock.patch.object(gplaces.net, "post_json",
                               side_effect=OSError("boom")):
            self.assertEqual(gplaces.nearby(37.0, -122.0), [])
            self.assertIsNone(gplaces.search("x"))


if __name__ == "__main__":
    unittest.main()
