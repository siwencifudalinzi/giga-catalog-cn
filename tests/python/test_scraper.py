import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import requests

from src.giga_catalog.scraper import (
    create_session,
    decode_product_html,
    discover_products,
    inspect_search_page,
    parse_product_page,
    parse_search_page,
    _extract_code,
    _response_html,
)


FIXTURES = Path(__file__).parents[1] / "fixtures"


class GigaParserTests(unittest.TestCase):
    def test_decodes_declared_utf8_and_legacy_cp932_without_replacement(self) -> None:
        """A wrong decoder would corrupt Japanese catalog text or ignore a declaration."""
        self.assertEqual(
            decode_product_html("日本語タイトル".encode("utf-8"), "UTF-8"),
            "日本語タイトル",
        )
        self.assertEqual(
            decode_product_html("日本語タイトル".encode("cp932")),
            "日本語タイトル",
        )
        self.assertEqual(decode_product_html(b"\x82\xa0", "cp932"), "あ")

    def test_parses_only_the_matching_product_cover_and_metadata_container(self) -> None:
        """A global selector would choose the related-product cover instead of the product cover."""
        product = parse_product_page(
            (FIXTURES / "product_spsf.html").read_text(encoding="utf-8"), 8123
        )

        self.assertEqual(
            product,
            {
                "productId": 8123,
                "code": "SPSF-44",
                "series": "SPSF",
                "number": 44,
                "title": "魔法美少女 特別編",
                "actors": ["女優 一", "女優 二"],
                "releaseDate": "2026-07-01",
                "cover": "https://www.giga-web.jp/db_titles/spsf/pac_s.jpg",
                "previewBase": "https://www.giga-web.jp/db_titles/spsf/sample/",
                "previewCount": 18,
            },
        )

    def test_rejects_a_page_without_the_required_product_containers(self) -> None:
        """A successful non-product response must not become a catalog record."""
        self.assertIsNone(parse_product_page("<html><body>top page</body></html>", 8123))

    def test_parses_cards_and_ignores_sidebar_product_links(self) -> None:
        """A whole-page link scrape would turn sidebar and review links into products."""
        products = parse_search_page(
            (FIXTURES / "search_page.html").read_text(encoding="utf-8")
        )

        self.assertEqual(
            products,
            [
                {
                    "productId": 8123,
                    "code": "SPSF-44",
                    "series": "SPSF",
                    "number": 44,
                    "title": "検索結果タイトル 一",
                    "actors": ["女優 一", "女優 二"],
                    "releaseDate": "2026-07-01",
                    "cover": "https://www.giga-web.jp/db_titles/spsf/pac_s.jpg",
                    "previewBase": "https://www.giga-web.jp/db_titles/spsf/sample/",
                    "previewCount": 18,
                },
                {
                    "productId": 8124,
                    "code": "G1-7",
                    "series": "G1",
                    "number": 7,
                    "title": "検索結果タイトル 二",
                    "actors": ["女優 三"],
                    "releaseDate": "2026-07-02",
                    "cover": "https://www.giga-web.jp/db_titles/g1/pac_s.jpg",
                    "previewBase": "https://www.giga-web.jp/db_titles/g1/sample/",
                    "previewCount": 18,
                },
            ],
        )

    def test_parses_current_link_title_and_plain_release_date_markup(self) -> None:
        """Requiring legacy h5/dl markup would make a live directory page look empty."""
        products = parse_search_page(
            """
            <div class="col s6 m4 l3 center thumBox">
              <div class="pac_thum_box">
                <a href="/product/index.php?product_id=7738">
                  <img src="/db_titles/spsf/current/pac_s.jpg" />
                </a>
              </div>
              <a href="/product/index.php?product_id=7738">
                <span style="font-weight:bold;">現在の検索結果 Ep_1.2.0 タイトル</span>
              </a>
              （SPSF-38）<br />
              <!-- 監督 :
                <a href="supervisor.php?supervisor_id=52">監督 名</a><br />
                出演 :
                <a href="../search/index.php?actor_id=4014">女優 一</a>
                <a href="../search/index.php?actor_id=3575">女優 二</a>
              -->
              DVDリリース日 2026-08-14<br />
            </div>
            """
        )

        self.assertEqual(
            products,
            [
                {
                    "productId": 7738,
                    "code": "SPSF-38",
                    "series": "SPSF",
                    "number": 38,
                    "title": "現在の検索結果 Ep_1.2.0 タイトル",
                    "actors": ["女優 一", "女優 二"],
                    "releaseDate": "2026-08-14",
                    "cover": "https://www.giga-web.jp/db_titles/spsf/current/pac_s.jpg",
                    "previewBase": (
                        "https://www.giga-web.jp/db_titles/spsf/current/sample/"
                    ),
                    "previewCount": 18,
                }
            ],
        )

    def test_parses_official_zero_suffix_card_and_detail_as_one_canonical_product(self) -> None:
        """Rejecting official ``00`` codes leaves a complete live product unreconciled."""
        expected = {
            "productId": 7390,
            "code": "THZA-0",
            "series": "THZA",
            "number": 0,
            "title": "Zero suffix product",
            "actors": ["Actor Zero"],
            "releaseDate": "2025-01-24",
            "cover": "https://www.giga-web.jp/db_titles/thza/thza00_example/pac_s.jpg",
            "previewBase": "https://www.giga-web.jp/db_titles/thza/thza00_example/sample/",
            "previewCount": 18,
        }

        search_records = parse_search_page(
            (FIXTURES / "search_thza_zero.html").read_text(encoding="utf-8")
        )
        detail_record = parse_product_page(
            (FIXTURES / "product_thza_zero.html").read_text(encoding="utf-8"),
            7390,
        )

        self.assertEqual(search_records, [expected])
        self.assertEqual(detail_record, expected)

    def test_parses_one_letter_source_variant_without_widening_its_boundary(self) -> None:
        """A legacy official variant is source evidence, not an arbitrary suffix grammar."""
        expected = {
            "productId": 2021,
            "code": "YNO-3B",
            "series": "YNO",
            "number": 3,
            "title": "Legacy letter variant",
            "actors": [],
            "releaseDate": "2004-05-22",
            "cover": "https://www.giga-web.jp/db_titles/yno/yno03b/pac_s.jpg",
            "previewBase": "https://www.giga-web.jp/db_titles/yno/yno03b/sample/",
            "previewCount": 18,
        }

        self.assertEqual(_extract_code("（YNO-03b）"), "YNO-3B")
        for invalid in ("YNO-03bc", "YNO-03-beta", "YNO-b03", "YNO-03BETA"):
            with self.subTest(invalid=invalid):
                self.assertIsNone(_extract_code(invalid))
        self.assertEqual(
            parse_search_page(
                (FIXTURES / "search_yno_letter_variant.html").read_text(
                    encoding="utf-8"
                )
            ),
            [expected],
        )
        self.assertEqual(
            parse_product_page(
                (FIXTURES / "product_yno_letter_variant.html").read_text(
                    encoding="utf-8"
                ),
                2021,
            ),
            expected,
        )

    def test_product_page_uses_the_exact_contiguous_sample_count(self) -> None:
        """Detail-page sample markup should override the conservative legacy count."""
        html = (
            (FIXTURES / "product_spsf.html")
            .read_text(encoding="utf-8")
            .replace(
                "</body>",
                """
                <a href="/db_titles/spsf/sample/001_l.jpg"></a>
                <img src="/db_titles/spsf/sample/001_l.jpg">
                <img src="/db_titles/spsf/sample/002_l.jpg">
                <img data-src="/db_titles/spsf/sample/003_l.jpg">
                <img src="/db_titles/spsf/sample/005_l.jpg">
                <img src="/db_titles/other/sample/004_l.jpg">
                </body>
                """,
            )
        )

        product = parse_product_page(html, 8123)

        self.assertEqual(product["previewCount"], 3)
        self.assertEqual(
            product["previewBase"],
            "https://www.giga-web.jp/db_titles/spsf/sample/",
        )

    def test_inspection_accounts_for_every_card_without_changing_legacy_parser(self) -> None:
        """Silently dropping malformed or duplicate cards would make a full audit incomplete."""
        valid = IncrementalDiscoveryTests._directory_page(1)
        malformed_with_id = (
            '<div class="thumBox"><a href="/product/index.php?product_id=2">'
            "missing fields</a></div>"
        )
        missing_id = '<div class="thumBox"><span>missing id</span></div>'
        html = valid + malformed_with_id + missing_id + valid

        inspection = inspect_search_page(html)

        self.assertEqual(inspection.total_cards, 4)
        self.assertEqual(inspection.parsed_cards, 2)
        self.assertEqual(inspection.unresolved_product_ids, [2])
        self.assertEqual(inspection.unidentifiable_cards, 1)
        self.assertEqual(inspection.duplicate_product_ids, [1])
        self.assertEqual(
            [record["productId"] for record in parse_search_page(html)],
            [1],
        )


