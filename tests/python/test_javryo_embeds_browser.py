import unittest
from unittest.mock import AsyncMock, Mock

from src.giga_catalog.javryo_embeds_browser import aggregate_path_status, classify_observation, safe_evidence, source_request_allowed
from scripts.verify_javryo_embeds import should_queue_candidate
from src.giga_catalog.javryo_embeds_browser import bounded_route_handler


class PlaybackClassificationTests(unittest.TestCase):
    def test_promising_retry_only_rechecks_direct_playback_evidence(self):
        previous = {"sourceUrlHash": "source", "embedUrlHash": "embed",
                    "playbackStatus": "retryable", "attempts": 1,
                    "paths": {"direct": {"status": "verified"}}}
        self.assertTrue(should_queue_candidate(previous, "source", "embed", retry=True,
                                               retry_promising=True))
        previous["paths"]["direct"]["status"] = "retryable"
        self.assertFalse(should_queue_candidate(previous, "source", "embed", retry=True,
                                                retry_promising=True))
        previous["paths"]["direct"]["status"] = "media_reachable"
        self.assertTrue(should_queue_candidate(previous, "source", "embed", retry=True,
                                               retry_promising=True))
        previous["attempts"] = 3
        self.assertFalse(should_queue_candidate(previous, "source", "embed", retry=True,
                                                retry_promising=True))

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
        self.assertEqual(classify_observation({"httpStatus": 200, "videoCount": 1,
                          "duration": 0, "mediaStatus": 206,
                          "events": ["timeupdate"]}), "verified")

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


class BoundedBrowserRequestTests(unittest.IsolatedAsyncioTestCase):
    async def test_source_route_applies_media_limit_and_range(self):
        handler = bounded_route_handler(source_embed_host="bysejikuar.com")
        routes = []
        for _ in range(3):
            route = Mock(request=Mock(url="https://bysejikuar.com/startup.mp4", resource_type="media", headers={}),
                         continue_=AsyncMock(), abort=AsyncMock())
            await handler(route)
            routes.append(route)
        routes[0].continue_.assert_awaited_once_with(headers={"range": "bytes=0-262143"})
        routes[1].continue_.assert_awaited_once()
        routes[2].abort.assert_awaited_once()
        routes[2].continue_.assert_not_awaited()

    async def test_extensionless_media_is_bounded_and_images_are_skipped(self):
        handler = bounded_route_handler()
        media = Mock(request=Mock(url="https://cdn.example/start", resource_type="media", headers={}),
                     continue_=AsyncMock(), abort=AsyncMock())
        await handler(media)
        media.continue_.assert_awaited_once_with(headers={"range": "bytes=0-262143"})
        image = Mock(request=Mock(url="https://javryo.com/poster.jpg", resource_type="image"),
                     continue_=AsyncMock(), abort=AsyncMock())
        await handler(image)
        image.abort.assert_awaited_once()
