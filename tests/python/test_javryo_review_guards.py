import json
import subprocess
import unittest
from unittest.mock import AsyncMock, Mock, PropertyMock

from src.giga_catalog import javryo_embeds_browser as browser


class SourceAndEventGuardTests(unittest.TestCase):
    def test_page_console_flood_is_suppressed_without_removing_event_listeners(self):
        program = """
let messages = 0;
const listeners = {};
global.window = {console:{log:()=>messages++,debug:()=>messages++,warn:()=>messages++}};
global.document = {addEventListener:(name,callback)=>listeners[name]=callback};
""" + browser.EVENT_SCRIPT + """
for (let i=0; i<100000; i++) {window.console.log({i});window.console.debug(i);}
window.console.warn('diagnostic');
process.stdout.write(JSON.stringify({messages,listeners:Object.keys(listeners).sort()}));
"""
        result = subprocess.run(["node"], input=program, text=True, capture_output=True, check=True)
        self.assertEqual(json.loads(result.stdout),
                         {"messages": 0, "listeners": ["click", "playing", "timeupdate"]})

    def test_catalog_link_proof_requires_a_trusted_anchor_click(self):
        program = """
global.window = {};
global.HTMLVideoElement = class {};
const listeners = {};
global.document = {addEventListener:(name,callback)=>listeners[name]=callback};
""" + browser.EVENT_SCRIPT + """
const anchor = {href:'https://bysejikuar.com/e/example'};
const target = {closest:selector=>selector==='a[href]'?anchor:null};
listeners.click({isTrusted:false,target});
const before = window.__gigaLastClickedHref || null;
listeners.click({isTrusted:true,target});
console.log(JSON.stringify([before,window.__gigaLastClickedHref || null]));
"""
        result = subprocess.run(["node"], input=program, text=True, capture_output=True, check=True)
        self.assertEqual(json.loads(result.stdout), [None, "https://bysejikuar.com/e/example"])

    def test_internal_byse_frame_requires_exact_host_path_and_same_file_id(self):
        target = "https://bysejikuar.com/e/7p4h1pwsjiaw"
        valid = "https://n1mwq.org/dw3/7p4h1pwsjiaw"
        self.assertTrue(browser.internal_player_matches(valid, target))
        self.assertTrue(browser.internal_player_matches(valid.replace("/dw3/", "/zzab/"), target))
        for url in (valid + "?sig=example", valid.replace("n1mwq.org", "n1mwq.org.evil"),
                    valid.replace("7p4h1pwsjiaw", "advert"), valid.replace("https:", "http:"),
                    valid.replace("/dw3/", "/ad/")):
            self.assertFalse(browser.internal_player_matches(url, target))

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
    async def test_popup_document_status_is_kept_before_its_frame_is_available(self):
        target = "https://streamtape.com/v/example"
        context = Mock(_gigaDocumentStatusesByUrl={})
        response = Mock(url=target, status=404, request=Mock(resource_type="document"))
        type(response).frame = PropertyMock(side_effect=RuntimeError("popup frame pending"))
        browser.remember_document_status(response, context)
        page = Mock(url=target, context=context, _gigaFrameStatuses={}, _gigaDocumentStatus=None)
        evidence = await browser._observe(page, target, source_page=False, timeout_ms=5, navigate=False)
        self.assertEqual(evidence["httpStatus"], 404)

    async def test_existing_watch_uses_observed_http_status_instead_of_assuming_200(self):
        target = "https://streamtape.com/v/example"
        page = Mock(url=target, _gigaFrameStatuses={}, _gigaDocumentStatus=404)
        evidence = await browser._observe(page, target, source_page=False, timeout_ms=5, navigate=False)
        self.assertEqual(evidence["httpStatus"], 404)
        self.assertEqual(browser.classify_observation(evidence), "dead")

    async def test_only_real_primary_shell_can_select_internal_player(self):
        target = "https://bysejikuar.com/e/example"
        main = Mock(url=target)
        element = Mock(evaluate=AsyncMock(return_value=False))
        child = Mock(url="https://n1mwq.org/dw3/example", parent_frame=main,
                     frame_element=AsyncMock(return_value=element))
        page = Mock(main_frame=main, frames=[main, child])
        self.assertIs(await browser.intended_player_frame(page, target), main)
        element.evaluate.return_value = True
        self.assertIs(await browser.intended_player_frame(page, target), child)
        child.parent_frame = Mock()
        self.assertIs(await browser.intended_player_frame(page, target), main)

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
