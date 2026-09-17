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
pass（_ad_inner 标记的 img2img p2）在所有钩子入口直接 return，不注入不计数不
回传。v1.4.8 诊断补全：ADetailer postprocess_image 还会对 copy(外层 p) 做两次
显式钩子重调（真机实证 extensions/adetailer/scripts/!adetailer.py L909/L926，
copy 只继承外层 p、不带 _ad_inner——这才是 v1.4.5 起防御"未生效"的真因）：
before_process 以随 p 的 _feetag_pass 标记幂等跳过（浅拷贝自动继承标记），
postprocess 按 ADetailer 空壳 Processed（images/info 全空）识别跳过。
status.json（state/pass/images/error/ts + choices）只在内容变化时重写，
供编辑器轮询；params/cmd 的读取仅在 apply 点击时发生，天然节流。

v1.4.3 起总线带总开关：插件目录放置 bus.armed 标志文件才启用全部总线行为
（JS 轮询 / status 写入 / featag_out 回传 / apply 回填），默认关闭——词条注入
不受开关影响。放置/删除即刻生效，无需重启。

v1.4.6 修复 cmd 指令的多消费者竞态：cmd.json 是单槽文件，旧版浏览器 JS 经
/file= 直读（只读不删，靠各自 localStorage 的 ts 去重），多浏览器 / 多页签
并存时同一条命令会被多方同时触发（实测：用户与测试浏览器先后点生成，用户
命令被测试页面抢走）。改为服务端原子消费端点 GET /feetag/bus/cmd：先重命名
到临时名再读删（同卷原子操作，改名成功者独得），每条命令全局恰有一个消费者
能取到，其余请求得到 404；JS 轮询改调该端点，lastTs 去重保留为双保险。

v1.4.7 修正 USDU 脚本选中契约：params.usdu 节平铺（enable=true 选中脚本 +
同节字段回填）。旧实现读 params["usdu"]["usdu"] 双重嵌套、与编辑器面板写的
params.scripts.usdu 互不匹配，Tiled / Tiled VAE / USDU 参数注入从未实际生效。

v1.4.9 根治 editor_path 随编辑器发版 exe 改名失效的问题（config 硬编码完整
路径，历史上 v2.4.1→v2.6.0→v2.6.1→v2.7.5 已三度断链、自动启动静默失效）：
launch_editor 起始处经 _resolve_editor_path 解析——configured 指向的 exe
存在则原样使用；已失效则在同目录扫描 feetaghelper-v*.exe，按文件名版本号
元组（(2, 7, 5) 式比较，兼容 v 前缀与任意多段数字）取最新者，并把解析结果
写回 config（下次 UI / 自动启动直接显示新路径）；同目录无候选时返回原值，
保持"文件不存在"的原有报错行为。进程防重探测（_is_process_running）与最终
subprocess 均使用解析后的路径。

v1.4.10 根治 negative_path 被旧页面内存值反复回写冲空的问题（before_process
_save_config 以 UI 值落盘，旧页面持有空反向路径时会把 config 冲回空）：支持
插件目录根放置 negative_path.pin（纯文本一行=反向词条 txt 完整路径）。读取：
每次注入现读（一次 stat+read），pin 存在且非空时反向有效路径以 pin 为准、
无视 config / UI 值；pin 不存在 / 空文件 → 回退 config 原逻辑（零迁移）。
写入：UI 反向文本框保持可编辑，显示值 = pin 优先；用户提交（.change 持久化
链路）除写 config 外同步把新值写进 pin 文件（用户输入即新的固定值，空值=
无 pin 回退 config）。_preview 反向状态行带「（已由 negative_path.pin 固定）」
标记；放置/修改/删除即刻生效，无需重启。

v1.4.12 设置固定升级为统一 settings.pin（JSON，任意 config 键子集，六键
enabled / path / negative_path / merge_lines / autostart / editor_path 全
覆盖），替代逐键独立 pin 文件：读取优先级逐键 settings.pin > 旧独立 pin
（negative_path.pin / positive_path.pin 作迁移兼容并入，老用户文件原样可读）
> config（零迁移）。根治 config 各键被旧页面内存值经 _save_config 反复回写
冲掉的问题（path 与 negative_path / editor_path 同族）。UI 六个控件初始值
= pin 覆盖后的有效值（_effective_config）；任一控件提交（.change 持久化链）
除写 config 外经 _persist_pin_key 把该键固化进 settings.pin（用户显式操作=
固化意图；路径键空值 = 从 pin 删该键回退 config 并同步清空对应旧独立 pin
文件，防止解除固定被旧文件顶回；布尔键恒写显式值）。before_process /
_preview / launch_editor / 自动启动全部消费点统一走 pin 有效值；_preview
状态行带「（已由 settings.pin 固定）」标记（经旧独立 pin 固定则显示对应
文件名）。每次现读、即刻生效，无需重启。

v1.4.13 指令延迟压缩：发布指令到点生成的链路从「轮询发现(≤500ms) + apply
盲等(700ms) + 切页驻留(150ms)」（实测隐藏页签定时器被 Chromium 节流后
write→busy 高达 3.5~4.3s）压到平均 ~300ms 内。服务端配合点：status.json
新增 applied_ts 字段（毫秒时间戳，粘滞保留最近值），apply handler（含
ADetailer 段）完成时经 _mark_applied 刷新之、状态机字段（state/pass/images）
沿用最近值不改写——浏览器 JS 点 apply 后轮询 /feetag/bus/status，见
applied_ts > cmd.ts 即参数已在服务端算完回包，小驻留后立即点生成（取代
固定盲等 700ms）；信号缺失（旧版 JS / 写失败）由 JS 超时回落旧盲等，服务端
零额外风险。JS 侧同步：轮询 500ms→200ms、切页签提前、信号等待双节拍驱动
（Worker 心跳 + 50ms setInterval，隐藏页不退化），见 javascript/feetag_generate.js。

v1.4.15 方案 A（快照锁定提示词）：编辑器「替换并生成」的参数快照（排队快照制）
额外携带触发时刻的完整提示词文本——params.json 顶层可选 prompt 键。before_process
注入时 bus 武装且该键非空 → 正向 tag 文本用快照值替代 prompt.txt 实时读取
（_read_snapshot_prompt；仍走 strip_meta_tags→expand_breaks→_inject 全管线，
p.prompt 页面基底不动，日志带「（快照锁定）」标注）——修「替换并生成」连点 /
排队期间后续替换、库组自动轮换改写 txt 导致先发出的快照读到被覆盖词（挑着
生成 / 模型丢失）。无键 / 空白 / bus 未武装（params.json 陈旧残留不得锁定）/
读失败 → 回落现状读 txt，语义不变。params.json 只被编辑器覆盖写、无删除方，
生成轮内（写 params+cmd → apply → 点生成 → before_process）无下一轮覆盖窗口，
轮末读盘安全；读一次小 json 的开销可接受。

v1.4.16 盲测 P1×4 修复：
① 接线分组分线——未安装 Tiled Diffusion / Ultimate SD upscale 扩展的环境，
  旧版「全有或全无」等全部字段组件到齐才接线，可选组组件永不出现 → apply
  永不接线且零日志，JS 仍照常点生成钮，参数静默停留在界面旧值（总线核心
  功能被废）。现 base / hires（A1111 原生组件）为硬依赖；可选组
  （tiled / tiledvae / usdu）经 A1111 脚本注册表判定在场，未装则整组裁剪并
  打日志（apply handler 对 params 缺组键本就跳过，v1.4.7 平铺契约不变）。
② Reload UI 接线复位——A1111 重建界面（webui.py 主循环 before_ui_callback
  → create_ui）会重跑全部脚本 ui()，模块级 _WIRED=True 不复位导致新 apply
  钮永不接线（apply 静默死亡直到重启进程），_SCRIPT_LISTS 等持旧引用无界
  累加；on_before_ui 回调复位全部捕获状态，重建后重新接线。
③ 总线看门狗——生成任务异常路径不触发 postprocess，status 永久卡 busy、
  无 error 写出（编辑器只能靠自身超时兜底）。守护线程按任务活性判定：
  shared.state.job 已清空而状态仍 busy、连续两轮（约 10s）才写 error——
  数小时的慢生成（大图 tiled 超分）不会误伤；判定链路不可用时宁可不写
  （退回编辑器侧 120s 超时兜底）。
④ 配置 / 状态文件原子写——config.json / settings.pin / 旧独立 pin /
  status.json 的写入改经 _atomic_write_text（同目录临时文件 + os.replace
  原子落盘），config 族读写并持 _config_lock 串行：读者（编辑器面板轮询、
  下次 _load_config）不再可能读到半截 JSON，崩溃 / 并发写不再有截断窗口
  （positive path 被半截 config 静默清空的风险根除）。

v1.4.17 连接引导（方案 B 插件侧配套，终稿=生成页面板）：txt2img 生成页挂
「生成页总线 · 连接引导」小面板（_build_bus_panel，经 on_after_component 在
锚点组件 txt2img_gallery 的 with 上下文内创建——A1111 把该回调补丁进组件
__init__，回调内建件即挂载锚点正后方；不在设置手风琴，避免引导入口藏进
设置），三件成组：
① armed 总线开关（gr.Checkbox「启用生成页总线（允许编辑器触发生成）」，
   常驻安全提示「开启 = 允许本机编辑器程序替你点击生成按钮」）：勾/取消 =
   经 set_bus_armed 写/删插件目录 bus.armed，与手工放置**完全同一文件、
   同一语义**，不另起第二套开关状态。armed 判定本就实时（bus_armed() 每次
   调用 os.path.isfile，全部端点 / 生成钩子逐请求现查——编辑器远程写删
   文件即刻生效，无需重启或刷新页面）；初始值 = 标志文件现状回显，WebUI
   重启后保持一致。开关显示随外部写入同步：featag_generate.js 的探测改为
   双向（armed↔disarmed 都检测，15s 节拍），检测到翻转时 syncArmedDisplay
   程序化勾/取消面板开关（dispatch change 让 gradio 拾取，触发的服务端写
   与文件现值幂等）。此前新用户必须在插件目录手工创建空文件 bus.armed
   （无任何界面），开关补上引导链缺口。
② 插件目录路径展示（_plugin_dir_display：EXT_DIR 由插件文件位置计算、与
   settings.pin / params.json 同源勿手填；data-dir 属性 + 可见 code 文本
   双通道，剪贴板被浏览器策略拦时照抄）。
③ 「复制插件目录」按钮（复制由 featag_generate.js 独立委托点击处理器完成：
   navigator.clipboard.writeText → execCommand/select 兜底 → 路径文本就在
   按钮旁；点击后短暂显示「已复制 ✓」；document 级 elem_id 委托与挂载
   位置无关）。
仅 WebUI 仓（armed/总线为 WebUI 侧概念，ComfyUI 生成链未立项不加）。
新用户引导链就此全程界面可点：装插件 → 生成页面板复制目录 → 粘进编辑器
检测（编辑器直写 bus.armed 开闸，页面开关 ≤15s 回显）→ 面板可触发生成。

