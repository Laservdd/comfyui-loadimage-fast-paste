"""Register the quick-node upload route. Native ``/upload/image`` is left unchanged."""

from __future__ import annotations

import logging

from quick_loadimg.index import PastedIndex
from quick_loadimg.listing import note_session_paste
from quick_loadimg.nodes import pasted_dir, quick_image_choices
from quick_loadimg.upload import prepare_pasted_upload, to_web_response

logger = logging.getLogger("quick_loadimg")

_installed = False
_index: PastedIndex | None = None


def get_index() -> PastedIndex | None:
    return _index


def _resource_path(resource) -> str | None:
    info = {}
    get_info = getattr(resource, "get_info", None)
    if callable(get_info):
        try:
            info = get_info() or {}
        except Exception:
            info = {}
    return info.get("path") or getattr(resource, "canonical", None)


def routes_already_applied(router) -> bool:
    """True after ComfyUI has copied ``PromptServer.routes`` onto the app."""
    resources = getattr(router, "resources", None)
    if not callable(resources):
        return False
    for resource in resources():
        if _resource_path(resource) in ("/upload/image", "/api/upload/image"):
            return True
    return False


def install() -> bool:
    """Start the pasted hash index and add routes used only by FastPasteImage."""
    global _installed, _index
    if _installed:
        return True

    try:
        from server import PromptServer
    except Exception:
        logger.warning("PromptServer is unavailable; quick paste route was not installed")
        return False

    server = getattr(PromptServer, "instance", None)
    app = getattr(server, "app", None) if server is not None else None
    router = getattr(app, "router", None) if app is not None else None
    if server is None or router is None:
        logger.warning("PromptServer.instance is missing; quick paste route was not installed")
        return False

    try:
        folder = pasted_dir()
    except Exception:
        logger.warning("input directory is unavailable; quick paste route was not installed")
        return False

    index = PastedIndex(folder)
    index.start()
    _index = index

    async def upload_pasted(request):
        try:
            import folder_paths

            post = await request.post()
            result = prepare_pasted_upload(
                post,
                index,
                folder_paths.get_input_directory(),
                server,
            )
        except Exception:
            logger.exception("quick load image upload failed")
            from aiohttp import web

            return web.Response(status=500)
        body = result.body or {}
        if result.status == 200 and body.get("name"):
            subfolder = body.get("subfolder") or ""
            choice = f"{subfolder}/{body['name']}" if subfolder else body["name"]
            note_session_paste(choice)
        return to_web_response(result)

    async def image_choices(request):
        from aiohttp import web

        query = request.rel_url.query.get("q", "")
        current = request.rel_url.query.get("current") or None
        try:
            choices = quick_image_choices(query=query, current=current)
        except Exception:
            logger.exception("failed to list quick load image choices")
            return web.json_response({"choices": []}, status=500)
        return web.json_response({"choices": choices})

    if routes_already_applied(router):
        # Frontend calls fetchApi, which prefixes /api. Register both forms
        # because the /api copy in add_routes() has already been generated.
        for path in ("/quick-loadimg/upload", "/api/quick-loadimg/upload"):
            router.add_route("POST", path, upload_pasted)
        for path in ("/quick-loadimg/choices", "/api/quick-loadimg/choices"):
            router.add_route("GET", path, image_choices)
    else:
        # add_routes() later mirrors every RouteTableDef entry under /api.
        server.routes.route("POST", "/quick-loadimg/upload")(upload_pasted)
        server.routes.route("GET", "/quick-loadimg/choices")(image_choices)
    _installed = True
    logger.info("quick load image node route installed")
    return True
