"""Persistent hash index for ComfyUI ``input/pasted`` clipboard uploads.

A matching historical file is reused even when earlier numbered names are missing.
A new image still takes the first free name in native order: ``image.png``, then
``image (1).png``, ``image (2).png``, and so on.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading

logger = logging.getLogger("quick_loadimg")

INDEX_VERSION = 1
INDEX_FILENAME = ".quick-loadimg-index.json"
IMAGE_EXTENSIONS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".jpe",
    ".jfif",
    ".gif",
    ".webp",
    ".bmp",
    ".tiff",
    ".tif",
    ".apng",
    ".ico",
    ".heic",
    ".heif",
    ".avif",
}
_SLOT_SUFFIX = re.compile(r"^(?P<base>.*) \((?P<idx>[1-9]\d*)\)$")


def default_hasher_factory():
    """Return the same hash constructor ComfyUI uses for duplicate checks."""
    try:
        import node_helpers

        return node_helpers.hasher()
    except Exception:
        import hashlib

        return hashlib.sha256


def hash_bytes(data: bytes, hasher_factory) -> str:
    hasher = hasher_factory()
    hasher.update(data)
    return hasher.hexdigest()


def hash_file(path: str, hasher_factory) -> str:
    hasher = hasher_factory()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def format_slot_name(base: str, ext: str, slot: int) -> str:
    """Match ``server.image_upload``: slot 0 is the original name, then `` (n)``."""
    if slot <= 0:
        return f"{base}{ext}"
    return f"{base} ({slot}){ext}"


def iter_memberships(filename: str):
    """Yield ``(base, ext, slot)`` chains that contain this filename.

    ``image (2).png`` is slot 2 of an ``image.png`` upload and slot 0 of an
    ``image (2).png`` upload. Native only generates one `` (n)`` suffix, so a
    name is not also assigned to earlier chains.
    """
    stem, ext = os.path.splitext(filename)
    yield stem, ext, 0
    match = _SLOT_SUFFIX.fullmatch(stem)
    if match:
        yield match.group("base"), ext, int(match.group("idx"))


def iter_image_names(folder: str):
    try:
        entries = os.scandir(folder)
    except FileNotFoundError:
        return
    with entries:
        for entry in entries:
            name = entry.name
            if name.startswith("."):
                continue
            try:
                is_file = entry.is_file()
            except OSError:
                logger.warning("skipping unreadable pasted file %s", name, exc_info=True)
                continue
            if not is_file:
                continue
            ext = os.path.splitext(name)[1].lower()
            if ext not in IMAGE_EXTENSIONS:
                continue
            yield name


class PastedIndex:
    """Hash cache for one ``input/pasted`` directory."""

    def __init__(self, folder: str, hasher_factory=None):
        self.folder = folder
        self.index_path = os.path.join(folder, INDEX_FILENAME)
        self.hasher_factory = hasher_factory or default_hasher_factory()
        self.files: dict[str, dict] = {}
        self.chains: dict[tuple[str, str], dict] = {}
        self._lock = threading.Lock()
        self._ready = threading.Event()
        self._ready.set()
        self._started = False
        self.stats = {"files": 0, "hashed": 0, "reused": 0}

    def start(self) -> threading.Thread | None:
        """Build the index on a daemon thread. Further calls do nothing."""
        if self._started:
            return None
        self._started = True
        self._ready.clear()
        thread = threading.Thread(
            target=self._build_safe,
            name="quick-loadimg-index",
            daemon=True,
        )
        thread.start()
        return thread

    def wait_ready(self, timeout: float | None = None) -> bool:
        return self._ready.wait(timeout)

    def rebuild(self) -> None:
        with self._lock:
            self._rebuild_unlocked()

    def digest(self, data: bytes) -> str:
        return hash_bytes(data, self.hasher_factory)

    def find_duplicate(self, upload_name: str, digest: str) -> str | None:
        """Return the filename native would reuse, or None."""
        self._wait_until_ready()
        with self._lock:
            base, ext = os.path.splitext(upload_name)
            return self._hit_name(base, ext, digest)

    def next_free_name(self, upload_name: str) -> str:
        self._wait_until_ready()
        with self._lock:
            base, ext = os.path.splitext(upload_name)
            chain = self.chains.get((base, ext))
            slot = 0 if chain is None else int(chain["next_slot"])
            return format_slot_name(base, ext, slot)

    def store_bytes(self, upload_name: str, data: bytes) -> tuple[str, bool]:
        """Save ``data`` using native names. Return ``(filename, is_duplicate)``."""
        digest = hash_bytes(data, self.hasher_factory)
        self._wait_until_ready()
        with self._lock:
            base, ext = os.path.splitext(upload_name)
            hit = self._hit_name(base, ext, digest)
            if hit is not None:
                return hit, True

            chain = self.chains.get((base, ext))
            slot = 0 if chain is None else int(chain["next_slot"])
            while True:
                name = format_slot_name(base, ext, slot)
                path = os.path.join(self.folder, name)
                if os.path.exists(path):
                    if name not in self.files:
                        self._ingest_existing(name)
                        hit = self._hit_name(base, ext, digest)
                        if hit is not None:
                            self._save_unlocked()
                            return hit, True
                    slot += 1
                    continue
                os.makedirs(self.folder, exist_ok=True)
                with open(path, "wb") as handle:
                    handle.write(data)
                self._remember_file(path, name, digest)
                self._save_unlocked()
                return name, False

    def note_file(self, filename: str) -> None:
        """Rehash one file after a native overwrite and refresh chain pointers."""
        if os.path.basename(filename) != filename:
            return
        self._wait_until_ready()
        with self._lock:
            self._ingest_existing(filename)
            self._save_unlocked()

    def _wait_until_ready(self) -> None:
        if not self._ready.wait():
            raise TimeoutError("pasted image index was not ready")

    def _build_safe(self) -> None:
        try:
            self.rebuild()
        except Exception:
            logger.exception("failed to build pasted image index")
        finally:
            self._ready.set()

    def _rebuild_unlocked(self) -> None:
        loaded = self._load_files()
        files, hashed, reused = self._collect_disk(loaded)
        self.files = files
        self._recompute_chains()
        self._save_unlocked()
        self.stats = {"files": len(files), "hashed": hashed, "reused": reused}
        logger.info(
            "pasted image index ready (%s files, %s hashed, %s reused)",
            len(files),
            hashed,
            reused,
        )

    def _collect_disk(self, loaded: dict) -> tuple[dict, int, int]:
        if not os.path.isdir(self.folder):
            return {}, 0, 0
        updated = dict(loaded)
        seen: set[str] = set()
        hashed = 0
        reused = 0
        for name in iter_image_names(self.folder):
            seen.add(name)
            path = os.path.join(self.folder, name)
            try:
                st = os.stat(path)
            except OSError:
                logger.warning("skipping unreadable pasted file %s", name, exc_info=True)
                continue
            size = int(st.st_size)
            mtime_ns = int(st.st_mtime_ns)
            prev = updated.get(name)
            if (
                isinstance(prev, dict)
                and prev.get("digest")
                and int(prev.get("size", -1)) == size
                and int(prev.get("mtime_ns", -1)) == mtime_ns
            ):
                reused += 1
                continue
            try:
                digest = hash_file(path, self.hasher_factory)
            except OSError:
                logger.warning("skipping unreadable pasted file %s", name, exc_info=True)
                continue
            hashed += 1
            if hashed % 500 == 0:
                logger.info("indexing pasted images: %s hashed so far", hashed)
            updated[name] = {"digest": digest, "size": size, "mtime_ns": mtime_ns}
        for name in list(updated):
            if name not in seen:
                del updated[name]
        return updated, hashed, reused

    def _recompute_chains(self) -> None:
        """Rebuild lookup chains from ``self.files``.

        Every existing file on a naming chain can satisfy a later paste. The
        earliest native slot wins. ``next_slot`` stays the first missing name,
        which is where a genuinely new image is written.
        """
        grouped: dict[tuple[str, str], list] = {}
        for name, meta in self.files.items():
            for base, ext, slot in iter_memberships(name):
                grouped.setdefault((base, ext), []).append((slot, name, meta))

        chains: dict[tuple[str, str], dict] = {}
        for key, items in grouped.items():
            slots = {slot for slot, _name, _meta in items}
            next_slot = 0
            while next_slot in slots:
                next_slot += 1
            by_hash: dict[str, dict] = {}
            for slot, name, meta in sorted(items, key=lambda row: (row[0], row[1])):
                digest = meta["digest"]
                if digest in by_hash:
                    continue
                by_hash[digest] = {
                    "name": name,
                    "size": meta["size"],
                    "mtime_ns": meta["mtime_ns"],
                    "slot": slot,
                }
            chains[key] = {"next_slot": next_slot, "by_hash": by_hash}
        self.chains = chains

    def _hit_name(self, base: str, ext: str, digest: str, *, _retry: bool = True) -> str | None:
        chain = self.chains.get((base, ext))
        if not chain:
            return None
        entry = chain["by_hash"].get(digest)
        if not entry:
            return None
        name = entry["name"]
        path = os.path.join(self.folder, name)
        try:
            st = os.stat(path)
        except OSError:
            if not _retry:
                return None
            self.files.pop(name, None)
            self._recompute_chains()
            return self._hit_name(base, ext, digest, _retry=False)
        size = int(st.st_size)
        mtime_ns = int(st.st_mtime_ns)
        if size == int(entry["size"]) and mtime_ns == int(entry["mtime_ns"]):
            return name
        if not _retry:
            return None
        self._ingest_existing(name)
        return self._hit_name(base, ext, digest, _retry=False)

    def _ingest_existing(self, name: str) -> None:
        path = os.path.join(self.folder, name)
        if not os.path.isfile(path):
            self.files.pop(name, None)
            self._recompute_chains()
            return
        st = os.stat(path)
        digest = hash_file(path, self.hasher_factory)
        self._remember_file(path, name, digest, st=st)

    def _remember_file(self, path: str, name: str, digest: str, st=None) -> None:
        if st is None:
            st = os.stat(path)
        self.files[name] = {
            "digest": digest,
            "size": int(st.st_size),
            "mtime_ns": int(st.st_mtime_ns),
        }
        self._recompute_chains()

    def _load_files(self) -> dict:
        if not os.path.isfile(self.index_path):
            return {}
        try:
            with open(self.index_path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, json.JSONDecodeError):
            logger.warning("ignoring unreadable paste index %s", self.index_path)
            return {}
        if not isinstance(payload, dict) or payload.get("version") != INDEX_VERSION:
            logger.warning("ignoring incompatible paste index %s", self.index_path)
            return {}
        raw_files = payload.get("files")
        if not isinstance(raw_files, dict):
            return {}
        loaded: dict[str, dict] = {}
        for name, meta in raw_files.items():
            if not isinstance(name, str) or not isinstance(meta, dict):
                continue
            if "digest" not in meta or "size" not in meta or "mtime_ns" not in meta:
                continue
            loaded[name] = {
                "digest": str(meta["digest"]),
                "size": int(meta["size"]),
                "mtime_ns": int(meta["mtime_ns"]),
            }
        return loaded

    def _save_unlocked(self) -> None:
        if not os.path.isdir(self.folder):
            if not self.files:
                return
            os.makedirs(self.folder, exist_ok=True)
        payload = {"version": INDEX_VERSION, "files": self.files}
        temporary = self.index_path + ".tmp"
        with open(temporary, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, self.index_path)