v1.4.18 Wave B（用户拍板，编辑器配套）三件：
①「使用网页端插件」开关 + bus.direct 文件契约：插件根 bus.direct 文件，
   **存在 = 后端直发模式（编辑器生成不经页面）、不存在 = 现行页面链路
   （默认）**；空文件即可、逐请求实时判定（bus_direct() 每次现查）、删 =
   立即回页面模式——与 bus.armed 同一套文件化契约（编辑器 Wave A 按此
   文件决定开窗方式）。生成页面板 armed 开关旁新增 Checkbox「使用网页端
   插件的页面链路（其他插件照常生效）」：勾 = 删 bus.direct、取消勾 = 写
   入（初始值 not bus_direct() 回显；JS 15s 探测同步显示，同 armed 机制）。
② 后端直发链路（bus.direct 存在时）：cmd 消费改服务端——专用消费线程
   （_direct_poller_loop，armed+direct 双开时 0.5s 节拍）经 _consume_cmd
   原子自取命令并执行；/feetag/bus/cmd 端点在直发模式一律 404 且不消费
   （页面 JS 与直发互斥，改名原子性保证不双消费；模式翻转瞬间在途命令至
   多按旧模式执行一条，编辑器重发即愈）。执行 = 本机调 /sdapi/v1/txt2img
   （进程内经 shared.cmd_opts.port，需 WebUI 以 --api 启动；参考 ad-retest
   直发经验——before_process 注入钩子对 API 路径同样生效，R1 实证 payload
   留空时注入链是提示词唯一来源）：params 平铺契约经 _direct_payload 纯
   映射（base 直传；hires：enable→enable_hr / steps→hr_second_pass_steps /
   denoise→denoising_strength / upscaler→hr_upscaler；tiled/usdu/
   adetailer 等页面插件键不映射——直发语义即不经页面、页面插件不参与，
   编辑器面板的 ADetailer 值同样不生效）；成图落 featag_out/（parameters
   pnginfo 同页面链路）；busy/done/error/pass 计数走既有状态机；
   shared.state 在 API 生成期同样更新（api.py L475 state.begin，进度端点
   照常），另置 _direct_running 护栏防看门狗误杀。直发模式页面 JS 轮询
   照常（cmd 恒 404，不执行）。
③ 页面状态快照/恢复（页面链路模式的持久化，best-effort 档、gradio 版本
   敏感——README 注明）：JS 采集（已接线字段表 elem_id 组件当前值 +
   *_script_container 内未接线脚本输入的通用 DOM 扫描）→ POST
   /feetag/bus/page-state 存 bus.page_state.json（原子写）；触发 = 输入
   防抖 1.2s + pagehide keepalive 兜底。恢复 = 页面加载时 GET 回放（程序
   化设值 + dispatch input/change，A1111 updateInput 同款）；恢复只在加载
   时，生成时总线 apply 对受控字段的写入照常覆盖（受控字段编辑器赢、其余
   恢复用户值）。默认开；插件目录放置 bus.page_state.disabled 即整体关闭。
④ 设置手风琴布局重组（用户点名）：「启用注入」独立成行 + 分隔线（总开关
   层级感）；正向 / 反向路径并排一行（用语统一「正向 / 反向」）；双预览 +
   刷新钮 Group 成组；编辑器路径 + 复制插件路径 + 立即启动编辑器（原有
   手动钮，原名「立即启动编辑器（测试）」去后缀）+ 启动时自动拉起 Group
   成组。复制插件路径与生成页面板两处并存（同源 EXT_DIR 实时计算无漂移：
   页面板服务新用户引导、设置区服务分组收纳），elem_id 不同
   （feetag_copy_dir_settings）、JS 同一委托函数处理两处。

v1.4.19（X-174 真机验收实锤修复，用户即将把直发做成编辑器侧一键按钮）：
① **直发路径同图双落盘 + pass 双计根除**——X-174 实证一次直发生成 =
   featag_out 两张 md5 逐字节相同图（同 seed）+ pass +2、日志恰一次注入
   一条回传。根因：API 生成与页面生成同走全部脚本钩子——before_process
   （API 处理线程）注入 + pass+1 + busy，postprocess 落 featag_out/ + done；
   v1.4.18 的 _direct_generate 自己又落一份盘、计一次 pass = 双双重复。
   收口侧选直发线程（_direct_generate 不落盘、不计数、不写 done，只留
   开场 busy 与异常 error），全部留给钩子——与页面链路共用同一份钩子代码、
   页面行为零变化；不在钩子侧识别直发轮次跳过，因 _direct_running 布尔
   区分不了"直发在途时用户手动页面生成"，按它跳过会误伤页面轮次。
   正常轮次顺序：直发线程 busy → before_process 注入/计数/busy →
   postprocess 落盘/done（钩子在 HTTP 响应返回前同步跑完）→ 直发线程收尾。
② **launch_editor 脱离进程树**（X-174 环境坑ⓐ：webui.py stop 的
   taskkill /F /T 按快照父子链递归杀，编辑器作为 WebUI 直接子进程被连带
   杀——DETACHED_PROCESS / CREATE_NEW_PROCESS_GROUP 只隔离控制台信号，
   挡不住显式树杀）：Windows 分支改经 cmd /c start 中转（编辑器挂 cmd
   名下、cmd 随即退出，快照父子链断开，taskkill /T 不再沿链命中）并附
   CREATE_BREAKAWAY_FROM_JOB（宿主被放进 kill-on-close 的 Job 对象时脱出；
   Job 禁止 breakaway 则 CreateProcess 报错，自动回退旧直启方式，保底
   行为不劣化）。ComfyUI 版同步镜像（共享函数铁律）。
③ **launch_editor 畸形路径熔断**（用户桌面弹「找不到 '\\' 文件」后追加的
   防复发护栏）：cmd 中转送 shell 前，剥引号/空白后为空或仅由分隔符与
   点号组成（\、\\、/、.、.. 等）的路径一律不送 cmd / start，直接回
   「编辑器路径无效」error——shell 类调用收到空目标会触发系统级弹窗。

v1.4.20（X-177，用户拍板）：编辑器自荐路径 hint 契约——插件根新文件
editor.hint（单行文本 = 编辑器 exe 绝对路径，编辑器侧在连接「检测」时
幂等写入；编辑器永不改插件 config，单向传值）。_resolve_editor_path 解析
链插入 hint 档：pin 有效 > config 用户值有效 > **editor.hint 有效** >
同目录扫描最新版（v1.4.9 救援保持）> 原有兜底——用户值恒优先（设置了
就永不被 hint 覆盖），没设置则 hint 直接生效（立即启动 / 自动启动零配置
可用）。hint 档**不写回 config**（与扫描档不同，保持单向传值语义）。
读取容忍缺失 / 空 / 畸形（熔断守卫同款语义，无效 = 跳过该档）与编码
兜底（utf-8-sig → gb18030，取首行剥引号）。ComfyUI 版同步镜像（v1.4.10）。
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
# 后端直发模式（v1.4.18 Wave B）：bus.direct 存在 = 编辑器生成走后端直发
# （服务端自取 cmd 调 /sdapi/v1/txt2img 纯参数出图，不经页面、页面插件不
# 参与）；不存在 = 现行页面链路（默认）。文件契约同 bus.armed：空文件即可、
# 逐请求实时判定、删 = 立即回页面模式（编辑器 Wave A 按此文件决定开窗方式）。
DIRECT_PATH = os.path.join(EXT_DIR, "bus.direct")
# 页面状态快照/恢复（v1.4.18 Wave B）：页面链路模式的参数持久化——JS 采集/
# 回放（best-effort，gradio 版本敏感），服务端只做存取。放置
# bus.page_state.disabled = 关闭该功能（默认开）。
PAGE_STATE_PATH = os.path.join(EXT_DIR, "bus.page_state.json")
PAGE_STATE_OFF_PATH = os.path.join(EXT_DIR, "bus.page_state.disabled")
# 统一设置固定文件（v1.4.12）：settings.pin（JSON，任意 config 键子集）。config
# 全部六键会被旧页面内存值经 _save_config 反复回写冲掉（path / negative_path /
# editor_path 同族问题），pin 文件不在该写回链路上、不可被冲掉——文件里出现的
# 键以 pin 为准；UI 任一控件提交值经 _persist_pin_key 固化进该文件（路径键
# 空值 = 解除该键固定回退 config）。已被 .gitignore 排除。
SETTINGS_PIN_PATH = os.path.join(EXT_DIR, "settings.pin")
# 旧独立 pin（v1.4.10 机制，保留作迁移兼容读取层——老用户已有该文件）：存在且
# 非空时并入 _read_pin_overrides（settings.pin 同键优先于它）；UI 提交经
# _persist_pin_key 同步更新（若文件存在），防止"解除固定"被旧文件顶回。
NEGATIVE_PIN_PATH = os.path.join(EXT_DIR, "negative_path.pin")
POSITIVE_PIN_PATH = os.path.join(EXT_DIR, "positive_path.pin")
# 编辑器自荐路径（v1.4.20，X-177 契约）：插件根 editor.hint，单行文本 =
# 编辑器 exe 绝对路径（编辑器侧在连接「检测」时幂等写入；编辑器永不改
# 插件 config，单向传值）。解析链位次：pin 有效 > config 用户值有效 >
# editor.hint 有效 > 同目录扫描最新版（v1.4.9 救援保持）> 原有兜底——
# 用户值恒优先，hint 只在用户未设置 / 已失效时兜住（零配置可用）。
# 已被 .gitignore 排除。
EDITOR_HINT_PATH = os.path.join(EXT_DIR, "editor.hint")

PLUGIN_VERSION = "1.4.20"

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

# —— M-31b 超分区脚本注入表（v1.4.5 落地，v1.4.7 起契约与面板对齐）——
# Tiled Diffusion（multidiffusion 扩展，AlwaysVisible 常驻；tab=txt2img/img2img，
# 前缀式 uid 与两处后缀式特例并存——已对照源码逐条锁定）：
# params 契约（v1.4.7）：params.tiled.{enable,...} / params.tiledvae.{...} /
# params.usdu.{enable,...}（均为平铺节；usdu.enable=true 额外触发脚本下拉选中）
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

