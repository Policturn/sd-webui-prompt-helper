# -*- coding: utf-8 -*-
"""离线自测：不启动 WebUI，用 mock 验证注入核心逻辑。

用法：python test_prompt_helper.py
"""

import base64
import html
import importlib.util
import json
import os
import sys
import tempfile
import threading
import time
import types

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT_PATH = os.path.join(HERE, "scripts", "prompt_helper.py")
REAL_TXT = r"E:\桌面\AI file\Design file\prompt-helper\prompt.txt"


# ---- mock gradio / modules.scripts ----
class _Comp:
    _stack = []  # v1.4.18 布局断言：上下文管理器维护子组件树

    def __init__(self, *args, **kwargs):
        self._change_calls = []  # 记录 .change 接线（kwargs），供 _wire_controls 断言
        self._args = args        # 记录构造位置参数（label/value 等在 kwargs）
        self._kwargs = kwargs
        self._children = []
        if _Comp._stack:
            _Comp._stack[-1]._children.append(self)

    def __enter__(self):
        _Comp._stack.append(self)
        return self

    def __exit__(self, *args):
        _Comp._stack.pop()
        return False

    def change(self, *args, **kwargs):
        self._change_calls.append(kwargs)

    def submit(self, *args, **kwargs):
        pass

    def click(self, *args, **kwargs):
        pass


gradio = types.ModuleType("gradio")
for name in ("Accordion", "Row", "Group", "Checkbox", "Radio", "Textbox", "HTML", "Button"):
    setattr(gradio, name, type(name, (_Comp,), {}))
gradio.update = lambda **kwargs: dict(kwargs, __type__="update")

scripts_mod = types.ModuleType("modules.scripts")


class Script:  # noqa: N801 - 模拟 modules.scripts.Script
    pass


scripts_mod.Script = Script
scripts_mod.AlwaysVisible = object()

callbacks_mod = types.ModuleType("modules.script_callbacks")
_registered_callbacks = []
_after_component_cbs = []
_before_ui_cbs = []
callbacks_mod.on_app_started = lambda cb, name=None: _registered_callbacks.append(cb)
callbacks_mod.on_after_component = lambda cb, name=None: _after_component_cbs.append(cb)
callbacks_mod.on_before_ui = lambda cb, name=None: _before_ui_cbs.append(cb)

modules_pkg = types.ModuleType("modules")
modules_pkg.scripts = scripts_mod
modules_pkg.script_callbacks = callbacks_mod

sys.modules["gradio"] = gradio
sys.modules["modules"] = modules_pkg
sys.modules["modules.scripts"] = scripts_mod
sys.modules["modules.script_callbacks"] = callbacks_mod

# 真实加载模块（runpy.run_path 返回全局字典副本，补丁不会生效）
spec = importlib.util.spec_from_file_location("prompt_helper", SCRIPT_PATH)
mod = importlib.util.module_from_spec(spec)
sys.modules["prompt_helper"] = mod
spec.loader.exec_module(mod)
ns = mod.__dict__

# before_process 会把参数写回配置文件，测试期间改用临时配置，避免污染真实配置；
# 总线文件（status/params/cmd/featag_out/bus.armed）同理重定向到临时目录，
# 并放置 bus.armed 使总线处于启用态（与未启用态的行为差异另有专项用例）
TMP_DIR = tempfile.mkdtemp()
ns["CONFIG_PATH"] = os.path.join(TMP_DIR, "config.json")
ns["PARAMS_PATH"] = os.path.join(TMP_DIR, "params.json")
ns["CMD_PATH"] = os.path.join(TMP_DIR, "cmd.json")
ns["STATUS_PATH"] = os.path.join(TMP_DIR, "status.json")
ns["FEETAG_OUT_DIR"] = os.path.join(TMP_DIR, "featag_out")
ns["ARMED_PATH"] = os.path.join(TMP_DIR, "bus.armed")
# pin 文件同理重定向到临时目录（真实 pin 若存在会影响既有用例语义）：
# 统一 settings.pin + 旧独立 pin（negative/positive）三份
ns["NEGATIVE_PIN_PATH"] = os.path.join(TMP_DIR, "negative_path.pin")
ns["POSITIVE_PIN_PATH"] = os.path.join(TMP_DIR, "positive_path.pin")
ns["SETTINGS_PIN_PATH"] = os.path.join(TMP_DIR, "settings.pin")
# Wave B（v1.4.18）总线文件同理重定向：直发模式标志 / 页面状态快照
ns["DIRECT_PATH"] = os.path.join(TMP_DIR, "bus.direct")
ns["PAGE_STATE_PATH"] = os.path.join(TMP_DIR, "bus.page_state.json")
ns["PAGE_STATE_OFF_PATH"] = os.path.join(TMP_DIR, "bus.page_state.disabled")
# v1.4.21 破坏性变更审计日志同理重定向（护栏用例会触发真实写审计行）
ns["AUDIT_LOG_PATH"] = os.path.join(TMP_DIR, "config.audit.log")
open(ns["ARMED_PATH"], "w").close()
ns["_status_snapshot"] = None


class FakeP:
    def __init__(self):
        self.prompt = "masterpiece, best quality"
        self.negative_prompt = "lowres"
        self.extra_generation_params = {}


class FakeProcessed:
    def __init__(self, images, info="Steps: 24"):
        self.images = images
        self.info = info


def check(label, cond):
    print(("  PASS  " if cond else "  FAIL  ") + label)
    if not cond:
        raise SystemExit(1)


print("== read_tag_file ==")
text, msg = ns["read_tag_file"](REAL_TXT)
print(f"  真实 prompt.txt -> [{msg}] {text[:60]}...")
check("读取真实词条文件", text is not None and "1girl" in text)

with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="gbk") as f:
    f.write("白发, 黑瞳\n, long hair")
    gbk_path = f.name
text, msg = ns["read_tag_file"](gbk_path)
print(f"  GBK 多行文件 -> [{msg}] {text}")
check("GBK 解码 + 换行合并", text == "白发, 黑瞳 , long hair")
os.unlink(gbk_path)

text, msg = ns["read_tag_file"](r"C:\__no_such_file__.txt")
check("缺失文件返回错误", text is None and "不存在" in msg)

with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
    f.write("   \n  ")
    empty_path = f.name
text, msg = ns["read_tag_file"](empty_path)
check("空文件返回错误", text is None and "为空" in msg)
os.unlink(empty_path)

text, msg = ns["read_tag_file"]('  "' + REAL_TXT + '"  ')
check("路径带引号/空格可容错", text is not None)


def _b64url(s):
    return base64.urlsafe_b64encode(s.encode("utf-8")).decode("ascii").rstrip("=")


def _linked(tags_text, meta):
    """模拟 FeeTagHelper 构建区的 txt 链路输出：平铺 tag 流 + 末尾元数据 tag。"""
    return tags_text + ", <fth:meta:" + _b64url(json.dumps(meta, separators=(",", ":"))) + ">"


print("== 元数据剥离 / BREAK 展开 ==")
META = {"v": 1, "breaks": [2], "pick": [{"path": "seg-1", "key": "smile"}]}

clean, metas = ns["strip_meta_tags"](_linked("1girl, smile, dress", META))
check("元数据 tag 整体剥离", clean == "1girl, smile, dress")
check("元数据解码为 dict", metas == [META])

clean, metas = ns["strip_meta_tags"]("a, b, c")
check("无元数据时原样返回", clean == "a, b, c" and metas == [])

clean, metas = ns["strip_meta_tags"]("a, <fth:meta:zzzz>, b")
check("解码失败静默丢弃整 tag", clean == "a, b" and metas == [])

check("BREAK 展开：第 N 个 tag 后空行分隔",
      ns["expand_breaks"]("1girl, smile, dress", {"v": 1, "breaks": [2], "pick": []})
      == "1girl, smile\n\ndress")
check("BREAK 防御性修剪：首/尾/越界位置忽略",
      ns["expand_breaks"]("a, b, c", {"v": 1, "breaks": [0, 3, 9], "pick": []}) == "a, b, c")
check("无 breaks / 无元数据不改动文本",
      ns["expand_breaks"]("a, b, c", {"v": 1, "pick": []}) == "a, b, c"
      and ns["expand_breaks"]("a, b", None) == "a, b")

print("== _preview ==")
with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
    f.write("blurry, bad hands")
    NEG_TXT = f.name
pos, neg, hint = ns["_preview"](REAL_TXT, "", True)
check("预览：反向留空提示未设置", bool(pos) and "未设置" in hint)
pos, neg, hint = ns["_preview"](REAL_TXT, NEG_TXT, True)
check("预览：双文件内容与状态", pos and neg == "blurry, bad hands"
      and "正向" in hint and "反向" in hint)
pos, neg, hint = ns["_preview"](r"C:\__no__.txt", NEG_TXT, True)
check("预览：正向缺失显示错误", pos == "" and "✗ 正向" in hint)
print(f"  {hint}")

print("== before_process 注入（v1.4.1 起恒定前置）==")
script = ns["PromptHelperScript"]()
# 与插件注入管线一致地算期望值（prompt.txt 将来携带元数据 tag 时断言依然成立）
raw = ns["read_tag_file"](REAL_TXT)[0]
base, _metas = ns["strip_meta_tags"](raw)
base = ns["expand_breaks"](base, _metas[-1] if _metas else None)

p = FakeP()
script.before_process(p, True, REAL_TXT, NEG_TXT, True, False, "")
check("注入恒在最前（正反向各自生效）", p.prompt == base + ", masterpiece, best quality"
      and p.negative_prompt == "blurry, bad hands, lowres")

recorded = json.loads(p.extra_generation_params.get("fth_meta", "{}"))
neg_recorded = json.loads(p.extra_generation_params.get("fth_meta_negative", "{}"))
check("无元数据时也写入注入统计（injected_tags / full_text / plugin）",
      recorded.get("plugin") == ns["PLUGIN_VERSION"]
      and recorded.get("injected_tags") == ns["_count_tags"](base)
      and recorded.get("full_text") == p.prompt
      and neg_recorded.get("injected_tags") == 2
      and neg_recorded.get("full_text") == "blurry, bad hands, lowres")

p = FakeP()
script.before_process(p, True, REAL_TXT, "", True, False, "")
check("反向留空不注入", p.prompt == base + ", masterpiece, best quality"
      and p.negative_prompt == "lowres"
      and "fth_meta_negative" not in p.extra_generation_params)

p = FakeP()
script.before_process(p, True, REAL_TXT, r"C:\__no_neg__.txt", True, False, "")
check("反向文件缺失时跳过反向", p.prompt == base + ", masterpiece, best quality"
      and p.negative_prompt == "lowres")

p = FakeP()
script.before_process(p, False, REAL_TXT, NEG_TXT, True, False, "")
check("停用时不注入", p.prompt == "masterpiece, best quality" and p.negative_prompt == "lowres"
      and not p.extra_generation_params)

p = FakeP()
p.prompt = ["a", "b"]
script.before_process(p, True, REAL_TXT, "", True, False, "")
check("列表提示词逐项注入", p.prompt == [base + ", a", base + ", b"])

p = FakeP()
script.before_process(p, True, r"C:\__no_such_file__.txt", NEG_TXT, True, False, "")
check("正向缺失时跳过且反向仍注入", p.prompt == "masterpiece, best quality"
      and p.negative_prompt == "blurry, bad hands, lowres"
      and "fth_meta" not in p.extra_generation_params)

os.unlink(NEG_TXT)

print("== before_process 元数据注入管线 ==")
with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
    f.write(_linked("1girl, smile, dress, hat", {"v": 1, "breaks": [2, 4], "pick": []}))
    META_TXT = f.name
with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
    f.write(_linked("blurry, bad hands", {"v": 1, "breaks": [1], "pick": [{"path": "g", "key": "blurry"}]}))
    META_NEG = f.name

p = FakeP()
script.before_process(p, True, META_TXT, META_NEG, True, False, "")
check("剥离元数据 + BREAK 展开（尾部位置防御性修剪）",
      p.prompt == "1girl, smile\n\ndress, hat, masterpiece, best quality"
      and p.negative_prompt == "blurry\n\nbad hands, lowres"
      and "<fth:meta:" not in p.prompt + p.negative_prompt)
