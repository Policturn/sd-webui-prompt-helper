# -*- coding: utf-8 -*-
"""外部提示词注入（prompt-helper 桥接插件）

在文生图 / 图生图页面各提供一个常驻控制栏：指定正向 / 反向两个 txt 词条
文件（由 prompt-helper / FeeTagHelper 等外部词条编辑器实时输出），每次
生成任务开始时现场重新读取文件，并把词条分别拼接到本次的正向、反向提示词
（反向路径留空则只注入正向）。

注入发生在提示词编译之前的 before_process 阶段，因此：
- 每次点击"生成"（包括队列中的每个任务）都会重新读盘，始终拿到最新词条；
- 高清修复未单独填写提示词时，自动继承注入后的提示词；
- 注入结果会写入生成信息 / PNG 元数据，但不会回写到提示词输入框。

FeeTagHelper 构建区可能在 txt 末尾追加元数据 tag（<fth:meta:…>，携带 BREAK
位置 / 选一记录）。注入前会剥离该 tag（解码失败静默丢弃），按其中的 breaks
把平铺 tag 流断开为空行分隔（A1111 BREAK 语法），并把解码后的元数据写入
PNG 的 extra_generation_params（键 fth_meta / fth_meta_negative，附插件版本）。

v1.4.1 起注入位置确定化：词条恒定拼接在提示词最前，最终送入 CLIP 的文本
结构恒为 [注入词条][提示框原有内容]（旧版"插入位置"选项已移除，config 里的
残留 position 键会被读取白名单忽略）。fth_meta / fth_meta_negative 同时新增
injected_tags（注入区 tag 数，按逗号拆分计数，与编辑器自然条 offset 同基准）
与 full_text（注入后的完整提示词）两个字段——75 token 分块发生在 CLIP 编码
内部，before_process 阶段拿不到分块结果，编辑器端用自带 tokenizer 依据
full_text 自行计算分块对齐。

另提供可选联动：WebUI 启动完成时自动拉起外部词条编辑器（on_app_started
回调；防重复启动；编辑器作为独立进程运行，关闭 WebUI 不会连带关闭它）。

v1.4.2 起提供「生成」页总线（P1，生成页-实施设计.md）：编辑器把 params.json
（要覆盖的参数）与 cmd.json（触发指令）写进本插件目录，javascript/feetag_generate.js
轮询 cmd.json，点隐藏 apply 钮让服务端读 params.json 并以 gr.update 回填界面组件
（语义键 → elem_id 选择器表见 _FIELD_TABLES，组件经 on_after_component 捕获），
再由 JS 切页签点生成钮走 UI 队列。每次生成 before_process 置状态 busy、
postprocess 把成品图存 featag_out/（毫秒时间戳命名）并置 done；ADetailer 内部
pass（_ad_inner 标记）在所有钩子入口直接 return，不注入不计数不回传。
status.json（state/pass/images/error/ts + choices）只在内容变化时重写，
供编辑器轮询；params/cmd 的读取仅在 apply 点击时发生，天然节流。
"""

import base64
import binascii
import html
import json
import os
import re
import subprocess
import threading
import time

import gradio as gr
from modules import script_callbacks
from modules.scripts import AlwaysVisible, Script

EXT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(EXT_DIR, "config.json")

# 生成页总线文件（编辑器直写 / 浏览器 JS 经 /file= 读取，均在插件目录内）
PARAMS_PATH = os.path.join(EXT_DIR, "params.json")
CMD_PATH = os.path.join(EXT_DIR, "cmd.json")
STATUS_PATH = os.path.join(EXT_DIR, "status.json")
FEETAG_OUT_DIR = os.path.join(EXT_DIR, "featag_out")

PLUGIN_VERSION = "1.4.2"

CONTROL_KEYS = ("enabled", "path", "negative_path", "merge_lines",
                "autostart", "editor_path")

DEFAULT_CONFIG = {
    "enabled": True,
    "path": "",
    "negative_path": "",
    "merge_lines": True,
    "autostart": False,
    "editor_path": "",
}

# 文生图 / 图生图两个页面的组件表，用于设置双向同步
_TAB_CONTROLS = {}