class _Response:
    def __init__(
        self,
        html: str = "",
        status_code: int = 200,
        url: str = "https://www.giga-web.jp/search/index.php",
        headers: dict = None,
        content: bytes = None,
    ) -> None:
        self.content = html.encode("utf-8") if content is None else content
        self.status_code = status_code
        self.url = url
        self.headers = headers or {"Content-Type": "text/html; charset=UTF-8"}


class _GateSession:
    def __init__(self) -> None:
        self.headers = {}
        self.cookies = {"old_check": "yes"}
        self.calls = []

    def get(self, url: str, **kwargs: object) -> _Response:
        self.calls.append((url, kwargs))
        return _Response(status_code=302)


class _SequencedGateSession(_GateSession):
    def __init__(self, responses: list[object]) -> None:
        super().__init__()
        self.cookies = {}
        self.responses = responses

    def get(self, url: str, **kwargs: object) -> _Response:
        self.calls.append((url, kwargs))
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        if response.status_code == 302:
            self.cookies["old_check"] = "yes"
        return response


class ResponseDecodingTests(unittest.TestCase):
    def test_uses_meta_charset_when_http_content_type_has_no_charset(self) -> None:
        """Ignoring a meta declaration would decode a headerless response with the wrong codec."""
        response = _Response(
            headers={"Content-Type": "text/html"},
            content=(
                b'<html><head><meta charset="windows-1252"></head>'
                b'<body>\x93title\x94</body></html>'
            ),
        )

        self.assertIn("\u201ctitle\u201d", _response_html(response))

    def test_uses_an_unquoted_meta_charset_when_http_content_type_has_no_charset(self) -> None:
        """A valid unquoted charset value must not be lost while reading the bounded prefix."""
        response = _Response(
            headers={"Content-Type": "text/html"},
            content=b'<meta charset=windows-1252><p>\x93title\x94</p>',
        )

        self.assertIn("\u201ctitle\u201d", _response_html(response))

    def test_uses_http_equiv_meta_content_type_when_header_has_no_charset(self) -> None:
        """Only parsing the short meta form would miss legacy pages' encoding declaration."""
        response = _Response(
            headers={"Content-Type": "text/html"},
            content=(
                b'<meta http-equiv="Content-Type" '
                b'content="text/html; charset=windows-1252"><p>\x93title\x94</p>'
            ),
        )

        self.assertIn("\u201ctitle\u201d", _response_html(response))

    def test_http_charset_wins_over_a_conflicting_meta_charset(self) -> None:
        """Letting meta override HTTP would corrupt responses whose transport declares UTF-8."""
        response = _Response(
            headers={"Content-Type": "text/html; charset=UTF-8"},
            content=b'<meta charset="windows-1252"><p>caf\xc3\xa9</p>',
        )

        self.assertIn("caf\u00e9", _response_html(response))


