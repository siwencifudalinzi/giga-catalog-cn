"""Read legacy catalog JSON into the current in-memory contract."""

import json
from pathlib import Path

from src.giga_catalog.codes import normalize_code


def _iter_legacy_products(legacy_catalog):
    if isinstance(legacy_catalog, list):
        yield from legacy_catalog
        return

    for series in legacy_catalog["series"].values():
        yield from series["videos"].values()


def migrate_legacy(data_path: Path, links_path: Path) -> tuple[list[dict], dict[str, dict]]:
    """Migrate legacy products and provider links without writing output files."""
    legacy_products = json.loads(data_path.read_text(encoding="utf-8"))
    legacy_links = json.loads(links_path.read_text(encoding="utf-8"))

    products = []
    for legacy_product in _iter_legacy_products(legacy_products):
        if legacy_product is None:
            continue
        product = dict(legacy_product)
        code = normalize_code(product.get("code"))
        if code is None:
            continue
        product["code"] = code
        product.setdefault("productId", None)
        products.append(product)

    links = {}
    for code, legacy_link in legacy_links.items():
        normalized_code = normalize_code(code)
        if normalized_code is None:
            continue
        link = dict(legacy_link)
        if "st" in link:
            link["streamtape"] = link.pop("st")
        if "gf" in link:
            link["gofile"] = link.pop("gf")
        links[normalized_code] = link

    return products, links
