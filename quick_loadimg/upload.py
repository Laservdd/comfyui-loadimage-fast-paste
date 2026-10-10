"""Fast path for clipboard uploads into ``input/pasted``."""

from __future__ import annotations

import logging
import os

from quick_loadimg.index import PastedIndex

logger = logging.getLogger("quick_loadimg")


class UploadResult:
    def __init__(self, status: int, body: dict | None = None):
        self.status = status
        self.body = body


def _post_get(post, key: str, default=None):
    getter = getattr(post, "get", None)
    if not callable(getter):
        return default
    try:
        return getter(key, default)
    except Exception:
        return default


def _upload_filename(post) -> str | None:
    image = _post_get(post, "image")
    if image is None:
        return None
    filename = getattr(image, "filename", None)
    if not filename:
        return None
    return str(filename)


def _is_plain_filename(filename: str | None) -> bool:
    if not filename or filename in (".", ".."):
        return False
    if os.path.basename(filename) != filename:
        return False
    return True


def _is_overwrite(value) -> bool:
    return value in ("true", "1", True, 1)


def _is_pasted_input(post, image_save_function) -> bool:
    if image_save_function is not None or post is None:
        return False
    if not _is_plain_filename(_upload_filename(post)):
        return False
    subfolder = _post_get(post, "subfolder", "")
    if subfolder is None:
        return False
    text = str(subfolder).strip().replace("\\", "/")
    if not text or ".." in text.split("/"):
        return False
    if os.path.normpath(text) != "pasted":
        return False
    upload_type = _post_get(post, "type", None)
    return upload_type in (None, "", "input")


def is_fast_pasted(post, image_save_function=None) -> bool:
    """Clipboard paste that should skip the native per-file hash walk."""
    if not _is_pasted_input(post, image_save_function):
        return False
    return not _is_overwrite(_post_get(post, "overwrite"))


def is_pasted_overwrite(post, image_save_function=None) -> bool:
    if not _is_pasted_input(post, image_save_function):
        return False
    return _is_overwrite(_post_get(post, "overwrite"))


def _is_inside(parent: str, child: str) -> bool:
    parent_abs = os.path.abspath(parent)
    child_abs = os.path.abspath(child)
    try:
        return os.path.commonpath((parent_abs, child_abs)) == parent_abs
    except ValueError:
        return False


def asset_payload(view) -> dict | None:
    """Match the ``asset`` object ``server.image_upload`` adds, when present."""
    if view is None:
        return None
    asset = view.asset
    return {
        "id": asset.id,
        "name": asset.name,
        "asset_hash": view.asset_hash,
        "size": view.size,
        "mime_type": view.mime_type,
        "tags": view.tags,
    }


def register_asset(server, *, filepath, name, upload_type, subfolder, content_written):
    manager = getattr(server, "asset_manager", None)
    if manager is None or not hasattr(manager, "register_upload"):
        return None
    return manager.register_upload(
        abs_path=filepath,
        name=name,
        upload_type=upload_type,
        subfolder=subfolder,
        content_written=content_written,
    )


def build_upload_body(name: str, subfolder, upload_type: str, asset_view) -> dict:
    body = {"name": name, "subfolder": subfolder, "type": upload_type}
    payload = asset_payload(asset_view)
    if payload is not None:
        body["asset"] = payload
    return body


def _read_upload(image) -> bytes:
    fileobj = getattr(image, "file", None)
    if fileobj is None:
        raise ValueError("upload is missing a file body")
    try:
        fileobj.seek(0)
    except Exception:
        pass
    data = fileobj.read()
    if isinstance(data, str):
        data = data.encode("utf-8")
    if not isinstance(data, (bytes, bytearray)):
        raise TypeError("upload body is not bytes")
    return bytes(data)


def prepare_pasted_upload(post, index: PastedIndex, input_dir: str, server=None) -> UploadResult:
    """Dedup or store one pasted upload. Does not import aiohttp."""
    image = _post_get(post, "image")
    upload_name = _upload_filename(post)
    if image is None or not upload_name:
        return UploadResult(400)

    subfolder = _post_get(post, "subfolder", "")
    full_folder = os.path.abspath(os.path.join(input_dir, os.path.normpath(str(subfolder))))
    if full_folder != os.path.abspath(index.folder):
        raise RuntimeError("pasted upload folder does not match the hash index")
    initial = os.path.abspath(os.path.join(full_folder, upload_name))
    if not _is_inside(input_dir, initial):
        return UploadResult(400)

    data = _read_upload(image)
    name, is_duplicate = index.store_bytes(upload_name, data)
    filepath = os.path.abspath(os.path.join(full_folder, name))
    if not _is_inside(input_dir, filepath):
        return UploadResult(400)

    upload_type = _post_get(post, "type", None) or "input"
    asset_view = None
    try:
        asset_view = register_asset(
            server,
            filepath=filepath,
            name=name,
            upload_type=upload_type,
            subfolder=subfolder,
            content_written=not is_duplicate,
        )
    except Exception:
        logger.exception("asset registration failed for pasted upload %s", name)
        asset_view = None
    return UploadResult(200, build_upload_body(name, subfolder, upload_type, asset_view))


def to_web_response(result: UploadResult):
    from aiohttp import web

    if result.body is None:
        return web.Response(status=result.status)
    return web.json_response(result.body)


def rewind_upload(post) -> None:
    image = _post_get(post, "image")
    fileobj = getattr(image, "file", None) if image is not None else None
    if fileobj is None:
        return
    fileobj.seek(0)