recorded = json.loads(p.extra_generation_params.get("fth_meta", "{}"))
neg_recorded = json.loads(p.extra_generation_params.get("fth_meta_negative", "{}"))
check("元数据 + 注入统计写入 PNG extra_generation_params（附插件版本）",
      recorded.get("breaks") == [2, 4] and recorded.get("plugin") == ns["PLUGIN_VERSION"]
      and neg_recorded.get("breaks") == [1])
check("injected_tags 按展开前平铺 tag 流计数（BREAK 不吃掉逗号）",
      recorded.get("injected_tags") == 4 and neg_recorded.get("injected_tags") == 2
      and recorded.get("full_text") == p.prompt
      and neg_recorded.get("full_text") == p.negative_prompt)

os.unlink(META_TXT)
os.unlink(META_NEG)

print("== before_process 快照锁定提示词（v1.4.15 方案 A）==")
# 用例 1：params.json 顶层带非空 prompt 键 → 正向注入用快照文本替代 prompt.txt 读取
with open(ns["PARAMS_PATH"], "w", encoding="utf-8") as f:
    json.dump({"prompt": "snapshot locked tags, <lora:swapA>"}, f)
_logs = []
_orig_log = ns["_log"]
ns["_log"] = _logs.append
try:
    p = FakeP()
    script.before_process(p, True, REAL_TXT, "", True, False, "")
finally:
    ns["_log"] = _orig_log
check("params.prompt 有值 → 注入用快照文本（txt 内容不参与）",
      p.prompt == "snapshot locked tags, <lora:swapA>, masterpiece, best quality")
check("快照注入日志带（快照锁定）标注",
      any("正向已注入" in line and "快照锁定" in line for line in _logs))
recorded = json.loads(p.extra_generation_params.get("fth_meta", "{}"))
check("快照注入统计照写（injected_tags / full_text 走同一管线）",
      recorded.get("injected_tags") == 2 and recorded.get("full_text") == p.prompt)

# 用例 2：params.json 无 prompt 键 → 现状读 txt
with open(ns["PARAMS_PATH"], "w", encoding="utf-8") as f:
    json.dump({"base": {"width": 832}}, f)
p = FakeP()
script.before_process(p, True, REAL_TXT, "", True, False, "")
check("params 无 prompt 键 → 回落读 txt",
      p.prompt == base + ", masterpiece, best quality")

# 用例 3：prompt 键为空白串 → 同无键，回落读 txt
with open(ns["PARAMS_PATH"], "w", encoding="utf-8") as f:
    json.dump({"prompt": "   "}, f)
p = FakeP()
script.before_process(p, True, REAL_TXT, "", True, False, "")
check("params.prompt 空白 → 回落读 txt",
      p.prompt == base + ", masterpiece, best quality")
os.unlink(ns["PARAMS_PATH"])

print("== 生成页总线：bus 读取 ==")
check("bus json：文件缺失返回 None", ns["_read_bus_json"](ns["CMD_PATH"]) is None)
with open(ns["CMD_PATH"], "w", encoding="utf-8") as f:
    f.write('{"action": "gener')  # 半截 JSON（编辑器写入瞬间）
check("bus json：损坏内容返回 None", ns["_read_bus_json"](ns["CMD_PATH"]) is None)
with open(ns["CMD_PATH"], "w", encoding="utf-8") as f:
    json.dump({"action": "generate", "page": "txt2img", "ts": 1}, f)
check("bus json：正常内容读回", ns["_read_bus_json"](ns["CMD_PATH"])["page"] == "txt2img")
os.unlink(ns["CMD_PATH"])

print("== 生成页总线：status 状态机（内容不变不重写）==")
ns["_gen_pass"] = 0
ns["_status_snapshot"] = None
ns["_write_status"]("idle")
check("status 首写 idle", os.path.isfile(ns["STATUS_PATH"]))
with open(ns["STATUS_PATH"], encoding="utf-8") as f:
    first = json.load(f)
check("status 结构（state/pass/images/error/ts/plugin/choices）",
      first["state"] == "idle" and first["pass"] == 0 and first["images"] == []
      and first["error"] is None and isinstance(first["ts"], int)
      and first["choices"] == {} and first["plugin"] == ns["PLUGIN_VERSION"])
mtime_before = os.stat(ns["STATUS_PATH"]).st_mtime_ns
ns["_write_status"]("idle")
check("内容未变化不重写", os.stat(ns["STATUS_PATH"]).st_mtime_ns == mtime_before)
ns["_gen_pass"] = 1
ns["_write_status"]("busy")
ns["_write_status"]("done", images=["a.png", "b.png"])
with open(ns["STATUS_PATH"], encoding="utf-8") as f:
    done = json.load(f)
check("busy→done 推进并携带图片清单", done["state"] == "done" and done["pass"] == 1
      and done["images"] == ["a.png", "b.png"] and done["ts"] >= first["ts"])

print("== 生成页总线：组件捕获 + apply 参数回填 ==")
check("on_after_component 回调已注册", len(_after_component_cbs) >= 1)


class FakeComp:
    """带 elem_id / 组件属性的最小替身（滑杆范围、下拉 choices 等）。"""

    def __init__(self, elem_id=None, **attrs):
        self.elem_id = elem_id
        for k, v in attrs.items():
            setattr(self, k, v)


cb = _after_component_cbs[-1]
width_comp = FakeComp(elem_id="txt2img_width", minimum=64, maximum=2048)
cb(width_comp)
cb(FakeComp(elem_id="unrelated_thing"))
check("表内组件捕获、表外忽略",
      ns["_UI_COMPONENTS"].get("txt2img_width") is width_comp
      and "unrelated_thing" not in ns["_UI_COMPONENTS"])

slider = FakeComp(minimum=64, maximum=2048)
targets = [
    ("base", "width", slider, "slider_int"),
    ("base", "seed", FakeComp(), "number"),
    ("base", "sampler_name", FakeComp(choices=["Euler a", "Euler"]), "dropdown"),
    ("base", "cfg_scale", FakeComp(minimum=1.0, maximum=30.0), "slider_float"),
    ("hires", "enable", FakeComp(), "checkbox"),
    ("hires", "denoise", FakeComp(minimum=0.0, maximum=1.0), "slider_float"),
]
handler = ns["_make_apply_handler"](targets)
with open(ns["PARAMS_PATH"], "w", encoding="utf-8") as f:
    json.dump({"base": {"width": 832, "seed": -1, "sampler_name": "Euler a", "cfg_scale": 7.5},
               "hires": {"enable": True, "denoise": 0.45}}, f)
check("apply：各类型转换回填", handler() == [
    {"__type__": "update", "value": 832},
    {"__type__": "update", "value": -1},
    {"__type__": "update", "value": "Euler a"},
    {"__type__": "update", "value": 7.5},
    {"__type__": "update", "value": True},
    {"__type__": "update", "value": 0.45},
])
with open(ns["PARAMS_PATH"], "w", encoding="utf-8") as f:
    json.dump({"base": {"width": 99999, "sampler_name": "NoSuch", "unknown": 1},
               "hires": {"denoise": None}}, f)
check("apply：越界夹取 / 非法下拉跳过 / 未注册键与 null 不覆盖", handler() == [
    {"__type__": "update", "value": 2048},
    {"__type__": "update"},
    {"__type__": "update"},
    {"__type__": "update"},
    {"__type__": "update"},
    {"__type__": "update"},
])
with open(ns["PARAMS_PATH"], "w", encoding="utf-8") as f:
    f.write("{{{broken")
check("apply：params 损坏时全部不覆盖", handler() == [{"__type__": "update"}] * 6)

# v1.4.6 回归：ADetailer 字段并入同一事件后，输出数恒为 targets+ad_fields——
# adetailer_infotext 缺失/为空时若缺段，gradio 抛 "didn't receive enough output
# values" 并把常规参数更新一并丢弃（真机冒烟实锤，apply 静默失效）
ad_a = FakeComp(value="")
handler_ad = ns["_make_apply_handler"](targets, ad_fields=[(ad_a, "Steps"), (FakeComp(), lambda p: None)])
with open(ns["PARAMS_PATH"], "w", encoding="utf-8") as f:
    json.dump({"base": {"width": 512}}, f)
outs = handler_ad()
check("apply：无 AD 文本时补齐 no-op 段（输出数 = targets + ad_fields）",
      len(outs) == 8 and outs[0] == {"__type__": "update", "value": 512}
      and outs[6:] == [{"__type__": "update"}] * 2)
with open(ns["PARAMS_PATH"], "w", encoding="utf-8") as f:
    json.dump({"base": {"width": 512},
               "adetailer_infotext": "Steps: 20"}, f)
outs = handler_ad()  # 离线无 infotext_utils → 解析兜底 parsed={} → AD 段全 no-op
check("apply：有 AD 文本时输出数仍恒为 8（解析失败兜底不缺段）",
      len(outs) == 8 and outs[0] == {"__type__": "update", "value": 512}
      and outs[6:] == [{"__type__": "update"}] * 2)
os.unlink(ns["PARAMS_PATH"])

print("== 生成页总线：apply 完成信号 applied_ts（v1.4.13，取代盲等 700ms）==")


def _now_ms():
    return int(time.time() * 1000)


def _wait_ms_past(ts):
    while _now_ms() <= ts:  # 毫秒同值边界防抖：确保后续 applied_ts 严格更大
        time.sleep(0.001)


def _read_status_file():
    with open(ns["STATUS_PATH"], encoding="utf-8") as f:
        return json.load(f)


# 初值：status 写入即带 applied_ts 字段（0 = 尚无 apply 完成；旧消费者按多余键忽略）
ns["_gen_pass"] = 0
ns["_status_snapshot"] = None
ns["_applied_ts"] = 0
ns["_status_last"] = {"state": "idle", "images": None, "error": None, "adetailer": None}
ns["_write_status"]("idle")
check("status 新增 applied_ts 字段（初值 0）", _read_status_file().get("applied_ts") == 0)

sig_handler = ns["_make_apply_handler"]([
    ("base", "width", FakeComp(minimum=64, maximum=2048), "slider_int")])
with open(ns["PARAMS_PATH"], "w", encoding="utf-8") as f:
    json.dump({"base": {"width": 640}}, f)
cmd_ts = _now_ms()
_wait_ms_past(cmd_ts)
outs = sig_handler()
check("apply 完成即写 applied_ts（毫秒时间戳 > cmd.ts，JS 判据同式）",
      outs == [{"__type__": "update", "value": 640}]
      and _read_status_file()["applied_ts"] > cmd_ts)

# 状态机字段保持：done + 图片清单在信号刷新时原样保留（不干扰编辑器轮询语义）
ns["_write_status"]("done", images=["a.png"])
before = _read_status_file()
_wait_ms_past(before["applied_ts"])
sig_handler()
after = _read_status_file()
check("applied_ts 刷新不改状态机字段（state/pass/images）",
      after["state"] == "done" and after["images"] == ["a.png"]
      and after["pass"] == before["pass"] and after["applied_ts"] > before["applied_ts"])

# 粘滞携带：后续常规状态写入保留最近 applied_ts（信号不被状态刷新冲掉）
ns["_write_status"]("busy")
check("后续状态写入粘滞携带 applied_ts",
      _read_status_file()["applied_ts"] == after["applied_ts"])

# params 缺失：事件完成仍发信号（no-op apply 也是完成）
os.unlink(ns["PARAMS_PATH"])
ns["_write_status"]("idle")
idle_applied = _read_status_file()["applied_ts"]
_wait_ms_past(idle_applied)
sig_handler()
check("params 缺失：事件完成仍发信号", _read_status_file()["applied_ts"] > idle_applied)

# 未启用态不发信号（handler 早退；此时 JS 也根本不轮询 cmd）
os.remove(ns["ARMED_PATH"])
armed_off_applied = _read_status_file()["applied_ts"]
_wait_ms_past(armed_off_applied)
sig_handler()
check("未启用态不发信号", _read_status_file()["applied_ts"] == armed_off_applied)
open(ns["ARMED_PATH"], "w").close()

