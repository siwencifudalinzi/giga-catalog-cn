import unittest

from src.giga_catalog.javryo import (
    apply_overlay_to_catalog,
    build_manifest_entries,
    extract_movie_code,
    extract_movie_page,
    extract_sitemap_urls,
    extract_wrapper_target,
    streamtape_page_is_live,
)


class JavryoParsingTests(unittest.TestCase):
    def test_overlay_adds_page_without_overwriting_existing_links(self) -> None:
        catalog = {
            "series": [{"videos": [{
                "code": "SPSF-72",
                "links": {"streamtape": "https://ouo.io/original"},
            }, {
                "code": "SPSF-73",
            }]}],
            "totals": {"videos": 1, "linkedVideos": 1},
            "refresh": {
                "counts": {"linked": 1, "linkAdded": 0, "linkUpdated": 0},
            },
        }
        changed = apply_overlay_to_catalog(catalog, {
            "entries": {
                "SPSF-72": {
                    "pageUrl": "https://javryo.com/movies/spsf-72-sample/",
                    "streamtapeUrl": "https://streamtape.com/v/abc/SPSF-72.mp4",
                    "status": "streamtape_verified",
                    "checkedAt": "2026-09-27T00:00:00Z",
                },
                "SPSF-73": {
                    "pageUrl": "https://javryo.com/movies/spsf-73-sample/",
                    "streamtapeUrl": "",
                    "status": "matched",
                    "checkedAt": "2026-09-27T00:00:00Z",
                },
            }
        })
        self.assertEqual(changed, 2)
        self.assertEqual(
            catalog["series"][0]["videos"][0]["links"],
            {
                "streamtape": "https://ouo.io/original",
                "javryo": "https://javryo.com/movies/spsf-72-sample/",
            },
        )
        self.assertEqual(catalog["totals"]["linkedVideos"], 2)
        self.assertEqual(
            catalog["refresh"]["counts"],
            {"linked": 2, "linkAdded": 1, "linkUpdated": 1},
        )

    def test_manifest_entry_upgrades_only_verified_streamtape(self) -> None:
        entries = build_manifest_entries({
            "entries": {
                "SPSF-72": {
                    "pageUrl": "https://javryo.com/movies/spsf-72-sample/",
                    "streamtapeUrl": "https://streamtape.com/v/abc/SPSF-72.mp4",
                    "status": "streamtape_verified",
                    "checkedAt": "2026-09-27T00:00:00Z",
                },
                "SPSF-71": {
                    "pageUrl": "https://javryo.com/movies/spsf-71-sample/",
                    "streamtapeUrl": "",
                    "status": "matched",
                    "checkedAt": "2026-09-27T00:00:00Z",
                },
            }
        })
        self.assertEqual(list(entries), ["SPSF-72"])
        self.assertEqual(entries["SPSF-72"]["standard.javryo"]["provider"], "streamtape")

    def test_extracts_only_movie_urls_from_sitemap(self) -> None:
        xml = """<?xml version="1.0"?>
        <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
          <url><loc>https://javryo.com/movies/spsf-72-title/</loc></url>
          <url><loc>https://javryo.com/category/giga/</loc></url>
        </urlset>"""
        self.assertEqual(
            extract_sitemap_urls(xml),
            ["https://javryo.com/movies/spsf-72-title/"],
        )

    def test_extracts_and_normalizes_code_from_movie_url(self) -> None:
        self.assertEqual(
            extract_movie_code("https://javryo.com/movies/akbd-012-heroine-battle/"),
            "AKBD-12",
        )
        self.assertIsNone(
            extract_movie_code("https://evil.example/movies/akbd-12-title/")
        )

    def test_extracts_movie_metadata_and_wrapper_links(self) -> None:
        html = """
        <html><head><meta property="og:title" content="SPSF-72 Sample"></head>
        <body class="postid-21835"><div data-post="21835"></div>
        <a href="https://javryo.com/links/first/">Download</a>
        <a href="/links/second/">Mirror</a></body></html>
        """
        self.assertEqual(
            extract_movie_page(html),
            {
                "title": "SPSF-72 Sample",
                "postId": 21835,
                "wrapperUrls": [
                    "https://javryo.com/links/first/",
                    "https://javryo.com/links/second/",
                ],
            },
        )

    def test_extracts_encoded_wrapper_target(self) -> None:
        html = (
            '<a id="link" href="http://ouo.io/qs/id?'
            's=https%3A%2F%2Fstreamtape.com%2Fv%2Fabc_1%2FSPSF-72.mp4">go</a>'
        )
        self.assertEqual(
            extract_wrapper_target(html),
            "https://streamtape.com/v/abc_1/SPSF-72.mp4",
        )

    def test_streamtape_requires_watch_page_without_deleted_marker(self) -> None:
        live = "<html><title>SPSF-72.mp4 at Streamtape.com</title><video></video></html>"
        self.assertTrue(
            streamtape_page_is_live(
                "https://streamtape.com/v/abc_1/SPSF-72.mp4", 200, live
            )
        )
        self.assertFalse(
            streamtape_page_is_live(
                "https://streamtape.com/v/abc_1/SPSF-72.mp4",
                200,
                "Video not found",
            )
        )
        self.assertFalse(
            streamtape_page_is_live(
                "https://streamtape.com/get_video?id=x", 200, live
            )
        )


if __name__ == "__main__":
    unittest.main()
