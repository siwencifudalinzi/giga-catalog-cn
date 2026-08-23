import copy
import json
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

try:
    from src.giga_catalog import release
except ImportError:
    release = None


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SOURCE_COMMIT = "a" * 40
SITE_ID = "78c2aad4-65e1-4203-b0be-ce3a6bfdd244"
PRODUCTION_URL = "https://giga-catalog-cn.netlify.app"
FILE_HASHES = {
    "data/catalog.json": (
        "e5f1eb4d806641698a35efe20e098efd20d7d57a9b90ee69079d5bb650920726"
    ),
    "index.html": (
        "9a3e246041d3c27dc3645f79cb0d1eb41c277965614655d17119ed7498b956ec"
    ),
    "js/app.js": (
        "f6734488a34d37403338512c3f4be5840996926268bb0b4a63b7220e81f6dbb5"
    ),
}
PUBLIC_SHA256 = (
    "9abea1a53d461af383276a5049808d15d728a56888d586b5fcdff9c21aa3100f"
)
NETLIFY_SHA256 = (
    "b6e54df8efa6da1f1f9ab07e59a6651f201ad68f0c008048ec349167f89c161c"
)
SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; base-uri 'self'; object-src 'none'; "
        "frame-ancestors 'none'; script-src 'self'; style-src 'self'; "
        "img-src 'self' https: data:; connect-src 'self'"
    ),
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(), geolocation=(), microphone=()",
}


def production_headers(cache_control):
    return {**SECURITY_HEADERS, "Cache-Control": cache_control}


