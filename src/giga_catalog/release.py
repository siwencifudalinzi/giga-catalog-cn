"""Deterministic release identity and production verification helpers."""

import hashlib
import http.client
import json
import re
import socket
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable, Dict, Mapping, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


SCHEMA_VERSION = 1
MANIFEST_RELATIVE_PATH = "giga-release.json"
DEPLOY_CONTROL_PATHS = {".nojekyll"}
CATALOG_RELATIVE_PATH = "data/catalog.json"
HOME_RELATIVE_PATH = "index.html"
MISSING_PROBE_RELATIVE_PATH = "js/__giga_release_probe_missing__.js"
FEATURED_COVERS_RELATIVE_PATH = "data/featured-covers.json"
STYLESHEET_RELATIVE_PATH = "css/style.css"
PRIVATE_PROBE_RELATIVE_PATHS = (
    "data/raw/products.json",
    "scripts/refresh.py",
    "tests/python/test_refresh.py",
)
SITE_ID = "78c2aad4-65e1-4203-b0be-ce3a6bfdd244"
PRODUCTION_URL = "https://giga-catalog-cn.netlify.app"
HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")
COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
DEPLOY_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{8,128}$")
MAX_MANIFEST_BYTES = 1024 * 1024
MAX_PUBLIC_FILE_BYTES = 32 * 1024 * 1024
MAX_RELEASE_BYTES = 128 * 1024 * 1024
PRECHECK_WALL_TIMEOUT = 45.0
VERIFY_WALL_TIMEOUT = 240.0
READ_CHUNK_BYTES = 64 * 1024
MANIFEST_KEYS = {
    "schemaVersion",
    "sourceCommit",
    "publicSha256",
    "files",
    "catalogSha256",
    "netlifyTomlSha256",
}
CONTENT_IDENTITY_KEYS = (
    "publicSha256",
    "catalogSha256",
    "netlifyTomlSha256",
)
REQUIRED_CSP_DIRECTIVES = {
    "default-src": {"'self'"},
    "base-uri": {"'self'"},
    "object-src": {"'none'"},
    "frame-ancestors": {"'none'"},
    "script-src": {"'self'"},
    "style-src": {"'self'"},
    "img-src": {"'self'", "https:", "data:"},
    "connect-src": {"'self'"},
}
CACHE_POLICIES = {
    "": (0, True),
    HOME_RELATIVE_PATH: (0, True),
    CATALOG_RELATIVE_PATH: (300, True),
    FEATURED_COVERS_RELATIVE_PATH: (300, True),
    MANIFEST_RELATIVE_PATH: (None, False),
    "js/app.js": (3600, True),
    STYLESHEET_RELATIVE_PATH: (3600, True),
}


class ReleaseError(RuntimeError):
    """Release input or remote state is invalid."""


class RemoteFetchError(RuntimeError):
    """A retryable transport, size, or deadline failure."""


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_NO_REDIRECT_OPENER = build_opener(_NoRedirectHandler)


@dataclass(frozen=True)
class LiveCheck:
    state: str
    attempts: int
    detail: str


@dataclass(frozen=True)
class DownloadLimits:
    manifest_bytes: int
    file_bytes: int
    total_bytes: int
    wall_seconds: float


@dataclass(frozen=True)
class _RemoteResource:
    body: bytes
    headers: Mapping[str, Tuple[str, ...]]


@dataclass
class _DownloadBudget:
    limits: DownloadLimits
    deadline: float
    downloaded: int = 0

    def consume(self, amount: int) -> None:
        self.downloaded += amount
        if self.downloaded > self.limits.total_bytes:
            raise RemoteFetchError("total release byte limit exceeded")