# 可选扩展组（v1.4.16 分组分线，盲测 P1-1）：组名 → 依赖的扩展脚本标题
# （A1111 脚本注册表 runner.scripts 里脚本对象的 title，已对照 H 盘 1.10.1
# 扩展源码逐条核实）。未装扩展的环境组内组件永不出现——旧版「全有或全无」
# 会让 apply 永不接线，故按注册表判定后整组裁剪。base / hires 为 A1111
# 原生组件，硬依赖（未到齐 = 页面仍在构建，继续等）。
_OPTIONAL_SECTIONS = {
    "tiled": "Tiled Diffusion",
    "tiledvae": "Tiled VAE",
    "usdu": "Ultimate SD upscale",
}
# (页, 组) → 扩展在场与否 的判定缓存（on_before_ui 时清空，UI 重建后重判）
_SECTION_STATE = {}


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
# 生成页总线连接引导面板（v1.4.17 方案变更：armed 开关 + 插件目录展示 +
# 复制按钮三件套，挂 txt2img 生成页本体而非设置手风琴）。A1111 把
# after_component 回调补丁进组件 __init__（modules/gradio_extensons.py），
# 回调触发时锚点组件的 with 上下文仍在栈上——在回调里创建组件 = 挂载在
# 锚点正后方（本插件面板的标准挂法）。锚点 = txt2img 结果图库。
_BUS_PANEL = {}            # 面板组件（armed_toggle / armed_status / plugin_dir / copy_dir_button）
_BUS_PANEL_BUILT = False   # 防重入：面板自身组件的创建经同一补丁会再触发本回调
_BUS_PANEL_ANCHOR = "txt2img_gallery"

# status.json 写入去重（内容未变化不重写）+ 生成计数
_status_lock = threading.Lock()
_status_snapshot = None
_gen_pass = 0
# apply 完成信号（v1.4.13）：_write_status 增量簿记——applied_ts 粘滞携带，
# _status_last 记录最近一次真实写入的状态机字段，_mark_applied 沿用它们只刷
# applied_ts（不干扰 busy/done 状态机，编辑器轮询语义不变）
_applied_ts = 0
_status_last = {"state": "idle", "images": None, "error": None, "adetailer": None}
# cmd.json 原子消费锁（v1.4.6）：fastapi 线程池并发处理 GET /feetag/bus/cmd，
# 锁 + 改名保证"读后即删"退路也只可能有一个赢家
_cmd_lock = threading.Lock()
# config 族文件写锁（v1.4.16）：config.json（_save_config 的全量写 /
# _resolve_editor_path 的读-改-写回）与 settings.pin / 旧独立 pin 的写入
# 可能来自生成线程（before_process）与 UI 线程（控件提交 / 启动按钮）并发，
# 串行化 + _atomic_write_text 原子落盘共同消灭半截文件
_config_lock = threading.Lock()


def bus_armed():
    """总线总开关：bus.armed 标志文件存在 = 启用。每次现查（一次 stat，代价可忽略），
    放置/删除文件即刻生效，无需重启 WebUI。"""
    return os.path.isfile(ARMED_PATH)


def set_bus_armed(enabled):
    """写 / 删 bus.armed 标志文件（v1.4.17，UI armed 开关落地）——与手工放置/
    删除**完全同一文件、同一语义**（服务端每次现查、浏览器 JS 15s 探测，勾选/
    取消即刻生效免重启），不另起第二套开关状态。返回 (是否成功, 消息)；
    写入走 _atomic_write_text（替换窗口抗并发），删除容忍文件本就不在。"""
    try:
        if enabled:
            _atomic_write_text(ARMED_PATH, "")  # 空文件 = 现行约定，内容从不被读
        else:
            try:
                os.remove(ARMED_PATH)
            except FileNotFoundError:
                pass
        return True, ("生成页总线已启用" if enabled else "生成页总线已关闭")
    except OSError as e:
        return False, f"bus.armed 操作失败：{e}"


def bus_direct():
    """直发模式开关（v1.4.18 Wave B）：bus.direct 标志文件存在 = 后端直发
    （编辑器生成经服务端调 /sdapi/v1/txt2img，不经页面）；不存在 = 现行页面
    链路（默认）。与 bus.armed 同款文件契约：每次现查（逐请求实时判定）、
    空文件即可、删 = 立即回页面模式。"""
    return os.path.isfile(DIRECT_PATH)


def set_bus_direct(direct_enabled):
    """写 / 删 bus.direct（v1.4.18，UI 直发开关落地）。返回 (是否成功, 消息)；
    语义与文件契约同 set_bus_armed 一套模式。"""
    try:
        if direct_enabled:
            _atomic_write_text(DIRECT_PATH, "")
        else:
            try:
                os.remove(DIRECT_PATH)
            except FileNotFoundError:
                pass
        return True, ("后端直发模式已启用" if direct_enabled else "页面链路模式已启用")
    except OSError as e:
        return False, f"bus.direct 操作失败：{e}"


def page_state_enabled():
    """页面状态快照/恢复功能开关（v1.4.18 Wave B）：默认开；插件目录放置
    bus.page_state.disabled 即关（JS 采集/回放与服务端存取全停）。"""
    return not os.path.isfile(PAGE_STATE_OFF_PATH)


# bus.page_state.json 写锁（POST 存档与文档读取的并发串行；原子写配套）
_page_state_lock = threading.Lock()


def _page_state_document():
    """GET /feetag/bus/page-state 的响应体（纯函数，离线可测）：功能开关 +
    两页已接线字段表的 elem_id 清单（JS 采集/回放的已知字段集；可选组
    tiled 系 elem_id 含在内——未装扩展时页面上不存在，JS 采集自然跳过）+
    已存状态（缺失 / 损坏 → None）。"""
    state = None
    try:
        with open(PAGE_STATE_PATH, "r", encoding="utf-8-sig") as f:
            loaded = json.load(f)
        if isinstance(loaded, dict):
            state = loaded
    except (OSError, ValueError):
        state = None
    return {
        "enabled": page_state_enabled(),
        "fields": {("img2img" if key else "txt2img"): [row[2] for row in _FIELD_TABLES[key]]
                   for key in (False, True)},
        "state": state,
    }


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


def _read_snapshot_prompt():
    """读 params.json 顶层 prompt 键（v1.4.15 方案 A：快照锁定提示词）。

    「替换并生成」的参数快照额外携带触发时刻的完整提示词文本（编辑器侧 formatList(positive)）。
    返回非空 str = 本次注入的 tag 文本用它替代 prompt.txt 实时读取——排队期间后续替换/库组
    轮换改写 txt 不再吃掉先发出的快照（修「挑着生成/模型丢失」）。
    生命周期论证：params.json 只被编辑器覆盖写、无删除方（cmd.json 才是取走即删的那个）；
    生成轮内时序 = 编辑器写 params+cmd → 页面 JS 点 apply（params 回填 UI）→ 点生成 →
    before_process（此处读取）——下一轮 params 要等上一张结算后才发出，轮内无覆盖窗口。
    无键 / 空白 / 非字符串 / 文件缺失损坏 → 返回 ""（调用方回落读 txt）。
    """
    params = _read_bus_json(PARAMS_PATH)
    if not isinstance(params, dict):
        return ""
    value = params.get("prompt")
    return value.strip() if isinstance(value, str) else ""


def _consume_cmd():
    """原子取走一条 cmd 指令（v1.4.6）：先重命名到临时名再读删。

    多浏览器 / 多页签并存时，每条命令全局恰有一个消费者能取到：重命名是同目录
    同卷的原子操作，改名成功的请求独得该命令，其余请求拿到 FileNotFoundError
    → 返回 None（端点转 404）。改名被占用（旧版页面经 /file= 读取的瞬间）时
    退回"读后即删"，服务端 _cmd_lock 串行化 + 客户端 lastTs 去重双保险。
    半截 JSON（编辑器写入瞬间被取走）短暂重试后仍失败则消费丢弃并打日志，
    防止坏文件反复触发；返回 dict 或 None，不抛错。
    """
    tmp = f"{CMD_PATH}.consuming-{os.getpid()}-{threading.get_ident()}-{time.time_ns()}"
    with _cmd_lock:
        try:
            os.rename(CMD_PATH, tmp)
        except FileNotFoundError:
            return None  # 无命令 / 已被其他消费者取走：常态
        except OSError:
            # 改名失败（文件被短暂占用）：退回读后即删
            data = _read_bus_json(CMD_PATH)
            try:
                os.remove(CMD_PATH)
            except OSError:
                pass
            return data if isinstance(data, dict) else None
        data = None
        for attempt in range(3):  # 编辑器写文件的一瞬可能读到半个 JSON，短暂重试
            try:
                with open(tmp, "r", encoding="utf-8-sig") as f:
                    data = json.load(f)
                break
            except (OSError, ValueError):
                if attempt < 2:
                    time.sleep(0.15)
        try:
            os.remove(tmp)
        except OSError:
            pass
        if data is None:
            _log("cmd.json 内容损坏，已消费丢弃（编辑器侧可重新触发）")
        return data if isinstance(data, dict) else None


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


def _write_status(state, images=None, error=None, adetailer=None, applied_ts=None):
    """写 status.json（state/pass/images/error/ts + choices [+ adetailer] [+ applied_ts]）。

    内容签名（state/pass/images/error/adetailer/choices/applied_ts）未变化时不重写
    ——客户端高频轮询的只是不再变化的文件，磁盘零增长；ts 仅在真实写入时刷新。
    applied_ts（v1.4.13）：apply 完成毫秒时间戳，粘滞保留最近值（后续常规状态
    写入原样携带，JS 的信号判据 applied_ts > cmd.ts 不受状态刷新冲掉）；显式
    传参（_mark_applied）即刷新。任何写入异常只打日志，绝不影响生成。
    """
    global _status_snapshot, _applied_ts, _status_last
    if applied_ts:
        _applied_ts = int(applied_ts)
    choices = _publish_choices()
    signature = json.dumps([state, _gen_pass, list(images or []), error, adetailer, choices, _applied_ts],
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
            "applied_ts": _applied_ts,
        }
        if adetailer is not None:
            payload["adetailer"] = adetailer
        try:
            # v1.4.16 原子落盘：编辑器高频轮询本文件，临时文件 + replace 消灭半截读
            _atomic_write_text(STATUS_PATH, json.dumps(payload, ensure_ascii=False))
            _status_snapshot = signature
            _status_last = {"state": state, "images": images,
                            "error": error, "adetailer": adetailer}
        except OSError as e:
            _log(f"status.json 写入失败：{e}")


def _mark_applied():
    """apply 事件完成信号（v1.4.13）：status.json 刷新 applied_ts 为当前毫秒时间戳，
    状态机字段（state/pass/images/error/adetailer）沿用最近一次真实值——不干扰
    busy/done 状态机与编辑器轮询语义。

    浏览器 JS 点 apply 后轮询 /feetag/bus/status，见 applied_ts > cmd.ts 即本次
    参数已在服务端算完回包，随即（小驻留后）点生成，取代旧版固定盲等 700ms；
    JS 侧等不到信号（旧版脚本 / 字段缺失 / 写失败）时超时回落旧盲等，此处零
    额外风险。调用点：_make_apply_handler 的 handler 末尾（armed 路径，含
    params 空 / 损坏的 no-op 完成——事件完成本身就是信号）。
    """
    last = _status_last
    _write_status(last["state"], images=last["images"], error=last["error"],
                  adetailer=last["adetailer"], applied_ts=int(time.time() * 1000))


