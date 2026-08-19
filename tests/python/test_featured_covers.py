"""Regression coverage for the small local homepage-cover cache."""

import io
import json
import os
import subprocess
import struct
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from src.giga_catalog.featured_covers import cache_featured_covers


def _catalog(videos):
    return {"series": [{"code": "NEW", "videos": videos}]}


def _video(number, *, cover=None):
    return {
        "code": f"NEW-{number}",
        "number": number,
        "cover": cover or f"https://www.giga-web.jp/db_titles/new/new{number:02d}/pac_s.jpg",
    }


def _jpeg_bytes():
    image = Image.new("RGB", (24, 16), "red")
    payload = io.BytesIO()
    image.save(payload, format="JPEG")
    return payload.getvalue()


def _oversized_png_header():
    return b"\x89PNG\r\n\x1a\n" + struct.pack(
        ">IIIBBBBB",
        13,
        50000,
        50000,
        8,
        2,
        0,
        0,
        0,
    )


def _cache_snapshot(output_path, manifest_path):
    root = output_path.parent.parent
    paths = [manifest_path, *output_path.rglob("*")]
    return {
        path.relative_to(root): path.read_bytes()
        for path in paths
        if path.is_file()
    }


class _Response:
    def __init__(self, content, *, headers=None, status_code=200, chunks=None):
        self.content = content
        self.headers = headers or {"Content-Type": "image/jpeg"}
        self.status_code = status_code
        self._chunks = chunks
        self.closed = False

    def raise_for_status(self):
        return None

    def iter_content(self, chunk_size):
        yield from self._chunks if self._chunks is not None else [self.content]

    def close(self):
        self.closed = True


class _Session:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if isinstance(self.response, Exception):
            raise self.response
        response = _Response(self.response)
        self.responses = getattr(self, "responses", []) + [response]
        return response


class _ResponseMapSession:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        response = self.responses[url]
        if isinstance(response, Exception):
            raise response
        return response


