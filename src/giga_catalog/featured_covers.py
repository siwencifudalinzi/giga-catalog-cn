"""Build the tiny, deterministic local cache used by the homepage's LCP covers."""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import tempfile
import time
import warnings
from pathlib import Path
from typing import Mapping, MutableMapping, Optional, Sequence, Tuple
from urllib.parse import urlparse

from PIL import Image, ImageOps
import requests

from src.giga_catalog.codes import normalize_code


COVER_WIDTH = 320
COVER_HEIGHT = 480
MAX_FEATURED_COVERS = 6
MANIFEST_SCHEMA_VERSION = 2
MAX_DOWNLOAD_BYTES = 8 * 1024 * 1024
MAX_IMAGE_DIMENSION = 8192
MAX_IMAGE_PIXELS = 32 * 1024 * 1024
ALLOWED_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp"}
ALLOWED_IMAGE_FORMATS = {"JPEG", "PNG", "WEBP"}
_GENERATION_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def cache_featured_covers(
    catalog_path: Path,
    output_dir: Path,
    manifest_path: Path,
    *,
    session: Optional[object] = None,
    retries: int = 3,
    timeout: Tuple[float, float] = (5.0, 20.0),
    replacer: Optional[object] = None,
) -> dict:
    """Prepare and atomically publish at most six current homepage covers.

    A failed source never replaces a working manifest.  Existing entries whose
    source and validated WebP are unchanged make this operation completely
    offline and byte-stable.
    """
    catalog = json.loads(Path(catalog_path).read_text(encoding="utf-8"))
    selected = _select_featured(catalog)
    if not selected:
        return {"cached": [], "failures": [], "published": False}
    existing = _load_manifest(manifest_path, output_dir)
    existing_by_code = {entry["code"]: entry for entry in existing.get("covers", [])}
    client = session or requests.Session()
    retries = max(1, int(retries))
    prepared: list[dict] = []
    failures: list[dict] = []

    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="featured-covers-", dir=output_dir.parent
    ) as staging:
        staging_root = Path(staging)
        staging_dir = staging_root / "generation"
        staging_dir.mkdir()
        for item in selected:
            old = existing_by_code.get(item["code"])
            destination = staging_dir / _filename(item["code"])
            try:
                old_path = (
                    _entry_path(old, output_dir)
                    if old and old.get("source") == item["source"]
                    else None
                )
                if old_path is not None and _valid_cover(old_path):
                    shutil.copyfile(old_path, destination)
                else:
                    payload = _download(item["source"], client, retries, timeout)
                    _write_webp(payload, destination)
                prepared.append(item)
            except Exception as error:  # keep a usable prior manifest intact
                failures.append({"code": item["code"], "error": str(error)})

        # This cache is a small consistency unit: publishing a mixed new/old
        # set creates unpredictable LCP behavior.  Any failure keeps every
        # previous byte and defers cleanup until a complete later refresh.
        if failures:
            return {"cached": [], "failures": failures, "published": False}

        generation = _generation_id(prepared, staging_dir)
        manifest = {
            "covers": [
                {
                    "code": item["code"],
                    "path": _public_path(item["code"], generation),
                    "source": item["source"],
                }
                for item in prepared
            ],
            "generation": generation,
            "schemaVersion": MANIFEST_SCHEMA_VERSION,
        }
        manifest_bytes = _serialize_manifest(manifest)
        old_bytes = manifest_path.read_bytes() if manifest_path.is_file() else None
        generation_dir = output_dir / "g" / generation
        if old_bytes == manifest_bytes and _generation_is_valid(
            generation_dir, prepared
        ):
            return {
                "cached": [item["code"] for item in prepared],
                "failures": [],
                "published": True,
            }

        generation_dir.parent.mkdir(parents=True, exist_ok=True)
        if generation_dir.exists():
            if not _generation_matches(generation_dir, staging_dir, prepared):
                raise OSError("existing featured-cover generation does not match")
        else:
            _replace(staging_dir, generation_dir, replacer)
        _atomic_write(manifest_path, manifest_bytes, replacer)
        _cleanup_old_generations(output_dir, generation)

    return {
        "cached": [item["code"] for item in prepared],
        "failures": failures,
        "published": True,
    }


