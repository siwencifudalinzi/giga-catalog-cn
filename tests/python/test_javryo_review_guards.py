import json
import subprocess
import unittest
from unittest.mock import AsyncMock, Mock

from src.giga_catalog import javryo_embeds_browser as browser


class SourceAndEventGuardTests(unittest.TestCase):
    def test_script_rejects_untrusted_non_video_and_paused_events(self):
        program = """
const listeners = {};
global.window = {};
global.HTMLVideoElement = class {
  constructor() { this.paused = false; this.currentTime = 1; }
  pause() { this.paused = true; }
};
const video = new HTMLVideoElement();
global.document = {addEventListener: (name, cb) => listeners[name] = cb,
                   querySelectorAll: () => [video]};
""" + browser.EVENT_SCRIPT + """
listeners.timeupdate({isTrusted:false,target:{}});
listeners.timeupdate({isTrusted:true,target:{}});
listeners.playing({isTrusted:false,target:video});
video.paused = true;
listeners.timeupdate({isTrusted:true,target:video});
const rejected = video.__gigaPlaybackEvents || window.__gigaPlaybackEvents || [];
video.paused = false;
listeners.playing({isTrusted:true,target:video});
console.log(JSON.stringify({rejected:[...rejected], accepted:video.__gigaPlaybackEvents || []}));
"""
        result = subprocess.run(["node"], input=program, text=True, capture_output=True, check=True)
        evidence = json.loads(result.stdout)
        self.assertEqual(evidence["rejected"], [])
        self.assertEqual(evidence["accepted"], ["playing"])

    def test_source_api_success_cannot_override_failed_iframe(self):
        for status, expected in ((403, "blocked"), (404, "dead"), (None, "retryable")):
            evidence = {"httpStatus": 200, "apiStatus": 200, "embedStatus": status,
                        "embedFrameSeen": True, "sourceClickObserved": True}
            self.assertEqual(browser.classify_source_observation(evidence), expected)
        evidence["embedStatus"] = 200
        self.assertEqual(browser.classify_source_observation(evidence), "reached")
        evidence["sourceClickObserved"] = False
        self.assertEqual(browser.classify_source_observation(evidence), "retryable")


class IntendedPlayerGuardTests(unittest.IsolatedAsyncioTestCase):
    async def test_foreign_ad_frame_cannot_verify_empty_player(self):
        target = "https://bysejikuar.com/e/example"
        locator = Mock(count=AsyncMock(return_value=0))
        main = Mock(url=target, parent_frame=None, locator=Mock(return_value=locator),
                    evaluate=AsyncMock(return_value={"count": 0, "duration": 0, "events": [], "text": ""}))
        ad = Mock(url="https://advert.example/", parent_frame=main, locator=Mock(return_value=locator),
                  evaluate=AsyncMock(return_value={"count": 1, "duration": 60, "events": ["playing"], "text": ""}))
        listeners = {}
        page = Mock(url=target, frames=[main, ad], main_frame=main,
                    wait_for_timeout=AsyncMock(), remove_listener=Mock())
        page.on = lambda name, callback: listeners.update({name: callback})
        async def goto(*args, **kwargs):
            await listeners["response"](Mock(url="https://advert.example/ad.mp4", status=206,
                headers={"content-type": "video/mp4"}, frame=ad, request=Mock(resource_type="media")))
            return Mock(status=200)
        page.goto = goto
        evidence = await browser._observe(page, target, source_page=False, timeout_ms=5)
        self.assertNotEqual(browser.classify_observation(evidence), "verified")
        ad.evaluate.assert_not_awaited()
