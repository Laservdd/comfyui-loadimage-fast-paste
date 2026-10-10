import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

const QUICK_NODE = "FastPasteImage";
let openMenuRoot = null;
let choicePanel = null;

function nodeClass(node) {
  return (
    node?.comfyClass ||
    node?.type ||
    node?.constructor?.comfyClass ||
    node?.constructor?.type ||
    node?.constructor?.nodeData?.name ||
    ""
  );
}

function selectedGraphNodes() {
  const found = [];
  const seen = new Set();
  const add = (node) => {
    if (!node || seen.has(node)) return;
    seen.add(node);
    found.push(node);
  };
  const canvas = app.canvas;
  const selected = canvas?.selected_nodes;
  if (selected) Object.values(selected).forEach(add);
  const selectedItems = canvas?.selectedItems;
  if (selectedItems) {
    for (const item of selectedItems) add(item);
  }
  const current = canvas?.current_node;
  if (current?.is_selected || current?.selected) add(current);
  const graphNodes = app.graph?._nodes || canvas?.graph?._nodes || [];
  for (const node of graphNodes) {
    if (node?.selected || node?.is_selected) add(node);
  }
  return found;
}

function selectedQuickNode() {
  const nodes = selectedGraphNodes();
  const quick = nodes.filter((node) => nodeClass(node) === QUICK_NODE);
  const native = nodes.filter((node) => nodeClass(node) === "LoadImage");
  if (quick.length !== 1 || native.length > 0) return null;
  return quick[0];
}

function imageWidget(node) {
  return (node?.widgets || []).find((widget) => widget.name === "image");
}

function setChoices(widget, choices) {
  if (!widget.options) widget.options = {};
  if (!Array.isArray(widget.options.values)) widget.options.values = [];
  widget.options.values.splice(0, widget.options.values.length, ...choices);
}

async function fetchChoices(query, current) {
  const params = new URLSearchParams();
  if (query) params.set("q", query);
  if (current) params.set("current", current);
  const response = await api.fetchApi("/quick-loadimg/choices?" + params.toString());
  if (!response.ok) return null;
  const data = await response.json();
  return Array.isArray(data.choices) ? data.choices : [];
}

function closeMenu() {
  if (openMenuRoot) {
    openMenuRoot.remove();
    openMenuRoot = null;
  }
}

function closeChoicePanel() {
  if (choicePanel) {
    choicePanel.remove();
    choicePanel = null;
  }
}

function paintChoiceRows(container, widget, choices, onPick) {
  container.replaceChildren();
  if (!choices.length) {
    const empty = document.createElement("div");
    empty.textContent = "无匹配图片";
    empty.style.padding = "4px";
    container.append(empty);
    return;
  }
  for (const name of choices) {
    const row = document.createElement("div");
    row.textContent = name;
    row.style.cssText = "padding:4px;cursor:pointer;";
    row.addEventListener("pointerdown", (pointerEvent) => {
      pointerEvent.preventDefault();
      pointerEvent.stopPropagation();
      const previous = widget.value;
      widget.value = name;
      widget.callback?.(name);
      const node = widget._quickNode || widget.node;
      node?.onWidgetChanged?.(widget.name, name, previous, widget);
      onPick();
    });
    container.append(row);
  }
}

function showChoicePanel(anchor, widget, choices) {
  closeChoicePanel();
  const root = document.createElement("div");
  root.dataset.quickLoadimg = "results";
  const rect = anchor.getBoundingClientRect();
  root.style.cssText = [
    "position:fixed",
    "z-index:100000",
    `left:${Math.max(8, rect.left)}px`,
    `top:${rect.bottom + 4}px`,
    "min-width:240px",
    "max-width:420px",
    "max-height:360px",
    "overflow:auto",
    "background:var(--comfy-menu-bg, #1f1f1f)",
    "color:var(--fg-color, #ddd)",
    "border:1px solid #666",
    "border-radius:6px",
    "padding:6px",
    "box-shadow:0 8px 24px rgba(0,0,0,.35)",
  ].join(";");
  paintChoiceRows(root, widget, choices, closeChoicePanel);
  document.body.append(root);
  choicePanel = root;
  setTimeout(() => {
    const closer = (pointerEvent) => {
      if (choicePanel !== root) return;
      if (root.contains(pointerEvent.target) || anchor.contains(pointerEvent.target)) return;
      closeChoicePanel();
      document.removeEventListener("pointerdown", closer, true);
    };
    document.addEventListener("pointerdown", closer, true);
  }, 0);
}