def _select_featured(catalog: Mapping[str, object]) -> list[dict]:
    series = catalog.get("series") if isinstance(catalog, Mapping) else None
    if not isinstance(series, Sequence) or not series or not isinstance(series[0], Mapping):
        return []
    videos = series[0].get("videos")
    if not isinstance(videos, Sequence):
        return []
    selected = []
    for video in sorted((item for item in videos if isinstance(item, Mapping)), key=_display_order):
        code = normalize_code(video.get("code"))
        source = video.get("cover")
        if code is None or not _is_giga_cover(source):
            continue
        selected.append({"code": code, "source": source})
        if len(selected) == MAX_FEATURED_COVERS:
            break
    return selected


def _display_order(video: Mapping[str, object]) -> tuple[int, str]:
    code = normalize_code(video.get("code")) or "ZZZ-999999999"
    try:
        number = int(video.get("number", code.rsplit("-", 1)[1]))
    except (TypeError, ValueError):
        number = 999999999
    return number if number >= 0 else 999999999, code


def _is_giga_cover(value: object) -> bool:
    if not isinstance(value, str):
        return False
    parsed = urlparse(value)
    return (
        parsed.scheme == "https"
        and parsed.netloc == "www.giga-web.jp"
        and parsed.path.startswith("/db_titles/")
    )


def _filename(code: str) -> str:
    normalized = normalize_code(code)
    if normalized is None:
        raise ValueError("invalid featured-cover code")
    return f"{normalized.lower()}.webp"


def _public_path(code: str, generation: str) -> str:
    if not _GENERATION_PATTERN.fullmatch(generation):
        raise ValueError("invalid featured-cover generation")
    return f"/media/featured-covers/g/{generation}/{_filename(code)}"