# FeeTagHelper 构建区元数据 tag：<fth:meta:BASE64URL>（base64url 无填充，字符集不含逗号，
# 不破坏 tag 流；载荷为紧凑 JSON：v / breaks / pick）。追加在 txt 末尾，注入前剥离。
META_TAG_RE = re.compile(r"<fth:meta:([A-Za-z0-9_-]+)>")

# ---------------------------------------------------------------------------
# 生成页总线（v1.4.2，P1）
# ---------------------------------------------------------------------------

_MISSING = object()  # params.json 中未出现的键：不覆盖（与 None 显式"保持现状"同义）

# 语义键 → elem_id 选择器表。elem_id 逐一对照 H 盘 A1111 1.10.1 源码核实：
# 常规参数在 modules/ui.py；采样/调度/步数由内置脚本 modules/processing_scripts/sampler.py
# 以 f"{tab}_sampling" 等生成；种子为内置 Seed 脚本 f"{tab}_seed"（gr.Number）；
# hires 组件 elem_id 见 modules/ui.py L311-339，其中 InputAccordion 的真实取值
# 组件是隐藏 checkbox（elem_id = "txt2img_hr" + "-checkbox"）。
_BASE_FIELDS = [
    ("width", "width", "slider_int"),
    ("height", "height", "slider_int"),
    ("seed", "seed", "number"),
    ("sampler_name", "sampling", "dropdown"),
    ("scheduler", "scheduler", "dropdown"),
    ("steps", "steps", "slider_int"),
    ("cfg_scale", "cfg_scale", "slider_float"),
    ("batch_size", "batch_size", "slider_int"),
    ("n_iter", "batch_count", "slider_int"),
]
_HIRES_FIELDS = [  # hires 仅文生图；hr_checkpoint（中途换模型）按论证结论 v1 不接
    ("enable", "txt2img_hr-checkbox", "checkbox"),
    ("upscaler", "txt2img_hr_upscaler", "dropdown"),
    ("hr_scale", "txt2img_hr_scale", "slider_float"),
    ("steps", "txt2img_hires_steps", "slider_int"),
    ("denoise", "txt2img_denoising_strength", "slider_float"),
]


def _field_table(is_img2img):
    """某页的可覆盖字段表：[(总线分区, 语义键, elem_id, 组件类型)]。"""
    tab = "img2img" if is_img2img else "txt2img"
    table = [("base", key, f"{tab}_{elem}", kind) for key, elem, kind in _BASE_FIELDS]
    if not is_img2img:
        table += [("hires", key, elem, kind) for key, elem, kind in _HIRES_FIELDS]
    return table


_FIELD_TABLES = {False: _field_table(False), True: _field_table(True)}
_WANTED_ELEM_IDS = {row[2] for rows in _FIELD_TABLES.values() for row in rows}

# on_after_component 捕获到的页面组件（elem_id -> gradio 组件）
_UI_COMPONENTS = {}

# status.json 写入去重（内容未变化不重写）+ 生成计数
_status_lock = threading.Lock()
_status_snapshot = None
_gen_pass = 0


def _read_bus_json(path):
    """读总线 JSON 文件（params/cmd）。缺失 / 损坏 / 正被写入时返回 None，不抛错。"""
    for attempt in range(2):  # 编辑器写文件的一瞬可能读到半个 JSON，短暂重试一次
        try:
            with open(path, "r", encoding="utf-8-sig") as f:
                return json.load(f)
        except (OSError, ValueError):
            if attempt == 0:
                time.sleep(0.1)
    return None


def _publish_choices():
    """把 WebUI 当前实际可用的采样器 / 调度 / 超分 choices 发给客户端，
    编辑器下拉据此渲染，避免填入 WebUI 里不存在的值。离线 / mock 环境返回 {}。"""
    try:
        from modules import sd_samplers, sd_schedulers, shared
        return {
            "samplers": [x.name for x in sd_samplers.visible_samplers()],
            "schedulers": [x.label for x in sd_schedulers.schedulers],
            "upscalers": [x.name for x in shared.sd_upscalers],
        }
    except Exception:
        return {}


