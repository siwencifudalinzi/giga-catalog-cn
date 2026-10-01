"""Promote fresh real GIGA click/direct proofs after background writers have stopped."""

import argparse
import copy
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.giga_catalog.javryo_checkpoints import durable_path, load_checkpoint, save_checkpoint
from src.giga_catalog.javryo_embeds_browser import VERIFICATION_VERSION, classify_observation, utc_now
from src.giga_catalog.resolved_links import load_json, source_url_hash, validate_final_url


def promote_records(state, proofs, candidates, generation):
    result = copy.deepcopy(state)
    promoted = []
    for code, proof in sorted(proofs.items()):
        row = result["results"].get(code)
        candidate = candidates.get(code, {})
        url = validate_final_url(candidate.get("embedUrl"), expected_provider="javryo_stream")
        if (not isinstance(row, dict) or not url
                or row.get("verificationVersion") != VERIFICATION_VERSION
                or row.get("sourceUrlHash") != candidate.get("sourceUrlHash")
                or row.get("embedUrlHash") != source_url_hash(url)
                or proof.get("generation") != generation
                or proof.get("targetUrlHash") != source_url_hash(url)
                or proof.get("verificationVersion") != VERIFICATION_VERSION
                or proof.get("playbackStatus") != "verified"):
            continue
        paths = proof.get("paths", {})
        if not all(paths.get(name, {}).get("status") == "verified"
                   and classify_observation(paths.get(name, {}).get("evidence", {})) == "verified"
                   for name in ("catalog", "direct")):
            continue
        catalog = paths["catalog"]["evidence"]
        if catalog.get("catalogClickObserved") is not True or catalog.get("catalogDocumentValidated") is not True:
            continue
        row["javryoSource"] = row.get("javryoSource", row.get("paths", {}).get("source", {}))
        row.update(sourceKind="catalog", paths={"source": copy.deepcopy(paths["catalog"]),
                   "direct": copy.deepcopy(paths["direct"])}, playbackStatus="verified",
                   finalUrl=url, checkedAt=proof["checkedAt"])
        promoted.append(code)
    return result, promoted


def ensure_writers_stopped():
    if os.name != "nt":
        return
    command = """$ErrorActionPreference = 'Stop'
    @(Get-CimInstance Win32_Process | Where-Object {
      $_.ProcessId -ne $PID -and
      (($_.Name -in @('python.exe','py.exe') -and $_.CommandLine -match 'verify_javryo_embeds\\.py') -or
       ($_.Name -in @('pwsh.exe','powershell.exe') -and $_.CommandLine -match 'run_javryo_verification\\.ps1'))
    }).Count"""
    process = subprocess.run(["powershell.exe", "-NoProfile", "-Command", command],
        capture_output=True, text=True, check=True, creationflags=subprocess.CREATE_NO_WINDOW)
    if int(process.stdout.strip()) != 0:
        raise RuntimeError("background checkpoint writers must finish before proof promotion")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, default=ROOT / "data/state/javryo-embed-verification.json")
    parser.add_argument("--proofs", type=Path, default=ROOT / "data/state/javryo-promising-verification.json")
    args = parser.parse_args()
    ensure_writers_stopped()
    backup = durable_path(args.state.name)
    state = load_checkpoint(args.state, backup)
    proofs = load_checkpoint(args.proofs, durable_path(args.proofs.name))["results"]
    candidates = load_json(ROOT / "data/javryo-embeds.json", {})["entries"]
    generation = load_json(ROOT / "public/data/catalog-bootstrap.json", {})["generation"]
    result, promoted = promote_records(state, proofs, candidates, generation)
    result["updatedAt"] = utc_now()
    save_checkpoint(args.state, backup, result)
    print(json.dumps({"promoted": promoted, "count": len(promoted)}))


if __name__ == "__main__":
    main()