class IncrementalDiscoveryTests(unittest.TestCase):
    @staticmethod
    def _directory_page(*product_ids: int) -> str:
        return "".join(
            f"""
            <div class="thumBox">
              <a href="/product/index.php?product_id={product_id}"><img src="/db_titles/{product_id}/pac_s.jpg" /></a>
              <p>作品番号 SPSF-{product_id}</p><h5>Title {product_id}</h5>
              <!-- <span class="yaku"><a href="/search/?actress={product_id}">Actor {product_id}</a></span> -->
              <dl><dt>DVDリリース日</dt><dd>2026/07/{product_id:02d}</dd></dl>
            </div>
            """
            for product_id in product_ids
        )

    @staticmethod
    def _directory_fetch(pages: dict):
        def fetch(url: str) -> _Response:
            page = int(parse_qs(urlparse(url).query)["count"][0])
            return _Response(pages.get(page, ""), url=url)

        return fetch

    def test_create_session_visits_the_gate_and_keeps_the_established_cookie(self) -> None:
        """Skipping the gate would leave product requests outside the normal browser flow."""
        fake_session = _GateSession()
        with patch("src.giga_catalog.scraper.requests.Session", return_value=fake_session):
            session = create_session()

        self.assertIs(session, fake_session)
        self.assertEqual(session.headers["Accept-Language"], "ja,en;q=0.5")
        self.assertEqual(
            session.calls,
            [
                (
                    "https://www.giga-web.jp/cookie_set.php",
                    {
                        "headers": {"Referer": "https://www.giga-web.jp/"},
                        "allow_redirects": False,
                        "timeout": 20,
                    },
                )
            ],
        )

    def test_create_session_honors_base_url_and_timeout(self) -> None:
        """CLI network flags must configure the real gate request rather than be decorative."""
        fake_session = _GateSession()
        with patch("src.giga_catalog.scraper.requests.Session", return_value=fake_session):
            create_session(base_url="https://catalog.test/root", timeout=7)

        self.assertEqual(fake_session.calls[0][0], "https://catalog.test/root/cookie_set.php")
        self.assertEqual(fake_session.calls[0][1]["timeout"], 7)
        self.assertEqual(
            fake_session.calls[0][1]["headers"]["Referer"],
            "https://catalog.test/root/",
        )

    def test_create_session_retries_transient_gate_failures_then_stops_on_cookie(self) -> None:
        """A transient gate outage must not abort discovery or trigger requests after success."""
        fake_session = _SequencedGateSession(
            [requests.ConnectionError("temporary"), _Response(status_code=503), _Response(status_code=302)]
        )
        with (
            patch("src.giga_catalog.scraper.requests.Session", return_value=fake_session),
            patch("src.giga_catalog.scraper.time.sleep") as sleep,
        ):
            session = create_session(timeout=7, retries=4, delay_seconds=0.25)

        self.assertIs(session, fake_session)
        self.assertEqual(len(fake_session.calls), 3)
        self.assertEqual([call.args for call in sleep.call_args_list], [(0.25,), (0.5,)])
        self.assertEqual(len(fake_session.responses), 0)

    def test_create_session_exhaustion_is_bounded_and_explicit(self) -> None:
        """Retry exhaustion must identify the age gate instead of leaking an ambiguous response."""
        fake_session = _SequencedGateSession([_Response(status_code=503)] * 3)
        with (
            patch("src.giga_catalog.scraper.requests.Session", return_value=fake_session),
            patch("src.giga_catalog.scraper.time.sleep") as sleep,
            self.assertRaisesRegex(RuntimeError, "age-gate.*3 attempts"),
        ):
            create_session(retries=3, delay_seconds=0.25)

        self.assertEqual(len(fake_session.calls), 3)
        self.assertEqual([call.args for call in sleep.call_args_list], [(0.25,), (0.5,)])

    def test_create_session_does_not_retry_status_600(self) -> None:
        """Only real 5xx responses are transient; an invalid 600 response is terminal."""
        fake_session = _SequencedGateSession(
            [_Response(status_code=600), _Response(status_code=302)]
        )
        with (
            patch("src.giga_catalog.scraper.requests.Session", return_value=fake_session),
            patch("src.giga_catalog.scraper.time.sleep") as sleep,
            self.assertRaisesRegex(RuntimeError, "http_600"),
        ):
            create_session(retries=3, delay_seconds=0.25)

        self.assertEqual(len(fake_session.calls), 1)
        self.assertEqual(len(fake_session.responses), 1)
        sleep.assert_not_called()

    def test_discovery_honors_base_url_retry_limit_and_known_audit_records(self) -> None:
        """Audit needs a complete record set and caller-controlled retry behavior."""
        calls = []

        def fetch(url: str) -> _Response:
            calls.append(url)
            if len(calls) == 1:
                return _Response(status_code=503)
            return _Response(self._directory_page(1), url=url)

        discovered, summary = discover_products(
            [{"productId": 1}],
            mode="audit",
            page_limit=1,
            delay_seconds=0,
            fetch=fetch,
            base_url="https://catalog.test/root",
            retries=2,
            include_known=True,
        )

        self.assertEqual(len(calls), 2)
        self.assertTrue(
            calls[0].startswith("https://catalog.test/root/search/index.php?")
        )
        self.assertEqual([record["productId"] for record in discovered], [1])
        self.assertEqual(summary["retries"], 1)

    def test_discovery_threads_retry_policy_into_live_age_gate(self) -> None:
        """CLI retry settings must protect session initialization as well as later pages."""
        fake_session = _GateSession()
        with patch(
            "src.giga_catalog.scraper.create_session", return_value=fake_session
        ) as gate:
            discover_products(
                [],
                mode="audit",
                page_limit=0,
                timeout=9,
                retries=5,
                delay_seconds=0.125,
            )

        gate.assert_called_once_with(
            base_url="https://www.giga-web.jp",
            timeout=9,
            retries=5,
            delay_seconds=0.125,
        )

    def test_incremental_stops_after_two_consecutive_all_known_pages(self) -> None:
        """Removing the two-known-page stop would keep a routine refresh crawling history."""
        discovered, summary = discover_products(
            [{"productId": 1}, {"productId": 2}],
            delay_seconds=0,
            fetch=self._directory_fetch(
                {
                    1: self._directory_page(3),
                    2: self._directory_page(1),
                    3: self._directory_page(2),
                    4: self._directory_page(4),
                }
            ),
        )

        self.assertEqual([product["productId"] for product in discovered], [3])
        self.assertEqual(
            summary,
            {
                "mode": "incremental",
                "pagesFetched": 3,
                "parsedProducts": 3,
                "newProducts": 1,
                "knownProducts": 2,
                "cursor": 3,
                "retries": 0,
                "errors": 0,
                "stopReason": "all_known",
                "cardsSeen": 3,
                "cardsResolved": 3,
                "detailFallbacks": 0,
                "cardIntegrityComplete": True,
                "pageReconciliation": [
                    {"page": 1, "cards": 1, "resolved": 1},
                    {"page": 2, "cards": 1, "resolved": 1},
                    {"page": 3, "cards": 1, "resolved": 1},
                ],
                "diagnostics": [],
            },
        )

    def test_audit_continues_over_known_pages_until_the_empty_terminal_page(self) -> None:
        """Applying incremental early-stop logic to audit mode would miss its terminal check."""
        discovered, summary = discover_products(
            [{"productId": 1}, {"productId": 2}],
            mode="audit",
            delay_seconds=0,
            fetch=self._directory_fetch(
                {
                    1: self._directory_page(1),
                    2: self._directory_page(2),
                    3: (FIXTURES / "search_empty.html").read_text(encoding="utf-8"),
                }
            ),
        )

        self.assertEqual(discovered, [])
        self.assertEqual(summary["pagesFetched"], 3)
        self.assertEqual(summary["knownProducts"], 2)
        self.assertEqual(summary["cursor"], 3)
        self.assertEqual(summary["stopReason"], "empty")

    def test_zero_suffix_card_reconciles_one_to_one_without_skipping_or_fallback(self) -> None:
        """A real zero-suffix card must parse, not be ignored or excused from accounting."""
        directory = (FIXTURES / "search_thza_zero.html").read_text(encoding="utf-8")
        detail = (FIXTURES / "product_thza_zero.html").read_text(encoding="utf-8")
        urls = []

        def fetch(url: str) -> _Response:
            urls.append(url)
            return _Response(detail if "/product/" in url else directory, url=url)

        discovered, summary = discover_products(
            [], mode="audit", page_limit=1, delay_seconds=0, fetch=fetch
        )

        self.assertEqual([record["code"] for record in discovered], ["THZA-0"])
        self.assertEqual(summary["pageReconciliation"], [{"page": 1, "cards": 1, "resolved": 1}])
        self.assertEqual(summary["cardsSeen"], 1)
        self.assertEqual(summary["cardsResolved"], 1)
        self.assertEqual(summary["detailFallbacks"], 0)
        self.assertTrue(summary["cardIntegrityComplete"])
        self.assertEqual(summary["errors"], 0)
        self.assertEqual(summary["diagnostics"], [])
        self.assertEqual([url for url in urls if "/product/" in url], [])

    def test_letter_variant_reconciles_before_an_authoritative_empty_terminal_page(self) -> None:
        """An old variant remains a resolved product card even when refresh later filters it."""
        directory = (FIXTURES / "search_yno_letter_variant.html").read_text(
            encoding="utf-8"
        )
        detail = (FIXTURES / "product_yno_letter_variant.html").read_text(
            encoding="utf-8"
        )

        def fetch(url: str) -> _Response:
            if "/product/" in url:
                return _Response(detail, url=url)
            page = int(parse_qs(urlparse(url).query)["count"][0])
            return _Response({1: directory, 2: ""}.get(page, ""), url=url)

        discovered, summary = discover_products(
            [],
            mode="audit",
            delay_seconds=0,
            fetch=fetch,
        )

        self.assertEqual([record["code"] for record in discovered], ["YNO-3B"])
        self.assertEqual(summary["pagesFetched"], 2)
        self.assertEqual(summary["stopReason"], "empty")
        self.assertEqual(summary["cardsSeen"], 1)
        self.assertEqual(summary["cardsResolved"], 1)
        self.assertEqual(
            summary["pageReconciliation"],
            [
                {"page": 1, "cards": 1, "resolved": 1},
                {"page": 2, "cards": 0, "resolved": 0},
            ],
        )
        self.assertTrue(summary["cardIntegrityComplete"])
        self.assertEqual(summary["errors"], 0)
        self.assertEqual(summary["diagnostics"], [])

    def test_malformed_card_is_recovered_from_its_detail_page(self) -> None:
        """A recoverable official card must be reconciled rather than silently omitted."""
        malformed = (
            '<div class="thumBox"><a href="/product/index.php?product_id=8123">'
            "missing fields</a></div>"
        )
        detail = (FIXTURES / "product_spsf.html").read_text(encoding="utf-8")
        urls = []

        def fetch(url: str) -> _Response:
            urls.append(url)
            if "/product/" in url:
                return _Response(detail, url=url)
            return _Response(malformed, url=url)

        discovered, summary = discover_products(
            [], mode="audit", page_limit=1, delay_seconds=0, fetch=fetch
        )

        self.assertEqual([record["productId"] for record in discovered], [8123])
        self.assertEqual(len([url for url in urls if "/product/" in url]), 1)
        self.assertEqual(summary["cardsSeen"], 1)
        self.assertEqual(summary["cardsResolved"], 1)
        self.assertEqual(summary["detailFallbacks"], 1)
        self.assertEqual(summary["parsedProducts"], 1)
        self.assertEqual(summary["errors"], 0)

    def test_failed_detail_fallback_stops_directory_with_explicit_diagnostic(self) -> None:
        """An unavailable detail page must fail closed after the configured retry bound."""
        malformed = (
            '<div class="thumBox"><a href="/product/index.php?product_id=8123">'
            "missing fields</a></div>"
        )
        detail_calls = 0

        def fetch(url: str) -> _Response:
            nonlocal detail_calls
            if "/product/" in url:
                detail_calls += 1
                return _Response(status_code=503, url=url)
            return _Response(malformed, url=url)

        discovered, summary = discover_products(
            [],
            mode="audit",
            page_limit=1,
            delay_seconds=0,
            retries=2,
            fetch=fetch,
        )

        self.assertEqual(discovered, [])
        self.assertEqual(detail_calls, 2)
        self.assertEqual(summary["stopReason"], "error")
        self.assertEqual(summary["errors"], 1)
        self.assertEqual(summary["cardsSeen"], 1)
        self.assertEqual(summary["cardsResolved"], 0)
        self.assertIn(
            {"type": "product_detail_unresolved", "page": 1, "productId": 8123},
            summary["diagnostics"],
        )

    def test_unidentifiable_card_stops_directory_instead_of_looking_empty(self) -> None:
        """A thumBox with no product ID can never support an authoritative audit."""
        discovered, summary = discover_products(
            [],
            mode="audit",
            page_limit=1,
            delay_seconds=0,
            fetch=lambda url: _Response('<div class="thumBox">broken</div>', url=url),
        )

        self.assertEqual(discovered, [])
        self.assertEqual(summary["stopReason"], "error")
        self.assertEqual(summary["cardsSeen"], 1)
        self.assertEqual(summary["cardsResolved"], 0)
        self.assertIn(
            {"type": "unidentifiable_cards", "page": 1, "count": 1},
            summary["diagnostics"],
        )

    def test_duplicate_product_id_breaks_unique_card_reconciliation(self) -> None:
        """Two official cards cannot be reconciled to one unique product record."""
        duplicate_page = self._directory_page(1, 1)
        discovered, summary = discover_products(
            [],
            mode="audit",
            page_limit=1,
            delay_seconds=0,
            fetch=lambda url: _Response(duplicate_page, url=url),
        )

        self.assertEqual(discovered, [])
        self.assertEqual(summary["stopReason"], "error")
        self.assertEqual(summary["cardsSeen"], 2)
        self.assertEqual(summary["cardsResolved"], 1)
        self.assertIn(
            {"type": "duplicate_product_id", "page": 1, "productId": 1},
            summary["diagnostics"],
        )

    def test_card_count_matches_unique_resolved_records_on_each_page(self) -> None:
        """Fallback records must participate in the same per-page card reconciliation."""
        malformed = (
            '<div class="thumBox"><a href="/product/index.php?product_id=8123">'
            "missing fields</a></div>"
        )
        directory = self._directory_page(1, 2) + malformed
        detail = (FIXTURES / "product_spsf.html").read_text(encoding="utf-8")

        def fetch(url: str) -> _Response:
            return _Response(detail if "/product/" in url else directory, url=url)

        discovered, summary = discover_products(
            [], mode="audit", page_limit=1, delay_seconds=0, fetch=fetch
        )

        self.assertEqual(len(discovered), 3)
        self.assertEqual(summary["cardsSeen"], 3)
        self.assertEqual(summary["cardsResolved"], 3)
        self.assertEqual(summary["pageReconciliation"], [{"page": 1, "cards": 3, "resolved": 3}])

    def test_retries_transient_directory_errors_without_treating_them_as_empty(self) -> None:
        """Classifying a connection failure as an empty page would truncate an audit."""
        responses = [
            requests.ConnectionError("temporary"),
            _Response(self._directory_page(3)),
            _Response(""),
        ]

        def fetch(url: str) -> _Response:
            next_response = responses.pop(0)
            if isinstance(next_response, BaseException):
                raise next_response
            return next_response

        discovered, summary = discover_products(
            [], mode="audit", delay_seconds=0, fetch=fetch
        )

        self.assertEqual([product["productId"] for product in discovered], [3])
        self.assertEqual(summary["pagesFetched"], 2)
        self.assertEqual(summary["retries"], 1)
        self.assertEqual(summary["errors"], 0)
        self.assertEqual(summary["stopReason"], "empty")

    def test_directory_408_retries_then_returns_an_explicit_error(self) -> None:
        """Treating an exhausted timeout as an empty page would silently truncate refreshes."""
        responses = [_Response(status_code=408) for _ in range(3)]

        def fetch(url: str) -> _Response:
            return responses.pop(0)

        _, summary = discover_products(
            [], mode="audit", page_limit=1, delay_seconds=0, fetch=fetch
        )

        self.assertEqual(summary["stopReason"], "error")
        self.assertEqual(summary["errors"], 1)
        self.assertEqual(summary["retries"], 2)
        self.assertEqual(summary.get("error"), "http_408_retries_exhausted")
        self.assertEqual(len(responses), 0)

    def test_directory_425_retries_then_returns_an_explicit_error(self) -> None:
        """A too-early response is transient and must not be treated as one failed page."""
        responses = [_Response(status_code=425) for _ in range(3)]

        def fetch(url: str) -> _Response:
            return responses.pop(0)

        _, summary = discover_products(
            [], mode="audit", page_limit=1, delay_seconds=0, fetch=fetch
        )

        self.assertEqual(summary["stopReason"], "error")
        self.assertEqual(summary["errors"], 1)
        self.assertEqual(summary["retries"], 2)
        self.assertEqual(summary.get("error"), "http_425_retries_exhausted")
        self.assertEqual(len(responses), 0)

    def test_directory_403_is_an_explicit_error_not_an_empty_terminal_page(self) -> None:
        """An access denial must not look like the normal empty audit terminal."""
        _, summary = discover_products(
            [],
            mode="audit",
            page_limit=1,
            delay_seconds=0,
            fetch=lambda url: _Response(status_code=403),
        )

        self.assertEqual(summary["stopReason"], "error")
        self.assertEqual(summary["errors"], 1)
        self.assertEqual(summary.get("error"), "http_403")

    def test_directory_does_not_retry_status_600(self) -> None:
        """The retry boundary ends at 599 instead of treating every larger status as 5xx."""
        calls = 0

        def fetch(url: str) -> _Response:
            nonlocal calls
            calls += 1
            return _Response(status_code=600)

        _, summary = discover_products(
            [],
            mode="audit",
            page_limit=1,
            delay_seconds=0,
            retries=3,
            fetch=fetch,
        )

        self.assertEqual(calls, 1)
        self.assertEqual(summary["retries"], 0)
        self.assertEqual(summary["stopReason"], "error")
        self.assertEqual(summary.get("error"), "http_600")

    def test_directory_top_redirect_is_an_explicit_error_not_an_empty_terminal_page(self) -> None:
        """An expired age-gate redirect must not be accepted as a valid directory response."""
        _, summary = discover_products(
            [],
            mode="audit",
            page_limit=1,
            delay_seconds=0,
            fetch=lambda url: _Response(status_code=302, headers={"Location": "/top.php"}),
        )

        self.assertEqual(summary["stopReason"], "error")
        self.assertEqual(summary["errors"], 1)
        self.assertEqual(summary.get("error"), "redirect_to_top")

    def test_retry_delay_grows_exponentially_for_transient_responses(self) -> None:
        """Using one fixed retry wait would keep hammering a rate-limited directory."""
        responses = [
            _Response(status_code=429),
            _Response(status_code=503),
            _Response(""),
        ]

        def fetch(url: str) -> _Response:
            return responses.pop(0)

        with patch("src.giga_catalog.scraper.time.sleep") as sleep:
            _, summary = discover_products(
                [], mode="audit", page_limit=1, delay_seconds=0.25, fetch=fetch
            )

        self.assertEqual(summary["retries"], 2)
        self.assertEqual(summary["errors"], 0)
        self.assertEqual(sleep.call_args_list[0].args, (0.25,))
        self.assertEqual(sleep.call_args_list[1].args, (0.5,))
        self.assertEqual(sleep.call_args_list[2].args, (1.0,))

    def test_tail_redirects_are_misses_but_network_errors_are_not(self) -> None:
        """Counting a transient failure as a miss would end sparse-ID probing before ID 13."""
        responses = {
            11: [_Response(status_code=302, headers={"Location": "/top.php"})],
            12: [
                requests.ConnectionError("temporary"),
                _Response(status_code=302, headers={"Location": "/top.php"}),
            ],
            13: [_Response(status_code=302, headers={"Location": "/top.php"})],
        }
        called_ids = []

        def fetch(url: str) -> _Response:
            product_id = int(parse_qs(urlparse(url).query)["product_id"][0])
            called_ids.append(product_id)
            next_response = responses[product_id].pop(0)
            if isinstance(next_response, BaseException):
                raise next_response
            return next_response

        discovered, summary = discover_products(
            [{"productId": 10}],
            mode="tail",
            page_limit=5,
            delay_seconds=0,
            fetch=fetch,
        )

        self.assertEqual(discovered, [])
        self.assertEqual(called_ids, [11, 12, 12, 13])
        self.assertEqual(summary["tailProbes"], 3)
        self.assertEqual(summary["tailMisses"], 3)
        self.assertEqual(summary["retries"], 1)
        self.assertEqual(summary["errors"], 0)
        self.assertEqual(summary["cursor"], 13)
        self.assertEqual(summary["stopReason"], "three_misses")

    def test_tail_network_failure_breaks_the_consecutive_miss_streak(self) -> None:
        """A failed request between redirects must not satisfy the three-consecutive-miss rule."""
        responses = {
            11: [_Response(status_code=302, headers={"Location": "/top.php"})],
            12: [_Response(status_code=302, headers={"Location": "/top.php"})],
            13: [
                requests.ConnectionError("temporary"),
                requests.ConnectionError("temporary"),
                requests.ConnectionError("temporary"),
            ],
            14: [_Response(status_code=302, headers={"Location": "/top.php"})],
            15: [_Response(status_code=302, headers={"Location": "/top.php"})],
            16: [_Response(status_code=302, headers={"Location": "/top.php"})],
        }
        called_ids = []

        def fetch(url: str) -> _Response:
            product_id = int(parse_qs(urlparse(url).query)["product_id"][0])
            called_ids.append(product_id)
            next_response = responses[product_id].pop(0)
            if isinstance(next_response, BaseException):
                raise next_response
            return next_response

        _, summary = discover_products(
            [{"productId": 10}],
            mode="tail",
            page_limit=7,
            delay_seconds=0,
            fetch=fetch,
        )

        self.assertEqual(called_ids, [11, 12, 13, 13, 13, 14, 15, 16])
        self.assertEqual(summary["errors"], 1)
        self.assertEqual(summary["tailMisses"], 3)
        self.assertEqual(summary["cursor"], 16)
        self.assertEqual(summary["stopReason"], "three_misses")


if __name__ == "__main__":
    unittest.main()