def build_manifest(
    public_dir: Path,
    source_commit: str,
    netlify_config: Path,
) -> dict:
    """Build the release manifest without including the manifest in its own hash."""
    _validate_source_commit(source_commit)
    public_dir = Path(public_dir)
    netlify_config = Path(netlify_config)
    if not public_dir.is_dir():
        raise ReleaseError(f"public directory does not exist: {public_dir}")
    if not netlify_config.is_file():
        raise ReleaseError(f"netlify.toml does not exist: {netlify_config}")

    files = _collect_public_hashes(public_dir)
    _require_endpoint_files(files)
    manifest = {
        "schemaVersion": SCHEMA_VERSION,
        "sourceCommit": source_commit,
        "publicSha256": _aggregate_hash(files),
        "files": files,
        "catalogSha256": files[CATALOG_RELATIVE_PATH],
        "netlifyTomlSha256": _hash_file(netlify_config),
    }
    return _validate_manifest_dict(manifest)


def write_manifest(
    public_dir: Path,
    source_commit: str,
    netlify_config: Path,
) -> dict:
    """Atomically write a compact UTF-8 release manifest."""
    manifest = build_manifest(public_dir, source_commit, netlify_config)
    target = Path(public_dir) / MANIFEST_RELATIVE_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.tmp")
    payload = _json_bytes(manifest)
    try:
        temporary.write_bytes(payload)
        temporary.replace(target)
    except BaseException:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise
    return manifest


def parse_manifest(raw: bytes) -> dict:
    """Parse and structurally validate a release manifest."""
    if not isinstance(raw, (bytes, bytearray)):
        raise ReleaseError("release manifest must be bytes")
    try:
        text = bytes(raw).decode("utf-8")
        value = json.loads(text, object_pairs_hook=_unique_object)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ReleaseError(f"malformed release manifest: {error}") from error
    return _validate_manifest_dict(value)


def validate_local_release(
    manifest: Mapping[str, object],
    public_dir: Path,
    netlify_config: Path,
) -> dict:
    """Verify a manifest against every local public byte and netlify.toml."""
    expected = _validate_manifest_dict(manifest)
    actual_files = _collect_public_hashes(Path(public_dir))
    expected_files = expected["files"]
    if set(actual_files) != set(expected_files):
        missing = sorted(set(expected_files) - set(actual_files))
        extra = sorted(set(actual_files) - set(expected_files))
        raise ReleaseError(
            f"public file set mismatch (missing={missing}, extra={extra})"
        )
    for relative_path, expected_hash in expected_files.items():
        if actual_files[relative_path] != expected_hash:
            raise ReleaseError(
                f"public file hash mismatch: {relative_path}"
            )
    netlify_hash = _hash_file(Path(netlify_config))
    if netlify_hash != expected["netlifyTomlSha256"]:
        raise ReleaseError("netlify.toml hash mismatch")
    return expected


def content_identity_matches(
    local_manifest: Mapping[str, object],
    live_manifest: Mapping[str, object],
) -> bool:
    """Compare deployable bytes/config while intentionally ignoring source commit."""
    local = _validate_manifest_dict(local_manifest)
    live = _validate_manifest_dict(live_manifest)
    return all(local[key] == live[key] for key in CONTENT_IDENTITY_KEYS)


def exact_identity_matches(
    local_manifest: Mapping[str, object],
    live_manifest: Mapping[str, object],
) -> bool:
    """Compare the complete release manifest identity."""
    local = _validate_manifest_dict(local_manifest)
    live = _validate_manifest_dict(live_manifest)
    return local == live