print("== 生成页总线：postprocess 回传 + ADetailer 内部 pass 防御 ==")
os.makedirs(ns["FEETAG_OUT_DIR"], exist_ok=True)


class FakeImage:
    def __init__(self):
        self.save_paths = []
        self.save_kwargs = []

    def save(self, path, **kwargs):
        self.save_paths.append(path)
        self.save_kwargs.append(kwargs)
        with open(path, "wb") as f:  # 真实落盘，postprocess 才能通过 listdir 断言
            f.write(b"png")


before_pass = ns["_gen_pass"]
inner_p = FakeP()
inner_p._ad_inner = True
script.before_process(inner_p, True, REAL_TXT, "", True, False, "")
check("before_process：内部 pass 直接 return（不注入不计数）",
      inner_p.prompt == "masterpiece, best quality" and ns["_gen_pass"] == before_pass)
script.postprocess(inner_p, FakeProcessed([]))
check("postprocess：内部 pass 不回传", ns["_gen_pass"] == before_pass
      and not os.listdir(ns["FEETAG_OUT_DIR"]))

# v1.4.8：ADetailer 对 copy(外层 p) 的显式重调（真机实证 !adetailer.py L909/L926）——
# copy 只继承外层 p（无 _ad_inner），首轮注入打上的 _feetag_pass 随浅拷贝继承，借此识别
legit_p = FakeP()
script.before_process(legit_p, True, REAL_TXT, "", True, False, "")  # 合法首轮：注入+计数
pass_first = ns["_gen_pass"]
legit_prompt = legit_p.prompt
check("before_process：首轮注入计数照常", ns["_gen_pass"] == pass_first and "1girl" in legit_prompt)

stray_copy = FakeP()
stray_copy.prompt = legit_prompt
stray_copy._feetag_pass = True  # copy.copy(外层 p) 的等价态：属性原样继承
script.before_process(stray_copy, True, REAL_TXT, "", True, False, "")
check("before_process：copy(p) 重调幂等跳过（不二次注入不计数）",
      stray_copy.prompt == legit_prompt and ns["_gen_pass"] == pass_first)

script.before_process(legit_p, True, REAL_TXT, "", True, False, "")
check("before_process：同一 p 重复触发不叠加注入",
      legit_p.prompt == legit_prompt and ns["_gen_pass"] == pass_first)

before_status = json.load(open(ns["STATUS_PATH"], encoding="utf-8"))
before_img_count = len(os.listdir(ns["FEETAG_OUT_DIR"]))
script.postprocess(stray_copy, FakeProcessed([], info=""))  # ADetailer 空壳：Processed(p, [], seed, "")
check("postprocess：空壳 Processed 重调不写状态不落图",
      json.load(open(ns["STATUS_PATH"], encoding="utf-8")) == before_status
      and len(os.listdir(ns["FEETAG_OUT_DIR"])) == before_img_count)
before_pass = ns["_gen_pass"]  # 重置基准（上方首轮用例已 +1）

gen_p = FakeP()
script.before_process(gen_p, True, REAL_TXT, "", True, False, "")
check("before_process：正常生成置 busy 且计数 +1", ns["_gen_pass"] == before_pass + 1
      and json.load(open(ns["STATUS_PATH"], encoding="utf-8"))["state"] == "busy")

imgs = [FakeImage(), FakeImage(), FakeImage()]
script.postprocess(gen_p, FakeProcessed(imgs))
saved = sorted(os.listdir(ns["FEETAG_OUT_DIR"]))
check("postprocess：全部落盘 fth_ 时间戳命名", len(saved) == 3
      and all(name.startswith("fth_") and name.endswith(".png") for name in saved)
      and [img.save_paths[0] for img in imgs] == [os.path.join(ns["FEETAG_OUT_DIR"], n) for n in saved])
check("postprocess：生成信息以 pnginfo 传递给 save",
      all(img.save_kwargs and img.save_kwargs[0].get("pnginfo") is not None for img in imgs))
with open(ns["STATUS_PATH"], encoding="utf-8") as f:
    final = json.load(f)
check("postprocess：状态 done + 绝对路径清单", final["state"] == "done"
      and final["images"] == [os.path.join(ns["FEETAG_OUT_DIR"], n) for n in saved]
      and final["pass"] == before_pass + 1)
img_noinfo = FakeImage()
script.postprocess(gen_p, FakeProcessed([img_noinfo], info=None))
check("postprocess：无生成信息时退回裸 save（不传 pnginfo）",
      img_noinfo.save_kwargs == [{}])

print("== 总线总开关 bus.armed（v1.4.3 默认关）==")
os.remove(ns["ARMED_PATH"])  # 拆除开关 → 总线全关
before_pass = ns["_gen_pass"]
before_status_mtime = os.stat(ns["STATUS_PATH"]).st_mtime_ns
before_img_count = len(os.listdir(ns["FEETAG_OUT_DIR"]))
p_dis = FakeP()
script.before_process(p_dis, True, REAL_TXT, "", True, False, "")
check("开关关：注入照常工作", p_dis.prompt.startswith(base + ", masterpiece"))
check("开关关：不写状态不计数", ns["_gen_pass"] == before_pass
      and os.stat(ns["STATUS_PATH"]).st_mtime_ns == before_status_mtime)
script.postprocess(p_dis, FakeProcessed([FakeImage()]))
check("开关关：不回传不落图", len(os.listdir(ns["FEETAG_OUT_DIR"])) == before_img_count
      and os.stat(ns["STATUS_PATH"]).st_mtime_ns == before_status_mtime)
targets_off = [("base", "width", FakeComp(minimum=64, maximum=2048), "slider_int")]
handler_off = ns["_make_apply_handler"](targets_off)
check("开关关：apply 全部 no-op", handler_off() == [{"__type__": "update"}])
open(ns["ARMED_PATH"], "w").close()  # 重新放回开关 → 即刻生效（无需重启语义）
ns["_gen_pass"] = before_pass + 1
ns["_write_status"]("idle")
check("开关开：重新放置即恢复", json.load(open(ns["STATUS_PATH"], encoding="utf-8"))["pass"] == before_pass + 1)
check("bus_armed 现查", ns["bus_armed"]() is True)

print("== 生成页总线：cmd 原子消费端点（v1.4.6 多消费者竞态修复）==")
check("bus json：文件缺失返回 None", ns["_read_bus_json"](ns["CMD_PATH"]) is None)


class FakeApp:
    """捕获 add_api_route 注册的端点处理器，供离线直调（v1.4.18 起 GET/POST
    可同路径并存，键带 method 前缀）。"""

    def __init__(self):
        self.routes = {}

    def add_api_route(self, path, endpoint, methods=None, include_in_schema=False):
        self.routes[f"{(methods or ['GET'])[0]} {path}"] = endpoint


fake_app = FakeApp()
ns["_register_bus_endpoints"](fake_app)
check("总线端点注册（status/image/cmd/progress/page-state GET+POST 共六条）",
      set(fake_app.routes) == {"GET /feetag/bus/status", "GET /feetag/bus/image",
                               "GET /feetag/bus/cmd", "GET /feetag/bus/progress",
                               "GET /feetag/bus/page-state",
                               "POST /feetag/bus/page-state"})

# progress 端点（v1.4.14）：armed 门控 404；转发返回 JSON + CORS 头；异常回 null JSON 不 5xx
class _FakeReq:
    def __init__(self, host): self.headers = {"host": host}
class _FakeUrlopen:
    def __init__(self, payload): self._p = payload
    def read(self): return self._p
    def __enter__(self): return self
    def __exit__(self, *a): return False
import urllib.request as _urlreq
bus_progress = fake_app.routes["GET /feetag/bus/progress"]
import types
_orig_remove = os.path.isfile
os.path.isfile = lambda p: True   # 模拟 bus.armed 在位（沿测试既有桩法）
try:
    _orig_urlopen = _urlreq.urlopen
    _urlreq.urlopen = lambda url, timeout=None: _FakeUrlopen(b'{"progress": 0.42, "eta": 8}')
    resp = bus_progress(_FakeReq("127.0.0.1:7860"))
    check("progress 端点：转发 JSON+CORS",
          resp.status_code == 200 and json.loads(resp.body)["progress"] == 0.42
          and resp.headers.get("Access-Control-Allow-Origin") == "*")
    _urlreq.urlopen = lambda url, timeout=None: (_ for _ in ()).throw(RuntimeError("down"))
    resp = bus_progress(_FakeReq("127.0.0.1:7860"))
    check("progress 端点：后端异常回 null JSON",
          resp.status_code == 200 and json.loads(resp.body)["progress"] is None)
finally:
    _urlreq.urlopen = _orig_urlopen
    os.path.isfile = _orig_remove
bus_cmd = fake_app.routes["GET /feetag/bus/cmd"]

CMD = {"action": "generate", "page": "txt2img", "ts": 1788840420000}

# 单消费者语义：取到 → 文件消失 → 再取 404
with open(ns["CMD_PATH"], "w", encoding="utf-8") as f:
    json.dump(CMD, f)
resp = bus_cmd()
check("cmd 端点：首次取到命令内容", resp.status_code == 200 and json.loads(resp.body) == CMD)
check("cmd 端点：消费后文件已删除", not os.path.isfile(ns["CMD_PATH"]))
check("cmd 端点：空槽返回 404", bus_cmd().status_code == 404)
check("cmd 端点：无临时文件残留",
      not [n for n in os.listdir(TMP_DIR) if ".consuming-" in n])

# 双消费者竞态：两轮询方（两线程，经 Barrier 同时发起）抢同一条 cmd——
# 恰一方 200 取到，另一方 404/None；修复前 /file= 直读会双方都取到
with open(ns["CMD_PATH"], "w", encoding="utf-8") as f:
    json.dump(CMD, f)
results = []
barrier = threading.Barrier(2)


def _grab():
    barrier.wait()
    results.append(ns["_consume_cmd"]())


grabbers = [threading.Thread(target=_grab) for _ in range(2)]
for t in grabbers:
    t.start()
for t in grabbers:
    t.join()
got = [r for r in results if isinstance(r, dict)]
check("竞态：两消费者恰一方取到同一条命令", len(got) == 1 and got[0] == CMD
      and not os.path.isfile(ns["CMD_PATH"]))

# 未启用态（bus.armed 拆除）：端点一律 404 且不消费
os.remove(ns["ARMED_PATH"])
with open(ns["CMD_PATH"], "w", encoding="utf-8") as f:
    json.dump(CMD, f)
check("cmd 端点：开关关一律 404 且不消费", bus_cmd().status_code == 404
      and os.path.isfile(ns["CMD_PATH"]))
open(ns["ARMED_PATH"], "w").close()

# 半截 JSON（编辑器写入瞬间被取走）：消费丢弃，返回 404 防坏文件反复触发
with open(ns["CMD_PATH"], "w", encoding="utf-8") as f:
    f.write('{"action": "gener')
check("cmd 端点：半截 JSON 消费丢弃不触发", bus_cmd().status_code == 404
      and not os.path.isfile(ns["CMD_PATH"]))

print("== 生成页总线：USDU 脚本选中契约（v1.4.7 平铺 params.usdu.enable）==")
scriptsel_comp = FakeComp(choices=["None", "Ultimate SD upscale", "Latent"])
usdu_width = FakeComp(minimum=0, maximum=2048)
sel_handler = ns["_make_apply_handler"]([
    ("usdu", "_select", scriptsel_comp, "scriptsel"),
    ("usdu", "tile_width", usdu_width, "slider_int"),
])
with open(ns["PARAMS_PATH"], "w", encoding="utf-8") as f:
    json.dump({"usdu": {"enable": True, "tile_width": 512}}, f)
outs = sel_handler()
check("USDU：enable=true 选中脚本（index 1）+ 字段回填",
      outs[0] == {"__type__": "update", "value": 1}
      and outs[1] == {"__type__": "update", "value": 512})