# ---------------------------------------------------------------------------
# 总线看门狗（v1.4.16，盲测 P1-4：生成异常时 status 永久卡 busy 的 error 写出）
# ---------------------------------------------------------------------------

# 巡检周期（秒）与写 error 所需的连续异常判定次数（2 次 × 5s ≈ 10s 宽限——
# 判定依据是任务活性而非超时，慢生成不受影响，宽限只防瞬态误读）
_WATCHDOG_INTERVAL = 5.0
_WATCHDOG_TRIES = 2
_watchdog_started = False


def _bus_watchdog_tick():
    """看门狗单次判定：status 为 busy 但 A1111 已无运行中的生成任务
    （shared.state.job 已清空，而 postprocess 未被调用——生成异常路径不触发
    postprocess，状态永久卡 busy）→ 返回 True。

    判定依据是任务活性而非耗时：数小时的慢生成（大图 tiled 超分）期间
    state.job 恒非空，不会被误伤；判定链路不可用（modules.shared 导入失败 /
    属性缺失，如离线 mock）返回 False——宁可不写 error，编辑器侧自有
    120s 超时兜底，看门狗只是把兜底提前并给出明确 error 消息。
    v1.4.18：直发生成在途（_direct_running）直接不判——API 路径的
    state.job 在另一线程更新（api.py L475 state.begin），护栏双保险。"""
    if not bus_armed() or _status_last.get("state") != "busy":
        return False
    if _direct_running:
        return False  # 直发生成在途（busy 由直发执行线程写入、done 由其收尾）
    try:
        from modules import shared
        job = getattr(shared.state, "job", None)
    except Exception:
        return False
    if job is None:
        return False
    return not job


def _bus_watchdog_step(consecutive):
    """看门狗单步推进（供循环线程与离线直测）：入参 = 已连续判定的异常次数，
    返回新的连续计数；达到 _WATCHDOG_TRIES 时写 error 并归零。自身异常绝不
    外抛（看门狗不得影响任何生成行为）。"""
    try:
        if _bus_watchdog_tick():
            consecutive += 1
            if consecutive >= _WATCHDOG_TRIES:
                _write_status("error", error="生成任务异常结束（postprocess 未被调用，"
                                              "看门狗检测：总线 busy 但已无运行中的生成任务）")
                consecutive = 0
        else:
            consecutive = 0
    except Exception:
        consecutive = 0
    return consecutive


def _bus_watchdog_loop():
    consecutive = 0
    while True:
        time.sleep(_WATCHDOG_INTERVAL)
        consecutive = _bus_watchdog_step(consecutive)


def _start_bus_watchdog():
    """启动总线看门狗守护线程（每进程一次，_on_app_started 调用；Reload UI
    会再次触发 app_started，经 _watchdog_started 幂等防重）。"""
    global _watchdog_started
    if _watchdog_started:
        return
    _watchdog_started = True
    threading.Thread(target=_bus_watchdog_loop, name="feetag-bus-watchdog",
                     daemon=True).start()


# ---------------------------------------------------------------------------
# 后端直发链路（v1.4.18 Wave B，bus.direct 存在时）
# ---------------------------------------------------------------------------

_direct_running = False          # 直发生成在途（看门狗护栏，见 _bus_watchdog_tick）
_direct_poller_started = False
DIRECT_POLL_INTERVAL = 0.5       # 直发消费线程节拍（秒）——未直发时只做两次 stat
_DIRECT_API_TIMEOUT = 900        # 本机 API 调用超时（大图 tiled 超分可达十分钟级）


def _direct_payload(params):
    """params.json（总线平铺契约）→ /sdapi/v1/txt2img payload（纯函数，离线可测）。

    边界（直发模式语义，用户拍板）：只映射 base / hires 纯参数键——直发不经
    页面，页面脚本插件本就不参与，tiled / tiledvae / usdu / adetailer_infotext
    等**页面插件键一律不映射**（编辑器只接管到 params 键范围）。API 字段名与
    总线语义键对照 H 盘 A1111 1.10.1 modules/api（Txt2ImgRequest 由
    StableDiffusionProcessingTxt2Img 生成，同名直传；hires 特例：enable→
    enable_hr、steps→hr_second_pass_steps、denoise→denoising_strength）。
    prompt / negative_prompt 恒空串：词条注入走 before_process 钩子读
    prompt.txt / params.prompt 快照（API 路径同样过全部脚本钩子，api-retest
    R1 实证：payload 留空时注入链是提示词唯一来源）。"""
    payload = {"prompt": "", "negative_prompt": ""}
    base = params.get("base") if isinstance(params.get("base"), dict) else {}
    for key in ("width", "height", "seed", "sampler_name", "scheduler",
                "steps", "cfg_scale", "batch_size", "n_iter"):
        if key in base and base[key] is not None:
            payload[key] = base[key]
    hires = params.get("hires") if isinstance(params.get("hires"), dict) else {}
    if hires.get("enable") is True:
        payload["enable_hr"] = True
        for src, dst in (("upscaler", "hr_upscaler"), ("hr_scale", "hr_scale"),
                         ("steps", "hr_second_pass_steps"),
                         ("denoise", "denoising_strength")):
            if src in hires and hires[src] is not None:
                payload[dst] = hires[src]
    return payload


def _direct_generate(cmd):
    """直发执行一条 generate 命令（v1.4.18，bus.direct 存在时由消费线程调用）：
    读 params.json → 映射 → 本机调 /sdapi/v1/txt2img（进程内经自身端口，
    需 WebUI 以 --api 启动，否则明确 error）；异常写 error。
    生成期间置 _direct_running（看门狗护栏）。

    v1.4.19 收口（X-174 真机实锤：直发一轮 = featag_out 两张 md5 相同图 +
    pass +2）：API 生成与页面生成同走全部脚本钩子——before_process（API
    处理线程内）注入 + pass+1 + busy，postprocess 落 featag_out/ + done。
    本函数**不落盘、不计数、不写 done**（v1.4.18 自落一份自计一次即双双
    重复的根因），只保留开场 busy（cmd 消费即反馈）与异常路径 error（API
    层失败时钩子不会运行，状态机不能悬停在上一轮 done）。收口选直发侧而非
    钩子侧：_direct_running 布尔区分不了"直发在途时用户手动页面生成"，
    钩子按它跳过会误伤页面轮次；直发侧不写则与页面链路共用同一份钩子代码，
    页面行为零变化。"""
    global _direct_running
    if cmd.get("page") not in (None, "txt2img"):
        _log(f"直发模式暂不支持该目标页：{cmd.get('page')}")
        _write_status("error", error=f"直发模式暂不支持 {cmd.get('page')} 命令"
                                     "（直发只做纯参数 txt2img，img2img 需要源图）")
        return
    params = _read_bus_json(PARAMS_PATH)
    params = params if isinstance(params, dict) else {}
    payload = _direct_payload(params)
    _direct_running = True
    try:
        _write_status("busy")
        import urllib.request
        from modules import shared
        port = getattr(shared.cmd_opts, "port", None) or 7860
        subpath = (getattr(shared.cmd_opts, "subpath", "") or "").strip("/")
        url = (f"http://127.0.0.1:{port}/{subpath}/sdapi/v1/txt2img" if subpath
               else f"http://127.0.0.1:{port}/sdapi/v1/txt2img")
        req = urllib.request.Request(
            url, data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=_DIRECT_API_TIMEOUT) as r:
            resp = json.loads(r.read().decode("utf-8"))
        # 响应返回时 API 处理线程的 postprocess 已落盘 featag_out/ 并写 done、
        # before_process 已注入并计数（钩子在 HTTP 响应之前同步跑完）。
        if not (resp.get("images") or []):
            raise RuntimeError("API 未返回任何图像")
        _log("直发生成完成（成图由 postprocess 钩子回传 featag_out/）")
    except Exception as e:  # noqa: BLE001 - 直发兜底，任何异常都写 error 不外抛
        _log(f"直发生成失败：{e}")
        try:
            _write_status("error", error=f"直发生成失败：{e}")
        except Exception:
            pass
    finally:
        _direct_running = False


def _direct_poller_loop():
    """直发消费线程（v1.4.18）：armed + direct 双开时以 0.5s 节拍原子取 cmd 并
    直发执行。与页面 JS 的互斥：直发模式下 /feetag/bus/cmd 端点一律 404 且
    不消费（见 _bus_cmd）——命令只可能被本线程取到；页面模式下本线程不取。
    模式翻转瞬间的在途命令至多按旧模式执行一条（_consume_cmd 原子性保证
    不会双执行），编辑器重发即可。线程自身异常永不外抛、永不退出。"""
    while True:
        time.sleep(DIRECT_POLL_INTERVAL)
        try:
            if not (bus_armed() and bus_direct()):
                continue
            cmd = _consume_cmd()
            if cmd and cmd.get("action") == "generate":
                _direct_generate(cmd)
        except Exception:
            continue  # 消费线程永不倒


def _start_direct_poller():
    global _direct_poller_started
    if _direct_poller_started:
        return
    _direct_poller_started = True
    threading.Thread(target=_direct_poller_loop, name="feetag-direct-poller",
                     daemon=True).start()


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


def _section_available(is_img2img, section):
    """可选扩展组在场判定（v1.4.16 分组分线）：查 A1111 脚本注册表——两个
    runner（scripts_txt2img / scripts_img2img）的 scripts 列表按脚本 title
    精确匹配；注册表在 UI 组件创建之前已填充，判定与组件创建时序无关。
    结果按 (页, 组) 缓存到 _SECTION_STATE（_on_before_ui 清空）。注册表不可
    读（A1111 接口变化 / 离线 mock）按在场处理——保持等待而非裁剪，宁可不
    接线也不静默丢组（此时退化为旧版全有或全无行为，不劣化）。"""
    key = (is_img2img, section)
    if key not in _SECTION_STATE:
        title = _OPTIONAL_SECTIONS[section]
        found = False
        try:
            from modules import scripts as a1111_scripts
            for attr in ("scripts_txt2img", "scripts_img2img"):
                runner = getattr(a1111_scripts, attr, None)
                for script in getattr(runner, "scripts", None) or []:
                    if getattr(script, "title", None) == title:
                        found = True
                        break
                if found:
                    break
        except Exception:
            found = True
        _SECTION_STATE[key] = found
    return _SECTION_STATE[key]