def compare_live_release(
    local_manifest: Mapping[str, object],
    base_url: str,
    *,
    attempts: int = 3,
    timeout: float = 10.0,
    delay: float = 2.0,
    limits: Optional[DownloadLimits] = None,
) -> LiveCheck:
    """Retry a complete content pre-check and classify production state."""
    local = _validate_manifest_dict(local_manifest)
    _validate_network_options(base_url, attempts, timeout, delay)
    limits = limits or DownloadLimits(
        MAX_MANIFEST_BYTES,
        MAX_PUBLIC_FILE_BYTES,
        MAX_RELEASE_BYTES,
        PRECHECK_WALL_TIMEOUT,
    )
    _validate_download_limits(limits)
    deadline = time.monotonic() + limits.wall_seconds
    mismatch_detail = None
    unavailable_detail = None
    performed_attempts = 0
    for attempt in range(1, attempts + 1):
        if time.monotonic() >= deadline:
            unavailable_detail = "production pre-check wall-clock deadline exceeded"
            break
        performed_attempts = attempt
        budget = _DownloadBudget(limits, deadline)
        try:
            raw = _fetch_bytes(
                base_url,
                MANIFEST_RELATIVE_PATH,
                timeout,
                attempt,
                budget,
                limits.manifest_bytes,
                "release manifest",
            )
            live = parse_manifest(raw)
            if content_identity_matches(local, live):
                _verify_remote_files(
                    local,
                    base_url,
                    timeout,
                    attempt,
                    budget,
                )
                return LiveCheck(
                    "matching",
                    attempt,
                    "content identity and all public files match",
                )
            mismatch_detail = "live release content identity differs"
        except ReleaseError as error:
            mismatch_detail = str(error)
        except (
            RemoteFetchError,
            HTTPError,
            URLError,
            TimeoutError,
            socket.timeout,
            OSError,
            http.client.HTTPException,
        ) as error:
            unavailable_detail = _error_detail(error)
        if attempt < attempts and not _retry_pause(delay, deadline):
            unavailable_detail = "production pre-check wall-clock deadline exceeded"
            break
    if mismatch_detail is not None:
        return LiveCheck("mismatching", performed_attempts, mismatch_detail)
    return LiveCheck(
        "unavailable",
        performed_attempts,
        unavailable_detail or "production release is unavailable",
    )


def verify_live_release(
    local_manifest: Mapping[str, object],
    base_url: str,
    *,
    attempts: int = 6,
    timeout: float = 30.0,
    delay: float = 10.0,
    limits: Optional[DownloadLimits] = None,
) -> LiveCheck:
    """Verify exact manifest identity, every public byte, endpoints, and 404."""
    local = _validate_manifest_dict(local_manifest)
    _validate_network_options(base_url, attempts, timeout, delay)
    limits = limits or DownloadLimits(
        MAX_MANIFEST_BYTES,
        MAX_PUBLIC_FILE_BYTES,
        MAX_RELEASE_BYTES,
        VERIFY_WALL_TIMEOUT,
    )
    _validate_download_limits(limits)
    deadline = time.monotonic() + limits.wall_seconds
    mismatch_detail = None
    unavailable_detail = None
    performed_attempts = 0

    for attempt in range(1, attempts + 1):
        if time.monotonic() >= deadline:
            unavailable_detail = "production verification wall-clock deadline exceeded"
            break
        performed_attempts = attempt
        budget = _DownloadBudget(limits, deadline)
        try:
            manifest_resource = _fetch_resource(
                base_url,
                MANIFEST_RELATIVE_PATH,
                timeout,
                attempt,
                budget,
                limits.manifest_bytes,
                "release manifest",
            )
            live = parse_manifest(manifest_resource.body)
            if not exact_identity_matches(local, live):
                raise ReleaseError("live release manifest identity differs")

            _verify_remote_files(
                local,
                base_url,
                timeout,
                attempt,
                budget,
                require_production_policy=True,
                manifest_headers=manifest_resource.headers,
            )
            return LiveCheck(
                "matching",
                attempt,
                "exact release and all public files verified",
            )
        except ReleaseError as error:
            mismatch_detail = str(error)
        except (
            RemoteFetchError,
            HTTPError,
            URLError,
            TimeoutError,
            socket.timeout,
            OSError,
            http.client.HTTPException,
        ) as error:
            unavailable_detail = _error_detail(error)
        if attempt < attempts and not _retry_pause(delay, deadline):
            unavailable_detail = "production verification wall-clock deadline exceeded"
            break

    if mismatch_detail is not None:
        return LiveCheck("mismatching", performed_attempts, mismatch_detail)
    return LiveCheck(
        "unavailable",
        performed_attempts,
        unavailable_detail or "production release is unavailable",
    )


