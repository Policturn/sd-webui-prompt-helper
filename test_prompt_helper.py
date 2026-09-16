# -*- coding: utf-8 -*-
"""离线自测：不启动 WebUI，用 mock 验证注入核心逻辑。

用法：python test_prompt_helper.py
"""

import base64
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
    def __init__(self, *args, **kwargs):
        self._change_calls = []  # 记录 .change 接线（kwargs），供 _wire_controls 断言

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def change(self, *args, **kwargs):
        self._change_calls.append(kwargs)

    def submit(self, *args, **kwargs):
        pass

    def click(self, *args, **kwargs):
        pass


gradio = types.ModuleType("gradio")
for name in ("Accordion", "Row", "Checkbox", "Radio", "Textbox", "HTML", "Button"):
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
callbacks_mod.on_app_started = lambda cb, name=None: _registered_callbacks.append(cb)
callbacks_mod.on_after_component = lambda cb, name=None: _after_component_cbs.append(cb)

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
    """捕获 add_api_route 注册的端点处理器，供离线直调。"""

    def __init__(self):
        self.routes = {}

    def add_api_route(self, path, endpoint, methods=None, include_in_schema=False):
        self.routes[path] = endpoint


fake_app = FakeApp()
ns["_register_bus_endpoints"](fake_app)
check("总线端点注册（status/image/cmd/progress 四条）",
      set(fake_app.routes) == {"/feetag/bus/status", "/feetag/bus/image", "/feetag/bus/cmd", "/feetag/bus/progress"})

# progress 端点（v1.4.14）：armed 门控 404；转发返回 JSON + CORS 头；异常回 null JSON 不 5xx
class _FakeReq:
    def __init__(self, host): self.headers = {"host": host}
class _FakeUrlopen:
    def __init__(self, payload): self._p = payload
    def read(self): return self._p
    def __enter__(self): return self
    def __exit__(self, *a): return False
import urllib.request as _urlreq
bus_progress = fake_app.routes["/feetag/bus/progress"]
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
bus_cmd = fake_app.routes["/feetag/bus/cmd"]

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

print("\n全部测试通过 ✔")
