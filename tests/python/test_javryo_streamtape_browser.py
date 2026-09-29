import unittest

from src.giga_catalog.javryo_streamtape_browser import matching_wrapper


class StreamtapeSourceTests(unittest.TestCase):
    def test_wrapper_must_match_current_streamtape_destination(self):
        crawl = {"targets": [
            {"target": "https://streamtape.com/v/old/file.mp4", "wrapperUrl": "https://javryo.com/links/old/"},
            {"target": "https://streamtape.com/v/new/file.mp4", "wrapperUrl": "https://javryo.com/links/new/"},
        ]}
        self.assertEqual(matching_wrapper(crawl, "https://streamtape.com/v/new/file.mp4"),
                         "https://javryo.com/links/new/")
        self.assertIsNone(matching_wrapper(crawl, "https://streamtape.com/v/other/file.mp4"))