def _verify_remote_files(
    manifest: Mapping[str, object],
    base_url: str,
    timeout: float,
    nonce: int,
    budget: _DownloadBudget,
    *,
    require_production_policy: bool = False,
    manifest_headers: Optional[Mapping[str, Tuple[str, ...]]] = None,
) -> None:
    response_headers = {}
    if manifest_headers is not None:
        response_headers[MANIFEST_RELATIVE_PATH] = manifest_headers
    for relative_path, expected_hash in manifest["files"].items():
        try:
            resource = _fetch_resource(
                base_url,
                relative_path,
                timeout,
                nonce,
                budget,
                budget.limits.file_bytes,
                relative_path,
            )
        except HTTPError as error:
            if error.code == 404:
                raise ReleaseError(
                    f"live public file is missing: {relative_path}"
                ) from error
            raise
        if _hash_bytes(resource.body) != expected_hash:
            raise ReleaseError(
                f"live public file hash mismatch: {relative_path}"
            )
        response_headers[relative_path] = resource.headers

    home_resource = _fetch_resource(
        base_url,
        "",
        timeout,
        nonce,
        budget,
        budget.limits.file_bytes,
        "home page",
    )
    if _hash_bytes(home_resource.body) != manifest["files"][HOME_RELATIVE_PATH]:
        raise ReleaseError("live home page hash mismatch")
    response_headers[""] = home_resource.headers

    status = _fetch_status(
        base_url,
        MISSING_PROBE_RELATIVE_PATH,
        timeout,
        nonce,
        budget,
    )
    if status != 404:
        raise ReleaseError(
            "missing JavaScript probe must return 404 "
            f"(received {status})"
        )

    if not require_production_policy:
        return

    for relative_path in (
        FEATURED_COVERS_RELATIVE_PATH,
        STYLESHEET_RELATIVE_PATH,
    ):
        if relative_path in response_headers:
            continue
        resource = _fetch_resource(
            base_url,
            relative_path,
            timeout,
            nonce,
            budget,
            budget.limits.file_bytes,
            relative_path,
        )
        response_headers[relative_path] = resource.headers

    for relative_path, headers in response_headers.items():
        _validate_security_headers(relative_path, headers)
    for relative_path, (max_age, must_revalidate) in CACHE_POLICIES.items():
        headers = response_headers.get(relative_path)
        if headers is None:
            raise ReleaseError(
                f"production policy resource was not verified: /{relative_path}"
            )
        _validate_cache_policy(
            relative_path,
            headers,
            max_age=max_age,
            must_revalidate=must_revalidate,
        )

    for relative_path in PRIVATE_PROBE_RELATIVE_PATHS:
        private_status = _fetch_status(
            base_url,
            relative_path,
            timeout,
            nonce,
            budget,
            label=f"private path /{relative_path}",
        )
        if private_status != 404:
            raise ReleaseError(
                f"private path /{relative_path} must return 404 "
                f"(received {private_status})"
            )


def validate_deploy_json(
    raw: bytes,
    expected_site_id: str = SITE_ID,
    production_url: str = PRODUCTION_URL,
) -> dict:
    """Validate the identity returned by ``netlify deploy --json``."""
    if not isinstance(raw, (bytes, bytearray)):
        raise ReleaseError("deploy JSON must be bytes")
    try:
        payload = json.loads(
            bytes(raw).decode("utf-8"),
            object_pairs_hook=_unique_object,
        )
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ReleaseError(f"malformed deploy JSON: {error}") from error
    if not isinstance(payload, dict):
        raise ReleaseError("deploy JSON must be an object")

    site_id = payload.get("site_id")
    deploy_id = payload.get("deploy_id")
    url = payload.get("url")
    deploy_url = payload.get("deploy_url")
    if site_id != expected_site_id:
        raise ReleaseError("deploy JSON site_id does not match the production project")
    if not isinstance(deploy_id, str) or not DEPLOY_ID_PATTERN.fullmatch(deploy_id):
        raise ReleaseError("deploy JSON has an invalid deploy_id")
    if url != production_url:
        raise ReleaseError("deploy JSON production URL does not match")
    expected_host = urlsplit(production_url).hostname
    deploy_parts = _validated_https_url(deploy_url, "deploy_url")
    if deploy_parts.hostname != f"{deploy_id}--{expected_host}":
        raise ReleaseError("deploy JSON deploy_url does not match deploy_id/site")
    _validated_https_url(url, "url")
    return payload


