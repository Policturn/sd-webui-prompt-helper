# -*- coding: utf-8 -*-
"""featag_out 存量图元数据修复工具（v1.4.22 单份化配套，一次性，随部署跑）。

背景：v1.4.2-1.4.21 的 postprocess 把成品图复制进 featag_out/ 时，parameters
用的是 processed.info（批次首图的 infotext）贴给所有图——批次内第 2 张起的
Seed 等字段是错的（首图自己的那份）。v1.4.22 起停复制、直接上报 WebUI 原生
落盘路径（逐图完整 infotext），新图无此瑕疵；本工具修历史存量：

  1. 扫 featag_out 全部 fth_* 文件，按文件名时间戳（fth_{YYYYmmdd_HHMMSSmmm}
     _{i}.png）分组为一轮；
  2. 判瑕疵轮：同轮 ≥2 张且 parameters 全部相同且非空（健康批次的逐图
     infotext 必含不同 Seed，全同 = 首图贴所有图实锤；首图自己的那份是对的，
     只修第 2 张起）；
  3. 有 WebUI 原件对应的（按 种子 + 实际像素尺寸 匹配，同键多候选取 mtime
     与副本最接近者）→ 用原件的 parameters 原子重写（临时文件 + os.replace，
     像素与其他文本块原样保留）；
  4. 无原件的（直发历史图，WebUI 当时未落盘）→ 按批量 seed 递增（s0+i）
     文本替换 Seed 字段尽力重构；
  5. 不可修复的（解析不出种子 / 无 parameters）列清单。

用法（部署机插件目录的 tools/ 下）：
  预览（默认干跑，不写盘）：
    "H:/sd-webui-aki-v4.4/venv/Scripts/python.exe" repair_featag_out_meta.py
  实修：
    同上 + --apply
可选参数：
  --featag-out DIR   featag_out 目录（默认 = 插件根/featag_out，自动推导）
  --outputs DIR      WebUI outputs 目录（默认自动推导 <WebUI根>/outputs，
                     推导失败必须显式传入）
报告：插件根 featag_out_meta_repair_report.json（修复数 / 重构数 / 跳过数 /
不可修数 + 逐文件明细），干跑与实修都写。
"""

import argparse
import datetime
import json
import os
import re
import sys

try:
    from PIL import Image, PngImagePlugin
except ImportError:
    print("需要 PIL（Pillow）：请用 WebUI venv 的 python 运行本脚本")
    sys.exit(2)

FTH_NAME_RE = re.compile(r"^fth_(\d{8}_\d{9})_(\d+)\.png$")
SEED_RE = re.compile(r"Seed:\s*(\d+)")
MTIME_WINDOW = 6 * 3600  # 原件与副本的写入间隔上限（秒）——超窗的同键候选视为无关历史图
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}

TOOL_DIR = os.path.dirname(os.path.abspath(__file__))
PLUGIN_ROOT = os.path.dirname(TOOL_DIR)


def parse_args():
    ap = argparse.ArgumentParser(description="featag_out 存量图元数据修复（v1.4.22 配套）")
    ap.add_argument("--featag-out", default=os.path.join(PLUGIN_ROOT, "featag_out"),
                    help="featag_out 目录（默认自动推导）")
    ap.add_argument("--outputs", default=None,
                    help="WebUI outputs 目录（默认推导 <WebUI根>/outputs，失败必须显式传）")
    ap.add_argument("--apply", action="store_true", help="实际写盘（默认干跑预览）")
    return ap.parse_args()


def autodetect_outputs(featag_out):
    """featag_out 位于 <WebUI根>/extensions/<插件名>/featag_out → 上跳三级。"""
    guess = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(featag_out))), "outputs")
    return guess if os.path.isdir(guess) else None


def read_png_meta(path):
    """读 PNG 头部元信息（不解码像素）：返回 (parameters 或 None, (w, h))。"""
    try:
        with Image.open(path) as img:
            return img.text.get("parameters"), img.size
    except Exception:
        return None, (0, 0)


def build_originals_index(outputs_dir):
    """扫 outputs 全树建索引：(seed, 尺寸) → [(路径, mtime), ...]。

    种子优先取自文件自身 parameters（与文件名形态无关，兼容自定义
    samples_filename_pattern）；读不出 parameters 的再退回文件名里的
    `-<seed>` 段（A1111 默认命名 00042-199846809[-prompt].png）。"""
    index = {}
    scanned = 0
    for root, _dirs, files in os.walk(outputs_dir):
        for name in files:
            if os.path.splitext(name)[1].lower() not in IMAGE_EXTS:
                continue
            path = os.path.join(root, name)
            text, size = read_png_meta(path)
            if size == (0, 0):
                continue
            m = SEED_RE.search(text or "")
            if not m:
                m = re.search(r"-(\d{3,})(?:-|\.)", name) or re.match(r"^(\d{3,})-", name)
            if not m:
                continue
            key = (int(m.group(1)), size)
            try:
                mtime = os.stat(path).st_mtime
            except OSError:
                continue
            index.setdefault(key, []).append((path, mtime))
            scanned += 1
            if scanned % 500 == 0:
                print(f"  … 已索引 {scanned} 张原图")
    return index, scanned


def rewrite_parameters(path, new_text):
    """原子重写 PNG 的 parameters（保留其他文本块与全部像素）。"""
    with Image.open(path) as img:
        img.load()
        info = PngImagePlugin.PngInfo()
        for key, value in (img.text or {}).items():
            if key == "parameters":
                continue
            info.add_text(key, value)
        info.add_text("parameters", new_text)
        tmp = path + ".tmp-repair"
        img.save(tmp, format="PNG", pnginfo=info)
    os.replace(tmp, path)


