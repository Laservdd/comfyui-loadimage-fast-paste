"""ComfyUI custom node: Load Image plus fast clipboard paste."""

import logging
import os
import sys

_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from quick_loadimg.nodes import NODE_CLASS_MAPPINGS, NODE_DISPLAY_NAME_MAPPINGS

WEB_DIRECTORY = "./web"

try:
    from quick_loadimg.patch import install

    install()
except Exception:
    logging.getLogger("quick_loadimg").exception(
        "quick load image route was not installed"
    )