with open(ns["PARAMS_PATH"], "w", encoding="utf-8") as f:
    json.dump({"usdu": {"enable": False, "tile_width": 512}}, f)
outs = sel_handler()
check("USDU：enable=false 不选中（字段照常回填）",
      outs[0] == {"__type__": "update"} and outs[1] == {"__type__": "update", "value": 512})
with open(ns["PARAMS_PATH"], "w", encoding="utf-8") as f:
    json.dump({"tiled": {"enable": True, "scale": 2}}, f)
outs = sel_handler()
check("USDU：usdu 节缺席不选中（与 tiled 平铺节互不干扰）",
      outs[0] == {"__type__": "update"} and outs[1] == {"__type__": "update"})
os.unlink(ns["PARAMS_PATH"])

print("== 生成页总线：可选扩展组分组分线（v1.4.16 盲测 P1-1）==")
# 离线 mock 无 A1111 脚本注册表（scripts_txt2img / scripts_img2img 缺席）
# → 可选组（tiled / tiledvae / usdu）判定为"未安装"，裁剪而非死等
check("离线无注册表：可选组判定为未安装（裁剪而非死等）",
      ns["_section_available"](False, "tiled") is False)


class FakeButton:
    def __init__(self):
        self.click_kwargs = None

    def click(self, *args, **kwargs):
        self.click_kwargs = kwargs


bare_btn = FakeButton()
ns["_BUS_BUTTONS"][False] = bare_btn
for section, _key, elem_id, _kind in ns["_FIELD_TABLES"][False]:
    if section in ("base", "hires"):
        cb(FakeComp(elem_id=elem_id))
base_hires_count = sum(1 for r in ns["_FIELD_TABLES"][False] if r[0] in ("base", "hires"))
check("未装扩展：base+hires 到齐即接线（不再被可选组拖死 → apply 静默失效根治）",
      ns["_WIRED"].get(False) is True and bare_btn.click_kwargs is not None)
check("裁剪后输出数 = base+hires（可选组整组剔除）",
      len(bare_btn.click_kwargs["outputs"]) == base_hires_count)
bare_handler = bare_btn.click_kwargs["fn"]
with open(ns["PARAMS_PATH"], "w", encoding="utf-8") as f:
    json.dump({"base": {"width": 768}, "tiled": {"enable": True, "scale": 2}}, f)
outs = bare_handler()
check("裁剪后 apply：base 照常回填（v1.4.7 平铺契约不变，可选组键自然忽略）",
      outs[0] == {"__type__": "update", "value": 768}
      and len(outs) == base_hires_count)
os.unlink(ns["PARAMS_PATH"])

# 扩展已安装：伪 runner 注册 title 后，组件未到齐前等待、到齐后全字段接线
class _FakeScript:
    def __init__(self, title):
        self.title = title


class _FakeRunner:
    def __init__(self, titles):
        self.scripts = [_FakeScript(t) for t in titles]


ns["_WIRED"].clear()
ns["_SECTION_STATE"].clear()
scripts_mod.scripts_txt2img = _FakeRunner(["Tiled Diffusion", "Tiled VAE"])
scripts_mod.scripts_img2img = _FakeRunner(["Tiled Diffusion", "Tiled VAE", "Ultimate SD upscale"])
try:
    check("注册表有扩展：可选组判定为在场", ns["_section_available"](False, "tiled") is True)
    full_btn = FakeButton()
    ns["_BUS_BUTTONS"][False] = full_btn
    for section, _key, elem_id, _kind in ns["_FIELD_TABLES"][False]:
        if section in ("base", "hires"):
            cb(FakeComp(elem_id=elem_id))
    check("扩展在场但组件未到：继续等待不接线（等待分支语义保持）",
          ns["_WIRED"].get(False) is None)
    for section, _key, elem_id, _kind in ns["_FIELD_TABLES"][False]:
        if section in ("tiled", "tiledvae"):
            cb(FakeComp(elem_id=elem_id))
    check("可选组组件到齐后接线（全字段，含 MD 组件）",
          ns["_WIRED"].get(False) is True
          and len(full_btn.click_kwargs["outputs"]) == len(ns["_FIELD_TABLES"][False]))
finally:
    del scripts_mod.scripts_txt2img
    del scripts_mod.scripts_img2img

print("== Reload UI 接线复位（v1.4.16 盲测 P1-2）==")
check("on_before_ui 回调已注册", len(_before_ui_cbs) >= 1)
ns["_SCRIPT_LISTS"].extend([FakeComp(elem_id="script_list"), FakeComp(elem_id="script_list")])
_before_ui_cbs[-1]()
check("Reload UI：接线/组件捕获/脚本下拉/按钮/组判定/页面控件表全部复位",
      not ns["_WIRED"] and not ns["_UI_COMPONENTS"] and not ns["_SCRIPT_LISTS"]
      and not ns["_SECTION_STATE"] and not ns["_BUS_BUTTONS"] and not ns["_TAB_CONTROLS"])
# 模拟重建：新按钮 + base/hires 组件重新捕获 → 重新接线
# （修复前 _WIRED=True 永不复位：新 apply 钮永不接线，静默死亡直到重启进程）
re_btn = FakeButton()
ns["_BUS_BUTTONS"][False] = re_btn
for section, _key, elem_id, _kind in ns["_FIELD_TABLES"][False]:
    if section in ("base", "hires"):
        cb(FakeComp(elem_id=elem_id))
check("重建后接线恢复（陈旧组件引用已清，不误接旧对象）",
      ns["_WIRED"].get(False) is True and re_btn.click_kwargs is not None)

print("== 总线看门狗（v1.4.16 盲测 P1-4：生成异常补写 error）==")
ns["_gen_pass"] = 0
ns["_status_snapshot"] = None
ns["_status_last"] = {"state": "idle", "images": None, "error": None, "adetailer": None}
ns["_write_status"]("busy")
check("busy + 判定链路不可用（离线 mock 无 shared）→ 不误判（宁可不写）",
      ns["_bus_watchdog_tick"]() is False)
modules_pkg.shared = types.SimpleNamespace(state=types.SimpleNamespace(job="Txt2Img"))
try:
    check("busy + 生成任务运行中 → 不误判（小时级慢生成不受伤，活性判定非超时）",
          ns["_bus_watchdog_tick"]() is False)
    modules_pkg.shared = types.SimpleNamespace(state=types.SimpleNamespace(job=""))
    check("busy + 无运行中任务 → 判定卡死", ns["_bus_watchdog_tick"]() is True)
    check("首轮判定只计数不写 error（宽限一轮防瞬态）",
          ns["_bus_watchdog_step"](0) == 1
          and json.load(open(ns["STATUS_PATH"], encoding="utf-8"))["state"] == "busy")
    check("连续第二次判定达阈值 → 写 error 并归零",
          ns["_bus_watchdog_step"](1) == 0
          and json.load(open(ns["STATUS_PATH"], encoding="utf-8"))["state"] == "error")
finally:
    del modules_pkg.shared
check("error 态不再触发看门狗（不重复写）", ns["_bus_watchdog_tick"]() is False)

print("== 原子写：config / status 半截读根治（v1.4.16 盲测 P1 ④）==")
ns["_save_config"]({"enabled": True, "path": "P", "negative_path": "",
                    "merge_lines": True, "autostart": False, "editor_path": ""})
check("save_config 原子写：内容完整且无 .tmp- 残留",
      json.load(open(ns["CONFIG_PATH"], encoding="utf-8"))["path"] == "P"
      and not [n for n in os.listdir(TMP_DIR) if ".tmp-" in n])


def _boom_replace(src, dst):
    raise OSError("replace failed (simulated)")


_orig_replace = os.replace
os.replace = _boom_replace
try:
    ns["_save_config"]({"enabled": False, "path": "Q", "negative_path": "",
                        "merge_lines": True, "autostart": False, "editor_path": ""})
    ns["_write_status"]("done", images=["x.png"])
finally:
    os.replace = _orig_replace
check("replace 失败：旧 config 完整保留（不再有截断窗口）",
      json.load(open(ns["CONFIG_PATH"], encoding="utf-8"))["path"] == "P")
check("replace 失败：status 保持旧值 + 临时文件已清理",
      json.load(open(ns["STATUS_PATH"], encoding="utf-8"))["state"] == "error"
      and not [n for n in os.listdir(TMP_DIR) if ".tmp-" in n])

# 并发写：双线程交替全量保存 + 并发读者。读者语义与 status 端点一致——
# PermissionError 是原子替换窗口的短暂句柄拒绝（重读即愈，非半截），
# ValueError（半截 JSON）或持续拒绝才是真失败
_read_errors = []
_stop = {"flag": False}


def _writer(tag):
    for i in range(40):
        ns["_save_config"]({"enabled": True, "path": f"{tag}{i}", "negative_path": "",
                            "merge_lines": True, "autostart": False, "editor_path": ""})


def _read_full(path, tries=6):
    for attempt in range(tries):
        try:
            with open(path, encoding="utf-8") as f:
                json.load(f)
            return None
        except PermissionError:
            time.sleep(0.005)
        except (OSError, ValueError) as e:
            return f"{type(e).__name__}: {e}"
    return "PermissionError 持续超过重试上限"


def _concurrent_reader():
    while not _stop["flag"]:
        err = _read_full(ns["CONFIG_PATH"])
        if err:
            _read_errors.append(err)
        time.sleep(0.001)


writers = [threading.Thread(target=_writer, args=(t,)) for t in ("A", "B")]
reader_t = threading.Thread(target=_concurrent_reader)
for t in writers:
    t.start()
reader_t.start()
for t in writers:
    t.join(10)
_stop["flag"] = True
reader_t.join(5)
check("并发写 config：读者全程零半截（写锁串行 + 原子替换，短暂拒绝经重读自愈）",
      not _read_errors and not [n for n in os.listdir(TMP_DIR) if ".tmp-" in n])

# settings.pin / 旧独立 pin 同一原子通道
ns["_persist_pin_key"]("editor_path", r"E:\pin\editor.exe")
check("settings.pin 原子写往返 + 无残留",
      ns["_read_pin_overrides"]().get("editor_path") == r"E:\pin\editor.exe"
      and not [n for n in os.listdir(TMP_DIR) if ".tmp-" in n])
ns["_persist_pin_key"]("editor_path", "")
check("settings.pin 解除固定后 overrides 复位",
      "editor_path" not in ns["_read_pin_overrides"]())

print("== 连接引导：插件目录展示（v1.4.17 方案 B 配套，仅 WebUI）==")
disp = ns["_plugin_dir_display"]()
check("插件目录展示：值为插件安装目录（EXT_DIR 同源计算，扩展根而非 scripts/ 层）",
      os.path.basename(ns["EXT_DIR"]) == "sd-webui-prompt-helper"
      and ns["EXT_DIR"] in disp)
check("插件目录展示：data-dir 属性在位（JS 复制主通道）", 'data-dir="' in disp)
check("插件目录展示：HTML 转义安全（escape 后整段在位，无裸标签注入）",
      html.escape(ns["EXT_DIR"]) in disp and "<script" not in disp)

print("== 连接引导：armed 总线开关（v1.4.17，bus.armed 同文件读写往返）==")
check("开关写：set_bus_armed(True) 放置 bus.armed",
      ns["set_bus_armed"](True)[0] and os.path.isfile(ns["ARMED_PATH"])
      and ns["bus_armed"]() is True)
check("开关删：set_bus_armed(False) 移除且幂等（文件不在也不抛）",
      ns["set_bus_armed"](False)[0] and not os.path.isfile(ns["ARMED_PATH"])
      and ns["set_bus_armed"](False)[0])
fb = ns["_armed_toggle"](True)
check("开关回执：启用文案 + 文件在位", "已启用" in fb and os.path.isfile(ns["ARMED_PATH"]))
fb = ns["_armed_toggle"](False)
check("开关回执：关闭文案 + 文件已删", "已关闭" in fb and not os.path.isfile(ns["ARMED_PATH"]))
# 写失败路径：原子替换被拦 → 回执失败、标志文件不被误置
os.replace = _boom_replace  # 复用原子写用例的拦截
try:
    fb = ns["_armed_toggle"](True)