def _write_status(state, images=None, error=None):
    """写 status.json（state/pass/images/error/ts + choices）。

    内容签名（state/pass/images/error/choices）未变化时不重写——客户端高频轮询
    的只是不再变化的文件，磁盘零增长；ts 仅在真实写入时刷新。
    任何写入异常只打日志，绝不影响生成。
    """
    global _status_snapshot
    choices = _publish_choices()
    signature = json.dumps([state, _gen_pass, list(images or []), error, choices],
                           ensure_ascii=False, default=str)
    with _status_lock:
        if signature == _status_snapshot:
            return
        payload = {
            "state": state,
            "pass": _gen_pass,
            "images": list(images or []),
            "error": error,
            "ts": int(time.time() * 1000),
            "plugin": PLUGIN_VERSION,
            "choices": choices,
        }
        try:
            with open(STATUS_PATH, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False)
            _status_snapshot = signature
        except OSError as e:
            _log(f"status.json 写入失败：{e}")


def _coerce_value(kind, value, comp):
    """把 params.json 里的原始值整理成组件可接受的值。越界夹取、
    下拉 choices 不含该值时返回 None（调用方跳过，防止 gradio 拒值）。"""
    if kind == "checkbox":
        return bool(value)
    if kind == "number":
        return int(float(value))
    if kind in ("slider_int", "slider_float"):
        num = float(value)
        minimum = getattr(comp, "minimum", None)
        maximum = getattr(comp, "maximum", None)
        if isinstance(minimum, (int, float)):
            num = max(minimum, num)
        if isinstance(maximum, (int, float)):
            num = min(maximum, num)
        return int(num) if kind == "slider_int" else round(num, 4)
    text = str(value)
    if kind == "dropdown":
        choices = list(getattr(comp, "choices", None) or [])
        if choices and text not in choices:
            return None
    return text


def _make_apply_handler(targets):
    """生成页隐藏 apply 钮的处理函数：读 params.json，按 targets（闭包含组件引用）
    产出 gr.update 列表。键不出现 / 显式 null / 值非法 → 原样 gr.update() 不覆盖。"""
    def handler():
        params = _read_bus_json(PARAMS_PATH)
        params = params if isinstance(params, dict) else {}
        updates, applied, skipped = [], [], []
        for section, key, comp, kind in targets:
            section_data = params.get(section)
            value = _MISSING
            if isinstance(section_data, dict) and key in section_data:
                value = section_data[key]
            if value is _MISSING or value is None:
                updates.append(gr.update())
                continue
            try:
                coerced = _coerce_value(kind, value, comp)
            except (TypeError, ValueError):
                coerced = None
            if coerced is None:
                updates.append(gr.update())
                skipped.append(f"{section}.{key}={value!r}")
                continue
            updates.append(gr.update(value=coerced))
            applied.append(f"{section}.{key}={coerced}")
        if applied or skipped:
            message = f"参数应用：{', '.join(applied)}" if applied else "参数应用：无生效键"
            if skipped:
                message += f"（跳过：{', '.join(skipped)}）"
            _log(message)
        return updates
    return handler


def _on_after_component(component, **kwargs):
    """捕获生成页参数组件（elem_id 在选择器表内的），供 apply 事件作 outputs。"""
    try:
        elem_id = getattr(component, "elem_id", None)
        if elem_id in _WANTED_ELEM_IDS:
            _UI_COMPONENTS[elem_id] = component
    except Exception:
        pass


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
    for key in ("enabled", "merge_lines", "autostart"):
        cfg[key] = bool(cfg[key])
    for key in ("path", "negative_path", "editor_path"):
        cfg[key] = str(cfg[key] or "")
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


def _inject(base, tags, sep=", "):
    # v1.4.1 起注入位置确定化：词条恒定拼接在最前，结构恒为 [注入词条][base]
    if isinstance(base, list):
        return [_inject(item, tags, sep) for item in base]
    if not base:
        return tags
    return f"{tags}{sep}{base}"