def _try_wire_page(is_img2img):
    """延迟接线：该页目标组件捕获齐全 + 按钮已创建时，注册 apply/ADetailer 事件。

    幂等（每页每轮 UI 只接一次）；由 _on_after_component 与 ui() 末尾共同触发。
    v1.4.16 分组分线（盲测 P1-1 修复）：旧版要求该页字段表全部到齐才接线——
    未安装 Tiled Diffusion / Ultimate SD upscale 扩展的环境（演示机、其他
    接收方）可选组组件永不出现，apply 永不接线且零日志，JS 仍照常点生成钮，
    参数静默停留在界面旧值。现 base / hires（A1111 原生组件）保持硬依赖
    ——未到齐说明页面仍在构建，继续等；可选组（tiled / tiledvae / usdu）
    经 _section_available 判定：未安装则整组裁剪并打日志，已安装则继续等
    其组件创建（扩展脚本的 ui() 可能晚于本脚本）。裁剪只影响回填的输出
    组件列表，apply handler 对 params 对应节本就「缺键跳过」，v1.4.7 平铺
    契约不变。"""
    if _WIRED.get(is_img2img) or is_img2img not in _BUS_BUTTONS:
        return
    rows = _FIELD_TABLES[is_img2img]
    unavailable = [s for s in _OPTIONAL_SECTIONS
                   if any(r[0] == s for r in rows)
                   and not _section_available(is_img2img, s)]
    rows = [r for r in rows if r[0] not in unavailable]
    missing = [e for _s, _k, e, _t in rows if e not in _UI_COMPONENTS]
    usdu_active = is_img2img and "usdu" not in unavailable
    if usdu_active and len(_SCRIPT_LISTS) < 2:
        missing.append("script_list")  # USDU 选中依赖的脚本下拉（两页各一份）尚未捕获齐
    if missing:
        return
    tab = "img2img" if is_img2img else "txt2img"
    targets = []
    if usdu_active:
        targets.append(("usdu", "_select", _SCRIPT_LISTS[1], "scriptsel"))
    targets += [(section, key, _UI_COMPONENTS[elem_id], kind)
                for section, key, elem_id, kind in rows]
    ad_fields = _AD_FIELDS.get(is_img2img) or []
    # ADetailer 回填并入同一事件（单事件双段更新）——独立第二按钮的接线在部分
    # 页面不可靠（config 实测 txt2img AD 依赖缺失），合并后彻底消除该变量
    outputs = [t[2] for t in targets] + [comp for comp, _key in ad_fields]
    apply_btn = _BUS_BUTTONS[is_img2img]
    apply_btn.click(fn=_make_apply_handler(targets, ad_fields), inputs=[],
                    outputs=outputs, show_progress=False, queue=False)
    _WIRED[is_img2img] = True
    if unavailable:
        skipped = "、".join(f"params.{s}（扩展「{_OPTIONAL_SECTIONS[s]}」未安装）"
                            for s in unavailable)
        _log(f"生成页（{tab}）总线已接线：apply {len(targets)} 项输出 + "
             f"ADetailer 回填 {len(ad_fields)} 项（同一事件）；整组跳过 {skipped}")
    else:
        _log(f"生成页（{tab}）总线已接线：apply {len(targets)} 项输出 + "
             f"ADetailer 回填 {len(ad_fields)} 项（同一事件）")


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
                # USDU 特例（v1.4.7 契约修正）：params.usdu.enable=true → 选中该脚本。
                # 旧实现读 params["usdu"]["usdu"] 双重嵌套且与面板的 params.scripts.usdu
                # 互不匹配，USDU 参数注入从未实际生效；现契约平铺：usdu 节出现且
                # enable=true 才选中（OFF / 缺节 = 不选，不影响界面既有选择）
                want = isinstance(section_data, dict) and section_data.get("enable") is True
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
        else:
            # v1.4.6 修复：无 AD 文本时也必须补齐 no-op 更新——事件输出数是
            # targets + ad_fields，缺段会让 gradio 抛 "didn't receive enough
            # output values" 并把前段（常规参数）的更新一并丢弃（apply 静默失效）
            updates.extend(gr.update() for _ in ad_fields)
        # v1.4.13 apply 完成信号：handler 跑完（含 params 空/损坏的 no-op 完成）
        # 即刷新 status.json 的 applied_ts，JS 见信号立即进生成点击（取代盲等）
        _mark_applied()
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


def _build_bus_panel():
    """生成页（txt2img）总线连接引导面板（v1.4.17 方案变更终稿）：
    armed 开关 + 插件目录展示 + 复制按钮三件套成组，挂生成页本体（锚点组件
    之后）而非设置手风琴——新用户在生成页即可完成全部连接操作。

    防重入：A1111 把 after_component 补丁进组件 __init__，面板自身组件的
    创建会再触发 _on_after_component——_BUS_PANEL_BUILT 先置位再建件。
    幂等：每轮 UI 只建一次（Reload UI 经 _on_before_ui 复位后随锚点重建）。"""
    global _BUS_PANEL_BUILT
    if _BUS_PANEL_BUILT:
        return
    _BUS_PANEL_BUILT = True
    with gr.Accordion("生成页总线 · 连接引导（编辑器触发生成）", open=True):
        armed_toggle = gr.Checkbox(
            # 初始态 = 标志文件现状回显（bus_armed() 每次现查；编辑器远程写删
            # 文件后，页面开关显示由 featag_generate.js 的探测链路 ≤15s 同步）
            value=bus_armed(),
            label="启用生成页总线（允许编辑器触发生成）",
            elem_id="feetag_bus_armed",
        )
        armed_status = gr.HTML(value=_armed_hint_html())
        direct_toggle = gr.Checkbox(
            # 勾选 = 页面链路（bus.direct 删除，默认）；取消勾选 = 后端直发
            # （写 bus.direct）。文件契约同 bus.armed；显示同步同 armed 机制。
            value=not bus_direct(),
            label="使用网页端插件的页面链路（其他插件照常生效）",
            elem_id="feetag_bus_direct",
        )
        direct_status = gr.HTML(value=_direct_hint_html())
        with gr.Row():
            plugin_dir = gr.HTML(value=_plugin_dir_display(),
                                 elem_id="feetag_plugin_dir")
            copy_dir_button = gr.Button(value="复制插件目录",
                                        elem_id="feetag_copy_dir", scale=0)
    # 开关勾/取消 = 写/删现有标志文件（同一文件同一语义，服务端每次请求实时
    # 判定，即刻生效免重启；gradio 事件不级联，JS 探测的程序化显示同步触发
    # handler 时写的是同一状态，幂等无害）
    armed_toggle.change(fn=_armed_toggle, inputs=[armed_toggle],
                        outputs=[armed_status])
    direct_toggle.change(fn=_direct_toggle, inputs=[direct_toggle],
                         outputs=[direct_status])
    _BUS_PANEL.update(armed_toggle=armed_toggle, armed_status=armed_status,
                      direct_toggle=direct_toggle, direct_status=direct_status,
                      plugin_dir=plugin_dir, copy_dir_button=copy_dir_button)


def _on_after_component(component, **kwargs):
    """捕获生成页参数组件（elem_id 在选择器表内的），供 apply 事件作 outputs；
    锚点组件（txt2img 图库）出现时在其上下文内挂载总线连接引导面板。"""
    try:
        elem_id = getattr(component, "elem_id", None)
        if elem_id in _WANTED_ELEM_IDS:
            _UI_COMPONENTS[elem_id] = component
        elif elem_id == _USDU_SCRIPT_LIST_ID:
            _SCRIPT_LISTS.append(component)  # 创建序：0=txt2img, 1=img2img
        elif elem_id == _BUS_PANEL_ANCHOR:
            _build_bus_panel()  # 回调发生在锚点 with 上下文内 → 面板挂锚点正后方
    finally:
        try:
            _try_wire_page(False)
            _try_wire_page(True)
        except Exception:
            pass


def _log(message):
    print(f"[prompt-helper] {message}")


def _atomic_write_text(path, text):
    """原子写文本文件（v1.4.16，共享函数）：先写同目录临时文件再 os.replace
    覆盖目标（同卷原子操作，Windows / Linux 均原子）。读者（编辑器面板轮询 /
    下次 _load_config）只会看到完整的旧版或新版内容，不会读到半截；进程在
    写入中途崩溃也只会留下临时文件、目标保持旧版。

    Windows 细节：目标正被并发读者持有句柄时 replace 可能短暂 PermissionError
    （CPython 的读取端不带 FILE_SHARE_DELETE，而 Rust / JS 读端带、不受影响）
    ——读取窗口只有微秒级，短暂重试后仍失败才抛 OSError（调用方兜底、目标
    保持旧版），临时文件尽力清理。"""
    tmp = f"{path}.tmp-{os.getpid()}-{threading.get_ident()}"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
        for attempt in range(5):
            try:
                os.replace(tmp, path)
                return
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.02)
    except OSError:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def _load_config():
    cfg = dict(DEFAULT_CONFIG)
    loaded = None
    for attempt in range(3):  # v1.4.16：原子替换窗口内 open 可能被短暂拒绝，重读即愈
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8-sig") as f:
                loaded = json.load(f)
            break
        except FileNotFoundError:
            break  # 配置缺失（新装机常态）：默认值即可，不重试不拖延
        except (OSError, ValueError):
            if attempt < 2:
                time.sleep(0.05)
    if isinstance(loaded, dict):
        for key in CONTROL_KEYS:
            if key in loaded:
                cfg[key] = loaded[key]
    for key in ("enabled", "merge_lines", "autostart"):
        cfg[key] = bool(cfg[key])
    for key in ("path", "negative_path", "editor_path"):
        cfg[key] = str(cfg[key] or "")
    return cfg


def _save_config(cfg):
    data = {key: cfg.get(key, DEFAULT_CONFIG[key]) for key in CONTROL_KEYS}
    try:
        # v1.4.16 原子写 + 写锁：并发保存（生成线程 / UI 线程）不再有半截或交错
        with _config_lock:
            _atomic_write_text(CONFIG_PATH, json.dumps(data, ensure_ascii=False, indent=2))
    except OSError:
        _log("设置写入失败（不影响本次注入）")


def _normalize_path(path):
    path = (path or "").strip().strip('"').strip("'")
    return os.path.expandvars(os.path.expanduser(path))


def _read_pin_file(path):
    """读单行路径 pin 文件（旧独立 pin 机制，v1.4.10）。存在且非空 → 返回路径
    （去引号/首尾空白 + expandvars/expanduser 容错，utf-8 / GBK 双编码兜底）；
    不存在 / 空文件 / 读取失败 → 返回 ""。"""
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            with open(path, "r", encoding=encoding) as f:
                return _normalize_path(f.read())
        except (OSError, ValueError):
            continue
    return ""


def _read_negative_pin():
    """读 negative_path.pin（v1.4.10 旧机制读取接口原样保留，现为
    _read_pin_overrides 的兼容层——老用户已有该文件）。"""
    return _read_pin_file(NEGATIVE_PIN_PATH)


def _read_pin_json(path):
    """读 JSON pin 文件（settings.pin）。缺失 / 损坏 / 非对象 → 返回 {}，
    不抛错；utf-8 / GBK 双编码兜底（与单行 pin 同款容错）。"""
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            with open(path, "r", encoding=encoding) as f:
                data = json.load(f)
        except (OSError, ValueError):
            continue
        return data if isinstance(data, dict) else {}
    return {}


