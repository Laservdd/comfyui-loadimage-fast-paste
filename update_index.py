"""Rebuild the clipboard-paste hash index without starting ComfyUI.

From a ComfyUI custom_nodes checkout this finds ``input/pasted`` two levels up.
Pass a ComfyUI root, an ``input`` directory, or the ``pasted`` directory itself
when the default location is not the one you want.

Examples:
  python update_index.py
  python update_index.py D:\\ComfyUI
  python update_index.py D:\\ComfyUI\\input\\pasted
"""

from __future__ import annotations

import logging
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from quick_loadimg.index import PastedIndex


def default_pasted_dir() -> str:
    return os.path.abspath(os.path.join(ROOT, "..", "..", "input", "pasted"))


def resolve_pasted_dir(argument: str | None, default_dir: str | None = None) -> str:
    if not argument:
        return default_dir if default_dir is not None else default_pasted_dir()
    path = os.path.abspath(argument)
    if os.path.isdir(os.path.join(path, "input")):
        return os.path.join(path, "input", "pasted")
    if os.path.basename(os.path.normpath(path)).lower() == "input":
        return os.path.join(path, "pasted")
    return path


def update_index(pasted_dir: str) -> PastedIndex:
    index = PastedIndex(pasted_dir)
    index.rebuild()
    return index


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = list(sys.argv[1:] if argv is None else argv)
    pasted = resolve_pasted_dir(args[0] if args else None)
    if not os.path.isdir(pasted):
        print(f"找不到目录: {pasted}")
        print("用法: python update_index.py [ComfyUI目录、input目录或pasted目录]")
        return 1
    index = update_index(pasted)
    stats = index.stats
    print(f"索引已写入: {index.index_path}")
    print(
        f"共 {stats['files']} 张，新计算 {stats['hashed']} 张，沿用缓存 {stats['reused']} 张"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
