# -*- coding: utf-8 -*-
"""外部提示词注入（prompt-helper 桥接插件）

在文生图 / 图生图页面各提供一个常驻控制栏：指定一个 txt 文件（由
prompt-helper / FeeTagHelper 等外部词条编辑器实时输出），每次生成任务
开始时现场重新读取该文件，并把词条拼接到本次的正向（可选反向）提示词。

注入发生在提示词编译之前的 before_process 阶段，因此：
- 每次点击"生成"（包括队列中的每个任务）都会重新读盘，始终拿到最新词条；
- 高清修复未单独填写提示词时，自动继承注入后的提示词；
- 注入结果会写入生成信息 / PNG 元数据，但不会回写到提示词输入框。

另提供可选联动：WebUI 启动完成时自动拉起外部词条编辑器（on_app_started
回调；防重复启动；编辑器作为独立进程运行，关闭 WebUI 不会连带关闭它）。
"""

import html
import json
import os
import subprocess
import time

import gradio as gr
from modules import script_callbacks
from modules.scripts import AlwaysVisible, Script

EXT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(EXT_DIR, "config.json")

POSITIONS = ("追加到末尾", "插入到最前")
CONTROL_KEYS = ("enabled", "path", "position", "inject_negative", "merge_lines",
                "autostart", "editor_path")

DEFAULT_CONFIG = {
    "enabled": True,
    "path": "",
    "position": POSITIONS[0],
    "inject_negative": False,
    "merge_lines": True,
    "autostart": False,
    "editor_path": "",
}

# 文生图 / 图生图两个页面的组件表，用于设置双向同步
_TAB_CONTROLS = {}


def _log(message):
    print(f"[prompt-helper] {message}")


def _load_config():
    cfg = dict(DEFAULT_CONFIG)
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8-sig") as f:
            loaded = json.load(f)
        if isinstance(loaded, dict):
            for key in CONTROL_KEYS:
                if key in loaded:
                    cfg[key] = loaded[key]
    except (OSError, ValueError):
        pass
    if cfg["position"] not in POSITIONS:
        cfg["position"] = DEFAULT_CONFIG["position"]
    for key in ("enabled", "inject_negative", "merge_lines", "autostart"):
        cfg[key] = bool(cfg[key])
    cfg["path"] = str(cfg["path"] or "")
    cfg["editor_path"] = str(cfg["editor_path"] or "")
    return cfg


def _save_config(cfg):
    data = {key: cfg.get(key, DEFAULT_CONFIG[key]) for key in CONTROL_KEYS}
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except OSError:
        _log("设置写入失败（不影响本次注入）")


def _normalize_path(path):
    path = (path or "").strip().strip('"').strip("'")
    return os.path.expandvars(os.path.expanduser(path))


def read_tag_file(path, merge_lines=True):
    """读取词条文件。返回 (内容或 None, 状态消息)。"""
    path = _normalize_path(path)
    if not path:
        return None, "未设置文件路径"
    if not os.path.isfile(path):
        return None, "文件不存在：" + path

    last_error = "编码无法识别（UTF-8 / GBK 均解码失败）"
    for _ in range(3):  # 编辑器写入瞬间可能短暂占用文件，重试几次
        for encoding in ("utf-8-sig", "utf-8", "gb18030"):
            try:
                with open(path, "r", encoding=encoding) as f:
                    text = f.read()
            except UnicodeDecodeError:
                continue
            except OSError as e:
                last_error = "读取失败：" + str(e)
                break
            text = text.strip()
            if not text:
                return None, "文件为空"
            if merge_lines:
                text = " ".join(text.split())
            return text, "文件正常"
        time.sleep(0.15)
    return None, last_error


def _inject(base, tags, prepend, sep=", "):
    if isinstance(base, list):
        return [_inject(item, tags, prepend, sep) for item in base]
    if not base:
        return tags
    return f"{tags}{sep}{base}" if prepend else f"{base}{sep}{tags}"