class ReleaseTestCase(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(
            release,
            "src.giga_catalog.release must implement the release behavior",
        )
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.public = self.root / "public"
        (self.public / "data").mkdir(parents=True)
        (self.public / "js").mkdir()
        (self.public / "index.html").write_bytes(b"home\n")
        (self.public / "data" / "catalog.json").write_bytes(b'{"ok":true}\n')
        (self.public / "js" / "app.js").write_bytes(b'console.log("ok")\n')
        self.manifest_path = (
            self.public / "giga-release.json"
        )
        self.manifest_path.write_text("stale manifest", encoding="utf-8")
        self.netlify = self.root / "netlify.toml"
        self.netlify.write_bytes(b'[build]\npublish="public"\n')

    def tearDown(self):
        if hasattr(self, "temporary"):
            self.temporary.cleanup()

    def build_manifest(self, source_commit=SOURCE_COMMIT):
        return release.build_manifest(
            self.public,
            source_commit,
            self.netlify,
        )


class ManifestTests(ReleaseTestCase):
    def test_full_public_hash_is_deterministic_and_excludes_release_manifest(self):
        (self.public / ".nojekyll").write_bytes(b"")
        first = self.build_manifest()
        self.manifest_path.write_text("different stale content", encoding="utf-8")
        second = self.build_manifest()

        self.assertEqual(first, second)
        self.assertEqual(first["files"], FILE_HASHES)
        self.assertEqual(first["publicSha256"], PUBLIC_SHA256)
        self.assertEqual(first["catalogSha256"], FILE_HASHES["data/catalog.json"])
        self.assertEqual(first["netlifyTomlSha256"], NETLIFY_SHA256)
        self.assertEqual(first["sourceCommit"], SOURCE_COMMIT)
        self.assertEqual(first["schemaVersion"], 1)
        self.assertNotIn("giga-release.json", first["files"])
        self.assertNotIn(".nojekyll", first["files"])

    def test_manifest_writer_emits_compact_utf8_and_validates_local_files(self):
        manifest = release.write_manifest(
            self.public,
            SOURCE_COMMIT,
            self.netlify,
        )

        raw = self.manifest_path.read_bytes()
        self.assertTrue(raw.endswith(b"\n"))
        self.assertNotIn(b": ", raw)
        self.assertEqual(json.loads(raw.decode("utf-8")), manifest)
        self.assertEqual(
            release.validate_local_release(
                release.parse_manifest(raw),
                self.public,
                self.netlify,
            ),
            manifest,
        )

    def test_parser_rejects_unsafe_duplicate_and_inconsistent_manifests(self):
        valid = self.build_manifest()
        cases = {}

        value = copy.deepcopy(valid)
        value["schemaVersion"] = 2
        cases["unexpected schema"] = json.dumps(value)

        value = copy.deepcopy(valid)
        value["schemaVersion"] = True
        cases["boolean schema"] = json.dumps(value)

        value = copy.deepcopy(valid)
        value["files"]["../index.html"] = value["files"].pop("index.html")
        cases["traversal"] = json.dumps(value)

        value = copy.deepcopy(valid)
        value["files"]["index.html"] = "A" * 64
        cases["malformed hash"] = json.dumps(value)

        value = copy.deepcopy(valid)
        value["catalogSha256"] = "0" * 64
        cases["catalog mismatch"] = json.dumps(value)

        value = copy.deepcopy(valid)
        value["publicSha256"] = "0" * 64
        cases["aggregate mismatch"] = json.dumps(value)

        compact = json.dumps(valid, separators=(",", ":"))
        needle = '"index.html":"' + FILE_HASHES["index.html"] + '"'
        cases["duplicate path"] = compact.replace(
            needle,
            needle + "," + needle,
        )

        for label, raw in cases.items():
            with self.subTest(label=label):
                with self.assertRaises(release.ReleaseError):
                    release.parse_manifest(raw.encode("utf-8"))

    def test_local_validation_rejects_missing_changed_and_unlisted_files(self):
        manifest = self.build_manifest()

        (self.public / "js" / "app.js").write_bytes(b"changed")
        with self.assertRaisesRegex(release.ReleaseError, "js/app.js"):
            release.validate_local_release(manifest, self.public, self.netlify)

        (self.public / "js" / "app.js").write_bytes(b'console.log("ok")\n')
        (self.public / "extra.txt").write_bytes(b"extra")
        with self.assertRaisesRegex(release.ReleaseError, "file set"):
            release.validate_local_release(manifest, self.public, self.netlify)

        (self.public / "extra.txt").unlink()
        self.netlify.write_bytes(b"different")
        with self.assertRaisesRegex(release.ReleaseError, "netlify.toml"):
            release.validate_local_release(manifest, self.public, self.netlify)

    def test_content_identity_ignores_source_commit_but_exact_identity_does_not(self):
        local = self.build_manifest()
        live = copy.deepcopy(local)
        live["sourceCommit"] = "b" * 40

        self.assertTrue(release.content_identity_matches(local, live))
        self.assertFalse(release.exact_identity_matches(local, live))


class _FixtureHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        fixture = self.server.fixture
        fixture.requests.append(
            {
                "path": self.path,
                "cache_control": self.headers.get("Cache-Control"),
                "pragma": self.headers.get("Pragma"),
            }
        )
        route = self.path.split("?", 1)[0]
        responses = fixture.routes.get(route, [(404, b"missing")])
        if len(responses) > 1:
            response = responses.pop(0)
        else:
            response = responses[0]
        if callable(response):
            response(self)
            return
        if len(response) == 2:
            status, body = response
            headers = {}
        else:
            status, body, headers = response
        self.send_response(status)
        header_items = (
            list(headers.items())
            if isinstance(headers, dict)
            else list(headers)
        )
        if not any(key.lower() == "content-length" for key, _ in header_items):
            self.send_header("Content-Length", str(len(body)))
        for key, value in header_items:
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        return


class HttpFixture:
    def __init__(self, routes):
        self.routes = {
            path: list(responses)
            for path, responses in routes.items()
        }
        self.requests = []
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _FixtureHandler)
        self.server.daemon_threads = True
        self.server.fixture = self
        self.thread = threading.Thread(
            target=self.server.serve_forever,
            daemon=True,
        )

    def __enter__(self):
        self.thread.start()
        host, port = self.server.server_address
        self.base_url = f"http://{host}:{port}"
        return self

    def __exit__(self, *args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


class LiveReleaseTests(ReleaseTestCase):
    def manifest_bytes(self, manifest=None):
        value = manifest if manifest is not None else self.build_manifest()
        return (
            json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")

    def full_routes(self, manifest=None):
        value = manifest if manifest is not None else self.build_manifest()
        return {
            "/giga-release.json": [
                (
                    200,
                    self.manifest_bytes(value),
                    production_headers("no-store"),
                ),
            ],
            "/": [
                (
                    200,
                    b"home\n",
                    production_headers("public, max-age=0, must-revalidate"),
                )
            ],
            "/index.html": [
                (
                    200,
                    b"home\n",
                    production_headers("public, max-age=0, must-revalidate"),
                )
            ],
            "/data/catalog.json": [
                (
                    200,
                    b'{"ok":true}\n',
                    production_headers("public, max-age=300, must-revalidate"),
                )
            ],
            "/data/featured-covers.json": [
                (
                    200,
                    b'{"covers":[]}\n',
                    production_headers("public, max-age=300, must-revalidate"),
                )
            ],
            "/js/app.js": [
                (
                    200,
                    b'console.log("ok")\n',
                    production_headers("public, max-age=3600, must-revalidate"),
                )
            ],
            "/css/style.css": [
                (
                    200,
                    b"body {}\n",
                    production_headers("public, max-age=3600, must-revalidate"),
                )
            ],
            "/js/__giga_release_probe_missing__.js": [(404, b"missing")],
            "/data/raw/products.json": [(404, b"missing")],
            "/scripts/refresh.py": [(404, b"missing")],
            "/tests/python/test_refresh.py": [(404, b"missing")],
        }

    @staticmethod
    def replace_headers(routes, path, transform):
        status, body, headers = routes[path][0]
        routes[path] = [(status, body, transform(dict(headers)))]

    def test_precheck_retries_malformed_json_then_matches_content_identity(self):
        live = self.build_manifest(source_commit="b" * 40)
        routes = self.full_routes(live)
        routes["/giga-release.json"] = [
            (200, b"{malformed"),
            (200, self.manifest_bytes(live)),
        ]
        with HttpFixture(routes) as fixture:
            result = release.compare_live_release(
                self.build_manifest(),
                fixture.base_url,
                attempts=2,
                timeout=1,
                delay=0,
            )

        self.assertEqual(result.state, "matching")
        self.assertEqual(result.attempts, 2)
        manifest_requests = [
            request
            for request in fixture.requests
            if request["path"].split("?", 1)[0]
            == "/giga-release.json"
        ]
        self.assertEqual(len(manifest_requests), 2)
        for request in fixture.requests:
            self.assertIn("no-cache", request["cache_control"])
            self.assertEqual(request["pragma"], "no-cache")
            self.assertIn("?verify=", request["path"])

    def test_precheck_rejects_matching_manifest_with_corrupt_public_file(self):
        routes = self.full_routes()
        routes["/js/app.js"] = [(200, b"corrupt")]
        with HttpFixture(routes) as fixture:
            result = release.compare_live_release(
                self.build_manifest(),
                fixture.base_url,
                attempts=2,
                timeout=1,
                delay=0,
            )

        self.assertEqual(result.state, "mismatching")
        self.assertEqual(result.attempts, 2)
        self.assertIn("js/app.js", result.detail)

    def test_precheck_rejects_matching_manifest_with_missing_public_file(self):
        routes = self.full_routes()
        routes["/js/app.js"] = [(404, b"missing")]
        with HttpFixture(routes) as fixture:
            result = release.compare_live_release(
                self.build_manifest(),
                fixture.base_url,
                attempts=2,
                timeout=1,
                delay=0,
            )

        self.assertEqual(result.state, "mismatching")
        self.assertEqual(result.attempts, 2)
        self.assertIn("js/app.js", result.detail)

    def test_precheck_rejects_matching_manifest_with_wrong_home_route(self):
        routes = self.full_routes()
        routes["/"] = [(200, b"wrong home")]
        with HttpFixture(routes) as fixture:
            result = release.compare_live_release(
                self.build_manifest(),
                fixture.base_url,
                attempts=2,
                timeout=1,
                delay=0,
            )

        self.assertEqual(result.state, "mismatching")
        self.assertEqual(result.attempts, 2)
        self.assertIn("home page", result.detail)

    def test_precheck_requires_original_missing_javascript_path_to_be_404(self):
        routes = self.full_routes()
        routes["/js/__giga_release_probe_missing__.js"] = [(200, b"home\n")]
        with HttpFixture(routes) as fixture:
            result = release.compare_live_release(
                self.build_manifest(),
                fixture.base_url,
                attempts=2,
                timeout=1,
                delay=0,
            )

        self.assertEqual(result.state, "mismatching")
        self.assertEqual(result.attempts, 2)
        self.assertIn("404", result.detail)

    def test_precheck_returns_mismatching_after_retryable_hash_mismatches(self):
        live = self.build_manifest()
        live["files"]["js/app.js"] = "0" * 64
        live["publicSha256"] = (
            "b6c8b243b01ef6d5673ee7911b7970ed2e83f2d22259149013823394481270af"
        )
        routes = {
            "/giga-release.json": [
                (200, self.manifest_bytes(live)),
            ]
        }
        with HttpFixture(routes) as fixture:
            result = release.compare_live_release(
                self.build_manifest(),
                fixture.base_url,
                attempts=3,
                timeout=1,
                delay=0,
            )

        self.assertEqual(result.state, "mismatching")
        self.assertEqual(result.attempts, 3)
        self.assertEqual(len(fixture.requests), 3)

    def test_precheck_returns_unavailable_after_http_and_network_failures(self):
        with HttpFixture(
            {"/giga-release.json": [(503, b"retry later")]}
        ) as fixture:
            http_result = release.compare_live_release(
                self.build_manifest(),
                fixture.base_url,
                attempts=2,
                timeout=1,
                delay=0,
            )

        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()
        network_result = release.compare_live_release(
            self.build_manifest(),
            f"http://127.0.0.1:{port}",
            attempts=2,
            timeout=0.2,
            delay=0,
        )

        self.assertEqual(http_result.state, "unavailable")
        self.assertEqual(http_result.attempts, 2)
        self.assertEqual(network_result.state, "unavailable")
        self.assertEqual(network_result.attempts, 2)

    def test_network_limits_have_named_long_term_defaults(self):
        self.assertEqual(release.MAX_MANIFEST_BYTES, 1024 * 1024)
        self.assertEqual(release.MAX_PUBLIC_FILE_BYTES, 32 * 1024 * 1024)
        self.assertEqual(release.MAX_RELEASE_BYTES, 128 * 1024 * 1024)
        self.assertGreater(release.PRECHECK_WALL_TIMEOUT, 0)
        self.assertGreater(release.VERIFY_WALL_TIMEOUT, 0)

    def test_truncated_manifest_is_retried_as_unavailable(self):
        routes = {
            "/giga-release.json": [
                (
                    200,
                    b'{"schemaVersion":',
                    {"Content-Length": "100"},
                ),
            ]
        }
        with HttpFixture(routes) as fixture:
            result = release.compare_live_release(
                self.build_manifest(),
                fixture.base_url,
                attempts=2,
                timeout=1,
                delay=0,
            )

        self.assertEqual(result.state, "unavailable")
        self.assertEqual(result.attempts, 2)
        self.assertIn("incomplete", result.detail.lower())

    def test_manifest_content_length_over_limit_is_retried_without_reading_body(self):
        def oversized(handler):
            handler.send_response(200)
            handler.send_header(
                "Content-Length",
                str(release.MAX_MANIFEST_BYTES + 1),
            )
            handler.end_headers()

        routes = {"/giga-release.json": [oversized]}
        with HttpFixture(routes) as fixture:
            result = release.compare_live_release(
                self.build_manifest(),
                fixture.base_url,
                attempts=2,
                timeout=1,
                delay=0,
            )

        self.assertEqual(result.state, "unavailable")
        self.assertEqual(result.attempts, 2)
        self.assertIn("Content-Length", result.detail)

    def test_chunked_public_file_over_single_file_limit_is_retried(self):
        def chunked(handler):
            handler.send_response(200)
            handler.send_header("Transfer-Encoding", "chunked")
            handler.end_headers()
            for chunk in (b"1234", b"5678", b"9"):
                handler.wfile.write(f"{len(chunk):X}\r\n".encode("ascii"))
                handler.wfile.write(chunk + b"\r\n")
                handler.wfile.flush()
            handler.wfile.write(b"0\r\n\r\n")

        limits = release.DownloadLimits(
            manifest_bytes=1024,
            file_bytes=8,
            total_bytes=4096,
            wall_seconds=2,
        )
        routes = self.full_routes()
        routes["/data/catalog.json"] = [chunked]
        with HttpFixture(routes) as fixture:
            result = release.compare_live_release(
                self.build_manifest(),
                fixture.base_url,
                attempts=2,
                timeout=1,
                delay=0,
                limits=limits,
            )

        self.assertEqual(result.state, "unavailable")
        self.assertEqual(result.attempts, 2)
        self.assertIn("file byte limit", result.detail)

    def test_precheck_enforces_total_download_limit_per_attempt(self):
        manifest_bytes = self.manifest_bytes()
        total_before_home = (
            len(manifest_bytes)
            + len(b'{"ok":true}\n')
            + len(b"home\n")
            + len(b'console.log("ok")\n')
        )
        limits = release.DownloadLimits(
            manifest_bytes=1024,
            file_bytes=1024,
            total_bytes=total_before_home,
            wall_seconds=2,
        )
        with HttpFixture(self.full_routes()) as fixture:
            result = release.compare_live_release(
                self.build_manifest(),
                fixture.base_url,
                attempts=2,
                timeout=1,
                delay=0,
                limits=limits,
            )

        self.assertEqual(result.state, "unavailable")
        self.assertEqual(result.attempts, 2)
        self.assertIn("total release byte limit", result.detail)

    def test_slow_small_chunks_cannot_extend_the_wall_clock_deadline(self):
        def slow(handler):
            body = b'{"ok":true}\n'
            handler.send_response(200)
            handler.send_header("Content-Length", str(len(body)))
            handler.end_headers()
            try:
                for byte in body:
                    handler.wfile.write(bytes([byte]))
                    handler.wfile.flush()
                    time.sleep(0.03)
            except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
                return

        limits = release.DownloadLimits(
            manifest_bytes=1024,
            file_bytes=1024,
            total_bytes=4096,
            wall_seconds=0.08,
        )
        routes = self.full_routes()
        routes["/data/catalog.json"] = [slow]
        started = time.monotonic()
        with HttpFixture(routes) as fixture:
            result = release.compare_live_release(
                self.build_manifest(),
                fixture.base_url,
                attempts=3,
                timeout=1,
                delay=0,
                limits=limits,
            )
            elapsed = time.monotonic() - started

        self.assertEqual(result.state, "unavailable")
        self.assertIn("deadline", result.detail.lower())
        self.assertLess(elapsed, 0.5)

    def test_body_read_keeps_shorter_per_request_timeout_than_wall_deadline(self):
        body = b'{"ok":true}\n'

        def stalled_after_headers(handler):
            handler.send_response(200)
            handler.send_header("Content-Length", str(len(body)))
            handler.end_headers()
            handler.wfile.flush()
            time.sleep(0.12)
            try:
                handler.wfile.write(body)
                handler.wfile.flush()
            except (
                BrokenPipeError,
                ConnectionAbortedError,
                ConnectionResetError,
            ):
                return

        limits = release.DownloadLimits(
            manifest_bytes=1024,
            file_bytes=1024,
            total_bytes=4096,
            wall_seconds=0.5,
        )
        routes = self.full_routes()
        routes["/data/catalog.json"] = [
            stalled_after_headers,
            (200, body),
        ]
        with HttpFixture(routes) as fixture:
            result = release.compare_live_release(
                self.build_manifest(),
                fixture.base_url,
                attempts=2,
                timeout=0.03,
                delay=0,
                limits=limits,
            )

        catalog_requests = [
            request
            for request in fixture.requests
            if request["path"].split("?", 1)[0] == "/data/catalog.json"
        ]
        self.assertEqual(result.state, "matching")
        self.assertEqual(result.attempts, 2)
        self.assertEqual(len(catalog_requests), 2)

    def test_missing_probe_404_does_not_read_slow_body_past_wall_deadline(self):
        body = b"missing"

        def slow_404_body(handler):
            handler.send_response(404)
            handler.send_header("Content-Length", str(len(body)))
            handler.end_headers()
            handler.wfile.flush()
            time.sleep(0.18)
            try:
                handler.wfile.write(body)
                handler.wfile.flush()
            except (
                BrokenPipeError,
                ConnectionAbortedError,
                ConnectionResetError,
            ):
                return

        limits = release.DownloadLimits(
            manifest_bytes=1024,
            file_bytes=1024,
            total_bytes=4096,
            wall_seconds=0.12,
        )
        routes = self.full_routes()
        routes["/js/__giga_release_probe_missing__.js"] = [slow_404_body]
        with HttpFixture(routes) as fixture:
            started = time.monotonic()
            result = release.compare_live_release(
                self.build_manifest(),
                fixture.base_url,
                attempts=1,
                timeout=1,
                delay=0,
                limits=limits,
            )
            elapsed = time.monotonic() - started

        self.assertLess(elapsed, limits.wall_seconds)
        self.assertEqual(result.state, "matching")
        self.assertEqual(result.attempts, 1)

    def test_manifest_redirect_to_same_origin_different_path_is_not_followed(self):
        routes = self.full_routes()
        routes["/giga-release.json"] = [
            (302, b"", {"Location": "/redirected-release.json"}),
        ]
        routes["/redirected-release.json"] = [
            (200, self.manifest_bytes()),
        ]
        with HttpFixture(routes) as fixture:
            result = release.compare_live_release(
                self.build_manifest(),
                fixture.base_url,
                attempts=2,
                timeout=1,
                delay=0,
            )

        requested_paths = [
            request["path"].split("?", 1)[0]
            for request in fixture.requests
        ]
        self.assertEqual(result.state, "unavailable")
        self.assertEqual(result.attempts, 2)
        self.assertNotIn("/redirected-release.json", requested_paths)

    def test_public_file_redirect_to_cross_origin_is_not_followed(self):
        with HttpFixture(
            {"/cross-origin-app.js": [(200, b'console.log("ok")\n')]}
        ) as target:
            routes = self.full_routes()
            routes["/js/app.js"] = [
                (
                    302,
                    b"",
                    {"Location": target.base_url + "/cross-origin-app.js"},
                ),
            ]
            with HttpFixture(routes) as fixture:
                result = release.compare_live_release(
                    self.build_manifest(),
                    fixture.base_url,
                    attempts=2,
                    timeout=1,
                    delay=0,
                )

        self.assertEqual(result.state, "unavailable")
        self.assertEqual(result.attempts, 2)
        self.assertEqual(target.requests, [])

    def test_missing_probe_redirect_to_404_is_not_original_404(self):
        routes = self.full_routes()
        routes["/js/__giga_release_probe_missing__.js"] = [
            (302, b"", {"Location": "/redirected-missing.js"}),
        ]
        routes["/redirected-missing.js"] = [(404, b"missing")]
        with HttpFixture(routes) as fixture:
            result = release.compare_live_release(
                self.build_manifest(),
                fixture.base_url,
                attempts=2,
                timeout=1,
                delay=0,
            )

        requested_paths = [
            request["path"].split("?", 1)[0]
            for request in fixture.requests
        ]
        self.assertEqual(result.state, "unavailable")
        self.assertEqual(result.attempts, 2)
        self.assertNotIn("/redirected-missing.js", requested_paths)

    def test_post_deploy_verifies_exact_manifest_and_every_public_file(self):
        manifest = self.build_manifest()
        routes = self.full_routes(manifest)
        app_headers = routes["/js/app.js"][0][2]
        routes["/js/app.js"] = [
            (200, b"wrong", app_headers),
            (200, b'console.log("ok")\n', app_headers),
        ]
        with HttpFixture(routes) as fixture:
            result = release.verify_live_release(
                manifest,
                fixture.base_url,
                attempts=2,
                timeout=1,
                delay=0,
            )

        self.assertEqual(result.state, "matching")
        self.assertEqual(result.attempts, 2)
        requested_paths = [
            request["path"].split("?", 1)[0]
            for request in fixture.requests
        ]
        for required in (
            "/giga-release.json",
            "/",
            "/index.html",
            "/data/catalog.json",
            "/data/featured-covers.json",
            "/js/app.js",
            "/css/style.css",
            "/js/__giga_release_probe_missing__.js",
            "/data/raw/products.json",
            "/scripts/refresh.py",
            "/tests/python/test_refresh.py",
        ):
            self.assertIn(required, requested_paths)

    def test_post_deploy_requires_missing_javascript_probe_to_return_404(self):
        manifest = self.build_manifest()
        routes = self.full_routes(manifest)
        routes["/js/__giga_release_probe_missing__.js"] = [(200, b"home\n")]
        with HttpFixture(routes) as fixture:
            result = release.verify_live_release(
                manifest,
                fixture.base_url,
                attempts=2,
                timeout=1,
                delay=0,
            )

        self.assertEqual(result.state, "mismatching")
        self.assertIn("404", result.detail)

    def test_post_deploy_rejects_missing_or_wrong_security_headers(self):
        cases = (
            ("Content-Security-Policy", None, "Content-Security-Policy"),
            (
                "Content-Security-Policy",
                "default-src *",
                "Content-Security-Policy",
            ),
            ("X-Content-Type-Options", "sniff", "X-Content-Type-Options"),
            ("X-Frame-Options", "SAMEORIGIN", "X-Frame-Options"),
            ("Referrer-Policy", "unsafe-url", "Referrer-Policy"),
            ("Permissions-Policy", "camera=*", "Permissions-Policy"),
        )
        for header, value, detail in cases:
            with self.subTest(header=header, value=value):
                routes = self.full_routes()

                def alter(headers):
                    if value is None:
                        headers.pop(header)
                    else:
                        headers[header] = value
                    return headers

                self.replace_headers(routes, "/", alter)
                with HttpFixture(routes) as fixture:
                    result = release.verify_live_release(
                        self.build_manifest(),
                        fixture.base_url,
                        attempts=1,
                        timeout=1,
                        delay=0,
                    )

                self.assertEqual(result.state, "mismatching")
                self.assertIn(detail, result.detail)

    def test_post_deploy_rejects_wrong_cache_policy_on_every_key_route(self):
        cases = (
            ("/", None, "Cache-Control"),
            ("/", "public, max-age=60", "max-age=0"),
            ("/index.html", "public, max-age=60", "max-age=0"),
            ("/data/catalog.json", "public, max-age=600", "max-age=300"),
            (
                "/data/featured-covers.json",
                "public, max-age=600",
                "max-age=300",
            ),
            (
                "/giga-release.json",
                "public, max-age=300",
                "no-store",
            ),
            ("/js/app.js", "public, max-age=7200", "max-age=3600"),
            ("/css/style.css", "public, max-age=7200", "max-age=3600"),
        )
        for path, cache_control, detail in cases:
            with self.subTest(path=path):
                routes = self.full_routes()

                def alter_cache(headers):
                    if cache_control is None:
                        headers.pop("Cache-Control")
                    else:
                        headers["Cache-Control"] = cache_control
                    return headers

                self.replace_headers(
                    routes,
                    path,
                    alter_cache,
                )
                with HttpFixture(routes) as fixture:
                    result = release.verify_live_release(
                        self.build_manifest(),
                        fixture.base_url,
                        attempts=1,
                        timeout=1,
                        delay=0,
                    )

                self.assertEqual(result.state, "mismatching")
                self.assertIn(detail, result.detail)

    def test_post_deploy_rejects_conflicting_or_extra_cache_directives(self):
        cases = (
            (
                "/giga-release.json",
                "no-store, public, max-age=31536000, immutable",
            ),
            (
                "/",
                "public, max-age=0, must-revalidate, immutable",
            ),
            (
                "/data/catalog.json",
                "private, max-age=300, must-revalidate",
            ),
            (
                "/data/featured-covers.json",
                "public, max-age=300, must-revalidate, stale-if-error=60",
            ),
            (
                "/js/app.js",
                "public, max-age=3600, must-revalidate, no-store",
            ),
            (
                "/css/style.css",
                "public, max-age=3600, must-revalidate, max-stale",
            ),
            (
                "/data/catalog.json",
                "public, max-age=300, max-age=600, must-revalidate",
            ),
            (
                "/data/catalog.json",
                "public, max-age=not-a-number, must-revalidate",
            ),
        )
        for path, cache_control in cases:
            with self.subTest(path=path, cache_control=cache_control):
                routes = self.full_routes()
                self.replace_headers(
                    routes,
                    path,
                    lambda headers: {
                        **headers,
                        "Cache-Control": cache_control,
                    },
                )
                with HttpFixture(routes) as fixture:
                    result = release.verify_live_release(
                        self.build_manifest(),
                        fixture.base_url,
                        attempts=1,
                        timeout=1,
                        delay=0,
                    )

                self.assertEqual(result.state, "mismatching")
                self.assertIn("Cache-Control", result.detail)

    def test_post_deploy_normalizes_cache_directive_case_and_ows(self):
        routes = self.full_routes()

        def use_normalized_equivalent(headers):
            headers.pop("Cache-Control")
            headers["cAcHe-CoNtRoL"] = (
                "  MUST-REVALIDATE , MAX-AGE = 0 , PUBLIC  "
            )
            return headers

        self.replace_headers(routes, "/", use_normalized_equivalent)
        with HttpFixture(routes) as fixture:
            result = release.verify_live_release(
                self.build_manifest(),
                fixture.base_url,
                attempts=1,
                timeout=1,
                delay=0,
            )

        self.assertEqual(result.state, "matching")

    def test_post_deploy_normalizes_csp_case_and_preserves_header_boundaries(self):
        routes = self.full_routes()
        status, body, headers = routes["/"][0]
        uppercase_csp = SECURITY_HEADERS["Content-Security-Policy"].upper()
        header_items = [
            (key, value)
            for key, value in headers.items()
            if key.lower() != "content-security-policy"
        ]
        header_items.extend(
            (
                ("CONTENT-SECURITY-POLICY", uppercase_csp),
                (
                    "content-security-policy",
                    SECURITY_HEADERS["Content-Security-Policy"],
                ),
            )
        )
        routes["/"] = [(status, body, header_items)]
        with HttpFixture(routes) as fixture:
            result = release.verify_live_release(
                self.build_manifest(),
                fixture.base_url,
                attempts=1,
                timeout=1,
                delay=0,
            )

        self.assertEqual(result.state, "matching")

    def test_post_deploy_rejects_expanded_or_incomplete_csp_policies(self):
        baseline = SECURITY_HEADERS["Content-Security-Policy"]
        cases = (
            baseline.replace("script-src 'self'", "script-src 'self' *"),
            baseline.replace(
                "script-src 'self'",
                "script-src 'self' 'self'",
            ),
            baseline.replace(
                "script-src 'self'",
                "script-src 'self' https:",
            ),
            baseline.replace(
                "script-src 'self'",
                "script-src 'self' 'unsafe-inline'",
            ),
            baseline.replace(
                "script-src 'self'",
                "script-src 'self' 'unsafe-eval'",
            ),
            baseline.replace(
                "script-src 'self'",
                "script-src 'self' https://cdn.example",
            ),
            baseline.replace(
                "script-src 'self'",
                "script-src 'self' 'nonce-abc123'",
            ),
            baseline.replace(
                "script-src 'self'",
                "script-src 'self' 'sha256-abc123'",
            ),
            "default-src 'self'",
        )
        for csp in cases:
            with self.subTest(csp=csp):
                routes = self.full_routes()
                self.replace_headers(
                    routes,
                    "/",
                    lambda headers: {
                        **headers,
                        "Content-Security-Policy": csp,
                    },
                )
                with HttpFixture(routes) as fixture:
                    result = release.verify_live_release(
                        self.build_manifest(),
                        fixture.base_url,
                        attempts=1,
                        timeout=1,
                        delay=0,
                    )

                self.assertEqual(result.state, "mismatching")
                self.assertIn("Content-Security-Policy", result.detail)

    def test_post_deploy_rejects_a_second_incomplete_csp_header(self):
        routes = self.full_routes()
        status, body, headers = routes["/"][0]
        routes["/"] = [
            (
                status,
                body,
                list(headers.items())
                + [("Content-Security-Policy", "default-src 'self'")],
            )
        ]
        with HttpFixture(routes) as fixture:
            result = release.verify_live_release(
                self.build_manifest(),
                fixture.base_url,
                attempts=1,
                timeout=1,
                delay=0,
            )

        self.assertEqual(result.state, "mismatching")
        self.assertIn("Content-Security-Policy", result.detail)

    def test_post_deploy_requires_every_private_path_to_return_404(self):
        for path in (
            "/data/raw/products.json",
            "/scripts/refresh.py",
            "/tests/python/test_refresh.py",
        ):
            with self.subTest(path=path):
                routes = self.full_routes()
                routes[path] = [(200, b"private")]
                with HttpFixture(routes) as fixture:
                    result = release.verify_live_release(
                        self.build_manifest(),
                        fixture.base_url,
                        attempts=1,
                        timeout=1,
                        delay=0,
                    )

                self.assertEqual(result.state, "mismatching")
                self.assertIn(path, result.detail)
                self.assertIn("404", result.detail)

    def test_precheck_does_not_require_production_response_headers(self):
        routes = self.full_routes()
        for path, responses in tuple(routes.items()):
            response = responses[0]
            if len(response) == 3:
                status, body, _headers = response
                routes[path] = [(status, body)]
        with HttpFixture(routes) as fixture:
            result = release.compare_live_release(
                self.build_manifest(),
                fixture.base_url,
                attempts=1,
                timeout=1,
                delay=0,
            )

        self.assertEqual(result.state, "matching")


class DeployJsonTests(ReleaseTestCase):
    def test_deploy_json_requires_expected_site_and_deploy_identity(self):
        payload = {
            "site_id": SITE_ID,
            "deploy_id": "67b1234567890abcdef12345",
            "url": PRODUCTION_URL,
            "deploy_url": (
                "https://67b1234567890abcdef12345--giga-catalog-cn.netlify.app"
            ),
        }

        result = release.validate_deploy_json(
            json.dumps(payload).encode("utf-8"),
            SITE_ID,
            PRODUCTION_URL,
        )

        self.assertEqual(result["site_id"], SITE_ID)
        self.assertEqual(result["deploy_id"], payload["deploy_id"])

    def test_deploy_json_rejects_wrong_or_malformed_identity(self):
        valid = {
            "site_id": SITE_ID,
            "deploy_id": "67b1234567890abcdef12345",
            "url": PRODUCTION_URL,
            "deploy_url": (
                "https://67b1234567890abcdef12345--giga-catalog-cn.netlify.app"
            ),
        }
        cases = {
            "wrong site": {**valid, "site_id": "wrong"},
            "missing deploy": {**valid, "deploy_id": ""},
            "wrong production URL": {**valid, "url": "https://evil.example"},
            "wrong deploy URL": {
                **valid,
                "deploy_url": "https://evil.example/deploy",
            },
            "malformed deploy URL": {
                **valid,
                "deploy_url": "https://giga-catalog-cn.netlify.app:bad",
            },
        }

        for label, payload in cases.items():
            with self.subTest(label=label):
                with self.assertRaises(release.ReleaseError):
                    release.validate_deploy_json(
                        json.dumps(payload).encode("utf-8"),
                        SITE_ID,
                        PRODUCTION_URL,
                    )


class ReleaseCliTests(ReleaseTestCase):
    def test_prepare_command_writes_a_valid_manifest(self):
        command = [
            sys.executable,
            str(REPOSITORY_ROOT / "scripts" / "release.py"),
            "prepare",
            "--public-dir",
            str(self.public),
            "--netlify-config",
            str(self.netlify),
            "--source-commit",
            SOURCE_COMMIT,
        ]

        completed = subprocess.run(
            command,
            cwd=REPOSITORY_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(
            release.validate_local_release(
                release.parse_manifest(self.manifest_path.read_bytes()),
                self.public,
                self.netlify,
            )["publicSha256"],
            PUBLIC_SHA256,
        )


if __name__ == "__main__":
    unittest.main()
