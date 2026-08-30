"""Parse GIGA product pages and refresh the catalog without broad crawling."""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from html import unescape
from html.parser import HTMLParser
import re
import time
from typing import Callable, Dict, Iterable, List, Optional, Tuple, Union
from urllib.parse import urljoin, urlparse

import requests

from src.giga_catalog.codes import normalize_code
from src.giga_catalog.previews import (
    preview_base_from_cover,
    preview_descriptor_from_cover,
)


BASE_URL = "https://www.giga-web.jp"
ROOT_URL = f"{BASE_URL}/"
TOP_URL = f"{BASE_URL}/top.php"
GATE_URL = f"{BASE_URL}/cookie_set.php"
SEARCH_URL = f"{BASE_URL}/search/index.php?count={{page}}&sort=1"
PRODUCT_URL = f"{BASE_URL}/product/index.php?product_id={{product_id}}"

_PRODUCT_ID_RE = re.compile(r"/product/index\.php\?[^\"']*product_id=(\d+)", re.I)
_CODE_RE = re.compile(
    r"(?<![A-Za-z0-9])([A-Za-z][A-Za-z0-9]*)\s*[-_ ]\s*(\d+)([A-Za-z]?)(?![A-Za-z0-9_-])"
)
_DATE_RE = re.compile(r"\b((?:19|20)\d{2})[/-](\d{1,2})[/-](\d{1,2})\b")
_CHARSET_RE = re.compile(r"charset\s*=\s*[\"']?([^\s;\"'>]+)", re.I)
_META_TAG_RE = re.compile(r"<meta\b[^>]*>", re.I)
_HTML_ATTR_RE = re.compile(
    r"([\w:-]+)\s*=\s*(?:\"([^\"]*)\"|'([^']*)'|([^\s\"'=<>`]+))", re.I
)
_VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
_TRANSIENT_STATUS_CODES = {408, 425, 429}


@dataclass
class _Node:
    tag: str
    attrs: Dict[str, str] = field(default_factory=dict)
    children: List[Union["_Node", str]] = field(default_factory=list)

    def attr(self, name: str) -> str:
        return self.attrs.get(name, "")