def _validate_manifest_dict(value: object) -> dict:
    if not isinstance(value, Mapping):
        raise ReleaseError("release manifest must be a JSON object")
    if set(value) != MANIFEST_KEYS:
        raise ReleaseError("release manifest has missing or unexpected fields")
    schema_version = value.get("schemaVersion")
    if (
        isinstance(schema_version, bool)
        or not isinstance(schema_version, int)
        or schema_version != SCHEMA_VERSION
    ):
        raise ReleaseError("unexpected release manifest schema")

    source_commit = value.get("sourceCommit")
    _validate_source_commit(source_commit)
    files_value = value.get("files")
    if not isinstance(files_value, Mapping) or not files_value:
        raise ReleaseError("release manifest files must be a non-empty object")

    files: Dict[str, str] = {}
    for relative_path, digest in files_value.items():
        _validate_relative_path(relative_path)
        _validate_hash(digest, f"file hash for {relative_path}")
        files[relative_path] = digest
    files = dict(sorted(files.items()))
    _require_endpoint_files(files)

    public_hash = value.get("publicSha256")
    catalog_hash = value.get("catalogSha256")
    netlify_hash = value.get("netlifyTomlSha256")
    _validate_hash(public_hash, "publicSha256")
    _validate_hash(catalog_hash, "catalogSha256")
    _validate_hash(netlify_hash, "netlifyTomlSha256")
    if catalog_hash != files[CATALOG_RELATIVE_PATH]:
        raise ReleaseError("catalogSha256 does not match the catalog file hash")
    if public_hash != _aggregate_hash(files):
        raise ReleaseError("publicSha256 does not match the file hash aggregate")

    return {
        "schemaVersion": SCHEMA_VERSION,
        "sourceCommit": source_commit,
        "publicSha256": public_hash,
        "files": files,
        "catalogSha256": catalog_hash,
        "netlifyTomlSha256": netlify_hash,
    }


def _collect_public_hashes(public_dir: Path) -> Dict[str, str]:
    if not public_dir.is_dir():
        raise ReleaseError(f"public directory does not exist: {public_dir}")
    files = {}
    for path in sorted(public_dir.rglob("*")):
        if path.is_symlink():
            raise ReleaseError(f"public tree contains a symbolic link: {path}")
        if not path.is_file():
            continue
        relative_path = path.relative_to(public_dir).as_posix()
        if relative_path == MANIFEST_RELATIVE_PATH or relative_path in DEPLOY_CONTROL_PATHS:
            continue
        _validate_relative_path(relative_path)
        files[relative_path] = _hash_file(path)
    return dict(sorted(files.items()))


def _aggregate_hash(files: Mapping[str, str]) -> str:
    framed = b"".join(
        relative_path.encode("utf-8")
        + b"\0"
        + digest.encode("ascii")
        + b"\n"
        for relative_path, digest in sorted(files.items())
    )
    return _hash_bytes(framed)


def _fetch_bytes(
    base_url: str,
    relative_path: str,
    timeout: float,
    nonce: int,
    budget: _DownloadBudget,
    max_bytes: int,
    label: str,
) -> bytes:
    return _fetch_resource(
        base_url,
        relative_path,
        timeout,
        nonce,
        budget,
        max_bytes,
        label,
    ).body


def _fetch_resource(
    base_url: str,
    relative_path: str,
    timeout: float,
    nonce: int,
    budget: _DownloadBudget,
    max_bytes: int,
    label: str,
) -> _RemoteResource:
    request = _request(base_url, relative_path, nonce)
    request_timeout = _bounded_timeout(timeout, budget.deadline)
    try:
        response = _NO_REDIRECT_OPENER.open(
            request,
            timeout=request_timeout,
        )
    except HTTPError as error:
        error.close()
        raise
    with response:
        body = _read_limited(
            response,
            budget,
            max_bytes,
            label,
            request_timeout,
        )
        headers = _normalize_headers(response.headers)
    return _RemoteResource(body, headers)


