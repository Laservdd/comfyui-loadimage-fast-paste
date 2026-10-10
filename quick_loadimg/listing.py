"""Image choices for the quick Load Image node.

The native input-root list is kept. With no query, ``pasted`` contributes the
newest historical files (at most ten) plus every file pasted in this process.
A filename query matches every pasted image and is not capped.
"""

from __future__ import annotations

import os

from quick_loadimg.index import iter_image_names

PASTED_COMBO_LIMIT = 10
_session_pastes: list[str] = []
_session_seen: set[str] = set()


def is_pasted_choice(name: str) -> bool:
    text = str(name).replace("\\", "/").lstrip("/")
    return text == "pasted" or text.startswith("pasted/")


def scan_pasted(folder: str) -> list[tuple[int, str]]:
    """Return ``(mtime_ns, pasted/<file>)`` for images in ``folder``."""
    if not os.path.isdir(folder):
        return []
    records: list[tuple[int, str]] = []
    for name in iter_image_names(folder):
        path = os.path.join(folder, name)
        try:
            mtime_ns = int(os.stat(path).st_mtime_ns)
        except OSError:
            continue
        records.append((mtime_ns, f"pasted/{name}"))
    return records


def pasted_choice_name(name: str) -> str:
    """Normalize a pasted upload to ``pasted/<file>``."""
    text = str(name).replace("\\", "/").strip("/")
    if text.startswith("pasted/"):
        return text
    return f"pasted/{text}"


def note_session_paste(name: str) -> str:
    """Remember a paste for this process, in first-seen order."""
    choice = pasted_choice_name(name)
    if choice not in _session_seen:
        _session_seen.add(choice)
        _session_pastes.append(choice)
    return choice


def session_pastes() -> list[str]:
    return list(_session_pastes)


def clear_session_pastes() -> None:
    _session_pastes.clear()
    _session_seen.clear()


def _matches(name: str, needle: str) -> bool:
    if not needle:
        return True
    text = str(name).replace("\\", "/").casefold()
    return needle in text or needle in text.rsplit("/", 1)[-1]


def _normalize_session(session: list | None) -> list[str]:
    source = session_pastes() if session is None else session
    names: list[str] = []
    seen = set()
    for name in source:
        choice = pasted_choice_name(name)
        if choice in seen:
            continue
        seen.add(choice)
        names.append(choice)
    return names


def visible_choices(
    native_files,
    pasted_records,
    query: str = "",
    limit: int = PASTED_COMBO_LIMIT,
    current: str | None = None,
    session: list | None = None,
) -> list:
    """Native files plus pasted files.

    An empty query keeps every native file, the newest ``limit`` pasted files
    that were not pasted in this session, then every session paste in order.
    A query filters both sides by filename. Native matches are not capped.
    Pasted matches include every historical filename, not only the newest ten.
    ``current`` is always retained.
    """
    needle = (query or "").strip().casefold()
    session_names = _normalize_session(session)
    session_seen = set(session_names)
    kept: list = []
    seen = set()
    for name in native_files:
        if is_pasted_choice(name) or name in seen:
            continue
        if not _matches(name, needle):
            continue
        seen.add(name)
        kept.append(name)

    ranked = sorted(pasted_records, key=lambda item: (-item[0], item[1]))
    if needle:
        for _mtime, name in ranked:
            if name in seen or not _matches(name, needle):
                continue
            seen.add(name)
            kept.append(name)
        for name in session_names:
            if name in seen or not _matches(name, needle):
                continue
            seen.add(name)
            kept.append(name)
    else:
        pasted_count = 0
        for _mtime, name in ranked:
            if name in session_seen or name in seen or pasted_count >= limit:
                continue
            seen.add(name)
            kept.append(name)
            pasted_count += 1
        for name in session_names:
            if name in seen:
                continue
            seen.add(name)
            kept.append(name)

    if current and current not in seen:
        kept.append(current)
    return kept


def latest_pasted_choices(folder: str, limit: int = PASTED_COMBO_LIMIT) -> list[str]:
    """Return ``pasted/<file>`` for the newest images, newest first."""
    return [
        name
        for name in visible_choices([], scan_pasted(folder), limit=limit)
        if is_pasted_choice(name)
    ]


def merge_image_choices(native_files, pasted_folder: str, limit: int = PASTED_COMBO_LIMIT) -> list:
    """Default dropdown: native files, newest historical pasted files, and session pastes."""
    return visible_choices(native_files, scan_pasted(pasted_folder), limit=limit)


def apply_image_list(input_types: dict, pasted_folder: str, limit: int = PASTED_COMBO_LIMIT) -> dict:
    """Return a quick-node ``INPUT_TYPES`` spec. Does not modify LoadImage."""
    spec = input_types["required"]["image"]
    if isinstance(spec, (list, tuple)) and spec and isinstance(spec[0], (list, tuple)):
        extra = spec[1] if len(spec) > 1 else {"image_upload": True}
        merged = merge_image_choices(spec[0], pasted_folder, limit)
        input_types["required"]["image"] = (merged, extra)
    return input_types