def _read_pin_overrides():
    """读统一固定文件 settings.pin（JSON，任意 config 键子集）并合并旧独立 pin。

    读取优先级（逐键）：settings.pin > 旧独立 pin（negative_path.pin /
    positive_path.pin，迁移兼容）> config（调用方回退，零迁移）。每次现读
    （一次 stat+read），放置/修改/删除即刻生效，无需重启 WebUI。返回仅含
    CONTROL_KEYS 内的键；路径键空值视作未固定（不遮蔽旧独立 pin / config），
    布尔键原样透传（调用方按键语义强转）。
    """
    overrides = {}
    for pin_path, key in ((NEGATIVE_PIN_PATH, "negative_path"),
                          (POSITIVE_PIN_PATH, "path")):
        value = _read_pin_file(pin_path)
        if value:
            overrides[key] = value
    for key, value in _read_pin_json(SETTINGS_PIN_PATH).items():
        if key not in CONTROL_KEYS:
            continue
        if key in ("path", "negative_path", "editor_path"):
            value = _normalize_path(str(value or ""))
            if not value:
                continue  # 空值 = 该键未固定，勿遮蔽旧独立 pin / config
        overrides[key] = value
    return overrides


def _effective_config():
    """config 经 settings.pin 逐键覆盖后的有效配置（v1.4.12 六键统一）。

    UI 初始值 / 自动启动等消费点统一走它：pin 里出现的键以 pin 为准（含旧
    独立 pin 兼容层），缺席键保持 config 原值；类型强转与 _load_config 同款。
    """
    cfg = _load_config()
    overrides = _read_pin_overrides()
    for key in CONTROL_KEYS:
        if key in overrides:
            cfg[key] = overrides[key]
    for key in ("enabled", "merge_lines", "autostart"):
        cfg[key] = bool(cfg[key])
    for key in ("path", "negative_path", "editor_path"):
        cfg[key] = str(cfg[key] or "")
    return cfg


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


# 编辑器 exe 自动探测：编辑器发版 exe 改名（如 feetaghelper-v2.7.5.exe
# → v2.8.0）会让 config 硬编码的完整路径失效，故按文件名版本号在同目录自动接管。
# 版本号解析为元组比较（v2.7.5 → (2, 7, 5)，兼容 v 前缀与任意多段数字）。
EDITOR_EXE_RE = re.compile(r"^feetaghelper-v(\d+(?:\.\d+)*)\.exe$", re.IGNORECASE)


def _editor_exe_version(filename):
    """从编辑器 exe 文件名解析版本号元组（feetaghelper-v2.7.5.exe → (2, 7, 5)）。
    不符合 feetaghelper-v<数字串>.exe 命名（含无版本号、非 .exe）返回 None。"""
    match = EDITOR_EXE_RE.match(filename)
    if not match:
        return None
    return tuple(int(part) for part in match.group(1).split("."))


def _read_editor_hint():
    """读插件根 editor.hint（v1.4.20 编辑器自荐路径契约）：单行 exe 绝对
    路径，取首行、剥引号与空白。任何读取异常都不外抛；文件缺失 / 空 /
    畸形（剥引号空白后为空或仅由分隔符点号组成——熔断守卫同款语义）/
    指向不存在的文件，一律返回 ""（= 该档无效，解析链跳过继续走扫描
    兜底）。编码兜底 utf-8-sig → gb18030。"""
    try:
        with open(EDITOR_HINT_PATH, "rb") as f:
            raw = f.read(4096)
    except OSError:
        return ""
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            text = raw.decode("gb18030")
        except UnicodeDecodeError:
            return ""
    lines = text.splitlines()
    line = lines[0].strip().strip("\"'").strip() if lines else ""
    if not line or all(ch in "\\/. " for ch in line) or not os.path.isfile(line):
        return ""
    return line


def _resolve_editor_path(configured):
    """解析编辑器 exe 路径（v1.4.20 起五档链）：① configured 存在 → 原样
    返回（config 用户值有效，恒优先）；② editor.hint 有效 → 直接用
    （编辑器自荐路径，不写回 config——编辑器永不改插件 config，单向传值）；
    ③ configured 已失效（编辑器发版 exe 改名）→ 在 configured 所在目录扫描
    feetaghelper-v*.exe，按版本号元组取最新者返回，并把解析结果写回 config
    （下次 UI / 自动启动直接显示新路径）；④ 同目录无任何候选 → 返回原值，
    保持"文件不存在"的原有报错行为。pin 档在调用方 launch_editor 先于本
    函数应用（位次最高）。"""
    path = _normalize_path(configured)
    if path and os.path.isfile(path):
        return path
    # v1.4.20 editor.hint 档：用户未设置 / 已失效时编辑器自荐路径兜住
    # （立即启动 / 自动启动零配置可用）；不写回 config，每次解析照走全链。
    hint = _read_editor_hint()
    if hint:
        return hint
    if not path:
        return path
    try:
        names = os.listdir(os.path.dirname(path))
    except OSError:
        return path
    best_version, best_name = None, ""
    for name in names:
        version = _editor_exe_version(name)
        if version is not None and (best_version is None or version >= best_version):
            best_version, best_name = version, name
    if best_version is None:
        return path
    resolved = os.path.join(os.path.dirname(path), best_name)
    try:  # 解析结果写回 config（读原文件 → 只改 editor_path → 原样写回；
          # v1.4.16 起持 _config_lock + 原子落盘，与其他 config 写入并发亦无半截）
        with _config_lock:
            with open(CONFIG_PATH, "r", encoding="utf-8-sig") as f:
                data = json.load(f)
            if isinstance(data, dict) and data.get("editor_path") != resolved:
                data["editor_path"] = resolved
                _atomic_write_text(CONFIG_PATH, json.dumps(data, ensure_ascii=False, indent=2))
                _log(f"editor_path 已失效，自动探测到最新版本并写回：{resolved}")
    except (OSError, ValueError):
        pass  # 写回失败不影响本次启动
    return resolved


def launch_editor(editor_path):
    """启动外部词条编辑器（独立进程，关闭 WebUI 不会连带关闭它）。返回 (是否成功, 消息)。

    v1.4.12：settings.pin 的 editor_path 键存在时以 pin 为准——先于
    _resolve_editor_path 应用（在 config / UI 值之上），失效自动探测照常接管：
    探测结果写回 config，pin 原值保持，每次启动都经探测重解析，编辑器发版
    exe 改名不断链（v1.4.9 机制不受影响）。
    """
    overrides = _read_pin_overrides()
    if overrides.get("editor_path"):
        editor_path = overrides["editor_path"]
    editor_path = _resolve_editor_path(editor_path)
    if not editor_path:
        return False, "未设置编辑器路径"
    # v1.4.19 熔断（用户桌面弹「找不到 '\\' 文件」后追加）：空 / 畸形路径
    # （剥引号与空白后为空，或仅由分隔符 / 点号组成，如 \、\\、/、.、..）
    # 一律不送 cmd / start——shell 类调用收到空目标会触发系统级「找不到
    # 文件」弹窗。放在 isfile 判定之前：畸形路径零 shell 交互、零歧义回执。
    stripped = editor_path.strip().strip("\"'").strip()
    if not stripped or all(ch in "\\/. " for ch in stripped):
        return False, "编辑器路径无效：" + editor_path
    if not os.path.isfile(editor_path):
        return False, "文件不存在：" + editor_path

    exe_name = os.path.basename(editor_path)
    if _is_process_running(exe_name):
        return True, f"{exe_name} 已在运行，跳过启动"

    flags = (subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
             if os.name == "nt" else 0)
    # v1.4.19（X-174 环境坑ⓐ）：DETACHED_PROCESS / CREATE_NEW_PROCESS_GROUP 只
    # 隔离控制台信号，挡不住显式树杀（外部 stop 按快照父子链递归强杀，如
    # webui.py stop 的 taskkill /F /T——编辑器作为宿主直接子进程被连带杀）。
    # Windows 下两道补强：① 经 cmd /c start 中转——编辑器挂到 cmd 名下、cmd
    # 随即退出，快照父子链断开，树杀不再沿链命中（启动后约 0.1s 起免疫）；
    # ② 附 CREATE_BREAKAWAY_FROM_JOB——宿主被启动器放进 kill-on-close 的
    # Job 对象时子进程脱出 Job（Job 拒绝 breakaway 则 CreateProcess 报错，
    # 裸旗标重试一次）；cmd 中转彻底不可用再回退直启，保底与旧版一致。
    if os.name == "nt":
        breakaway = getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0)
        for extra in (breakaway, 0):  # 先带 breakaway；Job 拒绝则裸旗标重试
            try:
                subprocess.Popen(
                    ["cmd", "/c", "start", "", editor_path],
                    cwd=os.path.dirname(editor_path),
                    creationflags=flags | extra, close_fds=True)
                return True, "已启动：" + editor_path
            except OSError:
                continue
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


def _pin_fixed_hint(key, overrides):
    """预览状态行的固定标记（v1.4.12）：settings.pin 优先显示，经旧独立 pin
    固定则显示对应文件名（v1.4.10 文案保留）；未固定返回空。"""
    if key not in overrides:
        return ""
    if key in _read_pin_json(SETTINGS_PIN_PATH):
        return "（已由 settings.pin 固定）"
    legacy = {"path": "positive_path.pin", "negative_path": "negative_path.pin"}.get(key)
    return f"（已由 {legacy} 固定）" if legacy else ""


def _preview(path, negative_path, merge_lines):
    parts = []

    # settings.pin 统一固定（v1.4.12）：逐键覆盖入参（UI 值），状态行带固定标记
    overrides = _read_pin_overrides()
    if overrides.get("path"):
        path = overrides["path"]
    if overrides.get("negative_path"):
        negative_path = overrides["negative_path"]
    if "merge_lines" in overrides:
        merge_lines = bool(overrides["merge_lines"])
    pos_hint = _pin_fixed_hint("path", overrides)
    neg_hint = _pin_fixed_hint("negative_path", overrides)

    text, message = read_tag_file(path, merge_lines)
    if text is None:
        pos_preview = ""
        parts.append(f"<span style='color:#e5484d'>✗ 正向：{html.escape(message)}{pos_hint}</span>")
    else:
        text, metas = strip_meta_tags(text)
        pos_preview = text
        meta_hint = " · 携带元数据" if metas else ""
        parts.append(f"<span style='color:#30a46c'>✓ 正向：{_file_hint(path, text)}{meta_hint}{pos_hint}</span>")

    neg_preview = ""
    if _normalize_path(negative_path):
        neg_text, neg_message = read_tag_file(negative_path, merge_lines)
        if neg_text is None:
            parts.append(f"<span style='color:#e5484d'>✗ 反向：{html.escape(neg_message)}{neg_hint}</span>")
        else:
            neg_text, neg_metas = strip_meta_tags(neg_text)
            neg_preview = neg_text
            meta_hint = " · 携带元数据" if neg_metas else ""
            parts.append(f"<span style='color:#30a46c'>✓ 反向：{_file_hint(negative_path, neg_text)}{meta_hint}{neg_hint}</span>")
    else:
        parts.append("<span style='color:#888'>反向：未设置（留空则不注入）</span>")

    return pos_preview, neg_preview, "<br>".join(parts)