finally:
    os.replace = _orig_replace
check("开关写失败：回执失败文案、bus.armed 不被误置",
      "✗" in fb and not os.path.isfile(ns["ARMED_PATH"]))
check("常驻提示含安全语义（允许编辑器替你点生成按钮）",
      "替你点击生成按钮" in ns["_armed_hint_html"]())

print("== 连接引导：生成页面板锚点挂载（v1.4.17 终稿：on_after_component 在锚点上下文建件）==")
ns["_BUS_PANEL"].clear()
ns["_BUS_PANEL_BUILT"] = False
cb(FakeComp(elem_id="txt2img_gallery"))  # 锚点组件出现 → 面板应挂载
check("锚点触发：面板组件建成（armed/direct 开关+状态行+目录展示+复制按钮）",
      set(ns["_BUS_PANEL"]) == {"armed_toggle", "armed_status", "direct_toggle",
                                "direct_status", "plugin_dir", "copy_dir_button"})
panel_toggle = ns["_BUS_PANEL"]["armed_toggle"]
fns = [c.get("fn") for c in panel_toggle._change_calls]
check("面板 armed 开关挂文件写入处理器", ns["_armed_toggle"] in fns)
check("面板 direct 开关挂文件写入处理器",
      ns["_direct_toggle"] in [c.get("fn") for c in ns["_BUS_PANEL"]["direct_toggle"]._change_calls])
cb(FakeComp(elem_id="txt2img_gallery"))  # 锚点重复出现（面板自身组件等）不重复建
check("面板构建幂等（防重入，不重复建件）",
      ns["_BUS_PANEL"]["armed_toggle"] is panel_toggle
      and len(panel_toggle._change_calls) == len(fns))
# Reload UI 复位后面板随锚点重建（_on_before_ui 清 _BUS_PANEL/_BUS_PANEL_BUILT）
_before_ui_cbs[-1]()
check("Reload UI：面板状态复位（随锚点重建）",
      not ns["_BUS_PANEL"] and ns["_BUS_PANEL_BUILT"] is False)
cb(FakeComp(elem_id="txt2img_gallery"))
check("重建后面板恢复", set(ns["_BUS_PANEL"]) == {"armed_toggle", "armed_status",
                                                  "direct_toggle", "direct_status",
                                                  "plugin_dir", "copy_dir_button"})

print("== Wave B：bus.direct 契约 + 直发开关（v1.4.18）==")
check("bus.direct 契约：写=直发模式（存在性实时判定）",
      ns["set_bus_direct"](True)[0] and ns["bus_direct"]() is True
      and os.path.isfile(ns["DIRECT_PATH"]))
fb = ns["_direct_toggle"](False)  # 取消勾选「使用网页端插件」= 写 bus.direct
check("直发开关：取消勾选写 bus.direct", os.path.isfile(ns["DIRECT_PATH"])
      and "直发" in fb)
fb = ns["_direct_toggle"](True)   # 勾选 = 页面链路 = 删文件
check("直发开关：勾选删 bus.direct（回页面链路）",
      not os.path.isfile(ns["DIRECT_PATH"]) and "页面链路" in fb)
check("直发提示含边界语义（纯参数出图、页面插件不参与）",
      "不经页面" in ns["_direct_hint_html"]() and "不参与" in ns["_direct_hint_html"]())

# 消费互斥：直发模式下 cmd 端点 404 且不消费（命令留给服务端消费线程）
ns["set_bus_armed"](True)  # 前文开关用例拆过 armed，此处确保总线在位再验互斥
ns["set_bus_direct"](True)
with open(ns["CMD_PATH"], "w", encoding="utf-8") as f:
    json.dump(CMD, f)
check("直发互斥：cmd 端点 404 且不消费（文件原样留给服务端线程）",
      bus_cmd().status_code == 404 and os.path.isfile(ns["CMD_PATH"]))
ns["set_bus_direct"](False)
resp = bus_cmd()
check("页面模式恢复：端点正常原子消费（同一命令）",
      resp.status_code == 200 and json.loads(resp.body) == CMD
      and not os.path.isfile(ns["CMD_PATH"]))

print("== Wave B：直发 payload 映射（纯函数，页面插件键不映射）==")
payload = ns["_direct_payload"]({
    "base": {"width": 832, "height": 1216, "seed": -1, "steps": 24,
             "sampler_name": "Euler a", "scheduler": "Karras", "cfg_scale": 7.5,
             "batch_size": 1, "n_iter": 2},
    "hires": {"enable": True, "upscaler": "R-ESRGAN 4x+ Anime6B",
              "hr_scale": 1.5, "steps": 10, "denoise": 0.4},
    "tiled": {"enable": True, "scale": 2}, "usdu": {"enable": True},
    "adetailer_infotext": "Steps: 20, ADetailer: face"})
check("直发映射：base 键直传", payload["width"] == 832 and payload["seed"] == -1
      and payload["sampler_name"] == "Euler a" and payload["n_iter"] == 2)
check("直发映射：hires 四键换 API 名",
      payload["enable_hr"] is True
      and payload["hr_upscaler"] == "R-ESRGAN 4x+ Anime6B"
      and payload["hr_scale"] == 1.5
      and payload["hr_second_pass_steps"] == 10
      and payload["denoising_strength"] == 0.4)
check("直发映射：页面插件键一律不映射（tiled/usdu/adetailer）",
      not any(str(k).startswith(("tiled", "usdu", "adetailer")) for k in payload))
check("直发映射：prompt 恒空串（注入留给 before_process 钩子）",
      payload["prompt"] == "" and payload["negative_prompt"] == "")
p2 = ns["_direct_payload"]({"base": {"width": 512}, "hires": {"enable": False, "denoise": 0.5}})
check("直发映射：hires 关闭不带 hr 键 / 缺键与 null 不进 payload",
      p2 == {"prompt": "", "negative_prompt": "", "width": 512})

# img2img 命令：直发明确报 error（HTTP 之前返回，离线可测）
ns["_gen_pass"] = 0
ns["_status_snapshot"] = None
ns["_status_last"] = {"state": "idle", "images": None, "error": None, "adetailer": None}
ns["_write_status"]("idle")
ns["_direct_generate"]({"action": "generate", "page": "img2img"})
err_status = json.load(open(ns["STATUS_PATH"], encoding="utf-8"))
check("直发：img2img 命令明确 error（需要源图，直发只做纯参数 txt2img）",
      err_status["state"] == "error" and "img2img" in (err_status["error"] or ""))

print("== Wave B：看门狗直发护栏 ==")
ns["_write_status"]("busy")
modules_pkg.shared = types.SimpleNamespace(state=types.SimpleNamespace(job=""))
try:
    check("护栏前基线：busy + 无运行任务仍判定卡死", ns["_bus_watchdog_tick"]() is True)
    ns["_direct_running"] = True
    check("看门狗护栏：直发生成在途不误杀（API 路径 state 独立线程更新）",
          ns["_bus_watchdog_tick"]() is False)
finally:
    ns["_direct_running"] = False
    del modules_pkg.shared

print("== v1.4.19：直发一轮 = 1 图 1 计数（X-174 双落盘双计根除）==")
# 场景重放：直发线程 _direct_generate 调 API 期间，API 处理线程同步跑脚本钩子
# （before_process 注入/计数/busy → postprocess 落盘/done，均在 HTTP 响应返回
# 之前完成）——mock urlopen 在吐响应体前执行钩子，忠实复刻 /sdapi/v1/txt2img
# 服务端管线。旧版（直发线程自落盘自计数）在此场景恰产出 2 图 / pass 2。
os.makedirs(ns["FEETAG_OUT_DIR"], exist_ok=True)
for _name in os.listdir(ns["FEETAG_OUT_DIR"]):
    os.unlink(os.path.join(ns["FEETAG_OUT_DIR"], _name))
ns["_gen_pass"] = 0
ns["_status_snapshot"] = None
ns["_status_last"] = {"state": "idle", "images": None, "error": None, "adetailer": None}
ns["_write_status"]("idle")
modules_pkg.shared = types.SimpleNamespace(
    cmd_opts=types.SimpleNamespace(port=7860, subpath=""))
with open(ns["PARAMS_PATH"], "w", encoding="utf-8") as f:
    json.dump({"base": {"width": 512, "steps": 5}}, f)
_direct_api_state = {}


def _fake_api_pipeline():
    """模拟 API 处理线程：真实 WebUI 里这两步在响应返回前于服务端跑完。"""
    api_p = FakeP()
    script.before_process(api_p, True, REAL_TXT, "", True, False, "")  # 注入+计数+busy
    _direct_api_state["p"] = api_p
    script.postprocess(api_p, FakeProcessed([FakeImage()], info="Steps: 5, Seed: 965732128"))


class _DirectResp:
    def __init__(self, payload):
        self._payload = payload

    def read(self):
        _fake_api_pipeline()  # 服务端管线先于响应体返回
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


ns["set_bus_direct"](True)
# 真实可解码的 1x1 PNG：旧版直发线程会用 PIL 解码后自落一份盘——字节合法
# 才能让「双落盘」断言对旧代码也有判别力（旧版在此场景 = 2 图 / pass 2）
_TINY_PNG_B64 = ("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4"
                 "z8AAAAMBAQDJ/pLvAAAAAElFTkSuQmCC")
_resp_body = json.dumps({"images": [_TINY_PNG_B64],
                         "info": json.dumps({"infotexts": ["Steps: 5"]})}).encode("utf-8")
_orig_urlopen_direct = _urlreq.urlopen
_urlreq.urlopen = lambda url, timeout=None: _DirectResp(_resp_body)
try:
    ns["_direct_generate"]({"action": "generate", "page": "txt2img"})
finally:
    _urlreq.urlopen = _orig_urlopen_direct
    del modules_pkg.shared
_files = os.listdir(ns["FEETAG_OUT_DIR"])
_st = _read_status_file()
check("直发一轮：恰 1 图落 featag_out（钩子唯一落盘，直发线程不再双写）",
      len(_files) == 1)
check("直发一轮：pass 恰 +1（before_process 唯一计数，直发线程不再双计）",
      ns["_gen_pass"] == 1 and _st["pass"] == 1)
check("直发一轮：done 且 images 指向唯一落盘图", _st["state"] == "done"
      and [os.path.basename(p) for p in _st["images"]] == _files)
check("直发一轮：API 线程注入照常（直发 prompt 恒空，注入链是唯一来源）",
      "1girl" in _direct_api_state["p"].prompt)
check("直发收尾：_direct_running 复位", ns["_direct_running"] is False)

# 失败路径：API 不可达（钩子不运行）→ 直发线程兜底写 error、不落图不计数
ns["_gen_pass"] = 0
ns["_status_snapshot"] = None
ns["_status_last"] = {"state": "idle", "images": None, "error": None, "adetailer": None}
ns["_write_status"]("idle")
modules_pkg.shared = types.SimpleNamespace(
    cmd_opts=types.SimpleNamespace(port=7860, subpath=""))
_urlreq.urlopen = lambda url, timeout=None: (_ for _ in ()).throw(RuntimeError("api down"))
try:
    ns["_direct_generate"]({"action": "generate", "page": "txt2img"})
finally:
    _urlreq.urlopen = _orig_urlopen_direct
    del modules_pkg.shared
_st = _read_status_file()
check("直发失败：error 写出（钩子未运行，直发线程兜底）",
      _st["state"] == "error" and "api down" in (_st["error"] or ""))
check("直发失败：不落新图不计数（before_process 未运行）",
      ns["_gen_pass"] == 0 and len(os.listdir(ns["FEETAG_OUT_DIR"])) == 1)

print("== v1.4.19：页面链路回归 = 1 图 1 计数（行为零变化防改窜）==")
for _name in os.listdir(ns["FEETAG_OUT_DIR"]):
    os.unlink(os.path.join(ns["FEETAG_OUT_DIR"], _name))