def main():
    args = parse_args()
    featag_out = os.path.abspath(args.featag_out)
    if not os.path.isdir(featag_out):
        print(f"featag_out 目录不存在：{featag_out}（用 --featag-out 指定）")
        return 2
    outputs = os.path.abspath(args.outputs) if args.outputs else autodetect_outputs(featag_out)
    if not outputs or not os.path.isdir(outputs):
        print("WebUI outputs 目录未定位到：请用 --outputs 显式传入（如 H:/sd-webui-aki-v4.4/outputs）")
        return 2

    print(f"featag_out = {featag_out}")
    print(f"outputs    = {outputs}")
    print(f"模式       = {'实修(--apply)' if args.apply else '干跑预览（加 --apply 实修）'}")

    # —— 分轮 ——
    rounds = {}
    unrecognized = []
    for name in sorted(os.listdir(featag_out)):
        m = FTH_NAME_RE.match(name)
        if m:
            rounds.setdefault(m.group(1), {})[int(m.group(2))] = os.path.join(featag_out, name)
        elif name.startswith("fth_"):
            unrecognized.append(name)

    print(f"扫描：{sum(len(v) for v in rounds.values())} 张 fth 图 / {len(rounds)} 轮"
          + (f" / {len(unrecognized)} 个无法识别文件名" if unrecognized else ""))

    # —— 判瑕疵轮 ——
    defective = []
    healthy_rounds = 0
    no_meta_files = []
    for stamp in sorted(rounds):
        files = rounds[stamp]
        texts = {}
        for idx, path in files.items():
            text, _size = read_png_meta(path)
            texts[idx] = text
        if len(files) < 2:
            healthy_rounds += 1
            continue
        vals = [texts[i] for i in sorted(files)]
        if any(v is None or not v.strip() for v in vals):
            no_meta_files.extend(files[i] for i in sorted(files) if not (texts[i] or "").strip())
            healthy_rounds += 1  # 无 parameters 的老副本（v1.4.2 hotfix 前）不属本瑕疵，跳过
            continue
        if all(v == vals[0] for v in vals):
            defective.append((stamp, files, texts))
        else:
            healthy_rounds += 1
    print(f"瑕疵轮（同轮多图 infotext 全同）：{len(defective)}"
          f" / 健康 {healthy_rounds} 轮"
          + (f" / 无 parameters 跳过 {len(no_meta_files)} 张" if no_meta_files else ""))

    # —— 原件索引 ——
    print("索引 WebUI 原件（读头部，不解码像素）…")
    index, scanned = build_originals_index(outputs)
    print(f"索引完成：{scanned} 张（按 种子+尺寸 建键）")

    # —— 修复 ——
    details = []
    repaired = reconstructed = unrepaired = 0
    for stamp, files, texts in defective:
        base_seed_m = SEED_RE.search(texts[0])
        base_seed = int(base_seed_m.group(1)) if base_seed_m else None
        for idx in sorted(files):
            path = files[idx]
            if idx == 0:
                continue  # 首图的 parameters 本就是它自己的，正确
            entry = {"round": stamp, "file": os.path.basename(path)}
            if base_seed is None:
                unrepaired += 1
                entry["action"] = "unrepaired"
                entry["reason"] = "首轮 parameters 解析不出 Seed"
                details.append(entry)
                continue
            want_seed = base_seed + idx
            _text, fth_size = read_png_meta(path)
            candidates = index.get((want_seed, fth_size), [])
            cands_in_window = [c for c in candidates
                               if abs(c[1] - os.stat(path).st_mtime) <= MTIME_WINDOW]
            if cands_in_window:
                orig_path = min(cands_in_window,
                                key=lambda c: abs(c[1] - os.stat(path).st_mtime))[0]
                orig_text, _ = read_png_meta(orig_path)
                if orig_text:
                    if args.apply:
                        rewrite_parameters(path, orig_text)
                    repaired += 1
                    entry["action"] = "repaired_from_original"
                    entry["source"] = orig_path
                    entry["seed"] = want_seed
                    details.append(entry)
                    continue
            # 兜底：seed 递增重构（直发历史，无原件）
            if args.apply:
                new_text = SEED_RE.sub(f"Seed: {want_seed}", texts[idx])
                rewrite_parameters(path, new_text)
            reconstructed += 1
            entry["action"] = "reconstructed_seed_increment"
            entry["seed"] = want_seed
            details.append(entry)

    # —— 报告 ——
    report = {
        "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "apply": args.apply,
        "featag_out": featag_out,
        "outputs": outputs,
        "originals_indexed": scanned,
        "rounds_total": len(rounds),
        "rounds_defective": len(defective),
        "repaired_from_original": repaired,
        "reconstructed_seed_increment": reconstructed,
        "unrepaired": unrepaired,
        "no_parameters_skipped": len(no_meta_files),
        "unrecognized_files": unrecognized,
        "details": details,
    }
    report_path = os.path.join(PLUGIN_ROOT, "featag_out_meta_repair_report.json")
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print("—— 结果 ——")
    print(f"  原件重写 : {repaired}")
    print(f"  种子重构 : {reconstructed}（直发历史，无原件的兜底）")
    print(f"  不可修复 : {unrepaired}")
    if unrepaired:
        for entry in details:
            if entry.get("action") == "unrepaired":
                print(f"    - {entry['file']}：{entry.get('reason', '')}")
    print(f"  明细报告 : {report_path}")
    if not args.apply:
        print("（干跑未写盘——确认数字后加 --apply 实修）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
