import unittest
from unittest.mock import AsyncMock, Mock, patch

from src.giga_catalog.javryo_streamtape_browser import matching_wrapper, verify_streamtape_candidate


class StreamtapeSourceTests(unittest.TestCase):
    def test_wrapper_must_match_current_streamtape_destination(self):
        crawl = {"targets": [
            {"target": "https://streamtape.com/v/old/file.mp4", "wrapperUrl": "https://javryo.com/links/old/"},
            {"target": "https://streamtape.com/v/new/file.mp4", "wrapperUrl": "https://javryo.com/links/new/"},
        ]}
        self.assertEqual(matching_wrapper(crawl, "https://streamtape.com/v/new/file.mp4"),
                         "https://javryo.com/links/new/")
        self.assertIsNone(matching_wrapper(crawl, "https://streamtape.com/v/other/file.mp4"))


class MissingWrapperTests(unittest.IsolatedAsyncioTestCase):
    async def test_direct_path_is_checked_when_source_wrapper_is_missing(self):
        page = Mock(goto=AsyncMock(return_value=Mock(status=200)))
        context = Mock(add_init_script=AsyncMock(), route=AsyncMock(),
                       new_page=AsyncMock(return_value=page), close=AsyncMock())
        browser = Mock(new_context=AsyncMock(return_value=context))
        with patch("src.giga_catalog.javryo_streamtape_browser._observe",
                   new=AsyncMock(return_value={"httpStatus": 403})) as observe:
            row = await verify_streamtape_candidate(browser,
                page_url="https://javryo.com/movies/athb-16-example/",
                final_url="https://streamtape.com/v/example", wrapper_url=None)
        observe.assert_awaited_once()
        self.assertEqual(row["paths"]["source"]["status"], "retryable")
        self.assertEqual(row["paths"]["direct"]["status"], "blocked")
        self.assertEqual(row["playbackStatus"], "blocked")