function menuPosition(event) {
  const width = 280;
  const height = 320;
  let x = event?.clientX;
  let y = event?.clientY;
  if (typeof x !== "number" || typeof y !== "number") {
    const rect = app.canvas?.canvas?.getBoundingClientRect?.();
    x = (rect?.left ?? 40) + (event?.canvasX ?? 40);
    y = (rect?.top ?? 40) + (event?.canvasY ?? 40);
  }
  x = Math.max(8, Math.min(x, window.innerWidth - width - 8));
  y = Math.max(8, Math.min(y, window.innerHeight - height - 8));
  return { x, y };
}

function openSearchMenu(event, widget) {
  closeMenu();
  closeChoicePanel();
  const root = document.createElement("div");
  root.dataset.quickLoadimg = "menu";
  const pos = menuPosition(event);
  root.style.cssText = [
    "position:fixed",
    "z-index:100000",
    `left:${pos.x}px`,
    `top:${pos.y}px`,
    "min-width:240px",
    "max-width:420px",
    "max-height:360px",
    "overflow:auto",
    "background:var(--comfy-menu-bg, #1f1f1f)",
    "color:var(--fg-color, #ddd)",
    "border:1px solid #666",
    "border-radius:6px",
    "padding:6px",
    "box-shadow:0 8px 24px rgba(0,0,0,.35)",
  ].join(";");

  const input = document.createElement("input");
  input.dataset.quickLoadimg = "search";
  input.placeholder = "搜索文件名";
  input.style.cssText = "width:100%;box-sizing:border-box;margin-bottom:4px;";
  const list = document.createElement("div");
  root.append(input, list);
  document.body.append(root);
  openMenuRoot = root;

  const load = async (query) => {
    const choices = await fetchChoices(query, widget.value);
    if (openMenuRoot !== root) return;
    if (choices) setChoices(widget, choices);
    paintChoiceRows(list, widget, choices || [], closeMenu);
  };

  input.addEventListener("input", () => {
    load(input.value);
  });
  input.addEventListener("keydown", (keyEvent) => {
    keyEvent.stopPropagation();
    if (keyEvent.key === "Escape") closeMenu();
  });
  load("");
  input.focus();

  setTimeout(() => {
    const closer = (pointerEvent) => {
      if (!root.contains(pointerEvent.target)) {
        closeMenu();
        document.removeEventListener("pointerdown", closer, true);
      }
    };
    document.addEventListener("pointerdown", closer, true);
  }, 0);
}

function bindQuickNode(node) {
  if (nodeClass(node) !== QUICK_NODE) return;
  node.previewMediaType = "image";
  keepFastPaste(node);
  const widget = imageWidget(node);
  if (!widget || widget._quickLoadImg) return;
  widget._quickLoadImg = true;
  widget._quickNode = node;
  widget.mouse = function (event) {
    const type = event?.type || "";
    if (type === "pointerdown" || type === "mousedown") {
      openSearchMenu(event, widget);
      return true;
    }
    return false;
  };
}

function keepFastPaste(node) {
  if (node._quickPasteLocked) return;
  node._quickPasteLocked = true;
  const fast = (files) => {
    const list = Array.isArray(files) ? files : [files];
    const file = list.find(looksLikeImage);
    if (!file) return;
    const stamp = `${file.name}:${file.size}:${file.lastModified}`;
    const now = Date.now();
    if (node._quickPasteStamp === stamp && now - (node._quickPasteAt || 0) < 300) return;
    node._quickPasteStamp = stamp;
    node._quickPasteAt = now;
    return uploadClipboard(node, file);
  };
  const define = (key) => {
    Object.defineProperty(node, key, {
      configurable: true,
      enumerable: true,
      get() {
        return fast;
      },
      set() {},
    });
  };
  define("pasteFile");
  define("pasteFiles");
}

