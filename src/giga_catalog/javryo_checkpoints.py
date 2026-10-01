"""Keep validation checkpoints outside disposable Temp workspaces."""

from pathlib import Path

from .resolved_links import atomic_write_json, load_json


def durable_path(name: str) -> Path:
    return Path.home() / "Documents" / "GIGA-JAVRyo-checkpoints" / name


def save_checkpoint(primary: Path, backup: Path, state: dict) -> None:
    atomic_write_json(backup, state)
    if primary.resolve() != backup.resolve():
        atomic_write_json(primary, state)


def load_checkpoint(primary: Path, backup: Path) -> dict:
    candidates = []
    for path in (primary, backup):
        state = load_json(path, None)
        if isinstance(state, dict) and isinstance(state.get("results"), dict):
            candidates.append(state)
    state = max(candidates, key=lambda value: (str(value.get("updatedAt", "")), len(value["results"]))) \
        if candidates else {"schemaVersion": 1, "results": {}}
    save_checkpoint(primary, backup, state)
    return state
