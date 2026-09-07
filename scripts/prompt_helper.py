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

v1.4.3 起总线带总开关：插件目录放置 bus.armed 标志文件才启用全部总线行为
（JS 轮询 / status 写入 / featag_out 回传 / apply 回填），默认关闭——词条注入
不受开关影响。放置/删除即刻生效，无需重启。
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
from modules import script_callbacks, scripts
from modules.scripts import AlwaysVisible
# 注意：不从 modules.scripts 导入 Script 基类名——register_scripts_from_module 会把
# 模块命名空间里的所有 Script 子类注册为脚本（含基类本身），基类实例的 title() 会
# 抛 NotImplementedError 造成启动日志 4 条报错（v1.4.5 修复，见通宵记录）。

EXT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(EXT_DIR, "config.json")

# 生成页总线文件（编辑器直写 / 浏览器 JS 经 /file= 读取，均在插件目录内）
PARAMS_PATH = os.path.join(EXT_DIR, "params.json")
CMD_PATH = os.path.join(EXT_DIR, "cmd.json")
STATUS_PATH = os.path.join(EXT_DIR, "status.json")
FEETAG_OUT_DIR = os.path.join(EXT_DIR, "featag_out")
# 总开关（v1.4.3）：bus.armed 存在才启用总线（status/featag_out/apply 回填/JS 轮询）。
# 默认关闭——词条注入（本插件核心功能）不受影响；排查期防止任何总线副作用。
ARMED_PATH = os.path.join(EXT_DIR, "bus.armed")

PLUGIN_VERSION = "1.4.5"

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

# —— M-31b 超分区脚本注入表（v1.4.5）——
# Tiled Diffusion（multidiffusion 扩展，AlwaysVisible 常驻；tab=txt2img/img2img，
# 前缀式 uid 与两处后缀式特例并存——已对照源码逐条锁定）：
_TILED_COMMON = [
    ("enable", "MD-{tab}-enabled-checkbox", "checkbox"),          # InputAccordion 隐藏勾选框
    ("method", "MD-{tab}-method", "dropdown"),
    ("tile_w", "MD-{tab}-latent-tile-width", "slider_int"),
    ("tile_h", "MD-{tab}-latent-tile-height", "slider_int"),
    ("overlap", "MD-{tab}-latent-tile-overlap", "slider_int"),
    ("upscaler", "MD-{tab}-upscaler-index", "dropdown"),
    ("scale", "MD-{tab}-upscaler-factor", "slider_float"),
]
_TILED_FIELDS = {
    False: _TILED_COMMON + [  # txt2img 独有：Overwrite image size
        ("overwrite_size", "MD-{tab}-overwrite-image-size", "checkbox"),
        ("image_width", "MD-overwrite-width-{tab}", "slider_int"),   # 后缀式特例
        ("image_height", "MD-overwrite-height-{tab}", "slider_int"),
    ],
    True: _TILED_COMMON + [   # img2img 独有：Keep input image size
        ("keep_input_size", "MD-{tab}-keep-input-size", "checkbox"),
    ],
}
# Tiled VAE（同扩展另一常驻脚本；tab 短拼法 t2i/i2i）
_TILEDVAE_FIELDS = [
    ("enable", "MDV-{tab}-enabled-checkbox", "checkbox"),
    ("vae_to_gpu", "MD-{tab}-vae2gpu", "checkbox"),
    ("encoder_tile_size", "MD-{tab}-enc-size", "slider_int"),
    ("decoder_tile_size", "MD-{tab}-dec-size", "slider_int"),
    ("fast_encoder", "MD-{tab}-fastenc", "checkbox"),
    ("color_fix", "MD-{tab}-fastenc-colorfix", "checkbox"),
    ("fast_decoder", "MD-{tab}-fastdec", "checkbox"),
]
# Ultimate SD upscale（img2img 可选脚本——注入时需同时把脚本下拉选中）
_USDU_FIELDS = [
    ("target_size_type", "ultimateupscale_target_size_type", "dropdown_index"),
    ("custom_width", "ultimateupscale_custom_width", "slider_int"),
    ("custom_height", "ultimateupscale_custom_height", "slider_int"),
    ("custom_scale", "ultimateupscale_custom_scale", "slider_float"),
    ("upscaler", "ultimateupscale_upscaler_index", "radio"),
    ("redraw_mode", "ultimateupscale_redraw_mode", "dropdown_index"),
    ("tile_width", "ultimateupscale_tile_width", "slider_int"),
    ("tile_height", "ultimateupscale_tile_height", "slider_int"),
    ("mask_blur", "ultimateupscale_mask_blur", "slider_int"),
    ("padding", "ultimateupscale_padding", "slider_int"),
    ("seams_fix_type", "ultimateupscale_seams_fix_type", "dropdown_index"),
]
_USDU_SCRIPT_TITLE = "Ultimate SD upscale"
_USDU_SCRIPT_LIST_ID = "script_list"   # txt2img/img2img 各渲染一份（按创建序区分页）


