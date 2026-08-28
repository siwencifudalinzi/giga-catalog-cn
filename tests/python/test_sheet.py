import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import requests

from src.giga_catalog import sheet as sheet_module
from src.giga_catalog.sheet import (
    SheetFormatError,
    download_sheet,
    parse_sheet_csv,
)


FIXTURE_PATH = Path(__file__).parents[1] / "fixtures" / "sheet.csv"


class SheetParserTests(unittest.TestCase):
    def test_maps_vidara_replacement_header_without_losing_uncensored_player4me(self) -> None:
        """The live sheet may replace only the normal Player4me column with Vidara."""
        text = (
            "NEW CODE,STREAMTAPE LINK,VIDARA LINK,GOFILE LINK,UNCENSORED,"
            "STREAMTAPE LINK,PLAYER4ME LINK,GOFILE LINK\n"
            "SPSF-61,https://ouo.io/a,https://ouo.io/vidara,https://ouo.io/c,,"
            "https://ouo.io/d,https://ouo.io/player4me,https://ouo.io/f\n"
        )

        links, conflicts = parse_sheet_csv(text)

        self.assertEqual(
            links["SPSF-61"],
            {
                "streamtape": "https://ouo.io/a",
                "vidara": "https://ouo.io/vidara",
                "gofile": "https://ouo.io/c",
                "uncensored": {
                    "streamtape": "https://ouo.io/d",
                    "player4me": "https://ouo.io/player4me",
                    "gofile": "https://ouo.io/f",
                },
            },
        )
        self.assertEqual(conflicts, [])

    def test_maps_normal_and_uncensored_link_columns(self) -> None:
        """The repeated provider headers belong to separate normal and uncensored groups."""
        links, conflicts = parse_sheet_csv(FIXTURE_PATH.read_text(encoding="utf-8"))

        self.assertEqual(
            links["SPSF-44"],
            {
                "streamtape": "https://ouo.io/later",
                "player4me": "https://ouo.io/b",
                "gofile": "https://ouo.io/c",
                "uncensored": {
                    "streamtape": "https://ouo.io/d",
                    "player4me": "https://ouo.io/e",
                    "gofile": "https://ouo.io/f",
                },
            },
        )
        self.assertEqual(
            links["SPSF-45"],
            {
                "player4me": "https://links.example/p4",
                "uncensored": {"streamtape": "https://links.example/uncensored"},
            },
        )
        self.assertEqual(
            conflicts,
            [
                {
                    "type": "invalid_url",
                    "code": "SPSF-45",
                    "provider": "streamtape",
                    "url": "not a url",
                },
                {
                    "type": "invalid_url",
                    "code": "SPSF-45",
                    "provider": "gofile",
                    "url": "ftp://links.example/file",
                },
                {
                    "type": "duplicate_code",
                    "code": "SPSF-44",
                },
                {
                    "type": "conflict",
                    "code": "SPSF-44",
                    "provider": "streamtape",
                    "existing": "https://ouo.io/a",
                    "incoming": "https://ouo.io/later",
                },
            ],
        )

    def test_ignores_blank_and_explanatory_rows(self) -> None:
        """Rows without a valid product code must not create entries or diagnostics."""
        text = (
            "NEW CODE,STREAMTAPE LINK,PLAYER4ME LINK,GOFILE LINK,UNCENSORED,"
            "STREAMTAPE LINK,PLAYER4ME LINK,GOFILE LINK\n"
            ",,,,,,,\n"
            "This sheet is updated weekly,,,,,,,\n"
        )

        self.assertEqual(parse_sheet_csv(text), ({}, []))

    def test_rejects_urls_with_whitespace_instead_of_importing_them(self) -> None:
        """A URL parser must not accept a syntactically broken HTTP host."""
        text = (
            "NEW CODE,STREAMTAPE LINK,PLAYER4ME LINK,GOFILE LINK,UNCENSORED,"
            "STREAMTAPE LINK,PLAYER4ME LINK,GOFILE LINK\n"
            "SPSF-1,https://exa mple.test/file,,,,,,\n"
        )

        links, diagnostics = parse_sheet_csv(text)

        self.assertEqual(links, {"SPSF-1": {}})
        self.assertEqual(diagnostics[0]["type"], "invalid_url")

    def test_rejects_empty_or_structurally_changed_sheet_headers(self) -> None:
        """A changed upstream schema must fail closed instead of looking link-free."""
        malformed_headers = (
            "",
            "NEW CODE,STREAMTAPE LINK,PLAYER4ME LINK,GOFILE LINK\n",
            (
                "UNCENSORED,STREAMTAPE LINK,PLAYER4ME LINK,GOFILE LINK,"
                "NEW CODE,STREAMTAPE LINK,PLAYER4ME LINK,GOFILE LINK\n"
            ),
            (
                "NEW CODE,STREAMTAPE LINK,PLAYER4ME LINK,UNCENSORED,"
                "STREAMTAPE LINK,PLAYER4ME LINK,GOFILE LINK\n"
            ),
            (
                "NEW CODE,STREAMTAPE LINK,PLAYER4ME LINK,GOFILE LINK,"
                "UNCENSORED,STREAMTAPE LINK,PLAYER4ME LINK\n"
            ),
            (
                "NEW CODE,STREAMTAPE LINK,STREAMTAPE LINK,PLAYER4ME LINK,"
                "GOFILE LINK,UNCENSORED,STREAMTAPE LINK,PLAYER4ME LINK,"
                "GOFILE LINK\n"
            ),
        )

        for text in malformed_headers:
            with self.subTest(text=text), self.assertRaises(SheetFormatError):
                parse_sheet_csv(text)