ns["_gen_pass"] = 0
ns["_status_snapshot"] = None
ns["_status_last"] = {"state": "idle", "images": None, "error": None, "adetailer": None}
ns["_write_status"]("idle")
ns["set_bus_direct"](False)
_page_p = FakeP()
script.before_process(_page_p, True, REAL_TXT, "", True, False, "")
script.postprocess(_page_p, FakeProcessed([FakeImage()], info="Steps: 24"))
_files = os.listdir(ns["FEETAG_OUT_DIR"])
_st = _read_status_file()
check("页面链路一轮：1 图 1 计数 done（与直发共用同一份钩子，语义不变）",
      len(_files) == 1 and ns["_gen_pass"] == 1 and _st["pass"] == 1
      and _st["state"] == "done")

print("== Wave B：页面状态快照/恢复（bus.page_state.json）==")
doc = ns["_page_state_document"]()
check("快照文档：默认开 + 已接线字段表 elem_id 清单（含可选组）+ 无存档时 state=None",
      doc["enabled"] is True and doc["state"] is None
      and doc["fields"]["txt2img"] == [r[2] for r in ns["_FIELD_TABLES"][False]]
      and doc["fields"]["img2img"] == [r[2] for r in ns["_FIELD_TABLES"][True]])


class _FakeJSONReq:
    def __init__(self, body):
        self._body = body

    async def json(self):
        return self._body


import asyncio
page_state_save = fake_app.routes["POST /feetag/bus/page-state"]
resp204 = asyncio.run(page_state_save(_FakeJSONReq({"txt2img_width": {"text": "832"},
                                                    "txt2img_cfg_scale": {"text": "7.5"}})))
check("快照存档：POST 204 + 原子落盘后 GET 文档带回",
      resp204.status_code == 204
      and ns["_page_state_document"]()["state"] == {"txt2img_width": {"text": "832"},
                                                    "txt2img_cfg_scale": {"text": "7.5"}})
check("快照存档：非对象 body 拒绝 400",
      asyncio.run(page_state_save(_FakeJSONReq([1, 2]))).status_code == 400)
open(ns["PAGE_STATE_OFF_PATH"], "w").close()
check("快照功能关：bus.page_state.disabled 在位 → enabled False + POST 403",
      ns["_page_state_document"]()["enabled"] is False
      and asyncio.run(page_state_save(_FakeJSONReq({"x": 1}))).status_code == 403)
os.remove(ns["PAGE_STATE_OFF_PATH"])
check("拆 disabled 文件即恢复（默认开）", ns["page_state_enabled"]() is True)
if os.path.isfile(ns["PAGE_STATE_PATH"]):
    os.unlink(ns["PAGE_STATE_PATH"])
ns["set_bus_direct"](False)  # 收尾：回页面链路模式，不污染后续用例

print("== 编辑器联动启动 ==")
check("on_app_started 回调已注册", len(_registered_callbacks) >= 1)
ok, msg = ns["launch_editor"]("")
check("空路径返回错误", not ok and "未设置" in msg)
ok, msg = ns["launch_editor"](r"C:\__no_editor__.exe")
check("无效路径返回错误", not ok and "不存在" in msg)
check("进程检测：不存在的进程", ns["_is_process_running"]("definitely_not_running_zzz.exe") is False)
with tempfile.NamedTemporaryFile("w", suffix=".bat", delete=False) as f:
    f.write("@exit 0")
    bat_path = f.name
ok, msg = ns["launch_editor"](bat_path)
print(f"  独立进程启动 -> [{msg}]")
check("独立进程启动成功", ok)
time.sleep(0.3)
try:
    os.unlink(bat_path)
except OSError:
    pass
ns["_on_app_started"]()  # 临时配置 autostart 默认关闭，应直接返回
check("autostart 关闭时启动回调无动作", True)

print("== v1.4.19：launch_editor 脱离进程树（树杀免疫，X-174 环境坑ⓐ）==")
# 拦截 Popen 捕获 argv/creationflags（不真拉起）；_is_process_running 恒 False
# 强制走启动分支。真实启动已由上方 .bat 用例覆盖。
import subprocess as _subproc
_launch_calls = []


class _FakePopenResult:
    returncode = 0


def _capture_popen(argv, **kwargs):
    _launch_calls.append((list(argv), kwargs))
    return _FakePopenResult()


_real_popen = _subproc.Popen
_real_proc_check2 = ns["_is_process_running"]
ns["_is_process_running"] = lambda exe_name: False
_subproc.Popen = _capture_popen
try:
    with tempfile.NamedTemporaryFile(suffix=".exe", delete=False) as f:
        f.write(b"MZ")
        _exe = f.name
    ok, msg = ns["launch_editor"](_exe)
finally:
    _subproc.Popen = _real_popen
    ns["_is_process_running"] = _real_proc_check2
    os.unlink(_exe)
check("启动成功回执", ok and msg.startswith("已启动"))
if os.name == "nt":
    _base_flags = (_subproc.DETACHED_PROCESS
                   | _subproc.CREATE_NEW_PROCESS_GROUP)
    _argv, _kw = _launch_calls[0]
    check("Windows 启动：cmd /c start 中转（编辑器挂 cmd 名下、cmd 即退，"
          "taskkill /T 快照父子链断开）",
          _argv[:4] == ["cmd", "/c", "start", ""] and _argv[4] == _exe
          and _kw.get("cwd") == os.path.dirname(_exe))
    check("Windows 启动：脱离旗标 + breakaway 附带（Job 脱出）",
          _kw.get("creationflags")
          == _base_flags | getattr(_subproc, "CREATE_BREAKAWAY_FROM_JOB", 0))

    # Job 拒绝 breakaway（CreateProcess 报错）→ cmd 中转裸旗标重试仍成功
    _launch_calls.clear()

    def _reject_breakaway(argv, **kwargs):
        _launch_calls.append((list(argv), kwargs))
        if (kwargs.get("creationflags") or 0) & getattr(
                _subproc, "CREATE_BREAKAWAY_FROM_JOB", 0):
            raise OSError(5, "Access is denied")
        return _FakePopenResult()

    ns["_is_process_running"] = lambda exe_name: False
    _subproc.Popen = _reject_breakaway
    try:
        with tempfile.NamedTemporaryFile(suffix=".exe", delete=False) as f:
            f.write(b"MZ")
            _exe = f.name
        ok, msg = ns["launch_editor"](_exe)
    finally:
        _subproc.Popen = _real_popen
        ns["_is_process_running"] = _real_proc_check2
        os.unlink(_exe)
    check("breakaway 被拒：cmd 中转裸旗标重试成功（保住父子链断开）",
          ok and _launch_calls[-1][0][:4] == ["cmd", "/c", "start", ""]
          and _launch_calls[-1][1].get("creationflags") == _base_flags)

    # cmd 中转彻底不可用（策略禁/镜像缺失）→ 回退直启（保底与旧版一致）
    _launch_calls.clear()

    def _block_cmd(argv, **kwargs):
        _launch_calls.append((list(argv), kwargs))
        if argv and argv[0] == "cmd":
            raise OSError("cmd blocked")
        return _FakePopenResult()

    ns["_is_process_running"] = lambda exe_name: False
    _subproc.Popen = _block_cmd
    try:
        with tempfile.NamedTemporaryFile(suffix=".exe", delete=False) as f:
            f.write(b"MZ")
            _exe = f.name
        ok, msg = ns["launch_editor"](_exe)
    finally:
        _subproc.Popen = _real_popen
        ns["_is_process_running"] = _real_proc_check2
        os.unlink(_exe)
    check("cmd 中转不可用：回退直启（argv=[exe]、脱离旗标保留）",
          ok and _launch_calls[-1][0] == [_exe]
          and _launch_calls[-1][1].get("creationflags") == _base_flags
          and len(_launch_calls) == 3)  # cmd+breakaway → cmd 裸旗标 → 直启

# 熔断（v1.4.19 追加）：空 / 畸形路径不触发任何 shell 调用——Popen 零调用
_launch_calls.clear()
ns["_is_process_running"] = lambda exe_name: False
_subproc.Popen = _capture_popen
try:
    _malformed = ["\\", "\\\\", "/", ".", "..", "   ", '""']
    _results = [ns["launch_editor"](bad) for bad in _malformed]
finally:
    _subproc.Popen = _real_popen
    ns["_is_process_running"] = _real_proc_check2
check("熔断：空 / 畸形路径（\\ \\\\ / . .. 空白 纯引号）一律 error 回执、"
      "零 shell 调用（防系统级「找不到文件」弹窗；空白/纯引号在解析层"
      "归空走「未设置」分支，同为 error）",
      all(not ok and ("编辑器路径无效" in msg or "未设置" in msg)
          for ok, msg in _results)
      and not _launch_calls)

print("== editor_path 自动探测（编辑器发版改名根治）==")
check("版本号解析（v 前缀 / 多段数字 / 无版本号）",
      ns["_editor_exe_version"]("feetaghelper-v2.7.5.exe") == (2, 7, 5)
      and ns["_editor_exe_version"]("feetaghelper-v2.6.exe") == (2, 6)
      and ns["_editor_exe_version"]("feetaghelper-v2.10.0.exe") == (2, 10, 0)
      and ns["_editor_exe_version"]("feetaghelper.exe") is None)

probe_dir = tempfile.mkdtemp()
for name in ("feetaghelper-v2.6.0.exe", "feetaghelper-v2.7.5.exe"):
    open(os.path.join(probe_dir, name), "wb").close()
stale = os.path.join(probe_dir, "feetaghelper-v2.4.1.exe")
with open(ns["CONFIG_PATH"], "w", encoding="utf-8") as f:
    json.dump({"enabled": True, "path": "", "negative_path": "", "merge_lines": True,
               "autostart": False, "editor_path": stale}, f)
resolved = ns["_resolve_editor_path"](stale)
check("失效路径解析到同目录版本号最新的 exe",
      resolved == os.path.join(probe_dir, "feetaghelper-v2.7.5.exe"))
check("解析结果已写回 config（下次 UI 直接显示）",
      json.load(open(ns["CONFIG_PATH"], encoding="utf-8"))["editor_path"] == resolved)
check("有效路径原样返回", ns["_resolve_editor_path"](resolved) == resolved)

empty_dir = tempfile.mkdtemp()
gone = os.path.join(empty_dir, "feetaghelper-v2.4.1.exe")
check("同目录无候选时返回原值", ns["_resolve_editor_path"](gone) == gone)
missing_dir = os.path.join(probe_dir, "__no_dir__", "feetaghelper-v1.0.0.exe")
check("目录不存在时返回原值不崩溃", ns["_resolve_editor_path"](missing_dir) == missing_dir)

# 全链路：launch_editor 用失效路径进来，进程探测应拿到解析后的新 exe 名
# （拦截进程探测避免真拉起，消息里出现新 exe 名即证明整条链路已用解析结果）
real_process_check = ns["_is_process_running"]
ns["_is_process_running"] = lambda exe_name: True
ok, msg = ns["launch_editor"](stale)
ns["_is_process_running"] = real_process_check
check("launch_editor 全链路使用解析后的 exe", ok and "feetaghelper-v2.7.5.exe" in msg)

print("== v1.4.20：editor.hint 编辑器自荐路径（X-177 契约，四档优先级）==")
ns["EDITOR_HINT_PATH"] = os.path.join(TMP_DIR, "editor.hint")
hint_dir = tempfile.mkdtemp()
hint_exe = os.path.join(hint_dir, "feetaghelper-v2.8.3.exe")
open(hint_exe, "wb").close()
scan_best = os.path.join(probe_dir, "feetaghelper-v2.7.5.exe")  # 扫描档期望值


def _write_hint(content):
    with open(ns["EDITOR_HINT_PATH"], "w", encoding="utf-8") as f:
        f.write(content)


# 档②：用户未设置（config 空）→ hint 直接命中（立即启动零配置可用）
_write_hint(hint_exe)
check("hint：config 未设置时 hint 直接命中（空 configured → hint 路径）",
      ns["_resolve_editor_path"]("") == hint_exe)
_write_hint(hint_exe + "\r\n")  # 编辑器写文件惯例带尾换行
check("hint：尾换行容忍", ns["_resolve_editor_path"]("") == hint_exe)