def _launch_click(editor_path):
    ok, message = launch_editor(editor_path)
    color = "#30a46c" if ok else "#e5484d"
    return f"<span style='color:{color}'>{'✓' if ok else '✗'} {html.escape(message)}</span>"


def _plugin_dir_display(compact=False):
    """连接引导（v1.4.17，方案 B 插件侧配套）：插件安装目录展示 HTML。

    编辑器连接设置改为「用户只填插件目录、其余自动推导」——此值即粘贴目标。
    路径由插件文件位置计算（EXT_DIR = 本文件上两级，与 settings.pin /
    params.json 同源），勿让用户手填。data-dir 属性（JS 复制主通道）+ 可见
    <code> 文本（剪贴板被浏览器策略拦时照抄）双通道。
    v1.4.18 两处并存：生成页面板（完整版带说明文案，elem_id=feetag_plugin_dir）
    + 设置手风琴编辑器组（compact=True 紧凑版，elem_id=feetag_plugin_dir_settings，
    服务分组收纳）——都从 EXT_DIR 静态计算，无漂移问题；JS 同一委托函数处理
    两处的复制按钮。"""
    safe = html.escape(EXT_DIR)
    code = f'<code data-dir="{safe}" style="word-break:break-all">{safe}</code>'
    if compact:
        return code
    return (f'<span style="color:#888;font-size:0.9em">生成页总线 · 插件目录'
            f'（编辑器连接设置粘贴用）：</span><br>{code}')


def _armed_hint_html():
    """armed 开关下方的常驻提示（v1.4.17）：安全语义（开 = 允许本机编辑器程序
    替你点生成按钮）+ 即刻生效说明；开关操作后由 _armed_toggle 的回执覆盖。"""
    return ("<span style='color:#888;font-size:0.9em'>开启 = 允许本机 FeeTagHelper "
            "编辑器程序替你点击生成按钮（写入参数并触发出图）；勾选/取消即刻"
            "生效（写/删插件目录 bus.armed 文件），WebUI 重启后状态保持。</span>")


def _armed_toggle(value):
    """armed 开关事件（v1.4.17）：勾/取消 = 经 set_bus_armed 写/删 bus.armed
    （与手工放置同一文件同一语义），返回状态行回执 HTML。控件初始值 =
    bus_armed() 现状回显（面板构建时取，重启后与标志文件保持一致；编辑器
    远程写删文件后由 JS 探测链路同步显示）。"""
    enabled = bool(value)
    ok, message = set_bus_armed(enabled)
    _log(f"生成页总线开关（UI）：{message}")
    if not ok:
        return f"<span style='color:#e5484d'>✗ {html.escape(message)}</span>"
    if enabled:
        return ("<span style='color:#30a46c'>✓ 生成页总线已启用（bus.armed 已放置，"
                "编辑器面板可触发生成）</span>")
    return ("<span style='color:#888'>✓ 生成页总线已关闭（bus.armed 已移除，"
            "编辑器触发生成即刻不再被响应）</span>")


def _direct_hint_html():
    """直发开关下方的常驻提示（v1.4.18）：模式语义 + 边界（页面插件不参与）。"""
    return ("<span style='color:#888;font-size:0.9em'>勾选 = 编辑器生成经本页面执行"
            "（ADetailer 等页面插件照常生效，默认）；取消勾选 = 后端直发：编辑器"
            "生成 = 纯参数出图，不经页面、页面插件（ADetailer 等）不参与。勾选/"
            "取消即刻生效（写/删插件目录 bus.direct 文件），WebUI 重启后状态保持。"
            "</span>")


def _direct_toggle(value):
    """直发开关事件（v1.4.18）：勾选 = 页面链路（删 bus.direct），取消勾选 =
    后端直发（写 bus.direct）——与编辑器 Wave A 读到的文件契约一致。初始值 =
    not bus_direct() 现状回显；编辑器远程写删文件后由 JS 探测链路同步显示。"""
    use_page = bool(value)
    ok, message = set_bus_direct(not use_page)
    _log(f"直发模式开关（UI）：{message}")
    if not ok:
        return f"<span style='color:#e5484d'>✗ {html.escape(message)}</span>"
    if use_page:
        return ("<span style='color:#30a46c'>✓ 页面链路模式（bus.direct 已移除，"
                "编辑器生成经本页面执行、页面插件照常生效）</span>")
    return ("<span style='color:#888'>✓ 后端直发模式（bus.direct 已放置：编辑器"
            "生成 = 纯参数出图，不经页面、页面插件不参与）</span>")


def _persist_settings(*values):
    _save_config(dict(zip(CONTROL_KEYS, values)))


# 路径键（布尔键恒写显式值；路径键空值 = 解除固定）
_PATH_PIN_KEYS = ("path", "negative_path", "editor_path")


def _legacy_pin_file(key):
    """该路径键对应的旧独立 pin 文件（v1.4.10 机制）；editor_path 无旧文件。"""
    return {"path": POSITIVE_PIN_PATH, "negative_path": NEGATIVE_PIN_PATH}.get(key)


def _persist_pin_key(key, value):
    """控件提交值固化进 settings.pin 对应键（v1.4.12 统一固定机制）。

    读-改-写整个 JSON（文件缺失 / 损坏视作 {}）：路径键（path / negative_path /
    editor_path）空值 = 从 pin 删除该键（回退 config），非空写归一化路径；
    布尔键恒写显式值（含 False——用户取消勾选同样是固化意图）。若该路径键
    存在旧独立 pin 文件，同步更新之（写值 / 清空），防止"解除固定"被旧文件
    顶回。独立于 config 持久化事件，写失败只打日志、不影响 config 已照常保存。
    """
    data = _read_pin_json(SETTINGS_PIN_PATH)
    if key in _PATH_PIN_KEYS:
        normalized = _normalize_path(value)
        if normalized:
            data[key] = normalized
        else:
            data.pop(key, None)
        legacy = _legacy_pin_file(key)
        if legacy and os.path.isfile(legacy):
            try:
                with _config_lock:
                    _atomic_write_text(legacy, normalized + "\n" if normalized else "")
            except OSError as e:
                _log(f"{os.path.basename(legacy)} 写入失败（settings.pin 已照常处理）：{e}")
    else:
        data[key] = bool(value)
    try:
        with _config_lock:
            _atomic_write_text(SETTINGS_PIN_PATH, json.dumps(data, ensure_ascii=False, indent=2))
    except OSError as e:
        _log(f"settings.pin 写入失败（config 已照常保存）：{e}")


def _make_pin_persister(key):
    """生成某控件的 settings.pin 写入处理器（闭包绑定键名，v1.4.12 统一接线：
    两页面同文件、后写者=最新提交值）。"""
    def handler(value):
        _persist_pin_key(key, value)
    return handler


def _echo(value):
    return value


