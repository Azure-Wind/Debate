from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any


class StateError(RuntimeError):
    pass


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    tmp.replace(path)


def _merge_list(existing: list[Any], new_items: list[Any]) -> list[Any]:
    out = list(existing)
    for item in new_items:
        if item not in out:
            out.append(item)
    return out


def deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        elif isinstance(v, list) and isinstance(out.get(k), list):
            out[k] = _merge_list(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def append_public_transcript(state: dict[str, Any], event: dict[str, Any]) -> None:
    state.setdefault("public_transcript", []).append(event)


def validate_monotonic_public_version(old: dict[str, Any], new: dict[str, Any]) -> None:
    old_v = int(old.get("meta", {}).get("version", 0))
    new_v = int(new.get("meta", {}).get("version", 0))
    if new_v <= old_v:
        raise StateError(f"PUBLIC_STATE version must increase: {old_v} -> {new_v}")


def apply_public_patch(state: dict[str, Any], patch: dict[str, Any], version: int) -> dict[str, Any]:
    old = copy.deepcopy(state)
    new_state = deep_merge(state, patch)
    new_state.setdefault("meta", {})["version"] = version
    validate_monotonic_public_version(old, new_state)
    return new_state


def apply_private_patch(state: dict[str, Any], patch: dict[str, Any], public_version: int, event_id: str) -> dict[str, Any]:
    new_state = deep_merge(state, patch)
    meta = new_state.setdefault("meta", {})
    meta["state_version"] = int(meta.get("state_version", 0)) + 1
    meta["last_publicly_known_version"] = public_version
    meta["last_cognitive_update_event"] = event_id
    return new_state


def atomic_append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