class _TreeParser(HTMLParser):
    """Small tolerant tree builder used only for bounded page containers."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _Node("document")
        self._stack = [self.root]

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        tag = tag.lower()
        node = _Node(tag, {name.lower(): value or "" for name, value in attrs})
        self._stack[-1].children.append(node)
        if tag not in _VOID_TAGS:
            self._stack.append(node)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        for index in range(len(self._stack) - 1, 0, -1):
            if self._stack[index].tag == tag:
                del self._stack[index:]
                return

    def handle_data(self, data: str) -> None:
        if data:
            self._stack[-1].children.append(data)

    def handle_comment(self, data: str) -> None:
        self._stack[-1].children.append(_Node("#comment", {"data": data}))


@dataclass
class SearchPageInspection:
    products: List[dict] = field(default_factory=list)
    total_cards: int = 0
    parsed_cards: int = 0
    unresolved_product_ids: List[int] = field(default_factory=list)
    unidentifiable_cards: int = 0
    duplicate_product_ids: List[int] = field(default_factory=list)


def decode_product_html(content: bytes, declared_encoding: Optional[str] = None) -> str:
    """Decode current UTF-8 pages with a strict, legacy-only CP932 fallback."""
    encoding = _declared_charset(declared_encoding)
    if encoding:
        return content.decode(encoding, "strict")

    try:
        return content.decode("utf-8", "strict")
    except UnicodeDecodeError:
        return content.decode("cp932", "strict")


def _declared_charset(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    match = _CHARSET_RE.search(value)
    if match:
        return match.group(1)
    candidate = value.strip()
    return candidate if candidate and "/" not in candidate and ";" not in candidate else None


def _meta_charset(content: bytes) -> Optional[str]:
    prefix = content[:4096].decode("ascii", "ignore")
    for tag in _META_TAG_RE.findall(prefix):
        attributes = {
            name.lower(): next((value for value in values if value), "")
            for name, *values in _HTML_ATTR_RE.findall(tag)
        }
        charset = attributes.get("charset")
        if charset:
            return charset
        if attributes.get("http-equiv", "").lower() == "content-type":
            return _declared_charset(attributes.get("content"))
    return None


def parse_product_page(html: str, product_id: int) -> Optional[dict]:
    """Return the validated catalog record from one product detail page."""
    root = _parse_html(html)
    works_pic = _find_by_id(root, "works_pic")
    works_txt = _find_by_id(root, "works_txt")
    if works_pic is None or works_txt is None:
        return None

    title_node = _first_descendant(works_pic, lambda node: node.tag == "h5")
    fields = _definition_fields(works_txt)
    code = _extract_code(_text(_field_value(fields, "作品番号")))
    cover_node = _matching_cover(works_pic, code)
    if title_node is None or cover_node is None or code is None:
        return None

    actors = _actors_from_container(_field_value(fields, "出演女優"))
    release_date = _normalize_date(_text(_field_value(fields, "DVDリリース日")))
    cover = urljoin(BASE_URL, cover_node.attr("src"))
    record = _record(
        product_id,
        code,
        _text(title_node),
        actors,
        release_date,
        cover,
    )
    preview_count = _contiguous_sample_count(root, cover)
    if preview_count > 0:
        record["previewCount"] = preview_count
    return record


def parse_search_page(html: str) -> List[dict]:
    """Extract product cards only from ``thumBox`` containers on a directory page."""
    return inspect_search_page(html).products


def inspect_search_page(html: str) -> SearchPageInspection:
    """Return card-level reconciliation evidence for a directory page."""
    root = _parse_html(html)
    products: List[dict] = []
    total_cards = 0
    parsed_cards = 0
    unresolved_product_ids = []
    unidentifiable_cards = 0
    product_id_counts: Dict[int, int] = {}
    seen_ids = set()
    for card in _descendants(root):
        if "thumbox" not in {part.lower() for part in card.attr("class").split()}:
            continue
        total_cards += 1
        product_id = _search_card_product_id(card)
        if product_id is not None:
            product_id_counts[product_id] = product_id_counts.get(product_id, 0) + 1
        record = _parse_search_card(card)
        if record is None:
            if product_id is None:
                unidentifiable_cards += 1
            else:
                unresolved_product_ids.append(product_id)
            continue
        parsed_cards += 1
        if record["productId"] not in seen_ids:
            products.append(record)
            seen_ids.add(record["productId"])
    return SearchPageInspection(
        products=products,
        total_cards=total_cards,
        parsed_cards=parsed_cards,
        unresolved_product_ids=unresolved_product_ids,
        unidentifiable_cards=unidentifiable_cards,
        duplicate_product_ids=sorted(
            product_id
            for product_id, count in product_id_counts.items()
            if count > 1
        ),
    )


def create_session(
    base_url: str = BASE_URL,
    timeout: float = 20,
    retries: int = 3,
    delay_seconds: float = 1.0,
) -> requests.Session:
    """Create one age-gated session before issuing catalog requests."""
    if retries <= 0:
        raise ValueError("retries must be positive")
    if delay_seconds < 0:
        raise ValueError("delay_seconds must be non-negative")
    root_url, top_url, gate_url, _, _ = _catalog_urls(base_url)
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": "GIGA-Catalog/1.0 (+catalog refresh)",
            "Accept-Language": "ja,en;q=0.5",
        }
    )
    for attempt in range(1, retries + 1):
        try:
            response = session.get(
                gate_url,
                headers={"Referer": root_url},
                allow_redirects=False,
                timeout=timeout,
            )
        except (requests.RequestException, OSError) as error:
            failure = f"{type(error).__name__}: {error}"
            transient = True
        else:
            if (
                response.status_code == 302
                and session.cookies.get("old_check") == "yes"
            ):
                # Current product detail pages redirect to top.php unless the
                # request came from a same-site catalog page.
                session.headers["Referer"] = f"{base_url.rstrip('/')}/search/"
                return session
            failure = f"http_{response.status_code}"
            transient = (
                response.status_code in _TRANSIENT_STATUS_CODES
                or 500 <= response.status_code < 600
            )
        if not transient or attempt == retries:
            raise RuntimeError(
                "GIGA age-gate session was not established after "
                f"{attempt} attempts: {failure}"
            )
        if delay_seconds > 0:
            time.sleep(delay_seconds * (2 ** (attempt - 1)))
    raise RuntimeError("GIGA age-gate session was not established")


def discover_products(
    existing: List[dict],
    mode: str = "incremental",
    page_limit: Optional[int] = None,
    delay_seconds: float = 1.0,
    fetch: Optional[Callable] = None,
    *,
    base_url: str = BASE_URL,
    timeout: float = 20,
    retries: int = 3,
    include_known: bool = False,
) -> Tuple[List[dict], dict]:
    """Fetch only unknown directory cards, with audit and sparse-tail modes."""
    if mode not in {"incremental", "audit", "tail"}:
        raise ValueError("mode must be 'incremental', 'audit', or 'tail'")
    if page_limit is not None and page_limit < 0:
        raise ValueError("page_limit must be non-negative")
    if timeout <= 0:
        raise ValueError("timeout must be positive")
    if retries <= 0:
        raise ValueError("retries must be positive")

    _, top_url, _, search_url, product_url = _catalog_urls(base_url)
    uses_live_fetch = fetch is None
    if fetch is None:
        session = create_session(
            base_url=base_url,
            timeout=timeout,
            retries=retries,
            delay_seconds=delay_seconds,
        )

        def fetch(url: str):
            return session.get(
                url,
                headers={"Referer": top_url},
                allow_redirects=False,
                timeout=timeout,
            )

    known_products = _known_products_by_id(existing)
    known_ids = set(known_products)
    if mode == "tail":
        effective_delay = max(delay_seconds, 2.0) if uses_live_fetch else delay_seconds
        return _discover_tail(
            known_ids,
            page_limit,
            effective_delay,
            fetch,
            product_url,
            retries,
        )
    return _discover_directory(
        known_ids,
        mode,
        page_limit,
        delay_seconds,
        fetch,
        search_url,
        product_url,
        retries,
        include_known,
        known_products,
    )


def _discover_directory(
    known_ids: set,
    mode: str,
    page_limit: Optional[int],
    delay_seconds: float,
    fetch: Callable,
    search_url: str = SEARCH_URL,
    product_url: str = PRODUCT_URL,
    retry_attempts: int = 3,
    include_known: bool = False,
    known_records: Optional[Dict[int, dict]] = None,
) -> Tuple[List[dict], dict]:
    discovered: List[dict] = []
    pages_fetched = 0
    parsed_products = 0
    known_products = 0
    retries = 0
    errors = 0
    consecutive_known = 0
    page = 1
    stop_reason = "page_limit"
    error = None
    returned_ids = set()
    new_products = 0
    cards_seen = 0
    detail_fallbacks = 0
    diagnostics = []
    page_reconciliation = []
    directory_ids = set()
    card_integrity_complete = True
    detail_reconciled = 0
    detail_missing = 0

    while page_limit is None or pages_fetched < page_limit:
        response, attempts, failed = _fetch_with_retry(
            search_url.format(page=page),
            fetch,
            delay_seconds,
            attempts=retry_attempts,
        )
        retries += attempts - 1
        if failed:
            errors += 1
            stop_reason = "error"
            error = _directory_error(response, retries_exhausted=True)
            break
        if not _is_directory_response(response):
            errors += 1
            stop_reason = "error"
            error = _directory_error(response)
            break

        pages_fetched += 1
        inspection = inspect_search_page(_response_html(response))
        cards_seen += inspection.total_cards
        page_records = {
            product["productId"]: product
            for product in inspection.products
        }
        failed_detail_ids = []
        for product_id in inspection.unresolved_product_ids:
            detail_response, attempts, failed = _fetch_with_retry(
                product_url.format(product_id=product_id),
                fetch,
                delay_seconds,
                attempts=retry_attempts,
            )
            retries += attempts - 1
            product = None
            if not failed and _is_directory_response(detail_response):
                product = parse_product_page(
                    _response_html(detail_response), product_id
                )
            if product is None:
                failed_detail_ids.append(product_id)
                diagnostics.append(
                    {
                        "type": "product_detail_unresolved",
                        "page": page,
                        "productId": product_id,
                    }
                )
            else:
                page_records[product_id] = product
                detail_fallbacks += 1

        duplicate_ids = set(inspection.duplicate_product_ids)
        duplicate_ids.update(
            product_id
            for product_id in page_records
            if product_id in directory_ids
        )
        diagnostics.extend(
            {
                "type": "duplicate_product_id",
                "page": page,
                "productId": product_id,
            }
            for product_id in sorted(duplicate_ids)
        )
        if inspection.unidentifiable_cards:
            diagnostics.append(
                {
                    "type": "unidentifiable_cards",
                    "page": page,
                    "count": inspection.unidentifiable_cards,
                }
            )

        page_products = list(page_records.values())
        parsed_products += len(page_products)
        page_reconciliation.append(
            {
                "page": page,
                "cards": inspection.total_cards,
                "resolved": len(page_records),
            }
        )
        directory_ids.update(page_records)
        page_integrity_complete = (
            not failed_detail_ids
            and not duplicate_ids
            and inspection.unidentifiable_cards == 0
            and inspection.total_cards == len(page_records)
        )
        if not page_integrity_complete:
            card_integrity_complete = False
            errors += 1
            stop_reason = "error"
            error = "unresolved_directory_cards"
            break
        if not page_products:
            stop_reason = "empty"
            break

        new_on_page = 0
        for product in page_products:
            product_id = product["productId"]
            if product_id in known_ids:
                known_products += 1
                if include_known and product_id not in returned_ids:
                    discovered.append(product)
                    returned_ids.add(product_id)
                continue
            discovered.append(product)
            returned_ids.add(product_id)
            known_ids.add(product_id)
            new_on_page += 1
            new_products += 1

        consecutive_known = consecutive_known + 1 if new_on_page == 0 else 0
        if mode == "incremental" and consecutive_known >= 2:
            stop_reason = "all_known"
            break
        page += 1

    if (
        mode == "audit"
        and include_known
        and stop_reason == "empty"
        and known_records
    ):
        for product_id in sorted(set(known_records) - directory_ids):
            detail_response, attempts, failed = _fetch_with_retry(
                product_url.format(product_id=product_id),
                fetch,
                delay_seconds,
                attempts=retry_attempts,
            )
            retries += attempts - 1
            if not failed and _is_top_redirect(detail_response):
                detail_missing += 1
                continue

            product = None
            if not failed and _is_directory_response(detail_response):
                product = parse_product_page(
                    _response_html(detail_response), product_id
                )
            previous_code = normalize_code(known_records[product_id].get("code"))
            if product is None or product.get("code") != previous_code:
                errors += 1
                stop_reason = "error"
                error = "omitted_product_detail_unresolved"
                diagnostics.append(
                    {
                        "type": "omitted_product_detail_unresolved",
                        "productId": product_id,
                    }
                )
                break

            discovered.append(copy.deepcopy(known_records[product_id]))
            returned_ids.add(product_id)
            parsed_products += 1
            known_products += 1
            detail_reconciled += 1

    summary = {
        "mode": mode,
        "pagesFetched": pages_fetched,
        "parsedProducts": parsed_products,
        "newProducts": new_products,
        "knownProducts": known_products,
        "cursor": page,
        "retries": retries,
        "errors": errors,
        "stopReason": stop_reason,
        "cardsSeen": cards_seen,
        "cardsResolved": len(directory_ids),
        "detailFallbacks": detail_fallbacks,
        "cardIntegrityComplete": card_integrity_complete,
        "pageReconciliation": page_reconciliation,
        "diagnostics": diagnostics,
    }
    if mode == "audit":
        summary["detailReconciled"] = detail_reconciled
        summary["detailMissing"] = detail_missing
    if error is not None:
        summary["error"] = error
    return discovered, summary


def _discover_tail(
    known_ids: set,
    page_limit: Optional[int],
    delay_seconds: float,
    fetch: Callable,
    product_url: str = PRODUCT_URL,
    retry_attempts: int = 3,
) -> Tuple[List[dict], dict]:
    discovered: List[dict] = []
    retries = 0
    errors = 0
    misses = 0
    probes = 0
    product_id = max(known_ids) + 1 if known_ids else 1
    cursor = product_id - 1
    stop_reason = "page_limit"

    while page_limit is None or probes < page_limit:
        response, attempts, failed = _fetch_with_retry(
            product_url.format(product_id=product_id),
            fetch,
            delay_seconds,
            attempts=retry_attempts,
        )
        retries += attempts - 1
        probes += 1
        cursor = product_id
        if failed:
            errors += 1
            misses = 0
            product_id += 1
            continue

        product = None if _is_top_redirect(response) else parse_product_page(
            _response_html(response), product_id
        )
        if product is None:
            misses += 1
            if misses == 3:
                stop_reason = "three_misses"
                break
        else:
            discovered.append(product)
            known_ids.add(product_id)
            misses = 0
        product_id += 1

    return discovered, {
        "mode": "tail",
        "pagesFetched": 0,
        "parsedProducts": len(discovered),
        "newProducts": len(discovered),
        "knownProducts": 0,
        "cursor": cursor,
        "retries": retries,
        "errors": errors,
        "tailProbes": probes,
        "tailMisses": misses,
        "stopReason": stop_reason,
    }


def _fetch_with_retry(
    url: str, fetch: Callable, delay_seconds: float, attempts: int = 3
) -> Tuple[object, int, bool]:
    for attempt in range(1, attempts + 1):
        if delay_seconds > 0:
            time.sleep(delay_seconds * (2 ** (attempt - 1)))
        try:
            response = fetch(url)
        except (requests.RequestException, OSError):
            if attempt == attempts:
                return None, attempt, True
            continue
        status_code = getattr(response, "status_code", 200)
        if (
            status_code in _TRANSIENT_STATUS_CODES
            or 500 <= status_code < 600
        ):
            if attempt == attempts:
                return response, attempt, True
            continue
        return response, attempt, False
    return None, attempts, True


def _response_html(response: object) -> str:
    if isinstance(response, str):
        return response
    if isinstance(response, bytes):
        return decode_product_html(response)
    content = getattr(response, "content", None)
    if content is not None:
        headers = getattr(response, "headers", {}) or {}
        content_type = headers.get("Content-Type") or headers.get("content-type")
        encoding = _declared_charset(content_type) or _meta_charset(content)
        return decode_product_html(content, encoding)
    text = getattr(response, "text", None)
    if isinstance(text, str):
        return text
    raise TypeError("fetch must return HTML text, bytes, or a response-like object")


def _is_top_redirect(response: object) -> bool:
    headers = getattr(response, "headers", {}) or {}
    location = headers.get("Location") or headers.get("location") or ""
    final_url = getattr(response, "url", "") or ""
    return _is_top_url(location) or _is_top_url(final_url)


def _is_directory_response(response: object) -> bool:
    return getattr(response, "status_code", 200) == 200 and not _is_top_redirect(response)


def _directory_error(response: object, retries_exhausted: bool = False) -> str:
    if response is None:
        return "network_retries_exhausted"
    if _is_top_redirect(response):
        return "redirect_to_top"
    status_code = getattr(response, "status_code", 0)
    error = f"http_{status_code}"
    if retries_exhausted:
        return f"{error}_retries_exhausted"
    return error


def _is_top_url(url: str) -> bool:
    return urlparse(urljoin(BASE_URL, url)).path.rstrip("/").endswith("/top.php")


def _catalog_urls(base_url: str) -> Tuple[str, str, str, str, str]:
    base = base_url.rstrip("/")
    if not base:
        raise ValueError("base_url must not be empty")
    root = f"{base}/"
    top = f"{base}/top.php"
    gate = f"{base}/cookie_set.php"
    search = f"{base}/search/index.php?count={{page}}&sort=1"
    product = f"{base}/product/index.php?product_id={{product_id}}"
    return root, top, gate, search, product


def _parse_html(html: str) -> _Node:
    parser = _TreeParser()
    parser.feed(html)
    parser.close()
    return parser.root


def _descendants(node: Optional[_Node]) -> Iterable[_Node]:
    if node is None:
        return
    for child in node.children:
        if isinstance(child, _Node):
            if child.tag != "#comment":
                yield child
                yield from _descendants(child)


def _find_by_id(root: _Node, element_id: str) -> Optional[_Node]:
    return _first_descendant(root, lambda node: node.attr("id") == element_id)


def _first_descendant(node: Optional[_Node], predicate: Callable[[_Node], bool]) -> Optional[_Node]:
    if node is None:
        return None
    for descendant in _descendants(node):
        if predicate(descendant):
            return descendant
    return None


def _text(node: Optional[_Node]) -> str:
    if node is None:
        return ""
    pieces: List[str] = []

    def collect(current: _Node) -> None:
        for child in current.children:
            if isinstance(child, str):
                pieces.append(child)
            elif child.tag != "#comment":
                collect(child)

    collect(node)
    return " ".join(unescape(" ".join(pieces)).split())


def _definition_fields(container: _Node) -> Dict[str, _Node]:
    fields: Dict[str, _Node] = {}
    for definition in _descendants(container):
        if definition.tag != "dl":
            continue
        dt = _first_descendant(definition, lambda node: node.tag == "dt")
        dd = _first_descendant(definition, lambda node: node.tag == "dd")
        if dt is not None and dd is not None:
            fields[_normalize_label(_text(dt))] = dd
    return fields


def _field_value(fields: Dict[str, _Node], label: str) -> Optional[_Node]:
    wanted = _normalize_label(label)
    exact = fields.get(wanted)
    if exact is not None:
        return exact
    if wanted == "DVDリリース日":
        for field_label, value in fields.items():
            if "DVD" in field_label and "日" in field_label:
                return value
    return None


def _normalize_label(value: str) -> str:
    return re.sub(r"[\s:：]", "", value)


def _extract_code(value: str) -> Optional[str]:
    match = _CODE_RE.search(value)
    if match is None:
        return None
    prefix, digits, variant = match.groups()
    if variant:
        return f"{prefix.upper()}-{int(digits)}{variant.upper()}"
    return normalize_code(f"{prefix}-{digits}")


def _normalize_date(value: str) -> str:
    match = _DATE_RE.search(value)
    if match is None:
        return value.strip()
    year, month, day = match.groups()
    return f"{year}-{int(month):02d}-{int(day):02d}"


def _actors_from_container(container: Optional[_Node]) -> List[str]:
    if container is None:
        return []
    anchors = [node for node in _descendants(container) if node.tag == "a"]
    if anchors:
        return _deduplicated(_text(anchor) for anchor in anchors)
    return _actors_from_nodes(container, include_comments=False)


def _actors_from_nodes(container: _Node, include_comments: bool) -> List[str]:
    actors: List[str] = []
    holders = [
        node
        for node in _descendants(container)
        if {part.lower() for part in node.attr("class").split()}
        & {"yaku", "actor", "actress"}
    ]
    for holder in holders:
        anchors = [node for node in _descendants(holder) if node.tag == "a"]
        if anchors:
            actors.extend(_text(anchor) for anchor in anchors)
        else:
            actors.append(_text(holder))
    if not holders:
        for anchor in _descendants(container):
            if anchor.tag == "a" and "product/index.php" not in anchor.attr("href"):
                actors.append(_text(anchor))
    if include_comments:
        for child in _comment_nodes(container):
            markup = child.attr("data")
            if "<" not in markup:
                continue
            actor_label = re.search(r"出演\s*[:：]?", markup)
            if actor_label is not None:
                markup = re.split(
                    r"<br\s*/?>",
                    markup[actor_label.end() :],
                    maxsplit=1,
                    flags=re.I,
                )[0]
            actors.extend(_actors_from_nodes(_parse_html(markup), False))
    return _deduplicated(actors)


def _comment_nodes(node: _Node) -> Iterable[_Node]:
    for child in node.children:
        if isinstance(child, _Node):
            if child.tag == "#comment":
                yield child
            else:
                yield from _comment_nodes(child)


def _deduplicated(values: Iterable[str]) -> List[str]:
    result: List[str] = []
    for value in values:
        value = value.strip()
        if value and value not in result:
            result.append(value)
    return result


def _record(
    product_id: int,
    code: str,
    title: str,
    actors: List[str],
    release_date: str,
    cover: str,
) -> dict:
    series, suffix = code.rsplit("-", 1)
    suffix_match = re.fullmatch(r"(\d+)(?:[A-Z])?", suffix)
    if suffix_match is None:
        raise ValueError("source product code has an invalid suffix")
    number = int(suffix_match.group(1))
    record = {
        "productId": int(product_id),
        "code": code,
        "series": series,
        "number": number,
        "title": title,
        "actors": actors,
        "releaseDate": release_date,
        "cover": cover,
    }
    record.update(preview_descriptor_from_cover(cover))
    return record


def _contiguous_sample_count(root: _Node, cover: str) -> int:
    preview_base = preview_base_from_cover(cover)
    if preview_base is None:
        return 0
    expected = urlparse(preview_base)
    indexes = set()
    for node in _descendants(root):
        for attribute in ("href", "src", "data-src"):
            value = node.attr(attribute)
            if not value:
                continue
            parsed = urlparse(urljoin(BASE_URL, value))
            if (
                parsed.scheme.lower() != expected.scheme.lower()
                or parsed.netloc.lower() != expected.netloc.lower()
                or parsed.query
                or parsed.fragment
            ):
                continue
            match = re.fullmatch(
                re.escape(expected.path) + r"(\d{3})_l\.jpg",
                parsed.path,
                re.I,
            )
            if match is not None:
                indexes.add(int(match.group(1)))

    count = 0
    while count + 1 in indexes:
        count += 1
    return count


def _matching_cover(container: _Node, code: Optional[str]) -> Optional[_Node]:
    candidates = [
        node
        for node in _descendants(container)
        if node.tag == "img" and "/pac_s.jpg" in node.attr("src").lower()
    ]
    if not candidates:
        return None
    if code is None:
        return candidates[0]
    series = code.rsplit("-", 1)[0].lower()
    for candidate in candidates:
        path_parts = urlparse(urljoin(BASE_URL, candidate.attr("src"))).path.lower().split("/")
        if series in path_parts:
            return candidate
    return candidates[0]


def _search_card_product_id(card: _Node) -> Optional[int]:
    for anchor in _descendants(card):
        if anchor.tag != "a":
            continue
        match = _PRODUCT_ID_RE.search(anchor.attr("href"))
        if match:
            return int(match.group(1))
    return None


def _parse_search_card(card: _Node) -> Optional[dict]:
    product_id = _search_card_product_id(card)
    title_node = _first_descendant(card, lambda node: re.fullmatch(r"h[1-6]", node.tag) is not None)
    if title_node is None:
        title_node = _first_descendant(
            card,
            lambda node: "title" in {part.lower() for part in node.attr("class").split()},
        )
    if title_node is None and product_id is not None:
        for anchor in _descendants(card):
            match = _PRODUCT_ID_RE.search(anchor.attr("href"))
            if (
                anchor.tag == "a"
                and match is not None
                and int(match.group(1)) == product_id
                and _first_descendant(anchor, lambda node: node.tag == "img") is None
                and _text(anchor)
            ):
                title_node = anchor
                break
    card_text = _text(card)
    code = None
    title_text = _text(title_node)
    if title_text:
        _, separator, trailing_text = card_text.partition(title_text)
        if separator:
            code = _extract_code(trailing_text)
    if code is None:
        code = _extract_code(card_text)
    cover_node = _matching_cover(card, code)
    if product_id is None or code is None or title_node is None or cover_node is None:
        return None
    fields = _definition_fields(card)
    release_text = _text(_field_value(fields, "DVDリリース日"))
    if not release_text:
        match = _DATE_RE.search(_text(card))
        release_text = match.group(0) if match is not None else ""
    release_date = _normalize_date(release_text)
    actors = _actors_from_nodes(card, include_comments=True)
    return _record(
        product_id,
        code,
        _text(title_node),
        actors,
        release_date,
        urljoin(BASE_URL, cover_node.attr("src")),
    )


def _known_products_by_id(existing: Iterable[dict]) -> Dict[int, dict]:
    products = {}
    for product in existing:
        value = product.get("productId") if isinstance(product, dict) else None
        try:
            if value is not None:
                products[int(value)] = product
        except (TypeError, ValueError):
            continue
    return products
