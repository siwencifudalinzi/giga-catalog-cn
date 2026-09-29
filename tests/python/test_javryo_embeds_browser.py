import unittest

from src.giga_catalog.javryo_embeds_browser import aggregate_path_status, classify_observation, safe_evidence, source_request_allowed


class PlaybackClassificationTests(unittest.TestCase):
    def test_source_page_uses_only_javryo_and_the_selected_embed_host(self):
        self.assertTrue(source_request_allowed("https://javryo.com/wp-json/dooplayer/v1/post/12", "bysejikuar.com"))
        self.assertTrue(source_request_allowed("https://bysejikuar.com/e/id", "bysejikuar.com"))
        self.assertFalse(source_request_allowed("https://creative.rmhfrtnd.com/ad", "bysejikuar.com"))
        self.assertFalse(source_request_allowed("https://n1mwq.org/h4wr/id", "bysejikuar.com"))

    def test_source_must_reach_and_direct_must_play_before_publication(self):
        self.assertEqual(aggregate_path_status({"source": {"status": "verified"},
                                                "direct": {"status": "verified"}}), "verified")
        self.assertEqual(aggregate_path_status({"source": {"status": "reached"},
                                                "direct": {"status": "verified"}}), "verified")
        self.assertEqual(aggregate_path_status({"source": {"status": "reached"},
                                                "direct": {"status": "media_reachable"}}), "media_reachable")
        self.assertEqual(aggregate_path_status({"source": {"status": "blocked"},
                                                "direct": {"status": "verified"}}), "blocked")
    def test_event_is_required_for_verified(self):
        observation = {"httpStatus": 200, "videoCount": 1, "duration": 3846,
                       "manifestStatus": 200, "events": ["playing"]}
        self.assertEqual(classify_observation(observation), "verified")
        observation["events"] = []
        self.assertEqual(classify_observation(observation), "media_reachable")
        self.assertEqual(classify_observation({"httpStatus": 200, "videoCount": 1,
                          "duration": 0, "events": ["playing"]}), "retryable")

    def test_empty_deleted_challenged_and_failed_media(self):
        self.assertEqual(classify_observation({"httpStatus": 200}), "retryable")
        self.assertEqual(classify_observation({"httpStatus": 200, "deleted": True}), "dead")
        self.assertEqual(classify_observation({"httpStatus": 403}), "blocked")
        self.assertEqual(classify_observation({"httpStatus": 200, "challenge": True}), "blocked")
        self.assertEqual(classify_observation({"httpStatus": 200, "videoCount": 1,
                          "duration": 200, "manifestStatus": 403}), "blocked")

    def test_evidence_strips_temporary_urls_and_credentials(self):
        raw = {"events": ["playing"], "manifestStatus": 200,
               "manifestUrl": "https://media.test/master.m3u8?token=secret",
               "cookie": "secret", "token": "secret", "segmentUrl": "https://media.test/one.ts"}
        serialized = str(safe_evidence(raw)).lower()
        for forbidden in ("m3u8", "token", "cookie", "one.ts", "secret"):
            self.assertNotIn(forbidden, serialized)
