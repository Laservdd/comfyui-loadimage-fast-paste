# comfyui-loadimage-fast-paste

ComfyUI 自定义节点。在不改动原生「加载图像」的前提下，提供一个可快速粘贴剪贴板图片的加载节点。

`input/pasted` 里图片很多时，原生粘贴会按 `image.png`、`image (1).png` 的顺序逐张读取并计算哈希。目录里有几千张图时，这一步会占满 CPU，粘贴也要等很久。本节点用 `input/pasted/.quick-loadimg-index.json` 记住已有文件的哈希，粘贴时只计算剪贴板内容一次。

## 安装

把本仓库放到 ComfyUI 的 `custom_nodes` 目录：

```text
ComfyUI/custom_nodes/comfyui-loadimage-fast-paste
```

不需要额外安装 Python 包。重启 ComfyUI 后，在节点菜单的 **image** 分类里找到 **加载图像 (快速粘贴)**。节点类名是 `FastPasteImage`。

更新代码后请重启 ComfyUI，并在浏览器里强制刷新页面（Ctrl+F5），否则前端仍可能使用旧脚本。

## 使用

1. 添加「加载图像 (快速粘贴)」。
2. 只选中这个节点。
3. 按 Ctrl+V 粘贴剪贴板中的图片。

图片会保存到 `input/pasted`，文件名与原生加载图像一致，例如 `image.png`、`image (1).png`。同一张图再次粘贴时会复用已有文件；内容不同时占用该命名链上的第一个空位。原生「加载图像」可以直接打开这些文件。

未选中本节点时，画布上的 Ctrl+V 仍走 ComfyUI 原来的粘贴逻辑。把普通文件拖进节点，或使用上传按钮，也仍走原生的 `/upload/image`，文件进入 `input` 根目录。

输出与原生加载图像相同，为 `IMAGE` 和 `MASK`。

## 下拉与搜索

默认下拉包含三部分：

- `input` 根目录中的图片，范围与原生加载图像一致。
- `pasted` 里最新的 10 张历史图片。
- 本次 ComfyUI 进程中粘贴成功的全部图片，按粘贴顺序保留。新粘贴不会把这 10 张历史图片挤出下拉。

在文件名搜索框中输入文字时，会在整个 `pasted` 目录里做不区分大小写的匹配，不限于下拉里那 10 张。

## 更新索引

ComfyUI 启动时会在后台建立或增量更新索引。也可以在不启动界面的情况下重建：

```text
python update_index.py
python update_index.py D:\ComfyUI
python update_index.py D:\ComfyUI\input
python update_index.py D:\ComfyUI\input\pasted
```

在 `custom_nodes/comfyui-loadimage-fast-paste` 下直接运行且不带参数时，脚本会使用上两级目录中的 `input/pasted`。索引写入 `input/pasted/.quick-loadimg-index.json`。未变化的文件会沿用已有哈希。

请使用启动 ComfyUI 的同一个 Python 解释器运行该脚本。

## 测试

在仓库根目录执行：

```text
python -m unittest tests.test_index -v
```