def _wire_controls(controls, is_img2img):
    """设置一变就存档；并让文生图 / 图生图两页面的组件互相同步。"""
    inputs = [controls[key] for key in CONTROL_KEYS]
    other = _TAB_CONTROLS.get(not is_img2img)
    for key in CONTROL_KEYS:
        comp = controls[key]
        comp.change(fn=_persist_settings, inputs=inputs, outputs=None)
        # v1.4.12 统一 pin：任一控件提交值除写 config 外同步固化进 settings.pin
        # （用户显式操作 = 固化意图；路径键空值 = 解除该键固定回退 config）
        comp.change(fn=_make_pin_persister(key), inputs=[comp], outputs=None)
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
        # v1.4.12：六控件初始值 = settings.pin 覆盖后的有效值（pin 优先显示）
        cfg = _effective_config()

        with gr.Accordion("外部提示词注入（实时读取 txt）", open=False,
                          elem_id=f"prompt-helper-{'img2img' if is_img2img else 'txt2img'}"):
            # —— v1.4.18 布局重组（用户点名）——
            # ① 总开关独立成行 + 分隔线（层级感：启用注入开着，下方设置区才有意义）
            with gr.Row():
                enabled = gr.Checkbox(
                    value=cfg["enabled"],
                    label="启用注入（词条恒拼接在提示词最前）",
                )
            gr.HTML('<hr style="border:none;border-top:1px solid var(--border-color-primary);'
                    'margin:0.4em 0 0.6em" />', elem_id="prompt-helper-divider")

            # ② 正向 / 反向路径并排同一行（用语统一「正向 / 反向」）
            with gr.Row():
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

            # ③ 预览组：双预览 + 刷新钮紧邻成组
            with gr.Group():
                with gr.Row():
                    preview = gr.Textbox(label="正向文件预览（只读）", lines=3, interactive=False)
                    negative_preview = gr.Textbox(label="反向文件预览（只读）", lines=3, interactive=False)
                with gr.Row():
                    status = gr.HTML()
                    refresh = gr.Button(value="刷新预览", scale=0)

            # ④ 编辑器组：编辑器路径 + 复制插件路径 + 立即启动编辑器（+ 启动时
            #    自动拉起）。复制插件路径与生成页面板两处并存（同源 EXT_DIR 实时
            #    计算无漂移；页面板那份服务新用户引导，这份服务分组收纳），
            #    elem_id 不同、JS 同一委托函数处理。
            with gr.Group():
                editor_path = gr.Textbox(
                    value=cfg["editor_path"],
                    label="词条编辑器路径 (exe)",
                    placeholder="例如：E:\\桌面\\AI file\\Design file\\prompt-helper\\feetaghelper.exe",
                    lines=1,
                )
                gr.HTML(value=_plugin_dir_display(compact=True),
                        elem_id="feetag_plugin_dir_settings")
                with gr.Row():
                    autostart = gr.Checkbox(value=cfg["autostart"],
                                            label="启动 WebUI 时自动打开词条编辑器")
                    settings_copy_button = gr.Button(
                        value="复制插件路径", elem_id="feetag_copy_dir_settings", scale=0)
                    launch_button = gr.Button(value="立即启动编辑器", scale=0)
                launch_status = gr.HTML()

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
            return  # ADetailer 内部 pass（_ad_inner 的 img2img p2）：不注入不计数不写状态
        if getattr(p, "_feetag_pass", False):
            return  # 本 p 已注入过：ADetailer postprocess_image 会拿 copy(外层 p) 重调本钩子
        #   （真机实证 !adetailer.py L926，copy 无 _ad_inner 标记）——幂等跳过，否则单次
        #   生成双计数/双注入行/双写配置。标记随 p 存活，每代 p 均为新建对象无需复位；
        #   代价：同 p 多轮 process_images 的脚本（loopback 类）只在首轮注入（旧版行为
        #   为每轮叠加注入，本就是错的）。
        p._feetag_pass = True
        global _gen_pass
        if bus_armed():
            _gen_pass += 1
            _write_status("busy")
        _save_config(dict(zip(CONTROL_KEYS, (enabled, path, negative_path,
                                              merge_lines, autostart, editor_path))))
        # settings.pin 统一固定（v1.4.12）：逐键覆盖入参（UI 值）——config 各键会被
        # 旧页面内存值经 _save_config 反复回写冲掉（path 与 negative_path 同族），
        # pin 不在该写回链路上、不可被冲掉；上方 _save_config 保存的仍是入参原值
        # （pin 兜底回写无害，同 v1.4.10 反向语义）。autostart / editor_path 在
        # _on_app_started / launch_editor 消费点覆盖，此处不参与生成行为。
        overrides = _read_pin_overrides()
        if "enabled" in overrides:
            enabled = bool(overrides["enabled"])
        if overrides.get("path"):
            path = overrides["path"]
        if overrides.get("negative_path"):
            negative_path = overrides["negative_path"]
        if "merge_lines" in overrides:
            merge_lines = bool(overrides["merge_lines"])
        if not enabled:
            return

        # 方案 A（v1.4.15 快照锁定提示词）：bus 武装且 params.json 带非空 prompt 键 → 本次注入的
        # tag 文本用快照锁定的提示词替代 prompt.txt 实时读取（txt 读取失败也无所谓——根本不读）；
        # 仍走 strip_meta_tags→expand_breaks→_inject 全管线（p.prompt 页面基底不动，语义与 txt
        # 注入完全一致）。无键 / 为空 → 现状读 txt。bus 未武装 = 手动面板生成，params.json 是
        # 陈旧残留，不得锁定（bus_armed 现查，一次 stat）。
        snap_prompt = _read_snapshot_prompt() if bus_armed() else ""
        if snap_prompt:
            tags, message = snap_prompt, "params.json prompt 键（快照锁定）"
        else:
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

        # 反向有效路径：已在上方经 settings.pin 统一覆盖（v1.4.10 的独立 pin
        # 机制并入 _read_pin_overrides 兼容层；pin 缺席则回退入参，零迁移）。
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

        ADetailer 内部 pass（_ad_inner 标记的 p2）走不到这里（白名单+标记双隔离）；
        会到达的是它对 copy(外层 p) 的显式重调（真机实证 !adetailer.py L909：
        need_call_postprocess 时以 Processed(p, [], seed, "") 空壳重调本钩子）——
        空壳 images/info 全空，由此识别跳过，不再把状态提前置 done。
        总开关关闭时整段跳过（不落图不写状态）。
        任何异常只置 error 状态 + 打日志，绝不影响生成任务本身。
        """
        if getattr(p, "_ad_inner", False) or not bus_armed():
            return
        if not getattr(processed, "images", None) and not getattr(processed, "info", ""):
            return  # ADetailer 重调的空壳 Processed：无图可回传，别提前置 done
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
    """注册总线端点（v1.4.4 只读 + v1.4.6 cmd 原子消费）：
      GET /feetag/bus/status         → status.json 内容（application/json）
      GET /feetag/bus/image?name=xx  → featag_out/<name>（basename 防穿越）
      GET /feetag/bus/cmd            → 原子取走 cmd.json：读取并删除，每条命令
                                       全局恰有一个消费者取到（200），其余 404
    三者都带 Access-Control-Allow-Origin: *——编辑器面板（Tauri webview 的
    tauri.localhost 源 / dev 的 localhost:5173 源）跨源读取 /file= 会被 CORS
    拦截（gradio 的 CORS 只放行本机同名源），自有端点解决之；cmd 端点同时
    解决多浏览器并存时单槽文件被竞态消费的问题（v1.4.6）。
    路由注册无条件（保证"放置 bus.armed 即生效"），内容按 bus_armed() 门控：
    未启用时一律 404，与总线默认关语义一致。"""
    try:
        from fastapi import Request
        from fastapi.responses import FileResponse, Response
    except Exception as e:  # fastapi 理论上必在（gradio 依赖）；防御性兜底
        _log(f"总线端点未注册（fastapi 导入失败）：{e}")
        return

    def _bus_status():
        if not bus_armed():
            return Response(status_code=404)
        data = None
        for attempt in range(3):  # v1.4.16：status 原子替换窗口内 open 可能被短暂拒绝，重读
            try:
                with open(STATUS_PATH, "rb") as f:
                    data = f.read()
                break
            except OSError:
                if attempt < 2:
                    time.sleep(0.02)
        if data is None:
            return Response(status_code=404)
        return Response(content=data, media_type="application/json",
                        headers={"Access-Control-Allow-Origin": "*"})

    def _bus_image(name: str = ""):
        if not bus_armed():
            return Response(status_code=404)
        path = os.path.join(FEETAG_OUT_DIR, os.path.basename(name or ""))
        if not os.path.isfile(path):
            return Response(status_code=404)
        return FileResponse(path, headers={"Access-Control-Allow-Origin": "*"})

    def _bus_cmd():
        if not bus_armed():
            return Response(status_code=404)
        if bus_direct():
            # v1.4.18 直发互斥：直发模式下命令由服务端消费线程（_direct_poller_loop）
            # 原子自取并直发执行——端点一律 404 且**不消费**，页面 JS 与直发不可
            # 双消费（_consume_cmd 改名原子性保证全局恰一个消费者）
            return Response(status_code=404)
        cmd = _consume_cmd()
        if cmd is None:
            return Response(status_code=404)
        return Response(content=json.dumps(cmd, ensure_ascii=False),
                        media_type="application/json",
                        headers={"Access-Control-Allow-Origin": "*"})

    def _bus_progress(request: Request):
        """（v1.4.14）生成进度转发端点：编辑器 webview 直连 /sdapi/v1/progress 被 A1111 CORS 拦
        （与 /file= 同款问题，G-2 实锤），经本端点同源转发。进度 API 挂在 WebUI 自身端口——从请求
        Host 头推（端口漂移安全），skip_current_image=true 免回传 base64 大图。异常回 JSON null
        （前端回落秒数显示），不 5xx 不刷日志。"""
        if not bus_armed():
            return Response(status_code=404)
        try:
            import urllib.request
            host = request.headers.get("host") or "127.0.0.1:7860"
            with urllib.request.urlopen(
                    f"http://{host}/sdapi/v1/progress?skip_current_image=true", timeout=3) as r:
                return Response(content=r.read(), media_type="application/json",
                                headers={"Access-Control-Allow-Origin": "*"})
        except Exception:
            return Response(content=json.dumps({"progress": None, "eta": None}),
                            media_type="application/json",
                            headers={"Access-Control-Allow-Origin": "*"})

    # —— 页面状态快照/恢复（v1.4.18 Wave B，best-effort 持久化）——
    def _bus_page_state():
        """GET：功能开关 + 两页已接线字段表 elem_id 清单（JS 采集/回放的已知
        字段集）+ 已存状态。armed 门控（与总线其他端点一致）。"""
        if not bus_armed():
            return Response(status_code=404)
        return Response(content=json.dumps(_page_state_document(), ensure_ascii=False),
                        media_type="application/json",
                        headers={"Access-Control-Allow-Origin": "*"})

    async def _bus_page_state_save(request: Request):
        """POST：JS 采集的页面状态回存 bus.page_state.json（原子写）。armed
        门控；功能关闭（bus.page_state.disabled 在位）或内容非对象 → 不落盘。"""
        if not bus_armed() or not page_state_enabled():
            return Response(status_code=403)
        try:
            body = await request.json()
        except Exception:
            return Response(status_code=400)
        if not isinstance(body, dict):
            return Response(status_code=400)
        try:
            with _page_state_lock:
                _atomic_write_text(PAGE_STATE_PATH, json.dumps(body, ensure_ascii=False))
        except OSError as e:
            _log(f"bus.page_state.json 写入失败：{e}")
            return Response(status_code=500)
        return Response(status_code=204, headers={"Access-Control-Allow-Origin": "*"})

    try:
        app.add_api_route("/feetag/bus/status", _bus_status, methods=["GET"], include_in_schema=False)
        app.add_api_route("/feetag/bus/image", _bus_image, methods=["GET"], include_in_schema=False)
        app.add_api_route("/feetag/bus/cmd", _bus_cmd, methods=["GET"], include_in_schema=False)
        app.add_api_route("/feetag/bus/progress", _bus_progress, methods=["GET"], include_in_schema=False)
        app.add_api_route("/feetag/bus/page-state", _bus_page_state, methods=["GET"], include_in_schema=False)
        app.add_api_route("/feetag/bus/page-state", _bus_page_state_save, methods=["POST"], include_in_schema=False)
        _log("总线端点已注册：GET /feetag/bus/status、/image、/cmd、/progress、/page-state + POST /page-state（bus.armed 门控）")
    except Exception as e:
        _log(f"总线端点注册失败（不影响其他功能）：{e}")


def _on_before_ui():
    """UI 重建复位（v1.4.16，盲测 P1-2 修复）：Reload UI / Restart Gradio 时
    A1111 重建整个界面并重跑全部脚本 ui()（webui.py 主循环：before_ui_callback
    → create_ui；Python 模块不重导入，模块级状态全部过期）。复位接线状态与
    组件捕获——否则新 apply 钮永不接线（_WIRED=True 挡住，apply 静默死亡
    直到重启进程），且 _SCRIPT_LISTS / _TAB_CONTROLS 持旧引用无界累加、
    _UI_COMPONENTS 里的陈旧条目会在重建中途被误当作已捕获组件接进事件。
    before_ui 在任何新组件创建之前触发，此处清空无竞态。"""
    _WIRED.clear()
    _UI_COMPONENTS.clear()
    _SCRIPT_LISTS.clear()
    _BUS_BUTTONS.clear()
    _AD_FIELDS.clear()
    _AD_BUTTONS.clear()
    _TAB_CONTROLS.clear()
    _SECTION_STATE.clear()
    global _BUS_PANEL_BUILT
    _BUS_PANEL.clear()
    _BUS_PANEL_BUILT = False  # 重建时锚点组件会再次触发，面板随之重建


def _on_app_started(demo=None, app=None):
    _start_bus_watchdog()
    _start_direct_poller()  # v1.4.18：直发消费线程（armed+direct 双开时才消费）
    if app is not None:
        _register_bus_endpoints(app)
    if bus_armed():
        try:
            os.makedirs(FEETAG_OUT_DIR, exist_ok=True)
        except OSError:
            pass
        _write_status("idle")  # 总线启用时发初态（含 choices），编辑器据此判断插件在线
    # v1.4.12：自动启动消费点走 pin 覆盖后的有效配置（autostart / editor_path
    # 六键统一；launch_editor 内部还会再过一次 pin，手动改 pin 即刻生效）
    cfg = _effective_config()
    if not cfg["autostart"]:
        return
    ok, message = launch_editor(cfg["editor_path"])
    _log(f"自动启动编辑器：{message}")


script_callbacks.on_before_ui(_on_before_ui)
script_callbacks.on_after_component(_on_after_component)
script_callbacks.on_app_started(_on_app_started)