def _load_manifest(path: Path, output_dir: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return {"covers": [], "schemaVersion": MANIFEST_SCHEMA_VERSION}
    if not isinstance(payload, MutableMapping) or not isinstance(payload.get("covers"), list):
        return {"covers": [], "schemaVersion": MANIFEST_SCHEMA_VERSION}
    generation = payload.get("generation")
    is_generation_manifest = isinstance(generation, str) and bool(
        _GENERATION_PATTERN.fullmatch(generation)
    )
    covers = []
    for entry in payload["covers"][:MAX_FEATURED_COVERS]:
        if not isinstance(entry, Mapping):
            continue
        code = normalize_code(entry.get("code"))
        source = entry.get("source")
        expected = (
            _public_path(code, generation)
            if code and is_generation_manifest
            else f"/media/featured-covers/{_filename(code)}" if code else None
        )
        if code and entry.get("path") == expected and _is_giga_cover(source):
            covers.append({"code": code, "path": expected, "source": source})
    return {
        "covers": covers,
        "generation": generation if is_generation_manifest else None,
        "schemaVersion": MANIFEST_SCHEMA_VERSION,
    }


def _entry_path(entry: Optional[Mapping[str, object]], output_dir: Path) -> Optional[Path]:
    if not isinstance(entry, Mapping):
        return None
    code = normalize_code(entry.get("code"))
    path = entry.get("path")
    if code is None or not isinstance(path, str):
        return None
    legacy = f"/media/featured-covers/{_filename(code)}"
    if path == legacy:
        return output_dir / _filename(code)
    match = re.fullmatch(
        rf"/media/featured-covers/g/([0-9a-f]{{64}})/{re.escape(_filename(code))}",
        path,
    )
    return output_dir / "g" / match.group(1) / _filename(code) if match else None


def _generation_id(entries: Sequence[Mapping[str, object]], directory: Path) -> str:
    content = [
        {
            "code": item["code"],
            "source": item["source"],
            "sha256": hashlib.sha256(
                (directory / _filename(str(item["code"]))).read_bytes()
            ).hexdigest(),
        }
        for item in entries
    ]
    return hashlib.sha256(_serialize_manifest({"covers": content})).hexdigest()


def _generation_is_valid(directory: Path, entries: Sequence[Mapping[str, object]]) -> bool:
    return directory.is_dir() and all(
        _valid_cover(directory / _filename(str(item["code"]))) for item in entries
    )


def _generation_matches(
    published: Path, staged: Path, entries: Sequence[Mapping[str, object]]
) -> bool:
    return _generation_is_valid(published, entries) and all(
        (published / _filename(str(item["code"]))).read_bytes()
        == (staged / _filename(str(item["code"]))).read_bytes()
        for item in entries
    )


def _cleanup_old_generations(output_dir: Path, active_generation: str) -> None:
    generation_root = output_dir / "g"
    try:
        for path in generation_root.iterdir():
            if path.name != active_generation and path.is_dir():
                shutil.rmtree(path)
        for path in output_dir.glob("*.webp"):
            path.unlink()
    except OSError:
        pass


def _download(
    url: str,
    session: object,
    retries: int,
    timeout: Tuple[float, float],
) -> bytes:
    last_error: Optional[Exception] = None
    for attempt in range(retries):
        response = None
        try:
            response = session.get(
                url,
                timeout=timeout,
                stream=True,
                allow_redirects=False,
            )
            if 300 <= response.status_code < 400:
                raise ValueError("redirected featured cover is not allowed")
            response.raise_for_status()
            content_type = (
                response.headers.get("Content-Type", "")
                .split(";", 1)[0]
                .strip()
                .lower()
            )
            if content_type not in ALLOWED_CONTENT_TYPES:
                raise ValueError("featured cover has an unsupported media type")
            declared_length = response.headers.get("Content-Length")
            if (
                declared_length is not None
                and int(declared_length) > MAX_DOWNLOAD_BYTES
            ):
                raise ValueError("featured cover exceeds the byte limit")
            chunks = []
            total = 0
            for chunk in response.iter_content(chunk_size=64 * 1024):
                if not chunk:
                    continue
                total += len(chunk)
                if total > MAX_DOWNLOAD_BYTES:
                    raise ValueError("featured cover exceeds the byte limit")
                chunks.append(chunk)
            if not chunks:
                raise ValueError("featured cover response was empty")
            return b"".join(chunks)
        except Exception as error:
            last_error = error
            if attempt + 1 < retries:
                time.sleep(0.2 * (attempt + 1))
        finally:
            if response is not None:
                response.close()
    raise RuntimeError(f"download failed: {last_error}")


def _write_webp(payload: bytes, destination: Path) -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        with Image.open(io.BytesIO(payload)) as source:
            if source.format not in ALLOWED_IMAGE_FORMATS:
                raise ValueError("featured cover has an unsupported image format")
            width, height = source.size
            if (
                width > MAX_IMAGE_DIMENSION
                or height > MAX_IMAGE_DIMENSION
                or width * height > MAX_IMAGE_PIXELS
            ):
                raise ValueError("featured cover exceeds image dimension limits")
            source.load()
            converted = ImageOps.fit(
                source.convert("RGB"),
                (COVER_WIDTH, COVER_HEIGHT),
                method=Image.Resampling.LANCZOS,
            )
            converted.save(destination, format="WEBP", quality=82, method=6)
    if not _valid_cover(destination):
        raise ValueError("generated cover did not pass WebP validation")


def _valid_cover(path: Path) -> bool:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(path) as image:
                image.load()
                return image.format == "WEBP" and image.size == (
                    COVER_WIDTH,
                    COVER_HEIGHT,
                )
    except (
        OSError,
        ValueError,
        Image.DecompressionBombWarning,
        Image.DecompressionBombError,
    ):
        return False


def _serialize_manifest(manifest: Mapping[str, object]) -> bytes:
    return (json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _replace(source: Path, target: Path, replacer: Optional[object]) -> None:
    if replacer is None:
        os.replace(source, target)
    else:
        replacer(source, target)


def _atomic_write(path: Path, content: bytes, replacer: Optional[object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        handle.write(content)
        temporary = Path(handle.name)
    try:
        _replace(temporary, path, replacer)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
