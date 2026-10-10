import os
os.environ.setdefault("VCBLOGGER_TEST_MODE", "1")

import unittest
from VCBlogger.utils.formatting import escape_html, format_incident_alert, person_link, person_name
from VCBlogger.vc.sessions import ActiveParticipant, ActiveSession


class TestFormattingSecurity(unittest.TestCase):
    def test_html_entities_are_escaped(self):
        self.assertEqual(escape_html("A & <B> \"Q\""), "A &amp; &lt;B&gt; &quot;Q&quot;")

    def test_person_link_escapes_names_and_hides_placeholders(self):
        rendered = person_link(123, "Name <admin> & friends", "x")
        self.assertIn("Name &lt;admin&gt; &amp; friends", rendered)
        self.assertNotIn("<admin>", rendered)
        self.assertEqual(person_name("User_123", "bob"), "@bob")
        self.assertEqual(person_name("", ""), "Unknown user")

    def test_incident_details_are_safe(self):
        rendered = format_incident_alert("test <gap>", -100123, "bad <b>details</b> & text")
        self.assertIn("test &lt;gap&gt;", rendered)
        self.assertIn("bad &lt;b&gt;details&lt;/b&gt; &amp; text", rendered)


class TestPresenceOnlyTracking(unittest.TestCase):
    def test_participant_tracking_does_not_store_mute_state(self):
        participant = ActiveParticipant(123)
        self.assertNotIn("is_muted", participant.to_dict())


if __name__ == "__main__":
    unittest.main()