# 档①：config 用户值有效 → hint 永不覆盖（用户值恒优先）
check("hint：config 用户值有效档恒优先（hint 在场也不覆盖）",
      ns["_resolve_editor_path"](scan_best) == scan_best)

# 档② vs 扫描档：configured 失效 + 同目录有更新候选 + hint 有效 → hint 胜、
# 且不写回 config（编辑器永不改插件 config，单向传值）
with open(ns["CONFIG_PATH"], "w", encoding="utf-8") as f:
    json.dump({"editor_path": stale}, f)
check("hint：与同目录扫描并存时 hint 胜（候选 v2.7.5 在场仍取 hint）",
      ns["_resolve_editor_path"](stale) == hint_exe)
check("hint：不写回 config（单向传值，config 保持用户原值）",
      json.load(open(ns["CONFIG_PATH"], encoding="utf-8"))["editor_path"] == stale)

# 档③：hint 无效 → 跳过该档回落既有链（扫描救援照常）
os.remove(ns["EDITOR_HINT_PATH"])
with open(ns["CONFIG_PATH"], "w", encoding="utf-8") as f:
    json.dump({"editor_path": stale}, f)
check("hint：文件缺失时回落扫描档（v1.4.9 救援不受影响）",
      ns["_resolve_editor_path"](stale) == scan_best)
for _bad in ("", "   ", "\\", "C:\\__no_such_hint__.exe"):
    _write_hint(_bad)
    check(f"hint：无效内容跳过该档（{_bad!r} → 扫描档接管）",
          ns["_resolve_editor_path"](stale) == scan_best)
os.remove(ns["EDITOR_HINT_PATH"])

# 启动链路：config 未配置 + hint 有效 → launch_editor 实际拉起 hint 指向的 exe
_launch_popen = []
_real_popen_hint = _subproc.Popen
_real_proc_hint = ns["_is_process_running"]
ns["_is_process_running"] = lambda exe_name: False
_subproc.Popen = lambda argv, **kw: _launch_popen.append(list(argv)) or _FakePopenResult()
try:
    _write_hint(hint_exe)
    ok, msg = ns["launch_editor"]("")
finally:
    _subproc.Popen = _real_popen_hint
    ns["_is_process_running"] = _real_proc_hint
    os.remove(ns["EDITOR_HINT_PATH"])
check("启动链路：未配置 + hint 有效 → 拉起 hint 指向的 exe（零配置可用）",
      ok and msg == "已启动：" + hint_exe
      and _launch_popen and _launch_popen[0][-1] == hint_exe)

# 档⓪（WebUI 特有）：settings.pin 有效 > hint——pin 在 launch_editor 先于
# 解析链应用，位次最高；拦截进程探测避免真拉起，回执 exe 名即证明目标
with open(ns["SETTINGS_PIN_PATH"], "w", encoding="utf-8") as f:
    json.dump({"editor_path": scan_best}, f)
try:
    _write_hint(hint_exe)
    ns["_is_process_running"] = lambda exe_name: True
    ok, msg = ns["launch_editor"](hint_exe)  # UI 传 hint_exe，pin 钉 scan_best
finally:
    os.remove(ns["SETTINGS_PIN_PATH"])
    os.remove(ns["EDITOR_HINT_PATH"])
check("hint：settings.pin 有效档位次最高（pin 在场 hint 不覆盖）",
      ok and "feetaghelper-v2.7.5.exe" in msg and hint_exe not in msg)

print("== settings.pin 统一固定（六键全覆盖，v1.4.12；旧独立 pin 兼容层）==")
check("pin 缺失：overrides 为空（回退 config）", ns["_read_pin_overrides"]() == {})
with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
    f.write("blurry, bad hands")
    PIN_NEG = f.name
with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
    f.write("1girl, smile")
    PIN_POS = f.name
with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
    f.write("worst quality, jpeg")
    PIN_NEG2 = f.name

# —— 旧独立 pin 兼容层（v1.4.10 文件原样可读，读取语义不变）——
check("_read_negative_pin 旧接口保留（现兼容层）", ns["_read_negative_pin"]() == "")
with open(ns["NEGATIVE_PIN_PATH"], "w", encoding="utf-8") as f:
    f.write('  "' + PIN_NEG + '"  \n')
check("旧 negative_path.pin：引号/空白容错并入 overrides",
      ns["_read_pin_overrides"]() == {"negative_path": PIN_NEG})
with open(ns["POSITIVE_PIN_PATH"], "w", encoding="gbk") as f:
    f.write(PIN_POS)
check("旧 positive_path.pin：GBK 兜底并入 overrides",
      ns["_read_pin_overrides"]() == {"negative_path": PIN_NEG, "path": PIN_POS})

# —— settings.pin：六键全覆盖 + 表外键忽略 + 同键优先于旧独立 pin ——
with open(ns["SETTINGS_PIN_PATH"], "w", encoding="utf-8") as f:
    json.dump({"path": PIN_POS, "negative_path": PIN_NEG2, "enabled": True,
               "merge_lines": True, "autostart": True,
               "editor_path": r"C:\__pin_editor__.exe", "unknown_key": 1}, f)
ov = ns["_read_pin_overrides"]()
check("settings.pin：六键全读取 + 表外键忽略",
      set(ov) == set(ns["CONTROL_KEYS"])
      and ov["path"] == PIN_POS and ov["enabled"] is True
      and ov["merge_lines"] is True and ov["autostart"] is True
      and ov["editor_path"] == r"C:\__pin_editor__.exe")
check("settings.pin：同键优先于旧独立 pin（negative_path）", ov["negative_path"] == PIN_NEG2)
with open(ns["SETTINGS_PIN_PATH"], "w", encoding="utf-8") as f:
    json.dump({"path": "   ", "negative_path": PIN_NEG2}, f)
check("settings.pin：路径键空值视作未固定（不遮蔽旧独立 pin / config）",
      ns["_read_pin_overrides"]() == {"negative_path": PIN_NEG2, "path": PIN_POS})

# —— before_process：pin 逐键覆盖入参（UI / config 值）——
with open(ns["SETTINGS_PIN_PATH"], "w", encoding="utf-8") as f:
    json.dump({"path": PIN_POS, "negative_path": PIN_NEG2}, f)
p = FakeP()
script.before_process(p, True, "", "", True, False, "")
check("pin 生效：UI 正反向为空仍注入 pin 指向文件",
      p.prompt == "1girl, smile, masterpiece, best quality"
      and p.negative_prompt == "worst quality, jpeg, lowres"
      and json.loads(p.extra_generation_params["fth_meta"])["full_text"] == p.prompt
      and json.loads(p.extra_generation_params["fth_meta_negative"])["full_text"] == p.negative_prompt)
p = FakeP()
script.before_process(p, True, r"C:\__other_pos__.txt", r"C:\__other_neg__.txt", True, False, "")
check("pin 生效：UI 值为别的路径同样以 pin 为准",
      p.prompt == "1girl, smile, masterpiece, best quality"
      and p.negative_prompt == "worst quality, jpeg, lowres")
with open(ns["SETTINGS_PIN_PATH"], "w", encoding="utf-8") as f:
    json.dump({"path": PIN_POS, "negative_path": PIN_NEG2, "enabled": False}, f)
p = FakeP()
script.before_process(p, True, PIN_POS, PIN_NEG2, True, False, "")
check("pin 生效：enabled 固定 False 覆盖 UI 勾选（六键全覆盖）",
      p.prompt == "masterpiece, best quality" and p.negative_prompt == "lowres"
      and not p.extra_generation_params)
with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
    f.write("a,\nb")
    ML_TXT = f.name
with open(ns["SETTINGS_PIN_PATH"], "w", encoding="utf-8") as f:
    json.dump({"path": ML_TXT, "merge_lines": False}, f)
p = FakeP()
script.before_process(p, True, ML_TXT, "", True, False, "")
check("pin 生效：merge_lines 固定 False 覆盖 UI True（换行保留）",
      p.prompt == "a,\nb, masterpiece, best quality")
os.unlink(ML_TXT)

# —— _preview：固定标记（settings.pin 优先，旧独立 pin 文案保留）——
with open(ns["SETTINGS_PIN_PATH"], "w", encoding="utf-8") as f:
    json.dump({"path": PIN_POS, "negative_path": PIN_NEG2}, f)
pos, neg, hint = ns["_preview"]("", "", True)
check("预览：settings.pin 固定标记（双路径键）",
      pos == "1girl, smile" and neg == "worst quality, jpeg"
      and hint.count("已由 settings.pin 固定") == 2)
with open(ns["SETTINGS_PIN_PATH"], "w", encoding="utf-8") as f:
    json.dump({"path": PIN_POS}, f)
pos, neg, hint = ns["_preview"]("", "", True)
check("预览：settings.pin 拆键后旧独立 pin 接管并显示其标记",
      neg == "blurry, bad hands" and "已由 negative_path.pin 固定" in hint
      and "已由 settings.pin 固定" in hint)

# —— UI 接线：六控件各挂独立 pin 写入处理器 ——
box_controls = {key: _Comp() for key in ns["CONTROL_KEYS"]}
ns["_wire_controls"](box_controls, False)
persister = {}
for key, comp in box_controls.items():
    fns = [c.get("fn") for c in comp._change_calls if c.get("fn") is not ns["_persist_settings"]]
    check(f"UI 接线：{key} 控件挂恰一个独立 pin 写入", len(fns) == 1)
    persister[key] = fns[0]
persister["path"](PIN_POS)
check("UI 提交：新值固化进 settings.pin 对应键",
      ns["_read_pin_overrides"]()["path"] == PIN_POS)
persister["enabled"](False)
check("UI 提交：布尔键恒写显式值（False 亦固化）",
      ns["_read_pin_overrides"]()["enabled"] is False)

# —— _persist_pin_key：路径键空提交删键 + 同步清旧独立 pin ——
ns["_persist_pin_key"]("negative_path", PIN_NEG)
check("persist：路径键写归一化值 + 已存在的旧 negative_path.pin 同步",
      ns["_read_pin_overrides"]()["negative_path"] == PIN_NEG
      and open(ns["NEGATIVE_PIN_PATH"], encoding="utf-8").read().strip() == PIN_NEG)
ns["_persist_pin_key"]("negative_path", "")
check("persist：路径键空提交删键 + 清空旧 pin（回退 config）",
      "negative_path" not in ns["_read_pin_overrides"]()
      and open(ns["NEGATIVE_PIN_PATH"], encoding="utf-8").read() == "")
ns["_persist_pin_key"]("path", "")
check("persist：正向键空提交删键 + 清空旧 positive_path.pin",
      "path" not in ns["_read_pin_overrides"]()
      and open(ns["POSITIVE_PIN_PATH"], encoding="utf-8").read() == "")

# —— _effective_config：UI 初始值 / 自动启动消费点 ——
with open(ns["SETTINGS_PIN_PATH"], "w", encoding="utf-8") as f:
    json.dump({"enabled": False, "path": PIN_POS, "merge_lines": False}, f)
with open(ns["CONFIG_PATH"], "w", encoding="utf-8") as f:
    json.dump({"enabled": True, "path": r"C:\cfg.txt", "negative_path": r"C:\cfgneg.txt",
               "merge_lines": True, "autostart": False, "editor_path": r"C:\cfg_ed.exe"}, f)
check("有效配置：pin 逐键覆盖、缺席键回退 config",
      ns["_effective_config"]() == {"enabled": False, "path": PIN_POS,
                                    "negative_path": r"C:\cfgneg.txt", "merge_lines": False,
                                    "autostart": False, "editor_path": r"C:\cfg_ed.exe"})

# —— launch_editor：editor_path 键存在以 pin 为准（先于失效自动探测）——
with open(ns["SETTINGS_PIN_PATH"], "w", encoding="utf-8") as f:
    json.dump({"editor_path": PIN_POS}, f)
real_process_check = ns["_is_process_running"]
ns["_is_process_running"] = lambda exe_name: True
ok, msg = ns["launch_editor"](r"C:\__ui_editor__.exe")
ns["_is_process_running"] = real_process_check
check("launch_editor：editor_path 在 pin 时以 pin 为准（UI 值被覆盖）",
      ok and os.path.basename(PIN_POS) in msg)