def _count_tags(text):
    """按逗号拆分统计非空 tag 数（与编辑器自然条 offset 同基准，BREAK 展开前计数）。"""
    return len([t for t in (x.strip() for x in text.split(",")) if t])


def _decode_meta_payload(payload):
    """base64url 解码元数据 tag 载荷。失败返回 None（调用方静默丢弃，不报错不中断）。"""
    try:
        padded = payload + "=" * (-len(payload) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
    except (ValueError, UnicodeDecodeError, binascii.Error):
        return None
    return data if isinstance(data, dict) else None


def strip_meta_tags(text):
    """剥离词条文本中的 <fth:meta:…> 元数据 tag。返回 (剥离后文本, 元数据列表)。

    元数据 tag 由 FeeTagHelper 构建区追加在 txt 末尾（base64url 编码的紧凑
    JSON，字段 v / breaks / pick）。无论解码是否成功，整个 tag 都被移除、
    不进入生成用提示词；解码失败的 tag 静默丢弃，不报错不中断。
    文本不含元数据 tag 时原样返回（分隔符不动）。
    """
    if not text or "<fth:meta:" not in text:
        return text, []

    metas = []

    def _take(match):
        meta = _decode_meta_payload(match.group(1))
        if meta is not None:
            metas.append(meta)
        return ""  # 解码失败的 tag 同样整个移除

    kept = []
    for chunk in text.split(","):
        chunk = META_TAG_RE.sub(_take, chunk).strip()
        if chunk:
            kept.append(chunk)
    return ", ".join(kept), metas


def expand_breaks(text, meta):
    """按元数据 breaks 在平铺 tag 流中插入 BREAK（A1111 语法：空行分隔）。

    breaks 语义：N = BREAK 前面的 tag 数，即在第 N 个 tag 之后断开；元数据
    tag 已被 strip_meta_tags 移除、不参与计数。越界（N ≤ 0 或 N ≥ tag 总数）
    与重复位置静默忽略——编辑器侧已修剪首尾/连续 BREAK，此处防御性再修剪。
    """
    if not isinstance(meta, dict):
        return text
    raw = meta.get("breaks")
    if not isinstance(raw, list) or not raw:
        return text
    tags = [t for t in (x.strip() for x in text.split(",")) if t]
    positions = sorted({n for n in raw if isinstance(n, int) and 0 < n < len(tags)})
    if not positions:
        return text
    segments, start = [], 0
    for n in positions:
        segments.append(", ".join(tags[start:n]))
        start = n
    segments.append(", ".join(tags[start:]))
    return "\n\n".join(segments)


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


def _file_hint(path, text):
    normalized = _normalize_path(path)
    try:
        updated = time.strftime("%H:%M:%S", time.localtime(os.path.getmtime(normalized)))
    except OSError:
        updated = "?"
    tag_count = _count_tags(text)
    return f"文件正常 · {tag_count} 个词条 · 文件更新于 {updated}"


def _record_meta_png(p, meta, key, fields):
    """把注入统计字段 + 剥离出的元数据（附插件版本号）写进 PNG 生成信息。

    fields 为本次注入的统计（injected_tags / full_text 等）；meta 可能为 None
    （txt 未携带元数据 tag 时统计字段照常记录）。记录失败不影响生成。
    """
    try:
        recorded = dict(meta) if meta else {}
        recorded.update(fields)
        recorded["plugin"] = PLUGIN_VERSION
        p.extra_generation_params[key] = json.dumps(recorded, ensure_ascii=False)
    except (AttributeError, TypeError, ValueError):
        pass  # 记录失败不影响生成


def _preview(path, negative_path, merge_lines):
    parts = []

    text, message = read_tag_file(path, merge_lines)
    if text is None:
        pos_preview = ""
        parts.append(f"<span style='color:#e5484d'>✗ 正向：{html.escape(message)}</span>")
    else:
        text, metas = strip_meta_tags(text)
        pos_preview = text
        meta_hint = " · 携带元数据" if metas else ""
        parts.append(f"<span style='color:#30a46c'>✓ 正向：{_file_hint(path, text)}{meta_hint}</span>")

    neg_preview = ""
    if _normalize_path(negative_path):
        neg_text, neg_message = read_tag_file(negative_path, merge_lines)
        if neg_text is None:
            parts.append(f"<span style='color:#e5484d'>✗ 反向：{html.escape(neg_message)}</span>")
        else:
            neg_text, neg_metas = strip_meta_tags(neg_text)
            neg_preview = neg_text
            meta_hint = " · 携带元数据" if neg_metas else ""
            parts.append(f"<span style='color:#30a46c'>✓ 反向：{_file_hint(negative_path, neg_text)}{meta_hint}</span>")
    else:
        parts.append("<span style='color:#888'>反向：未设置（留空则不注入）</span>")

    return pos_preview, neg_preview, "<br>".join(parts)


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
            enabled = gr.Checkbox(
                value=cfg["enabled"],
                label="启用注入（词条恒拼接在提示词最前）",
            )

            path = gr.Textbox(
                value=cfg["path"],
                label="正向词条 txt 文件路径",
                placeholder="例如：E:\\桌面\\AI file\\Design file\\prompt-helper\\prompt.txt",
                lines=1,
            )

            negative_path = gr.Textbox(
                value=cfg["negative_path"],
                label="反向词条 txt 文件路径（留空则不注入反向）",
                placeholder="例如：E:\\桌面\\AI file\\Design file\\prompt-helper\\negative.txt",
                lines=1,
            )

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

            with gr.Row():
                preview = gr.Textbox(label="正向文件预览（只读）", lines=3, interactive=False)
                negative_preview = gr.Textbox(label="反向文件预览（只读）", lines=3, interactive=False)
            status = gr.HTML()
            refresh = gr.Button(value="刷新预览")

        refresh.click(fn=_preview, inputs=[path, negative_path, merge_lines],
                      outputs=[preview, negative_preview, status])
        path.submit(fn=_preview, inputs=[path, negative_path, merge_lines],
                    outputs=[preview, negative_preview, status])
        negative_path.submit(fn=_preview, inputs=[path, negative_path, merge_lines],
                             outputs=[preview, negative_preview, status])
        launch_button.click(fn=_launch_click, inputs=[editor_path], outputs=[launch_status])

        # 生成页总线：隐藏 apply 钮。JS 轮询到 cmd.json 后先点它（服务端读
        # params.json → gr.update 回填界面组件，未捕获/缺失的组件自动跳过），
        # 稍候再点该页生成钮，走 UI 正常队列。visible=False 的 Button 仍在 DOM
        # 中可被 JS 点击（A1111 自家 img2img_update_resize_to 同款用法）。
        tab = "img2img" if is_img2img else "txt2img"
        targets = [(section, key, _UI_COMPONENTS[elem_id], kind)
                   for section, key, elem_id, kind in _FIELD_TABLES[is_img2img]
                   if elem_id in _UI_COMPONENTS]
        missing = [row[2] for row in _FIELD_TABLES[is_img2img]
                   if row[2] not in _UI_COMPONENTS]
        if missing:
            _log(f"生成页（{tab}）未捕获组件：{', '.join(missing)}（对应参数将跳过）")
        apply_button = gr.Button(value="feetag-apply", visible=False,
                                 elem_id=f"feetag_apply_{tab}")
        if targets:
            apply_button.click(fn=_make_apply_handler(targets), inputs=[],
                               outputs=[row[2] for row in targets],
                               show_progress=False, queue=False)

        controls = {
            "enabled": enabled,
            "path": path,
            "negative_path": negative_path,
            "merge_lines": merge_lines,
            "autostart": autostart,
            "editor_path": editor_path,
        }
        _wire_controls(controls, is_img2img)

        return [enabled, path, negative_path, merge_lines, autostart, editor_path]

    def before_process(self, p, enabled, path, negative_path, merge_lines, autostart, editor_path):
        """每次生成任务触发一次，早于提示词列表构建，改 p.prompt 即可全量生效。"""
        if getattr(p, "_ad_inner", False):
            return  # ADetailer 内部 pass：不注入不计数不写状态（防御行）
        global _gen_pass
        _gen_pass += 1
        _write_status("busy")
        _save_config(dict(zip(CONTROL_KEYS, (enabled, path, negative_path,
                                              merge_lines, autostart, editor_path))))
        if not enabled:
            return

        tags, message = read_tag_file(path, merge_lines)
        if tags is None:
            _log(f"正向跳过注入：{message}")
        else:
            tags, metas = strip_meta_tags(tags)
            meta = metas[-1] if metas else None
            if not tags:
                _log("正向跳过注入：剥离元数据 tag 后内容为空")
            else:
                injected = _count_tags(tags)  # 展开前计数 = 编辑器平铺 tag 流的 tag 数
                tags = expand_breaks(tags, meta)  # BREAK 元数据展开为空行分隔
                p.prompt = _inject(p.prompt, tags)
                _record_meta_png(p, meta, "fth_meta",
                                 {"injected_tags": injected, "full_text": p.prompt})
                if meta is not None:
                    _log(f"正向元数据已剥离并写入 PNG（breaks={meta.get('breaks')}）")
                shown = tags[:120] + ("…" if len(tags) > 120 else "")
                _log(f"正向已注入 {injected} 个 tag / {len(tags)} 个字符（{message}）：{shown}")

        if _normalize_path(negative_path):
            neg_tags, neg_message = read_tag_file(negative_path, merge_lines)
            if neg_tags is None:
                _log(f"反向跳过注入：{neg_message}")
            else:
                neg_tags, neg_metas = strip_meta_tags(neg_tags)
                neg_meta = neg_metas[-1] if neg_metas else None
                if not neg_tags:
                    _log("反向跳过注入：剥离元数据 tag 后内容为空")
                else:
                    neg_injected = _count_tags(neg_tags)
                    neg_tags = expand_breaks(neg_tags, neg_meta)
                    p.negative_prompt = _inject(p.negative_prompt, neg_tags)
                    _record_meta_png(p, neg_meta, "fth_meta_negative",
                                     {"injected_tags": neg_injected,
                                      "full_text": p.negative_prompt})
                    if neg_meta is not None:
                        _log(f"反向元数据已剥离并写入 PNG（breaks={neg_meta.get('breaks')}）")
                    _log(f"反向已注入 {neg_injected} 个 tag / {len(neg_tags)} 个字符（{neg_message}）")

    def postprocess(self, p, processed, *args):
        """生成完成：成品图复制到 featag_out/（毫秒时间戳命名）并置状态 done。

        ADetailer 内部 pass 走不到这里（脚本白名单已隔离），此处再防御一次。
        任何异常只置 error 状态 + 打日志，绝不影响生成任务本身。
        """
        if getattr(p, "_ad_inner", False):
            return
        try:
            os.makedirs(FEETAG_OUT_DIR, exist_ok=True)
            stamp = time.strftime("%Y%m%d_%H%M%S") + f"{int(time.time() * 1000) % 1000:03d}"
            saved = []
            for i, image in enumerate(list(processed.images or [])):
                path = os.path.join(FEETAG_OUT_DIR, f"fth_{stamp}_{i}.png")
                try:
                    image.save(path)
                    saved.append(path)
                except (AttributeError, OSError, ValueError) as e:
                    _log(f"回传图片 {i} 失败：{e}")
            _write_status("done", images=saved)
            _log(f"已回传 {len(saved)} 张图到 featag_out/")
        except Exception as e:  # noqa: BLE001 - 兜底，回传永不影响生成
            _log(f"回传异常：{e}")
            try:
                _write_status("error", error=str(e))
            except Exception:
                pass


def _on_app_started(demo=None, app=None):
    try:
        os.makedirs(FEETAG_OUT_DIR, exist_ok=True)
    except OSError:
        pass
    _write_status("idle")  # 启动即发初态（含 choices），编辑器据此判断插件在线
    cfg = _load_config()
    if not cfg["autostart"]:
        return
    ok, message = launch_editor(cfg["editor_path"])
    _log(f"自动启动编辑器：{message}")


script_callbacks.on_after_component(_on_after_component)
script_callbacks.on_app_started(_on_app_started)
