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
import time
import types

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT_PATH = os.path.join(HERE, "scripts", "prompt_helper.py")
REAL_TXT = r"E:\桌面\AI file\Design file\prompt-helper\prompt.txt"


# ---- mock gradio / modules.scripts ----
class _Comp:
    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def change(self, *args, **kwargs):
        pass

    def submit(self, *args, **kwargs):
        pass

    def click(self, *args, **kwargs):
        pass


gradio = types.ModuleType("gradio")
for name in ("Accordion", "Row", "Checkbox", "Radio", "Textbox", "HTML", "Button"):
    setattr(gradio, name, type(name, (_Comp,), {}))

scripts_mod = types.ModuleType("modules.scripts")


class Script:  # noqa: N801 - 模拟 modules.scripts.Script
    pass


scripts_mod.Script = Script
scripts_mod.AlwaysVisible = object()

callbacks_mod = types.ModuleType("modules.script_callbacks")
_registered_callbacks = []
callbacks_mod.on_app_started = lambda cb, name=None: _registered_callbacks.append(cb)

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

# before_process 会把参数写回配置文件，测试期间改用临时配置，避免污染真实配置
ns["CONFIG_PATH"] = os.path.join(tempfile.mkdtemp(), "config.json")


class FakeP:
    prompt = "masterpiece, best quality"
    negative_prompt = "lowres"
    extra_generation_params = {}


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

print("== before_process 注入 ==")
script = ns["PromptHelperScript"]()
# 与插件注入管线一致地算期望值（prompt.txt 将来携带元数据 tag 时断言依然成立）
raw = ns["read_tag_file"](REAL_TXT)[0]
base, _metas = ns["strip_meta_tags"](raw)
base = ns["expand_breaks"](base, _metas[-1] if _metas else None)

p = FakeP()
script.before_process(p, True, REAL_TXT, NEG_TXT, "追加到末尾", True, False, "")
check("正向追加 + 反向文件注入", p.prompt == "masterpiece, best quality, " + base
      and p.negative_prompt == "lowres, blurry, bad hands")

p = FakeP()
script.before_process(p, True, REAL_TXT, NEG_TXT, "插入到最前", True, False, "")
check("插入到最前（正反向各自生效）", p.prompt == base + ", masterpiece, best quality"
      and p.negative_prompt == "blurry, bad hands, lowres")

p = FakeP()
script.before_process(p, True, REAL_TXT, "", "追加到末尾", True, False, "")
check("反向留空不注入", p.prompt == "masterpiece, best quality, " + base
      and p.negative_prompt == "lowres")

p = FakeP()
script.before_process(p, True, REAL_TXT, r"C:\__no_neg__.txt", "追加到末尾", True, False, "")
check("反向文件缺失时跳过反向", p.prompt == "masterpiece, best quality, " + base
      and p.negative_prompt == "lowres")

p = FakeP()
script.before_process(p, False, REAL_TXT, NEG_TXT, "追加到末尾", True, False, "")
check("停用时不注入", p.prompt == "masterpiece, best quality" and p.negative_prompt == "lowres")

p = FakeP()
p.prompt = ["a", "b"]
script.before_process(p, True, REAL_TXT, "", "追加到末尾", True, False, "")
check("列表提示词逐项注入", p.prompt == ["a, " + base, "b, " + base])

p = FakeP()
script.before_process(p, True, r"C:\__no_such_file__.txt", NEG_TXT, "追加到末尾", True, False, "")
check("正向缺失时跳过且反向仍注入", p.prompt == "masterpiece, best quality"
      and p.negative_prompt == "lowres, blurry, bad hands")

os.unlink(NEG_TXT)

print("== before_process 元数据注入管线 ==")
with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
    f.write(_linked("1girl, smile, dress, hat", {"v": 1, "breaks": [2, 4], "pick": []}))
    META_TXT = f.name
with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
    f.write(_linked("blurry, bad hands", {"v": 1, "breaks": [1], "pick": [{"path": "g", "key": "blurry"}]}))
    META_NEG = f.name

p = FakeP()
script.before_process(p, True, META_TXT, META_NEG, "追加到末尾", True, False, "")
check("剥离元数据 + BREAK 展开（尾部位置防御性修剪）",
      p.prompt == "masterpiece, best quality, 1girl, smile\n\ndress, hat"
      and p.negative_prompt == "lowres, blurry\n\nbad hands"
      and "<fth:meta:" not in p.prompt + p.negative_prompt)
recorded = json.loads(p.extra_generation_params.get("fth_meta", "{}"))
neg_recorded = json.loads(p.extra_generation_params.get("fth_meta_negative", "{}"))
check("元数据写入 PNG extra_generation_params（附插件版本）",
      recorded.get("breaks") == [2, 4] and recorded.get("plugin") == ns["PLUGIN_VERSION"]
      and neg_recorded.get("breaks") == [1])

os.unlink(META_TXT)
os.unlink(META_NEG)

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

print("== config 读写 ==")
ns["_save_config"]({"enabled": False, "path": "X", "negative_path": "N", "position": "插入到最前",
                     "merge_lines": False, "autostart": True, "editor_path": "Y"})
cfg = ns["_load_config"]()
check("配置读写往返一致", cfg["position"] == "插入到最前" and cfg["enabled"] is False
      and cfg["merge_lines"] is False and cfg["negative_path"] == "N"
      and cfg["autostart"] is True and cfg["editor_path"] == "Y")

print("\n全部测试通过 ✔")