def _fetch_status(
    base_url: str,
    relative_path: str,
    timeout: float,
    nonce: int,
    budget: _DownloadBudget,
    label: str = "missing JavaScript probe",
) -> int:
    request = _request(base_url, relative_path, nonce)
    try:
        request_timeout = _bounded_timeout(timeout, budget.deadline)
        with _NO_REDIRECT_OPENER.open(
            request,
            timeout=request_timeout,
        ) as response:
            _read_limited(
                response,
                budget,
                budget.limits.file_bytes,
                label,
                request_timeout,
            )
            return response.status
    except HTTPError as error:
        if error.code != 404:
            error.close()
            raise
        error.close()
        return 404


def _normalize_headers(headers) -> Dict[str, Tuple[str, ...]]:
    collected: Dict[str, list] = {}
    for name, value in headers.items():
        key = name.lower()
        cleaned = value.strip()
        collected.setdefault(key, []).append(cleaned)
    return {key: tuple(values) for key, values in collected.items()}


def _validate_security_headers(
    relative_path: str,
    headers: Mapping[str, Tuple[str, ...]],
) -> None:
    label = f"/{relative_path}" if relative_path else "/"
    required_exact = {
        "x-content-type-options": ("nosniff", "X-Content-Type-Options"),
        "x-frame-options": ("deny", "X-Frame-Options"),
        "referrer-policy": (
            "strict-origin-when-cross-origin",
            "Referrer-Policy",
        ),
    }
    for header, (expected, display_name) in required_exact.items():
        actual = _single_header_value(headers, header)
        if actual is None or actual.lower() != expected:
            raise ReleaseError(
                f"{label} has invalid or missing {display_name} header"
            )

    permissions = _single_header_value(headers, "permissions-policy")
    if permissions is None:
        raise ReleaseError(
            f"{label} has invalid or missing Permissions-Policy header"
        )
    permission_directives = {
        directive.strip().lower().replace(" ", "")
        for directive in permissions.split(",")
        if directive.strip()
    }
    required_permissions = {"camera=()", "geolocation=()", "microphone=()"}
    if not required_permissions.issubset(permission_directives):
        raise ReleaseError(f"{label} has invalid Permissions-Policy header")

    csp_fields = headers.get("content-security-policy")
    if not csp_fields:
        raise ReleaseError(f"{label} is missing Content-Security-Policy header")
    policies = []
    for field in csp_fields:
        field_policies = field.split(",")
        if any(not policy.strip() for policy in field_policies):
            raise ReleaseError(
                f"{label} has malformed Content-Security-Policy header"
            )
        policies.extend(policy.strip() for policy in field_policies)
    for policy in policies:
        _validate_csp_policy(label, policy)


def _validate_cache_policy(
    relative_path: str,
    headers: Mapping[str, Tuple[str, ...]],
    *,
    max_age: Optional[int],
    must_revalidate: bool,
) -> None:
    label = f"/{relative_path}" if relative_path else "/"
    values = headers.get("cache-control")
    if not values:
        raise ReleaseError(f"{label} is missing Cache-Control header")
    directives = {}
    for value in values:
        raw_directives = value.split(",")
        if any(not directive.strip() for directive in raw_directives):
            raise ReleaseError(f"{label} has malformed Cache-Control header")
        for raw_directive in raw_directives:
            match = re.fullmatch(
                r"\s*([A-Za-z][A-Za-z0-9-]*)\s*"
                r"(?:=\s*([^\s,]+)\s*)?",
                raw_directive,
            )
            if match is None:
                raise ReleaseError(
                    f"{label} has malformed Cache-Control directive"
                )
            name = match.group(1).lower()
            directive_value = match.group(2)
            if name in directives:
                raise ReleaseError(
                    f"{label} has duplicate Cache-Control directive {name}"
                )
            directives[name] = (
                directive_value.lower() if directive_value is not None else None
            )

    if max_age is None:
        expected_directives = {"no-store": None}
    else:
        expected_directives = {
            "public": None,
            "max-age": str(max_age),
        }
        if must_revalidate:
            expected_directives["must-revalidate"] = None
    if directives != expected_directives:
        expected = ", ".join(
            name if value is None else f"{name}={value}"
            for name, value in expected_directives.items()
        )
        raise ReleaseError(
            f"{label} Cache-Control must be exactly {expected}"
        )