class DownloadSheetTests(unittest.TestCase):
    @staticmethod
    def _response(status: int, text: str = "NEW CODE\nSPSF-44\n") -> requests.Response:
        response = requests.Response()
        response.status_code = status
        response.url = "https://sheet.test/export.csv"
        response._content = text.encode("utf-8")
        response.encoding = "utf-8"
        return response

    @staticmethod
    def _stop_server(server: ThreadingHTTPServer, thread: threading.Thread) -> None:
        server.shutdown()
        thread.join()
        server.server_close()

    def _start_server(self, status: int = 200, delay: float = 0) -> str:
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
                if delay:
                    time.sleep(delay)
                body = b"NEW CODE\nSPSF-44\n"
                self.send_response(status)
                self.send_header("Content-Type", "text/csv; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                try:
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionAbortedError):
                    pass

            def log_message(self, format: str, *args: object) -> None:
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self._stop_server, server, thread)
        return f"http://127.0.0.1:{server.server_port}/sheet.csv"

    def test_downloads_csv_from_a_local_http_source(self) -> None:
        """The downloader returns decoded CSV without requiring public-network access in tests."""
        self.assertEqual(download_sheet(self._start_server(), timeout=1), "NEW CODE\nSPSF-44\n")

    def test_binary_download_is_bounded_and_preserves_exact_bytes(self) -> None:
        """A workbook download must retain ZIP bytes and reject oversized payloads."""
        downloader = getattr(sheet_module, "download_sheet_bytes", None)
        self.assertTrue(callable(downloader), "the bounded binary downloader is missing")
        response = self._response(200, text="PK-workbook")

        with patch(
            "src.giga_catalog.sheet.requests.get",
            return_value=response,
        ):
            self.assertEqual(
                downloader(
                    "https://sheet.test/export.xlsx",
                    timeout=3,
                    retries=1,
                    max_bytes=len(response.content),
                ),
                response.content,
            )
            with self.assertRaisesRegex(ValueError, "maximum size"):
                downloader(
                    "https://sheet.test/export.xlsx",
                    timeout=3,
                    retries=1,
                    max_bytes=len(response.content) - 1,
                )

    def test_forwards_timeout_to_a_slow_local_source(self) -> None:
        """A caller's short timeout must interrupt a slow CSV response."""
        with self.assertRaises(requests.Timeout):
            download_sheet(
                self._start_server(delay=0.1), timeout=0.01, retries=1
            )

    def test_raises_for_an_http_error_response(self) -> None:
        """A failing HTTP response must not be returned as a CSV payload."""
        with self.assertRaises(requests.HTTPError):
            download_sheet(self._start_server(status=500), timeout=1, retries=1)

    def test_retries_network_and_transient_http_failures_with_exponential_backoff(self) -> None:
        """Temporary Sheet failures should recover within one caller-controlled bound."""
        responses = [
            requests.ConnectionError("temporary"),
            self._response(429),
            self._response(200),
        ]

        def get(url: str, **kwargs: object) -> requests.Response:
            response = responses.pop(0)
            if isinstance(response, BaseException):
                raise response
            return response

        with (
            patch("src.giga_catalog.sheet.requests.get", side_effect=get) as request,
            patch("src.giga_catalog.sheet.time.sleep") as sleep,
        ):
            text = download_sheet(
                "https://sheet.test/export.csv",
                timeout=7,
                retries=4,
                delay_seconds=0.2,
            )

        self.assertEqual(text, "NEW CODE\nSPSF-44\n")
        self.assertEqual(request.call_count, 3)
        self.assertEqual([call.args for call in sleep.call_args_list], [(0.2,), (0.4,)])
        self.assertEqual(responses, [])

    def test_non_transient_client_error_fails_without_retry(self) -> None:
        """Retrying a permanent 4xx would waste the bound and obscure the source response."""
        with (
            patch(
                "src.giga_catalog.sheet.requests.get",
                return_value=self._response(404),
            ) as request,
            patch("src.giga_catalog.sheet.time.sleep") as sleep,
            self.assertRaises(requests.HTTPError),
        ):
            download_sheet(
                "https://sheet.test/export.csv",
                retries=5,
                delay_seconds=0.2,
            )

        self.assertEqual(request.call_count, 1)
        sleep.assert_not_called()

    def test_status_600_fails_without_retry(self) -> None:
        """Only 500 through 599 are transient, and an invalid 600 response is not success."""
        with (
            patch(
                "src.giga_catalog.sheet.requests.get",
                return_value=self._response(600),
            ) as request,
            patch("src.giga_catalog.sheet.time.sleep") as sleep,
            self.assertRaisesRegex(requests.HTTPError, "600"),
        ):
            download_sheet(
                "https://sheet.test/export.csv",
                retries=3,
                delay_seconds=0.2,
            )

        self.assertEqual(request.call_count, 1)
        sleep.assert_not_called()

    def test_timeout_and_too_early_statuses_are_retried(self) -> None:
        """HTTP 408 and 425 are temporary source responses, unlike permanent 4xx errors."""
        for status in (408, 425):
            with self.subTest(status=status):
                responses = [self._response(status), self._response(200)]
                with (
                    patch(
                        "src.giga_catalog.sheet.requests.get",
                        side_effect=responses,
                    ) as request,
                    patch("src.giga_catalog.sheet.time.sleep") as sleep,
                ):
                    text = download_sheet(
                        "https://sheet.test/export.csv",
                        retries=2,
                        delay_seconds=0.2,
                    )

                self.assertEqual(text, "NEW CODE\nSPSF-44\n")
                self.assertEqual(request.call_count, 2)
                self.assertEqual([call.args for call in sleep.call_args_list], [(0.2,)])

    def test_transient_http_exhaustion_is_bounded_and_preserves_status(self) -> None:
        """A terminal Sheet outage must stop exactly at the configured attempt count."""
        with (
            patch(
                "src.giga_catalog.sheet.requests.get",
                return_value=self._response(503),
            ) as request,
            patch("src.giga_catalog.sheet.time.sleep") as sleep,
            self.assertRaisesRegex(requests.HTTPError, "503"),
        ):
            download_sheet(
                "https://sheet.test/export.csv",
                retries=3,
                delay_seconds=0.2,
            )

        self.assertEqual(request.call_count, 3)
        self.assertEqual([call.args for call in sleep.call_args_list], [(0.2,), (0.4,)])


if __name__ == "__main__":
    unittest.main()