def _is_process_running(exe_name):
    """查询同名进程是否已在运行（用于防止编辑器被重复拉起）。"""
    exe_name = exe_name.lower()
    try:
        if os.name == "nt":
            # tasklist 在中文系统输出 GBK，errors="replace" 防止解码崩溃（exe 名匹配不受影响）
            result = subprocess.run(
                ["tasklist", "/FI", f"IMAGENAME eq {exe_name}"],
                capture_output=True, text=True, errors="replace", timeout=10,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            return exe_name in (result.stdout or "").lower()
        output = subprocess.run(
            ["pgrep", "-f", exe_name], capture_output=True, text=True, timeout=10,
        ).stdout
        return bool((output or "").strip())
    except (OSError, subprocess.SubprocessError):
        return False  # 检测失败时宁可重复启动，也不要让编辑器永远起不来


def launch_editor(editor_path):
    """启动外部词条编辑器（独立进程，关闭 WebUI 不会连带关闭它）。返回 (是否成功, 消息)。"""
    editor_path = _normalize_path(editor_path)
    if not editor_path:
        return False, "未设置编辑器路径"
    if not os.path.isfile(editor_path):
        return False, "文件不存在：" + editor_path

    exe_name = os.path.basename(editor_path)
    if _is_process_running(exe_name):
        return True, f"{exe_name} 已在运行，跳过启动"

    flags = (subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
             if os.name == "nt" else 0)
    try:
        subprocess.Popen([editor_path], cwd=os.path.dirname(editor_path),
                         creationflags=flags, close_fds=True)
    except OSError as e:
        return False, f"启动失败：{e}"
    return True, "已启动：" + editor_path


def _preview(path, merge_lines):
    normalized = _normalize_path(path)
    text, message = read_tag_file(path, merge_lines)
    if text is None:
        return "", f"<span style='color:#e5484d'>✗ {html.escape(message)}</span>"
    try:
        updated = time.strftime("%H:%M:%S", time.localtime(os.path.getmtime(normalized)))
    except OSError:
        updated = "?"
    tag_count = len([t for t in (x.strip() for x in text.split(",")) if t])
    hint = f"<span style='color:#30a46c'>✓ {html.escape(message)} · {tag_count} 个词条 · 文件更新于 {updated}</span>"
    return text, hint


def _launch_click(editor_path):
    ok, message = launch_editor(editor_path)
    color = "#30a46c" if ok else "#e5484d"
    return f"<span style='color:{color}'>{'✓' if ok else '✗'} {html.escape(message)}</span>"


def _persist_settings(*values):
    _save_config(dict(zip(CONTROL_KEYS, values)))


def _echo(value):
    return value


def _wire_controls(controls, is_img2img):
    """设置一变就存档；并让文生图 / 图生图两页面的组件互相同步。"""
    inputs = [controls[key] for key in CONTROL_KEYS]
    other = _TAB_CONTROLS.get(not is_img2img)
    for key in CONTROL_KEYS:
        comp = controls[key]
        comp.change(fn=_persist_settings, inputs=inputs, outputs=None)
        if other is not None and key in other:
            comp.change(fn=_echo, inputs=[comp], outputs=[other[key]])
            other[key].change(fn=_echo, inputs=[other[key]], outputs=[comp])
    _TAB_CONTROLS[is_img2img] = controls


class PromptHelperScript(Script):

    def title(self):
        return "外部提示词注入 (prompt-helper)"

    def show(self, is_img2img):
        return AlwaysVisible

    def ui(self, is_img2img):
        cfg = _load_config()

        with gr.Accordion("外部提示词注入（实时读取 txt）", open=False,
                          elem_id=f"prompt-helper-{'img2img' if is_img2img else 'txt2img'}"):
            with gr.Row():
                enabled = gr.Checkbox(value=cfg["enabled"], label="启用注入")
                position = gr.Radio(choices=list(POSITIONS), value=cfg["position"], label="插入位置")

            path = gr.Textbox(
                value=cfg["path"],
                label="词条 txt 文件路径",
                placeholder="例如：E:\\桌面\\AI file\\Design file\\prompt-helper\\prompt.txt",
                lines=1,
            )

            with gr.Row():
                inject_negative = gr.Checkbox(value=cfg["inject_negative"], label="同时注入反向提示词")
                merge_lines = gr.Checkbox(value=cfg["merge_lines"], label="将文件内换行合并为一行")

            autostart = gr.Checkbox(value=cfg["autostart"], label="启动 WebUI 时自动打开词条编辑器")
            editor_path = gr.Textbox(
                value=cfg["editor_path"],
                label="词条编辑器路径 (exe)",
                placeholder="例如：E:\\桌面\\AI file\\...\\feetaghelper.exe",
                lines=1,
            )
            launch_status = gr.HTML()
            launch_button = gr.Button(value="立即启动编辑器（测试）")

            preview = gr.Textbox(label="文件内容预览（只读）", lines=3, interactive=False)
            status = gr.HTML()
            refresh = gr.Button(value="刷新预览")

        refresh.click(fn=_preview, inputs=[path, merge_lines], outputs=[preview, status])
        path.submit(fn=_preview, inputs=[path, merge_lines], outputs=[preview, status])
        launch_button.click(fn=_launch_click, inputs=[editor_path], outputs=[launch_status])

        controls = {
            "enabled": enabled,
            "path": path,
            "position": position,
            "inject_negative": inject_negative,
            "merge_lines": merge_lines,
            "autostart": autostart,
            "editor_path": editor_path,
        }
        _wire_controls(controls, is_img2img)

        return [enabled, path, position, inject_negative, merge_lines, autostart, editor_path]

    def before_process(self, p, enabled, path, position, inject_negative, merge_lines, autostart, editor_path):
        """每次生成任务触发一次，早于提示词列表构建，改 p.prompt 即可全量生效。"""
        _save_config(dict(zip(CONTROL_KEYS, (enabled, path, position, inject_negative,
                                              merge_lines, autostart, editor_path))))
        if not enabled:
            return

        tags, message = read_tag_file(path, merge_lines)
        if tags is None:
            _log(f"跳过注入：{message}")
            return

        prepend = position == POSITIONS[1]
        p.prompt = _inject(p.prompt, tags, prepend)
        if inject_negative:
            p.negative_prompt = _inject(p.negative_prompt, tags, prepend)

        shown = tags[:120] + ("…" if len(tags) > 120 else "")
        _log(f"已注入 {len(tags)} 个字符（{message}）：{shown}")


def _on_app_started(demo=None, app=None):
    cfg = _load_config()
    if not cfg["autostart"]:
        return
    ok, message = launch_editor(cfg["editor_path"])
    _log(f"自动启动编辑器：{message}")


script_callbacks.on_app_started(_on_app_started)