class FeaturedCoverCacheTests(unittest.TestCase):
    def test_no_eligible_giga_cover_is_a_zero_write_noop(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            catalog_path = root / "catalog.json"
            output_path = root / "media" / "featured-covers"
            manifest_path = root / "featured-covers.json"
            catalog_path.write_text(
                json.dumps(_catalog([_video(1, cover="https://images.example/cover.jpg")])),
                encoding="utf-8",
            )

            result = cache_featured_covers(catalog_path, output_path, manifest_path, retries=1)

            self.assertFalse(result["published"])
            self.assertFalse(output_path.exists())
            self.assertFalse(manifest_path.exists())

    def test_cli_starts_from_the_scripts_directory(self):
        root = Path(__file__).resolve().parents[2]
        completed = subprocess.run(
            [sys.executable, str(root / "scripts" / "cache_featured_covers.py"), "--help"],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("--catalog", completed.stdout)

    def test_caches_first_six_latest_series_covers_in_display_order(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            catalog_path = root / "catalog.json"
            catalog_path.write_text(
                json.dumps(_catalog([_video(3), _video(1), _video(2), _video(4), _video(5), _video(6), _video(7)])),
                encoding="utf-8",
            )
            session = _Session(_jpeg_bytes())

            result = cache_featured_covers(
                catalog_path,
                root / "media" / "featured-covers",
                root / "featured-covers.json",
                session=session,
                retries=1,
            )

            self.assertEqual(result["cached"], ["NEW-1", "NEW-2", "NEW-3", "NEW-4", "NEW-5", "NEW-6"])
            manifest = json.loads((root / "featured-covers.json").read_text(encoding="utf-8"))
            self.assertEqual(
                [cover["code"] for cover in manifest["covers"]],
                ["NEW-1", "NEW-2", "NEW-3", "NEW-4", "NEW-5", "NEW-6"],
            )
            self.assertRegex(manifest["generation"], r"^[0-9a-f]{64}$")
            self.assertEqual(
                manifest["covers"][0]["path"],
                f"/media/featured-covers/g/{manifest['generation']}/new-1.webp",
            )
            for entry in manifest["covers"]:
                with Image.open(root / entry["path"].lstrip("/")) as image:
                    self.assertEqual((image.width, image.height, image.format), (320, 480, "WEBP"))
            self.assertTrue(all(response.closed for response in session.responses))

    def test_zero_suffix_cover_is_valid_and_sorts_before_positive_numbers(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            catalog_path = root / "catalog.json"
            catalog_path.write_text(
                json.dumps(_catalog([_video(2), _video(0), _video(1)])),
                encoding="utf-8",
            )
            session = _Session(_jpeg_bytes())

            result = cache_featured_covers(
                catalog_path,
                root / "media" / "featured-covers",
                root / "featured-covers.json",
                session=session,
                retries=1,
            )

            self.assertEqual(result["cached"], ["NEW-0", "NEW-1", "NEW-2"])
            manifest = json.loads(
                (root / "featured-covers.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                [cover["code"] for cover in manifest["covers"]],
                ["NEW-0", "NEW-1", "NEW-2"],
            )
            self.assertTrue(manifest["covers"][0]["path"].endswith("/new-0.webp"))

    def test_same_valid_manifest_reuses_files_without_network_or_byte_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            catalog_path = root / "catalog.json"
            catalog_path.write_text(json.dumps(_catalog([_video(1)])), encoding="utf-8")
            manifest_path = root / "featured-covers.json"
            output_path = root / "media" / "featured-covers"
            first = _Session(_jpeg_bytes())
            cache_featured_covers(catalog_path, output_path, manifest_path, session=first, retries=1)
            before = _cache_snapshot(output_path, manifest_path)
            second = _Session(AssertionError("network should not be used"))

            result = cache_featured_covers(catalog_path, output_path, manifest_path, session=second, retries=1)

            self.assertEqual(result["cached"], ["NEW-1"])
            self.assertEqual(second.calls, [])
            self.assertEqual(_cache_snapshot(output_path, manifest_path), before)

    def test_failed_new_download_preserves_existing_manifest_and_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            catalog_path = root / "catalog.json"
            output_path = root / "media" / "featured-covers"
            manifest_path = root / "featured-covers.json"
            catalog_path.write_text(json.dumps(_catalog([_video(1)])), encoding="utf-8")
            cache_featured_covers(catalog_path, output_path, manifest_path, session=_Session(_jpeg_bytes()), retries=1)
            before_manifest = manifest_path.read_bytes()
            before = _cache_snapshot(output_path, manifest_path)
            catalog_path.write_text(json.dumps(_catalog([_video(2)])), encoding="utf-8")

            result = cache_featured_covers(
                catalog_path, output_path, manifest_path, session=_Session(OSError("offline")), retries=1
            )

            self.assertEqual(result["cached"], [])
            self.assertEqual(manifest_path.read_bytes(), before_manifest)
            self.assertEqual(_cache_snapshot(output_path, manifest_path), before)

    def test_partial_download_failure_keeps_the_entire_previous_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            catalog_path = root / "catalog.json"
            output_path = root / "media" / "featured-covers"
            manifest_path = root / "featured-covers.json"
            catalog_path.write_text(json.dumps(_catalog([_video(1)])), encoding="utf-8")
            cache_featured_covers(catalog_path, output_path, manifest_path, session=_Session(_jpeg_bytes()), retries=1)
            before = _cache_snapshot(output_path, manifest_path)
            second, third = _video(2), _video(3)
            catalog_path.write_text(json.dumps(_catalog([second, third])), encoding="utf-8")

            result = cache_featured_covers(
                catalog_path,
                output_path,
                manifest_path,
                session=_ResponseMapSession({second["cover"]: _Response(_jpeg_bytes()), third["cover"]: OSError("offline")}),
                retries=1,
            )

            self.assertFalse(result["published"])
            self.assertEqual(_cache_snapshot(output_path, manifest_path), before)

    def test_rejects_oversized_content_length_without_replacing_the_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            catalog_path = root / "catalog.json"
            output_path = root / "media" / "featured-covers"
            manifest_path = root / "featured-covers.json"
            catalog_path.write_text(json.dumps(_catalog([_video(1)])), encoding="utf-8")
            cache_featured_covers(catalog_path, output_path, manifest_path, session=_Session(_jpeg_bytes()), retries=1)
            before = manifest_path.read_bytes()
            replacement = _video(2)
            catalog_path.write_text(json.dumps(_catalog([replacement])), encoding="utf-8")
            session = _ResponseMapSession({replacement["cover"]: _Response(_jpeg_bytes(), headers={"Content-Type": "image/jpeg", "Content-Length": str(9 * 1024 * 1024)})})

            result = cache_featured_covers(catalog_path, output_path, manifest_path, session=session, retries=1)

            self.assertFalse(result["published"])
            self.assertEqual(manifest_path.read_bytes(), before)
            self.assertTrue(session.calls[0][1]["stream"])
            self.assertFalse(session.calls[0][1]["allow_redirects"])
            self.assertTrue(session.responses[replacement["cover"]].closed)

    def test_rejects_chunked_overflow_and_non_image_mime_without_replacing_the_cache(self):
        for label, response in (
            ("chunked", _Response(_jpeg_bytes(), chunks=[_jpeg_bytes(), b"x" * (9 * 1024 * 1024)])),
            ("mime", _Response(_jpeg_bytes(), headers={"Content-Type": "text/html"})),
        ):
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                catalog_path = root / "catalog.json"
                output_path = root / "media" / "featured-covers"
                manifest_path = root / "featured-covers.json"
                catalog_path.write_text(json.dumps(_catalog([_video(1)])), encoding="utf-8")
                cache_featured_covers(catalog_path, output_path, manifest_path, session=_Session(_jpeg_bytes()), retries=1)
                before = manifest_path.read_bytes()
                replacement = _video(2)
                catalog_path.write_text(json.dumps(_catalog([replacement])), encoding="utf-8")

                result = cache_featured_covers(catalog_path, output_path, manifest_path, session=_ResponseMapSession({replacement["cover"]: response}), retries=1)

                self.assertFalse(result["published"])
                self.assertEqual(manifest_path.read_bytes(), before)
                self.assertTrue(response.closed)

    def test_rejects_an_oversized_pixel_header_before_image_conversion(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            catalog_path = root / "catalog.json"
            output_path = root / "media" / "featured-covers"
            manifest_path = root / "featured-covers.json"
            catalog_path.write_text(json.dumps(_catalog([_video(1)])), encoding="utf-8")
            cache_featured_covers(catalog_path, output_path, manifest_path, session=_Session(_jpeg_bytes()), retries=1)
            before = manifest_path.read_bytes()
            replacement = _video(2)
            catalog_path.write_text(json.dumps(_catalog([replacement])), encoding="utf-8")
            session = _ResponseMapSession({replacement["cover"]: _Response(_oversized_png_header(), headers={"Content-Type": "image/png"})})

            result = cache_featured_covers(catalog_path, output_path, manifest_path, session=session, retries=1)

            self.assertFalse(result["published"])
            self.assertEqual(manifest_path.read_bytes(), before)
            self.assertTrue(session.calls[0][1]["stream"])
            self.assertTrue(session.responses[replacement["cover"]].closed)

    def test_rejects_redirects_without_following_them_or_publishing_partial_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            catalog_path = root / "catalog.json"
            output_path = root / "media" / "featured-covers"
            manifest_path = root / "featured-covers.json"
            catalog_path.write_text(json.dumps(_catalog([_video(1)])), encoding="utf-8")
            cache_featured_covers(catalog_path, output_path, manifest_path, session=_Session(_jpeg_bytes()), retries=1)
            before = manifest_path.read_bytes()
            replacement = _video(2)
            catalog_path.write_text(json.dumps(_catalog([replacement])), encoding="utf-8")
            session = _ResponseMapSession({replacement["cover"]: _Response(_jpeg_bytes(), status_code=302, headers={"Location": "https://internal.invalid/"})})

            result = cache_featured_covers(catalog_path, output_path, manifest_path, session=session, retries=1)

            self.assertFalse(result["published"])
            self.assertEqual(manifest_path.read_bytes(), before)
            self.assertFalse(session.calls[0][1]["allow_redirects"])
            self.assertTrue(session.responses[replacement["cover"]].closed)

    def test_generation_publish_failure_keeps_the_previous_manifest_and_generation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            catalog_path = root / "catalog.json"
            output_path = root / "media" / "featured-covers"
            manifest_path = root / "featured-covers.json"
            catalog_path.write_text(json.dumps(_catalog([_video(1)])), encoding="utf-8")
            cache_featured_covers(catalog_path, output_path, manifest_path, session=_Session(_jpeg_bytes()), retries=1)
            before = _cache_snapshot(output_path, manifest_path)
            catalog_path.write_text(json.dumps(_catalog([_video(2)])), encoding="utf-8")

            with self.assertRaisesRegex(OSError, "generation rename"):
                cache_featured_covers(
                    catalog_path,
                    output_path,
                    manifest_path,
                    session=_Session(_jpeg_bytes()),
                    retries=1,
                    replacer=lambda source, target: (_ for _ in ()).throw(OSError("generation rename")),
                )

            self.assertEqual(_cache_snapshot(output_path, manifest_path), before)

    def test_manifest_replace_failure_keeps_the_previous_generation_byte_for_byte(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            catalog_path = root / "catalog.json"
            output_path = root / "media" / "featured-covers"
            manifest_path = root / "featured-covers.json"
            catalog_path.write_text(json.dumps(_catalog([_video(1)])), encoding="utf-8")
            cache_featured_covers(catalog_path, output_path, manifest_path, session=_Session(_jpeg_bytes()), retries=1)
            before_manifest = manifest_path.read_bytes()
            before_generation = _cache_snapshot(output_path, manifest_path)
            catalog_path.write_text(json.dumps(_catalog([_video(2)])), encoding="utf-8")

            def fail_manifest(source, target):
                if target == manifest_path:
                    raise OSError("manifest replace")
                os.replace(source, target)

            with self.assertRaisesRegex(OSError, "manifest replace"):
                cache_featured_covers(
                    catalog_path,
                    output_path,
                    manifest_path,
                    session=_Session(_jpeg_bytes()),
                    retries=1,
                    replacer=fail_manifest,
                )

            self.assertEqual(manifest_path.read_bytes(), before_manifest)
            for path, content in before_generation.items():
                self.assertEqual((root / path).read_bytes(), content)