def _single_header_value(
    headers: Mapping[str, Tuple[str, ...]],
    name: str,
) -> Optional[str]:
    values = headers.get(name)
    if values is None or len(values) != 1:
        return None
    return values[0].strip()


def _validate_csp_policy(label: str, policy: str) -> None:
    parsed_directives: Dict[str, set] = {}
    for raw_directive in policy.split(";"):
        tokens = raw_directive.split()
        if not tokens:
            continue
        name = tokens[0].lower()
        if name in parsed_directives:
            raise ReleaseError(
                f"{label} has duplicate Content-Security-Policy directive {name}"
            )
        sources = [token.lower() for token in tokens[1:]]
        if len(sources) != len(set(sources)):
            raise ReleaseError(
                f"{label} has duplicate Content-Security-Policy source in {name}"
            )
        parsed_directives[name] = set(sources)
    if parsed_directives != REQUIRED_CSP_DIRECTIVES:
        raise ReleaseError(
            f"{label} Content-Security-Policy must match the production baseline"
        )


def _read_limited(
    response,
    budget: _DownloadBudget,
    max_bytes: int,
    label: str,
    request_timeout: float,
) -> bytes:
    declared = _content_length(response, label)
    if declared is not None:
        if declared > max_bytes:
            raise RemoteFetchError(
                f"{label} Content-Length exceeds byte limit"
            )
        if budget.downloaded + declared > budget.limits.total_bytes:
            raise RemoteFetchError("total release byte limit exceeded")

    chunks = []
    received = 0
    while True:
        remaining_file = max_bytes - received
        remaining_total = budget.limits.total_bytes - budget.downloaded
        read_size = min(
            READ_CHUNK_BYTES,
            remaining_file + 1,
            remaining_total + 1,
        )
        if read_size <= 0:
            raise RemoteFetchError("total release byte limit exceeded")
        _set_response_timeout(
            response,
            budget.deadline,
            request_timeout,
        )
        reader = getattr(response, "read1", response.read)
        try:
            chunk = reader(read_size)
        except http.client.HTTPException as error:
            raise RemoteFetchError(
                f"incomplete {label} response: {error}"
            ) from error
        if time.monotonic() > budget.deadline:
            raise RemoteFetchError("wall-clock deadline exceeded")
        if not chunk:
            break
        received += len(chunk)
        budget.consume(len(chunk))
        if received > max_bytes:
            raise RemoteFetchError(f"{label} file byte limit exceeded")
        chunks.append(chunk)

    if declared is not None and received != declared:
        raise RemoteFetchError(
            f"incomplete {label} response "
            f"({received} of {declared} bytes)"
        )
    return b"".join(chunks)


def _content_length(response, label: str) -> Optional[int]:
    value = response.headers.get("Content-Length")
    if value is None:
        return None
    value = value.strip()
    if not value.isascii() or not value.isdecimal():
        raise RemoteFetchError(f"{label} has invalid Content-Length")
    declared = int(value)
    if declared < 0:
        raise RemoteFetchError(f"{label} has invalid Content-Length")
    return declared