function looksLikeImage(file) {
  if (!file) return false;
  if (file.type?.startsWith("image/")) return true;
  return /\.(png|jpe?g|webp|gif|bmp|tiff?)$/i.test(file.name || "");
}

function clipboardImage(event) {
  const data = event.clipboardData;
  if (!data) return null;
  const items = data.items ? Array.from(data.items) : [];
  for (const item of items) {
    const type = item.type || "";
    if (type.startsWith("image/") || item.kind === "file") {
      const file = item.getAsFile?.();
      if (looksLikeImage(file)) return file;
    }
  }
  for (const file of data.files || []) {
    if (looksLikeImage(file)) return file;
  }
  return null;
}

function pasteName(file) {
  if (file.type === "image/jpeg") return "image.jpg";
  if (file.type === "image/webp") return "image.webp";
  if (file.type === "image/gif") return "image.gif";
  return "image.png";
}

async function uploadClipboard(node, file) {
  const widget = imageWidget(node);
  if (!widget) return;
  const body = new FormData();
  body.append("image", file, pasteName(file));
  body.append("subfolder", "pasted");
  body.append("type", "input");
  const response = await api.fetchApi("/quick-loadimg/upload", { method: "POST", body });
  if (!response.ok) {
    console.error("快速粘贴上传失败", response.status, await response.text());
    return;
  }
  const data = await response.json();
  const value = data.subfolder ? `${data.subfolder}/${data.name}` : data.name;
  const previous = widget.value;
  if (!widget.options) widget.options = {};
  if (!Array.isArray(widget.options.values)) widget.options.values = [];
  if (!widget.options.values.includes(value)) widget.options.values.push(value);
  widget.value = value;
  widget.callback?.(value);
  node.onWidgetChanged?.(widget.name, value, previous, widget);
  showPreview(node, data);
  const choices = await fetchChoices("", value);
  if (choices?.length) setChoices(widget, choices);
  app.graph?.setDirtyCanvas?.(true, true);
}

function showPreview(node, data) {
  node.previewMediaType = "image";
  if (typeof api.apiURL !== "function") return;
  const params = new URLSearchParams({
    filename: data.name,
    type: data.type || "input",
    subfolder: data.subfolder || "",
  });
  const img = new Image();
  img.onload = () => app.graph?.setDirtyCanvas?.(true, true);
  img.src = api.apiURL("/view?" + params.toString());
  node.imgs = [img];
}

let panelQueryTimer = 0;
document.addEventListener(
  "input",
  (event) => {
    const node = selectedQuickNode();
    const el = event.target;
    if (!node || !(el instanceof HTMLInputElement) || el.dataset.quickLoadimg) return;
    if (!el.closest(".p-autocomplete, .p-select, .comfy-widget-combo")) return;
    window.clearTimeout(panelQueryTimer);
    const query = el.value;
    panelQueryTimer = window.setTimeout(async () => {
      const widget = imageWidget(node);
      if (!widget) return;
      const choices = await fetchChoices(query, widget.value);
      if (!choices) return;
      setChoices(widget, choices);
      if (query) showChoicePanel(el, widget, choices);
      else closeChoicePanel();
    }, 150);
  },
  true,
);

window.addEventListener(
  "paste",
  (event) => {
    const node = selectedQuickNode();
    if (!node) return;
    const file = clipboardImage(event);
    if (!file) return;
    event.preventDefault();
    event.stopPropagation();
    event.stopImmediatePropagation();
    uploadClipboard(node, file).catch((error) => {
      console.error("快速粘贴失败", error);
    });
  },
  true,
);

app.registerExtension({
  name: "Comfy.FastPasteImage",
  beforeRegisterNodeDef(nodeType, nodeData) {
    if (nodeData?.name !== QUICK_NODE) return;
    const onNodeCreated = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
      const result = onNodeCreated?.apply(this, arguments);
      bindQuickNode(this);
      return result;
    };
  },
  nodeCreated(node) {
    bindQuickNode(node);
  },
  loadedGraphNode(node) {
    bindQuickNode(node);
  },
});
