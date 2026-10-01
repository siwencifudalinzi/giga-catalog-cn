import unittest
import gzip
import io
from unittest.mock import Mock, patch

from src.giga_catalog.javryo_media_probe import MediaProbeBudget, fetch_probe


class MediaTransferLimitTests(unittest.TestCase):
    def test_compressed_metadata_is_fully_decoded_within_decoded_size_limit(self):
        body = b'{"embed_url":"' + b'x' * 10000 + b'"}'
        compressed = gzip.compress(body)
        stream = io.BytesIO(compressed)
        response = Mock(status_code=200, headers={"content-type": "application/json",
            "content-encoding": "gzip", "content-length": str(len(compressed))})
        response.raw.read.side_effect = lambda amount, decode_content: stream.read(amount)
        with patch("src.giga_catalog.javryo_media_probe.requests.request", return_value=response):
            result = fetch_probe(self.request("fetch", "https://cdn.example/api"), MediaProbeBudget())
        self.assertEqual(result["body"], body)
        self.assertNotIn("content-encoding", result["headers"])
        self.assertEqual(result["headers"]["content-length"], str(len(body)))

    def test_compressed_metadata_cannot_expand_past_response_limit(self):
        compressed = gzip.compress(b'x' * 600000)
        stream = io.BytesIO(compressed)
        response = Mock(status_code=200, headers={"content-type": "application/json",
            "content-encoding": "gzip", "content-length": str(len(compressed))})
        response.raw.read.side_effect = lambda amount, decode_content: stream.read(amount)
        with patch("src.giga_catalog.javryo_media_probe.requests.request", return_value=response):
            self.assertIsNone(fetch_probe(self.request("fetch", "https://cdn.example/api"), MediaProbeBudget()))

    def request(self, resource_type="media", url="https://cdn.example/start.mp4"):
        return {"url": url, "resourceType": resource_type, "method": "GET", "headers": {}, "data": None}

    def test_ignored_range_response_is_closed_without_reading_video(self):
        response = Mock(status_code=200, headers={"content-type": "video/mp4", "content-length": "50000000"})
        with patch("src.giga_catalog.javryo_media_probe.requests.request", return_value=response):
            self.assertIsNone(fetch_probe(self.request(), MediaProbeBudget()))
        response.raw.read.assert_not_called()
        response.close.assert_called_once()

    def test_oversized_partial_response_is_not_read(self):
        response = Mock(status_code=206, headers={"content-type": "video/mp4", "content-range": "bytes 0-500000/50000000"})
        with patch("src.giga_catalog.javryo_media_probe.requests.request", return_value=response):
            self.assertIsNone(fetch_probe(self.request(), MediaProbeBudget()))
        response.raw.read.assert_not_called()

    def test_extensionless_xhr_segments_share_two_request_budget(self):
        budget = MediaProbeBudget()
        responses = []
        for _ in range(3):
            response = Mock(status_code=206, headers={"content-type": "video/mp2t", "content-range": "bytes 0-3/1000"})
            response.raw.read.return_value = b"abcd"
            responses.append(response)
        with patch("src.giga_catalog.javryo_media_probe.requests.request", side_effect=responses):
            request = self.request("fetch", "https://cdn.example/segment")
            first = fetch_probe(request, budget)
            self.assertEqual(first["body"], b"abcd")
            self.assertEqual(first["headers"]["x-giga-bounded-probe"], "media")
            self.assertIsNotNone(fetch_probe(request, budget))
            self.assertIsNone(fetch_probe(request, budget))
        responses[2].raw.read.assert_not_called()
        self.assertEqual(budget.bytes_read, 8)

    def test_source_path_does_not_read_media_bytes(self):
        with patch("src.giga_catalog.javryo_media_probe.requests.request") as request:
            self.assertIsNone(fetch_probe(self.request(), MediaProbeBudget(allow_media=False)))
        request.assert_not_called()