# —— 拆除 pin：回退 config 原逻辑（零迁移）——
for pin_path in (ns["SETTINGS_PIN_PATH"], ns["NEGATIVE_PIN_PATH"], ns["POSITIVE_PIN_PATH"]):
    os.remove(pin_path)
check("拆除全部 pin 后 overrides 复位为空", ns["_read_pin_overrides"]() == {})
p = FakeP()
script.before_process(p, True, PIN_POS, PIN_NEG2, True, False, "")
check("pin 拆除：回退 UI 值照常注入", p.prompt == "1girl, smile, masterpiece, best quality"
      and p.negative_prompt == "worst quality, jpeg, lowres")
pos, neg, hint = ns["_preview"]("", "", True)
check("预览：pin 拆除后回退未设置提示（无固定标记）",
      pos == "" and "未设置" in hint and "固定" not in hint)
os.unlink(PIN_NEG)
os.unlink(PIN_POS)
os.unlink(PIN_NEG2)

print("== config 读写 ==")
ns["_save_config"]({"enabled": False, "path": "X", "negative_path": "N",
                    "merge_lines": False, "autostart": True, "editor_path": "Y"})
cfg = ns["_load_config"]()
check("配置读写往返一致", cfg["enabled"] is False
      and cfg["merge_lines"] is False and cfg["negative_path"] == "N"
      and cfg["autostart"] is True and cfg["editor_path"] == "Y")
with open(ns["CONFIG_PATH"], "w", encoding="utf-8") as f:
    json.dump({"enabled": True, "position": "追加到末尾", "inject_negative": True,
               "merge_lines": True, "path": "Z"}, f)
cfg = ns["_load_config"]()
check("旧版残留键（position 等）被白名单忽略", cfg["enabled"] is True and cfg["path"] == "Z"
      and "position" not in cfg and "inject_negative" not in cfg)

print("== ui() 冒烟：返回值契约 + 布局重组断言（v1.4.18 ④，mock 组件树离线可测）==")


def _walk(comp):
    yield comp
    for ch in getattr(comp, "_children", []):
        yield from _walk(ch)


def _texts(comp):
    """组件树内全部 label / value / placeholder 文本（布局分组断言用）。"""
    out = []
    for c in _walk(comp):
        kw = getattr(c, "_kwargs", {})
        for key in ("label", "value", "placeholder"):
            if kw.get(key):
                out.append(str(kw[key]))
    return out


_ui_root = _Comp()
with _ui_root:
    returned = script.ui(False)
check("ui()：返回值契约不变（6 控件，连接引导不进脚本参数）", len(returned) == 6)

acc = next((c for c in _ui_root._children
            if c._args and "外部提示词注入" in str(c._args[0])), None)
check("布局：设置手风琴在位", acc is not None)
kids = acc._children
first_row = kids[0]
check("布局①：启用注入独立成行居首（总开关层级感）",
      type(first_row).__name__ == "Row"
      and len(first_row._children) == 1
      and str(first_row._children[0]._kwargs.get("label", "")).startswith("启用注入"))
check("布局①：总开关后跟分隔线（prompt-helper-divider）",
      any(c._kwargs.get("elem_id") == "prompt-helper-divider" for c in kids))
path_row = next(r for r in kids
                if type(r).__name__ == "Row"
                and sum(1 for c in r._children if type(c).__name__ == "Textbox") == 2)
path_labels = [str(c._kwargs.get("label", "")) for c in path_row._children]
check("布局②：正向 / 反向路径并排同一行",
      any(l.startswith("正向") for l in path_labels)
      and any(l.startswith("反向") for l in path_labels))
all_texts = _texts(acc)
check("布局②：用语规范——全区无「负向 / 逆向」",
      not any(("负向" in t or "逆向" in t) for t in all_texts))
groups = [g for g in kids if type(g).__name__ == "Group"]
preview_group = next(g for g in groups if any("正向文件预览" in t for t in _texts(g)))
check("布局③：预览组内含双预览 + 刷新钮",
      any("反向文件预览" in t for t in _texts(preview_group))
      and any(t == "刷新预览" for t in _texts(preview_group)))
editor_group = next(g for g in groups if any("复制插件路径" in t for t in _texts(g)))
editor_texts = _texts(editor_group)
check("布局④：编辑器组含路径框 + 复制插件路径 + 立即启动编辑器（+ 启动时自动拉起）",
      any("词条编辑器路径" in t for t in editor_texts)
      and any(t == "复制插件路径" for t in editor_texts)
      and any(t == "立即启动编辑器" for t in editor_texts)
      and any("启动 WebUI 时自动打开" in t for t in editor_texts))
_js_src = open(os.path.join(HERE, "javascript", "feetag_generate.js"), encoding="utf-8").read()
check("两处复制钮并存：设置区 elem_id 在位 + JS 同一委托绑定双按钮",
      any(c._kwargs.get("elem_id") == "feetag_copy_dir_settings" for c in _walk(acc))
      and ns["_BUS_PANEL"]["copy_dir_button"]._kwargs.get("elem_id") == "feetag_copy_dir"
      and "#feetag_copy_dir_settings" in _js_src and "#feetag_copy_dir" in _js_src)
check("快照冲刷钩子：window.__feetagFlushPageState 暴露且直调 postSave（同步采集+POST，防抖链不动）",
      "window.__feetagFlushPageState" in _js_src
      and "postSave()" in _js_src.split("window.__feetagFlushPageState", 1)[1][:300])

# ---- v1.4.21 破坏性变更护栏（2026-09-17 15:20 生产事故防复发）----
print("== v1.4.21 破坏性变更护栏 ==")
AUDIT = os.path.join(TMP_DIR, "config.audit.log")


def _audit_events():
    if not os.path.isfile(AUDIT):
        return []
    out = []
    with open(AUDIT, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def _write_disk_config(**overrides):
    cfg = dict(ns["DEFAULT_CONFIG"])
    cfg.update(overrides)
    with open(ns["CONFIG_PATH"], "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False)


OLD_POS, OLD_NEG, OLD_EDT = r"E:\old\prompt.txt", r"E:\old\neg.txt", r"E:\old\edit.exe"
_write_disk_config(path=OLD_POS, negative_path=OLD_NEG, editor_path=OLD_EDT)
audit_before = len(_audit_events())
ns["_save_config"]({"enabled": True, "path": None, "negative_path": r"E:\new\neg.txt",
                    "merge_lines": False, "autostart": True, "editor_path": OLD_EDT})
disk = ns["_load_config"]()
check("护栏①：磁盘 path 非空 → 新值 None 被拒（保留旧值）", disk["path"] == OLD_POS)
check("护栏①：未被破坏的键照常写入（negative_path 换新 / merge_lines 翻转）",
      disk["negative_path"] == r"E:\new\neg.txt" and disk["merge_lines"] is False)
check("护栏①：guard.block 审计行在位（事件 / 键 / 旧新值齐）",
      any(e["event"] == "guard.block" and e["key"] == "path" and e["old"] == OLD_POS
          and e["new"] is None and e["source"] == "_save_config" for e in _audit_events()))

ns["_save_config"]({"enabled": True, "path": "", "negative_path": r"E:\new\neg.txt",
                    "merge_lines": False, "autostart": True, "editor_path": ""})
disk = ns["_load_config"]()
check("护栏②：path / editor_path 磁盘非空 → 空串同样被拒",
      disk["path"] == OLD_POS and disk["editor_path"] == OLD_EDT)
check("护栏②：两个键各留一条 guard.block",
      sum(1 for e in _audit_events() if e["event"] == "guard.block") >= 3)

_write_disk_config(path=OLD_POS, negative_path=OLD_NEG, editor_path=OLD_EDT)
ns["_save_config"]({"enabled": True, "path": OLD_POS, "negative_path": "",
                    "merge_lines": True, "autostart": False, "editor_path": OLD_EDT})
check("护栏③：negative_path 非空 → 空被拒（反向注入路径不丢）",
      ns["_load_config"]()["negative_path"] == OLD_NEG)

audit_before = len(_audit_events())
_write_disk_config(path="", negative_path="", editor_path="")
ns["_save_config"]({"enabled": False, "path": "", "negative_path": "", "merge_lines": True,
                    "autostart": False, "editor_path": ""})
disk = ns["_load_config"]()
check("护栏④：新装机（磁盘路径全空）零误拦——空值照常落盘",
      disk["path"] == "" and disk["negative_path"] == "" and disk["editor_path"] == "")
check("护栏④：enabled True→False 放行不拦（关总闸是合法操作）", disk["enabled"] is False)
check("护栏④：guard.flip 审计行在位（放行也留痕）",
      any(e["event"] == "guard.flip" and e["key"] == "enabled" for e in _audit_events()))

audit_before = len(_audit_events())
ns["_save_config"]({"enabled": True, "path": r"E:\new\prompt.txt", "negative_path": "",
                    "merge_lines": True, "autostart": True, "editor_path": r"E:\new\edit.exe"})
disk = ns["_load_config"]()
check("护栏⑤：正常保存零变化——非空换非空 / 空换非空 / enabled 开闸全放行",
      disk["path"] == r"E:\new\prompt.txt" and disk["editor_path"] == r"E:\new\edit.exe"
      and disk["enabled"] is True and disk["autostart"] is True)
check("护栏⑤：正常保存不产生审计行", len(_audit_events()) == audit_before)

with open(ns["SETTINGS_PIN_PATH"], "w", encoding="utf-8") as f:
    json.dump({"enabled": True, "path": r"E:\pin\pos.txt"}, f, ensure_ascii=False)
ns["_persist_pin_key"]("enabled", False)
ns["_persist_pin_key"]("path", "")
pin_data = json.load(open(ns["SETTINGS_PIN_PATH"], encoding="utf-8"))
check("pin 链：enabled True→False 恒写（解除语义不变）+ pin.flip 留痕",
      pin_data.get("enabled") is False
      and any(e["event"] == "pin.flip" and e["key"] == "enabled" for e in _audit_events()))
check("pin 链：路径清空 = 解除固定（键删除，放行）+ pin.clear 留痕",
      "path" not in pin_data
      and any(e["event"] == "pin.clear" and e["key"] == "path" for e in _audit_events()))

audit_before = len(_audit_events())
ns["_persist_pin_key"]("enabled", True)
ns["_persist_pin_key"]("path", r"E:\pin\new.txt")
check("pin 链：正常固化（False→True / 换新路径）零审计",
      len(_audit_events()) == audit_before)

real_audit = ns["AUDIT_LOG_PATH"]
ns["AUDIT_LOG_PATH"] = os.path.join(TMP_DIR, "no-such-dir", "config.audit.log")
_write_disk_config(path=OLD_POS)
ns["_save_config"]({"enabled": True, "path": "", "negative_path": "", "merge_lines": True,
                    "autostart": False, "editor_path": ""})
check("审计失败静默：audit 不可写时护栏照常拦截、不抛错",
      ns["_load_config"]()["path"] == OLD_POS)
ns["AUDIT_LOG_PATH"] = real_audit

ns["_config_corrupt_logged"] = False
with open(ns["CONFIG_PATH"], "w", encoding="utf-8") as f:
    f.write("{half json")
ns["_load_config"](); ns["_load_config"]()
corrupt_rows = [e for e in _audit_events() if e["event"] == "load.corrupt"]
check("config 损坏留痕：读失败落默认值时记 load.corrupt，且进程内只记一次",
      len(corrupt_rows) == 1 and ns["_load_config"]()["enabled"] is True)
_write_disk_config(path=OLD_POS)  # 恢复现场供后续用例

check("v1.4.21 JS：通用扫描采集 + 回放双双跳过插件控件（isPluginControl 守卫）",
      _js_src.count("if (isPluginControl(el)) continue;") == 2)
check("v1.4.21 JS：closest 钉死 prompt-helper-* 容器前缀",
      'el.closest(\'[id^="prompt-helper-"]\')' in _js_src)

print("\n全部测试通过 ✔")