def _field_table(is_img2img):
    """某页的可覆盖字段表：[(总线分区, 语义键, elem_id, 组件类型)]。"""
    tab = "img2img" if is_img2img else "txt2img"
    table = [("base", key, f"{tab}_{e}", kind) for key, e, kind in _BASE_FIELDS]
    if not is_img2img:
        table += [("hires", key, e, kind) for key, e, kind in _HIRES_FIELDS]
    short_tab = "i2i" if is_img2img else "t2i"   # Tiled Diffusion 与 Tiled VAE 均用短拼法（源码实锤）
    for key, fmt, kind in _TILED_FIELDS[is_img2img]:
        table.append(("tiled", key, fmt.format(tab=short_tab), kind))
    for key, fmt, kind in _TILEDVAE_FIELDS:
        table.append(("tiledvae", key, fmt.format(tab=short_tab), kind))
    if is_img2img:
        table += [("usdu", key, e, kind) for key, e, kind in _USDU_FIELDS]
    return table


_FIELD_TABLES = {False: _field_table(False), True: _field_table(True)}
_WANTED_ELEM_IDS = {row[2] for rows in _FIELD_TABLES.values() for row in rows}

# on_after_component 捕获到的页面组件（elem_id -> gradio 组件）
_UI_COMPONENTS = {}
# 脚本下拉（elem_id="script_list"，txt2img/img2img 各一份、同 id）——按创建序存放
_SCRIPT_LISTS = []
# 总线按钮与接线状态（延迟接线：等该页全部目标组件捕获齐全后再注册事件——
# USDU 等扩展的组件创建晚于本插件 ui()，ui() 时点快照会漏）
_BUS_BUTTONS = {}
_AD_BUTTONS = {}
_AD_FIELDS = {}
_WIRED = {}

# status.json 写入去重（内容未变化不重写）+ 生成计数
_status_lock = threading.Lock()
_status_snapshot = None
_gen_pass = 0


def bus_armed():
    """总线总开关：bus.armed 标志文件存在 = 启用。每次现查（一次 stat，代价可忽略），
    放置/删除文件即刻生效，无需重启 WebUI。"""
    return os.path.isfile(ARMED_PATH)


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


