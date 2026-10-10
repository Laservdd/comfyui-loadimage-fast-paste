"""Quick Load Image: native Load Image plus fast clipboard paste."""

from __future__ import annotations

import logging
import os

import nodes

from quick_loadimg.listing import scan_pasted, visible_choices

logger = logging.getLogger("quick_loadimg")


def pasted_dir() -> str:
    import folder_paths

    return os.path.join(folder_paths.get_input_directory(), "pasted")


def native_image_spec() -> tuple[list, dict]:
    """Read the native Load Image combo without replacing that class."""
    extra = {"image_upload": True}
    try:
        spec = nodes.LoadImage.INPUT_TYPES()["required"]["image"]
    except Exception:
        logger.exception("could not read native Load Image choices")
        return [], extra
    if isinstance(spec, (list, tuple)) and spec and isinstance(spec[0], (list, tuple)):
        files = list(spec[0])
        if len(spec) > 1 and isinstance(spec[1], dict):
            extra = dict(spec[1])
        extra.setdefault("image_upload", True)
        return files, extra
    return [], extra


def quick_image_choices(query: str = "", current: str | None = None) -> list:
    native_files, _extra = native_image_spec()
    return visible_choices(native_files, scan_pasted(pasted_dir()), query=query, current=current)


class FastPasteImage(nodes.LoadImage):
    """Load Image with a fast paste path and a short pasted-file list."""

    CATEGORY = "image"
    DESCRIPTION = (
        "与加载图像相同。选中本节点后 Ctrl+V 会快速粘贴到 pasted。"
        "下拉包含输入目录中的图片、pasted 里最新的 10 张历史图片，以及本次粘贴的图片。"
        "按文件名搜索时会匹配 pasted 里的全部图片。"
    )

    @classmethod
    def INPUT_TYPES(cls):
        files, extra = native_image_spec()
        choices = visible_choices(files, scan_pasted(pasted_dir()))
        if not choices:
            choices = [""]
        return {"required": {"image": (choices, extra)}}


NODE_CLASS_MAPPINGS = {"FastPasteImage": FastPasteImage}
NODE_DISPLAY_NAME_MAPPINGS = {"FastPasteImage": "加载图像 (快速粘贴)"}