def _bounded_timeout(timeout: float, deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise RemoteFetchError("wall-clock deadline exceeded")
    return min(timeout, remaining)


def _set_response_timeout(
    response,
    deadline: float,
    request_timeout: float,
) -> None:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise RemoteFetchError("wall-clock deadline exceeded")
    raw = getattr(getattr(response, "fp", None), "raw", None)
    network_socket = getattr(raw, "_sock", None)
    if network_socket is not None:
        network_socket.settimeout(min(request_timeout, remaining))


def _retry_pause(delay: float, deadline: float) -> bool:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return False
    if delay:
        time.sleep(min(delay, remaining))
    return time.monotonic() < deadline


def _request(base_url: str, relative_path: str, nonce: int) -> Request:
    encoded_path = "/".join(quote(part, safe="") for part in relative_path.split("/"))
    url = (
        f"{base_url.rstrip('/')}/{encoded_path}"
        f"?verify={time.time_ns()}-{nonce}"
    )
    return Request(
        url,
        headers={
            "Cache-Control": "no-cache, no-store, max-age=0",
            "Pragma": "no-cache",
            "User-Agent": "giga-catalog-release-check/1.0",
        },
    )


def _validate_network_options(
    base_url: str,
    attempts: int,
    timeout: float,
    delay: float,
) -> None:
    parts = urlsplit(base_url)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise ReleaseError("production URL must be HTTP(S)")
    if isinstance(attempts, bool) or not isinstance(attempts, int) or attempts <= 0:
        raise ReleaseError("attempts must be a positive integer")
    if timeout <= 0:
        raise ReleaseError("timeout must be positive")
    if delay < 0:
        raise ReleaseError("delay must be non-negative")


def _validate_download_limits(limits: DownloadLimits) -> None:
    for label, value in (
        ("manifest byte limit", limits.manifest_bytes),
        ("file byte limit", limits.file_bytes),
        ("total release byte limit", limits.total_bytes),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ReleaseError(f"{label} must be a positive integer")
    if (
        isinstance(limits.wall_seconds, bool)
        or not isinstance(limits.wall_seconds, (int, float))
        or limits.wall_seconds <= 0
    ):
        raise ReleaseError("wall-clock deadline must be positive")


def _validated_https_url(value: object, field: str):
    if not isinstance(value, str):
        raise ReleaseError(f"deploy JSON {field} must be a string")
    parts = urlsplit(value)
    try:
        port = parts.port
    except ValueError as error:
        raise ReleaseError(
            f"deploy JSON {field} must be a plain HTTPS URL"
        ) from error
    if (
        parts.scheme != "https"
        or not parts.hostname
        or parts.username is not None
        or parts.password is not None
        or port is not None
    ):
        raise ReleaseError(f"deploy JSON {field} must be a plain HTTPS URL")
    return parts


def _validate_source_commit(value: object) -> None:
    if not isinstance(value, str) or not COMMIT_PATTERN.fullmatch(value):
        raise ReleaseError("sourceCommit must be a full lowercase Git commit hash")


def _validate_hash(value: object, label: str) -> None:
    if not isinstance(value, str) or not HASH_PATTERN.fullmatch(value):
        raise ReleaseError(f"{label} must be a lowercase SHA-256")


def _validate_relative_path(value: object) -> None:
    if not isinstance(value, str) or not value or "\\" in value or "\0" in value:
        raise ReleaseError("manifest file path is malformed")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or value != path.as_posix()
        or any(part in {"", ".", ".."} for part in path.parts)
        or value == MANIFEST_RELATIVE_PATH
    ):
        raise ReleaseError(f"unsafe manifest file path: {value}")


def _require_endpoint_files(files: Mapping[str, str]) -> None:
    for required in (HOME_RELATIVE_PATH, CATALOG_RELATIVE_PATH):
        if required not in files:
            raise ReleaseError(f"release manifest is missing required file: {required}")


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ReleaseError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _hash_file(path: Path) -> str:
    if not path.is_file():
        raise ReleaseError(f"required file does not exist: {path}")
    return _hash_bytes(path.read_bytes())


def _hash_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _error_detail(error: BaseException) -> str:
    if isinstance(error, HTTPError):
        return f"HTTP {error.code}"
    return f"{type(error).__name__}: {error}"