def _write_status(state, images=None, error=None, adetailer=None):
    """写 status.json（state/pass/images/error/ts + choices [+ adetailer]）。

    内容签名（state/pass/images/error/adetailer/choices）未变化时不重写——客户端高频轮询
    的只是不再变化的文件，磁盘零增长；ts 仅在真实写入时刷新。
    任何写入异常只打日志，绝不影响生成。
    """
    global _status_snapshot
    choices = _publish_choices()
    signature = json.dumps([state, _gen_pass, list(images or []), error, adetailer, choices],
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
        if adetailer is not None:
            payload["adetailer"] = adetailer
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
    if kind == "dropdown_index":
        # type="index" 的下拉：接受选项序号（int）
        idx = int(float(value))
        choices = list(getattr(comp, "choices", []) or [])
        if choices and not 0 <= idx < len(choices):
            return None
        return idx
    if kind == "radio":
        # type="index" 的 Radio：编辑器传选项名 → 换算序号
        choices = list(getattr(comp, "choices", []) or [])
        text = str(value)
        if text in choices:
            return choices.index(text)
        return None
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
        choices = list(getattr(comp, "choices", []) or [])
        if choices and text not in choices:
            return None
    return text


def _try_wire_page(is_img2img):
    """延迟接线：该页全部目标组件捕获齐全 + 按钮已创建时，注册 apply/ADetailer 事件。
    幂等（每页只接一次）；由 _on_after_component 与 ui() 末尾共同触发。"""
    if _WIRED.get(is_img2img) or is_img2img not in _BUS_BUTTONS:
        return
    wanted = [row[2] for row in _FIELD_TABLES[is_img2img]]
    missing = [e for e in wanted if e not in _UI_COMPONENTS]
    if missing:
        return
    tab = "img2img" if is_img2img else "txt2img"
    targets = []
    if is_img2img and len(_SCRIPT_LISTS) > 1:
        targets.append(("usdu", "_select", _SCRIPT_LISTS[1], "scriptsel"))
    targets += [(section, key, _UI_COMPONENTS[elem_id], kind)
                for section, key, elem_id, kind in _FIELD_TABLES[is_img2img]]
    ad_fields = _AD_FIELDS.get(is_img2img) or []
    # ADetailer 回填并入同一事件（单事件双段更新）——独立第二按钮的接线在部分
    # 页面不可靠（config 实测 txt2img AD 依赖缺失），合并后彻底消除该变量
    outputs = [t[2] for t in targets] + [comp for comp, _key in ad_fields]
    apply_btn = _BUS_BUTTONS[is_img2img]
    apply_btn.click(fn=_make_apply_handler(targets, ad_fields), inputs=[],
                    outputs=outputs, show_progress=False, queue=False)
    _WIRED[is_img2img] = True
    _log(f"生成页（{tab}）总线已接线：apply {len(targets)} 项输出 + ADetailer 回填 {len(ad_fields)} 项（同一事件）")


def _make_apply_handler(targets, ad_fields=None):
    """生成页隐藏 apply 钮的处理函数：读 params.json，产出 gr.update 列表——
    前段=targets（base/hires/scripts 界面组件），后段=ad_fields（ADetailer infotext
    回填，v1.4.5 起并入同一事件）。键不出现 / 显式 null / 值非法 → gr.update() 不覆盖。"""
    ad_fields = ad_fields or []

    def handler():
        if not bus_armed():
            return [gr.update() for _ in targets] + [gr.update() for _ in ad_fields]
        params = _read_bus_json(PARAMS_PATH)
        params = params if isinstance(params, dict) else {}
        updates, applied, skipped = [], [], []
        for section, key, comp, kind in targets:
            section_data = params.get(section)
            value = _MISSING
            if isinstance(section_data, dict) and key in section_data:
                value = section_data[key]
            if kind == "scriptsel":
                # USDU 特例：scripts.usdu 出现（enable=true 或带任意字段）→ 选中该脚本
                usdu = section_data.get("usdu") if isinstance(section_data, dict) else None
                want = isinstance(usdu, dict) and (usdu.get("enable") is True or len(usdu) > 0)
                if want:
                    choices = list(getattr(comp, "choices", []) or [])
                    idx = choices.index(_USDU_SCRIPT_TITLE) if _USDU_SCRIPT_TITLE in choices else -1
                    updates.append(gr.update(value=idx) if idx >= 0 else gr.update())
                    applied.append(f"usdu.select={idx}")
                else:
                    updates.append(gr.update())
                continue
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

        # —— ADetailer infotext 回填段（单事件双段更新的第二段）——
        ad_text = params.get("adetailer_infotext")
        ad_text = ad_text if isinstance(ad_text, str) else ""
        if ad_text.strip():
            try:
                from modules import infotext_utils
                parsed = infotext_utils.parse_generation_parameters(ad_text)
            except Exception as e:
                _log(f"ADetailer infotext 解析失败：{e}")
                parsed = {}
            for comp, key in ad_fields:
                value = None
                if isinstance(key, str):
                    value = parsed.get(key)
                elif callable(key):
                    try:
                        value = key(parsed)
                    except Exception:
                        value = None
                if value is None:
                    updates.append(gr.update())
                    continue
                val = _paste_style_value(comp, value)
                if val is None:
                    updates.append(gr.update())
                    continue
                updates.append(gr.update(value=val))
            _log(f"ADetailer infotext 回填完成（{len(ad_fields)} 组件）")
        return updates
    return handler


def _paste_style_value(comp, value):
    """按 connect_paste 的同款规则把 infotext 文本值转成组件值（失败返回 None）。"""
    try:
        valtype = type(comp.value)
        if valtype is bool and value == "False":
            return False
        if valtype is int:
            return int(float(value))
        return valtype(value)
    except (TypeError, ValueError):
        return None


def _make_adetailer_apply_handler(fields):
    """ADetailer infotext 回填（M-31c/P4，通道定案见论证文档 §7.3）：
    读 params.json 的 adetailer_infotext（编辑器拼好的参数文本），经
    parse_generation_parameters 解析后按 ADetailer 自注册的 infotext_fields
    批量 gr.update——与 connect_paste 同机制，键驱动、缺键跳过。"""
    def handler():
        if not bus_armed() or not fields:
            return [gr.update() for _ in fields]
        params = _read_bus_json(PARAMS_PATH) or {}
        text = params.get("adetailer_infotext")
        text = text if isinstance(text, str) else ""
        if not text.strip():
            return [gr.update() for _ in fields]
        try:
            from modules import infotext_utils
            parsed = infotext_utils.parse_generation_parameters(text)
        except Exception as e:
            _log(f"ADetailer infotext 解析失败：{e}")
            return [gr.update() for _ in fields]
        updates, applied = [], []
        for comp, key in fields:
            value = None
            if isinstance(key, str):
                value = parsed.get(key)
            elif callable(key):
                try:
                    value = key(parsed)
                except Exception:
                    value = None
            if value is None:
                updates.append(gr.update())
                continue
            val = _paste_style_value(comp, value)
            if val is None:
                updates.append(gr.update())
                continue
            updates.append(gr.update(value=val))
            applied.append(f"{key}={val}")
        _log(f"ADetailer infotext 回填：{', '.join(applied) if applied else '无生效键'}")
        return updates
    return handler


def _adetailer_marks(p):
    """从 extra_generation_params 提取 ADetailer 参与标记（P4 pass 标注）。
    无 ADetailer 行 = 空表（status 不带该键）。"""
    try:
        marks = sorted(str(k) for k in (getattr(p, "extra_generation_params", {}) or {})
                       if str(k).startswith("ADetailer"))
        return marks or None
    except Exception:
        return None


def _on_after_component(component, **kwargs):
    """捕获生成页参数组件（elem_id 在选择器表内的），供 apply 事件作 outputs。"""
    try:
        elem_id = getattr(component, "elem_id", None)
        if elem_id in _WANTED_ELEM_IDS:
            _UI_COMPONENTS[elem_id] = component
        elif elem_id == _USDU_SCRIPT_LIST_ID:
            _SCRIPT_LISTS.append(component)  # 创建序：0=txt2img, 1=img2img
    finally:
        try:
            _try_wire_page(False)
            _try_wire_page(True)
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


class PromptHelperScript(scripts.Script):

    def title(self):
        return "外部提示词注入 (prompt-helper)"

    def show(self, is_img2img):
        return scripts.AlwaysVisible

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
        # 总线按钮（v1.4.5 起延迟接线：事件注册推迟到该页全部目标组件捕获齐全时
        # ——由 _on_after_component 调 _try_wire_page 完成——避免 USDU 等晚创建
        # 组件被 ui() 时点快照漏掉）。visible=False 的按钮仍可被 JS 点击
        # （A1111 自家 img2img_update_resize_to 同款用法）。
        tab = "img2img" if is_img2img else "txt2img"
        apply_button = gr.Button(value="feetag-apply", visible=False,
                                 elem_id=f"feetag_apply_{tab}")
        _BUS_BUTTONS[is_img2img] = apply_button

        # ADetailer infotext 回填通道（M-31c/P4）：复刻 connect_paste 的键驱动回填，
        # 仅作用于 ADetailer 自注册的 infotext_fields；扩展缺失时静默跳过
        ad_fields = []
        try:
            from modules import scripts as a1111_scripts
            runner = a1111_scripts.scripts_img2img if is_img2img else a1111_scripts.scripts_txt2img
            ad_script = runner.script("ADetailer")
            ad_fields = list(getattr(ad_script, "infotext_fields", None) or [])
        except Exception as e:
            _log(f"ADetailer infotext_fields 捕获失败（{tab}）：{e}")
        _AD_FIELDS[is_img2img] = ad_fields
        ad_button = gr.Button(value="feetag-adetailer-apply", visible=False,
                              elem_id=f"feetag_adetailer_apply_{tab}")
        _AD_BUTTONS[is_img2img] = ad_button
        if not ad_fields:
            _log(f"ADetailer 未安装或未注册 infotext_fields（{tab}）——回填通道跳过")

        _try_wire_page(is_img2img)

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
        if bus_armed():
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
        总开关关闭时整段跳过（不落图不写状态）。
        任何异常只置 error 状态 + 打日志，绝不影响生成任务本身。
        """
        if getattr(p, "_ad_inner", False) or not bus_armed():
            return
        try:
            os.makedirs(FEETAG_OUT_DIR, exist_ok=True)
            stamp = time.strftime("%Y%m%d_%H%M%S") + f"{int(time.time() * 1000) % 1000:03d}"
            saved = []
            # 把生成信息（parameters）写进回传副本，编辑器拿到的图自证参数
            geninfo = getattr(processed, "info", None)
            pnginfo = None
            if isinstance(geninfo, str) and geninfo:
                try:
                    from PIL import PngImagePlugin
                    pnginfo = PngImagePlugin.PngInfo()
                    pnginfo.add_text("parameters", geninfo)
                except Exception:
                    pnginfo = None
            for i, image in enumerate(list(processed.images or [])):
                path = os.path.join(FEETAG_OUT_DIR, f"fth_{stamp}_{i}.png")
                try:
                    if pnginfo is not None:
                        image.save(path, pnginfo=pnginfo)
                    else:
                        image.save(path)
                    saved.append(path)
                except (AttributeError, OSError, ValueError) as e:
                    _log(f"回传图片 {i} 失败：{e}")
            _write_status("done", images=saved, adetailer=_adetailer_marks(p))
            _log(f"已回传 {len(saved)} 张图到 featag_out/")
        except Exception as e:  # noqa: BLE001 - 兜底，回传永不影响生成
            _log(f"回传异常：{e}")
            try:
                _write_status("error", error=str(e))
            except Exception:
                pass


def _register_bus_endpoints(app):
    """注册总线只读端点（v1.4.4）：
      GET /feetag/bus/status         → status.json 内容（application/json）
      GET /feetag/bus/image?name=xx  → featag_out/<name>（basename 防穿越）
    两者都带 Access-Control-Allow-Origin: *——编辑器面板（Tauri webview 的
    tauri.localhost 源 / dev 的 localhost:5173 源）跨源读取 /file= 会被 CORS
    拦截（gradio 的 CORS 只放行本机同名源），自有端点解决之。
    路由注册无条件（保证"放置 bus.armed 即生效"），内容按 bus_armed() 门控：
    未启用时一律 404，与总线默认关语义一致。"""
    try:
        from fastapi.responses import FileResponse, Response
    except Exception as e:  # fastapi 理论上必在（gradio 依赖）；防御性兜底
        _log(f"总线端点未注册（fastapi 导入失败）：{e}")
        return

    def _bus_status():
        if not bus_armed():
            return Response(status_code=404)
        try:
            with open(STATUS_PATH, "rb") as f:
                return Response(content=f.read(), media_type="application/json",
                                headers={"Access-Control-Allow-Origin": "*"})
        except OSError:
            return Response(status_code=404)

    def _bus_image(name: str = ""):
        if not bus_armed():
            return Response(status_code=404)
        path = os.path.join(FEETAG_OUT_DIR, os.path.basename(name or ""))
        if not os.path.isfile(path):
            return Response(status_code=404)
        return FileResponse(path, headers={"Access-Control-Allow-Origin": "*"})

    try:
        app.add_api_route("/feetag/bus/status", _bus_status, methods=["GET"], include_in_schema=False)
        app.add_api_route("/feetag/bus/image", _bus_image, methods=["GET"], include_in_schema=False)
        _log("总线端点已注册：GET /feetag/bus/status、/feetag/bus/image（bus.armed 门控）")
    except Exception as e:
        _log(f"总线端点注册失败（不影响其他功能）：{e}")


def _on_app_started(demo=None, app=None):
    if app is not None:
        _register_bus_endpoints(app)
    if bus_armed():
        try:
            os.makedirs(FEETAG_OUT_DIR, exist_ok=True)
        except OSError:
            pass
        _write_status("idle")  # 总线启用时发初态（含 choices），编辑器据此判断插件在线
    cfg = _load_config()
    if not cfg["autostart"]:
        return
    ok, message = launch_editor(cfg["editor_path"])
    _log(f"自动启动编辑器：{message}")


script_callbacks.on_after_component(_on_after_component)
script_callbacks.on_app_started(_on_app_started)
