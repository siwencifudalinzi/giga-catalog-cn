import unittest

from src.giga_catalog.javryo_streams import collect_embed_candidates, normalize_embed_url


class EmbedCandidateTests(unittest.TestCase):
    def test_exact_https_hosts_and_paths(self):
        samples = (
            "https://bysejikuar.com/e/7p4h1pwsjiaw",
            "https://ryonanation.icu/v/mxwqxu5d7lr2-kr",
            "https://ryonads.icu/e/bja9bx010jqk.html",
            "https://short.icu/uVwI2uRFo",
            "https://dood.la/e/xcf7nn7vcyf1s6i9mq4cq4nfq9ew1srp",
            "https://player.mogulstream.icu/v/8zAn1Uqn8QkPBOf",
            "https://movearnpre.com/embed/1zv457ir60ax",
            "https://javryo.embed4me.com/#rte69",
        )
        for url in samples:
            with self.subTest(url=url):
                self.assertEqual(normalize_embed_url(url), url)

    def test_rejects_lookalikes_credentials_ports_queries_and_media(self):
        for url in (
            "https://bysejikuar.come/8w2h1taaje3f",
            "https://evil.bysejikuar.com/e/id",
            "https://user:pass@bysejikuar.com/e/id",
            "https://bysejikuar.com:8443/e/id",
            "http://bysejikuar.com/e/id",
            "https://bysejikuar.com/get/id",
            "https://bysejikuar.com/e/id?token=secret",
            "https://short.icu/id?token=x",
            "https://ryonads.icu/e/id.m3u8",
            "https://javryo.embed4me.com/#bad-value",
            "https://myvidplay.com/e/15m18xirm8ld",
            "https://vidhideplus.com/embed/vjmdjhgu2q67",
            "blob:https://bysejikuar.com/id",
        ):
            with self.subTest(url=url):
                self.assertIsNone(normalize_embed_url(url))

    def test_optional_short_poster_is_removed_from_public_url(self):
        self.assertEqual(normalize_embed_url("https://short.icu/id?image=https://javryo.com/poster.jpg"),
                         "https://short.icu/id")

    def test_mapping_mismatch_cannot_become_ready(self):
        page = "https://javryo.com/movies/athb-16-sample/"
        crawl = {"results": {"ATHB-16": {"code": "ATHB-16", "postId": 18159, "pageUrl": page}}}
        audit = {"results": {"ATHB-16": {"status": "found", "postId": 21833,
                  "embedUrl": "https://bysejikuar.com/e/abc"}}}
        candidate = collect_embed_candidates(crawl, audit)[0]
        self.assertEqual(candidate.status, "unsupported")
        self.assertIsNone(candidate.embed_url)
