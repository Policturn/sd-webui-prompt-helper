# sd-webui-prompt-helper（外部提示词注入）

SD WebUI（AUTOMATIC1111 / 秋叶整合包）扩展：把一个由外部词条编辑器
（prompt-helper / FeeTagHelper）实时输出的 txt 文件，在**每次生成时**重新
读取并拼接到提示词中。

## 安装

### 方式一：Git 克隆（推荐）

在整合包根目录（即有 `extensions` 文件夹的那一层）打开命令行：

```bat
git clone https://github.com/Policturn/sd-webui-prompt-helper extensions/sd-webui-prompt-helper
```

### 方式二：手动复制

把整个 `sd-webui-prompt-helper` 文件夹复制到整合包的 `extensions/` 目录
（秋叶整合包在 `H:\sd-webui-aki-v4.x\extensions\`）。

两种方式装完后都用启动器重启 WebUI。**无需安装任何依赖**（纯 Python + Gradio 基础组件），
兼容 AUTOMATIC1111 版 WebUI 1.6+ 与秋叶整合包 v4.x。

## 使用

1. 在文生图或图生图页面下方的脚本区找到 **"外部提示词注入（实时读取 txt）"** 折叠栏；
2. 正向词条路径 `path` 填你的词条 txt（默认已预填 `E:\桌面\AI file\Design file\prompt-helper\prompt.txt`）；
   需要注入反向提示词时，再填一个独立的反向词条文件路径 `negative_path`（**留空则不注入反向**）；
3. 点 **"刷新预览"** 确认能读到内容（正向 / 反向两个预览框，会显示词条数和文件更新时间）；
4. 正常生成即可。两个页面的设置互相同步，重启后自动记住；
5. （可选）勾选 **"启动 WebUI 时自动打开词条编辑器"** 并填入编辑器 exe 路径，
   以后每次启动 WebUI 会自动把词条编辑器拉起来；点"立即启动编辑器"可立即验证路径。

## 行为细节

- **每次生成读一次文件**：点一次"生成"读一次；队列 / 批量任务每个任务各读
  一次，始终获取编辑器输出的最新内容，中途改词条无需刷新页面。
- **settings.pin 统一固定文件（v1.4.12）**：插件目录根可放置 `settings.pin`
  （JSON，内容为任意设置键子集，六键 `enabled` / `path` / `negative_path` /
  `merge_lines` / `autostart` / `editor_path` 全支持）——文件里出现的键以 pin
  为准，不再受 config / 页面旧值回写影响（config 各键曾被旧页面内存值反复
  回写冲掉，`path` / `negative_path` / `editor_path` 同族）。推荐直接手编该
  文件固化设置，例如：

  ```json
  {
    "path": "E:\\词条\\prompt.txt",
    "negative_path": "E:\\词条\\negative.txt",
    "enabled": true
  }
  ```

  读取优先级（逐键）：**settings.pin > 旧独立 pin > config**；注入 / 预览 /
  编辑器启动每次现读，改文件即刻生效无需重启；预览状态行显示
  「（已由 settings.pin 固定）」标记。UI 各控件初始值 = pin 覆盖后的有效值；
  在页面上改动任一设置并提交会**同步固化进 settings.pin**（用户显式操作 =
  固化意图；路径框清空提交 = 解除该键固定、回退 config；布尔开关恒写显式值）。
  该文件含个人路径，已被 .gitignore 排除。
- **旧独立 pin 文件（兼容层）**：v1.4.10 的 `negative_path.pin`（纯文本一行=
  反向词条 txt 完整路径）原样可读，`positive_path.pin` 同款亦并入——同键时
  `settings.pin` 优先于它们；UI 提交会同步更新已存在的旧 pin 文件（含清空），
  防止"解除固定"被旧文件顶回。新用户直接用 `settings.pin` 即可，无需旧文件。
- **正向 / 反向双文件**：反向词条来自独立的 txt 文件，与正向互不影响——
  一方读取失败不影响另一方注入；反向路径留空则只注入正向。
- **注入位置固定在最前**（v1.4.1 起位置确定化）：词条恒定拼接在提示词最前面，
  最终送入 CLIP 的文本结构恒为 `[注入词条][提示框原有内容]`，正反向一致。
  旧版的"插入位置"选项已移除；config.json 里残留的 `position` 键会被读取
  白名单自动忽略，无需手动清理。
- 注入发生在服务端生成流程内部，**不会回写到提示词输入框**；注入后的完整
  提示词可在生成信息面板和 PNG 元数据里核对。
- 高清修复：未单独填写高清提示词时，自动继承注入后的提示词。
- 与"样式 (Styles)"的关系：注入先于样式应用，词条会作为基础提示词参与样式模板。
- 控制台日志前缀为 `[prompt-helper]`，注入 / 跳过都会打印原因。

## FeeTagHelper 元数据 tag（v1.4.0+）

FeeTagHelper 构建区开启"携带元数据"时，txt 末尾会追加一个
`<fth:meta:BASE64URL>` tag（携带 BREAK 分组位置 / 选一记录）。插件注入时会：

1. **剥离**该 tag——无论解码是否成功，它都不会进入生成用提示词
   （解码失败静默丢弃，不报错不中断）；
2. **展开 BREAK**——按元数据中的 `breaks` 位置把平铺 tag 流断开为
   **空行分隔**（A1111 BREAK 语法），还原编辑器里的分组结构；首尾 / 连续
   BREAK 已在编辑器侧修剪，插件侧防御性再修剪一次；
3. **写入 PNG**——解码后的元数据（附插件版本号）写进生成信息的
   `extra_generation_params`（键 `fth_meta` / `fth_meta_negative`），
   PNG 参数面板可见、读图可还原。

不需要该行为时，在 FeeTagHelper 设置里关闭"携带元数据"即可（txt 恢复纯平铺）。

## 实际送入 CLIP 的文本回传（v1.4.1+）

只要本次正向 / 反向注入成功，`fth_meta` / `fth_meta_negative` 就会记录
（txt 未携带元数据 tag 时也记录），供编辑器（如 FeeTagHelper 的 75 token
自然条）校准实际进入 CLIP 的文本：

```json
{
  "v": 1, "breaks": [5], "pick": [...],     // 编辑器元数据（有才带）
  "plugin": "1.4.1",
  "injected_tags": 22,                       // 注入区 tag 数（按逗号拆分计数，
                                             //   与编辑器自然条 offset 同基准，
                                             //   BREAK 展开前的平铺口径）
  "full_text": "注入词条, 提示框原有内容"      // 注入后的完整提示词
}
```

75 token 分块发生在 CLIP 编码内部，`before_process` 阶段拿不到分块结果
（块数 / 末块 token 数 / 是否截断），故不回传 `chunks` / `clipped`——编辑器
端用自带 tokenizer 依据 `full_text` 自行计算，对齐由 tokenizer 本身保证。

## 生成页总线（v1.4.2+，P1；v1.4.3 起带总开关；v1.4.6 起指令原子消费）

本插件同时是 FeeTagHelper「生成」页的服务端：编辑器把要覆盖的参数与触发指令
写进**插件目录**（与 config.json 同层），浏览器端 `javascript/feetag_generate.js`
轮询指令、服务端把参数回填到界面组件并走 UI 正常队列生成，成品图回传给编辑器。

| 文件 | 谁写 | 谁读 | 说明 |
|---|---|---|---|
| `params.json` | 编辑器 | 插件（apply 时读） | 要覆盖的参数，**只写要改的键**：`base`（width / height / seed / sampler_name / scheduler / steps / cfg_scale / batch_size / n_iter）+ `hires`（enable / upscaler / hr_scale / denoise / steps）+ `tiled` / `tiledvae`（分块放大，enable + 各参数）+ `usdu`（**enable=true 额外触发脚本下拉选中**，v1.4.7 契约平铺）+ `adetailer_infotext`（ADetailer 单行 infotext）；`img2img` / `extras` 段为后续版本预留 |
| `cmd.json` | 编辑器 | 插件（服务端原子消费） | `{"action":"generate","page":"txt2img","ts":...}`，ts 递增防重放。浏览器 JS 经 `GET /feetag/bus/cmd` 轮询：服务端读取并**删除**该文件，每条命令全局恰有一个消费者取到（200），其余请求 404——多浏览器 / 多页签并存不再竞态抢指令（v1.4.6 修复） |
| `status.json` | 插件 | 编辑器轮询 | `{"state":"idle/busy/done/error","pass":N,"images":[绝对路径],"image_roots":[授权根],"error","ts","plugin","choices","applied_ts"}`；内容不变不重写；`choices` 为 WebUI 当前实际可用的采样器 / 调度 / 超分列表（编辑器下拉对齐用）；`applied_ts`（v1.4.13）为最近一次 apply 完成的毫秒时间戳（粘滞携带，浏览器 JS 的 apply 完成信号判据 `applied_ts > cmd.ts`）。只读端点 `GET /feetag/bus/status` |
| `featag_out/` | 插件（历史） | 编辑器图库 | **v1.4.22 起停写**（单份化，见下）；目录与存量图原样保留（图库登记源不动）。只读端点 `GET /feetag/bus/image?name=` 同时服务存量与新版路径 |

**图片路径契约（v1.4.22 单份化，破坏性变更）**：成品图不再复制进
`featag_out/`——磁盘只存 WebUI 输出目录那份。`status.json` 的 `images` 改报
**WebUI 实际落盘的精确绝对路径**（来源 = A1111 落盘后写回 PIL 对象的
`already_saved_as`；含日期子目录，生成序，批次>1 时首元素可能是 grid；逐图
自带完整 parameters infotext + `fth_meta`，根治旧副本"批次首图 infotext 贴
所有图"的瑕疵）；新增 `image_roots` 字段 = 授权根数组（`p.outpath_samples` /
`p.outpath_grids` 每轮登记、跨轮累积粘滞携带，重启后端点从本字段自愈恢复）。
取图：`GET /feetag/bus/image?name=<encodeURIComponent(完整路径)>`（推荐形态
= `status.images` 原样传入；也接受授权根下的相对子路径，按最近 samples 根
解析——WebUI 序号命名跨日期目录天然重名，basename 形态已不可用）。授权 =
归一化后落在任一授权根内，根外任意路径 / `..` 越界 / 符号链接出根一律 404；
**文件被用户删除同样 404，编辑器按契约把 404 的图从展示列表剔除**（用户
自删属自发行为）；200 响应带 `Cache-Control: no-store`（v1.4.23），消除
WebView2 分钟级温存滞后——删除后立即剔除，不用等缓存过期。未落盘的图（`samples_save` 关闭 / API 未存盘）无路径可报，
跳过不上报（打日志）。存量 `fth_*` 图的"首图 infotext 贴所有图"瑕疵用
`tools/repair_featag_out_meta.py` 一次性修复（默认干跑，`--apply` 实修，报告
落插件根 `featag_out_meta_repair_report.json`）。

触发链路：编辑器写 params.json → 写 cmd.json → JS 轮询 `/feetag/bus/cmd` 取到
指令（恰一方）→ 点隐藏 apply 钮（服务端按
语义键 → elem_id 选择器表把参数回填到界面组件：未出现的键不覆盖、越界夹取、
下拉值非法跳过）→ JS 切到目标页签点生成钮 → WebUI 正常队列生成 → `postprocess`
钩子上报成图的 WebUI 落盘路径并置 done（v1.4.22 前是复制进 featag_out/）。
Highres-fix 全参数同通道支持
（hr_checkpoint 中途换模型暂不接）。ADetailer 等扩展的内部重绘 pass
（`_ad_inner` 标记）在插件所有钩子入口直接跳过——不注入词条、不计数、不回传。

指令延迟压缩（v1.4.13）：发布指令到点生成的延迟从「轮询发现(≤500ms) + apply
盲等(700ms) + 切页驻留(150ms)」压到平均 ~300ms 内（实测隐藏页签里两级
setTimeout 盲等被 Chromium 节流到 ~3s+，压缩前 write→busy 实测 3.5~4.3s）：

- 轮询间隔 500ms→200ms（armed 态；未 armed 的 15s 探测不变）；
- apply 盲等 700ms 改为**完成信号驱动**——服务端 apply handler 末尾向
  status.json 写 `applied_ts`（毫秒时间戳，状态机字段沿用最近值），JS 点
  apply 后以 50ms 粒度轮询 `/feetag/bus/status`，见 `applied_ts > cmd.ts`
  即参数已在服务端算完回包，小驻留（100ms，等 gr.update 客户端落值）后
  立即点生成；**信号超时 600ms（旧版脚本 / 字段缺失）回落旧盲等**
  （补足 700ms 总时长，行为与 v1.4.10~v1.4.12 等价）；
- 切页签提前到 apply 点击后立即执行（纯 DOM 操作，与 apply 事件回包无数据
  依赖）；
- 信号等待为双节拍驱动的状态机：50ms setInterval（前台细粒度）+ Worker
  心跳（隐藏页不被 Chromium 定时器节流，信号检测不因页签隐藏退化）。

轮询心跳（v1.4.11）：JS 的轮询节拍由 **Dedicated Worker 驱动**（Blob URL 内联
创建，零新增文件）——Worker 定时器不受 Chromium 后台节流影响（页面定时器在
标签页隐藏后退化为 ≥1s、隐藏超 5 分钟最长 1 分钟一次，曾导致 WebUI 非活跃窗口
时生成指令被延后消费）；Worker 每 200ms（v1.4.13 起，原 500ms）发心跳，页面
收到心跳才执行轮询与 apply/generate 点击（fetch 与 DOM 操作仍在主线程，消费
语义不变）。Worker 不可用时自动回退主线程定时器；页面卸载时 terminate。

鲁棒性（v1.4.16，盲测 P1 修复）：

- **接线分组分线**：未安装 Tiled Diffusion / Ultimate SD upscale 扩展的环境，
  `tiled` / `tiledvae` / `usdu` 参数节整组跳过（启动日志有提示），base /
  hires 等原生参数照常回填——apply 不再因扩展缺席而永不接线（旧版会静默
  用界面旧值出图）。params.json 契约（v1.4.7 平铺）不变。
- **Reload UI 兼容**：A1111 的 Reload UI / Restart Gradio 重建界面后自动
  重新接线（旧版一旦 Reload UI，apply 静默死亡直到重启进程）。
- **总线看门狗**：生成任务异常结束（不触发 postprocess）时，status.json
  约 10 秒内补写 `error` 状态（含明确消息）——按"任务是否仍在运行"判定
  而非超时，小时级的慢生成（大图 tiled 超分）不受影响。
- **原子写**：config.json / settings.pin / status.json 等一律"临时文件 +
  原子替换"落盘（替换窗口的短暂占用自动重试），编辑器面板与配置读取
  不会再见到半截 JSON。
- **破坏性变更护栏（v1.4.21）**：插件设置（启用开关 / 正反向词条路径 /
  编辑器路径）不允许被页面快照回放程序化改写——通用扫描采集与回放双双
  跳过 `prompt-helper-*` 容器（设置由 config.json / settings.pin 权威
  初始化）。服务端再加一层：`_save_config` 全量回写通道遇到「路径键磁盘
  非空 → 新值空」的破坏性清空直接拒绝该键（保留旧值），`enabled`
  True→False 与 pin 键的清空/翻转放行但全程留痕——审计追加在插件根
  `config.audit.log`（JSON 行：时间 / 事件 / 键 / 旧新值 / 来源），config
  读取损坏（文件存在但读不出）也留痕。背景：2026-09-17 生产事故（生成图
  提示词全空）根因即页面状态回放按索引错位把脏 DOM 值写进插件控件。

### 总开关：bus.armed（v1.4.3+）

总线**默认关闭**：在插件目录放置一个空的 `bus.armed` 文件才启用全部总线行为
（JS 轮询 / status 写入 / featag_out 回传 / 参数回填）；删除该文件即关闭。
放置/删除**即刻生效，无需重启 WebUI**。词条注入（本插件核心功能）与开关无关、始终可用。

**连接引导（v1.4.17 起）**：文生图生成页（结果图库正下方）挂有
「生成页总线 · 连接引导」小面板，多件成组：

1. **启用生成页总线**开关：勾选/取消 = 写入/删除插件目录的 `bus.armed`
   标志文件（与手工放置完全同一文件、同一语义）。开启 = 允许本机
   FeeTagHelper 编辑器程序替你点击生成按钮（写入参数并触发出图）。
   开关初始值 = 标志文件现状回显，重启后保持一致；外部（编辑器）直接
   写删该文件时，服务端**每次请求实时判定**（即刻生效），页面开关显示
   与轮询启停在 15 秒内自动同步，无需刷新。
2. **使用网页端插件的页面链路**开关（v1.4.18）：勾选（默认）= 编辑器生成
   经本页面执行、ADetailer 等页面插件照常生效；取消勾选 = 后端直发模式
   （详见下一节）。同样是文件化契约（`bus.direct`），外部写删 15 秒内同步
   显示。
3. **插件目录路径展示** + 4. **「复制插件目录」**按钮：FeeTagHelper 编辑器
   的连接设置只需粘贴这个插件安装目录（其余自动推导）。路径由插件安装
   位置自动计算，无需手填；点击复制（浏览器策略拦截时自动回退旧式复制，
   路径文本就在旁边可照抄）。设置手风琴的「编辑器」分组内另有
   **「复制插件路径」**按钮（v1.4.18，两处并存、同源实时计算，功能相同）。

`bus.armed` 文件契约（编辑器直写请照此）：路径 = 插件安装目录下
`bus.armed`（与 params.json / cmd.json / status.json 同目录）；内容不校验，
空文件（0 字节）即可；文件存在 = 总线启用，**删除 = 立即停用**（全部
`/feetag/bus/*` 端点即刻 404、生成钩子即刻跳过——逐请求实时判定）。

新用户从零到「编辑器面板能触发生成」：装插件 → 生成页面板复制插件目录
→ 粘贴到编辑器连接设置（编辑器检测后直写 bus.armed 开闸）→ 面板可触发
生成，全程界面可点、无需手工建文件。

### 后端直发模式（bus.direct，v1.4.18+）

面板「使用网页端插件的页面链路」开关控制生成走哪条链路，文件化契约同
bus.armed（编辑器按该文件决定开窗方式）：

- **文件不存在（默认）= 页面链路**：编辑器生成经本页面执行（隐藏 apply
  回填 → 点生成钮），ADetailer / Tiled 等**页面插件照常生效**。
- **文件存在（空文件即可）= 后端直发**：编辑器生成 = **纯参数出图**——
  服务端消费线程自取命令并本机调用 `/sdapi/v1/txt2img`，**不经页面、页面
  插件（ADetailer 等）不参与**，编辑器面板里的 ADetailer 值同样不生效
  （编辑器只接管到 params 键范围：tiled / tiledvae / usdu /
  adetailer_infotext 等页面插件键在直发模式一律不映射）。prompt 词条注入
  不受影响（API 路径同样过注入钩子，含 v1.4.15 快照锁定）。
- 逐请求实时判定，删 = 立即回页面模式；页面开关显示由 JS 15 秒探测同步。
  直发需 WebUI 以 `--api` 启动，否则该次生成回 error 状态并提示。出图由
  WebUI 原生落盘（v1.4.22：payload 恒带 `save_images: true`——API 默认不落盘，
  不补则直发图磁盘零副本；outdir 走 API 路径硬编码的
  `opts.outdir_txt2img_samples`，与页面图同目录体系，注意 API 路径忽略
  `outdir_samples` 总覆盖键）；busy/done/error 状态机、
  pass 计数、进度端点照常（直发在途有看门狗护栏，不会误报卡死）。
  **落盘 / 计数由生成钩子统一完成（v1.4.19 收口）**：API 生成与页面生成
  同走 before_process / postprocess 钩子——注入、pass 计数、落盘路径上报、
  done 全部由钩子做且只做一次，直发线程只负责触发与异常兜底
  （v1.4.18 曾由直发线程自落一份盘、自计一次 pass，与钩子合计同图双落盘
  + pass 双计，v1.4.19 根治；v1.4.22 前的 featag_out 复制亦已停）。

### 页面状态快照/恢复（v1.4.18+，best-effort）

页面链路模式的参数持久化：页面 JS 采集已接线参数组件（base / hires /
tiled 系可选组）当前值 + 脚本容器内其余输入的通用扫描，输入防抖 1.2 秒
及页面隐藏时回存 `bus.page_state.json`；下次加载页面时尽力回放。**恢复只
在加载时**——生成时总线对受控字段的写入照常覆盖（受控字段编辑器赢、其余
恢复用户值）。插件目录放置 `bus.page_state.disabled` 即整体关闭（默认开）。

**冲刷钩子（编辑器侧契约）**：页面全局暴露
`window.__feetagFlushPageState()`——立即执行一次采集 + POST（不等 1.2 秒
防抖，防"改完立刻移窗、防抖没来得及存"的边缘丢档）；失败静默，防抖兜底
链路不变。编辑器「藏回去」等移窗前动作应主动调用。

> 注：回放依赖程序化设值让 gradio 拾取（dispatch input/change，A1111
> updateInput 同款技巧），**对 gradio 版本敏感**——gradio 升级改 DOM 结构
> 时可能部分失效；失效仅影响恢复（下拉类组件只采集不回放），不影响生成。

> 注（v1.4.21）：**本插件自身的设置控件不进快照通道**（采集与回放均跳过
> `prompt-helper-*` 容器）——插件设置由 config.json / settings.pin 权威
> 初始化，程序化回放会经 change 事件触发服务端持久化链，历史上曾把脏 DOM
> 值（含索引错位塞入的值）写进 config / pin 造成注入静默失效。

以上总线文件均已被 .gitignore 排除；删除即完全复位（status.json 会在下次启动
WebUI 且开关开启时重新生成）。

## 常见问题

- **路径怎么填**：资源管理器里选中文件，Shift + 右键 → "复制文件地址"，粘贴进来即可。
- **编码**：自动兼容 UTF-8（含 BOM）与 GBK。
- **文件被编辑器占用的瞬间**：自动重试 3 次；仍失败则本次跳过注入并在控制台
  打印原因，不会中断生成。
- **想暂时停用**：取消勾选"启用注入"，或直接在编辑器里清空输出文件。
- **编辑器联动启动**：编辑器以独立进程运行——关闭 WebUI 不会连带关闭它；
  检测到同名进程已在运行时不会重复拉起（和 ComfyUI 版同时开启也只启动一份）。
  **进程树脱离（v1.4.19）**：Windows 下经 `cmd /c start` 中转启动（编辑器
  挂到 cmd 名下、cmd 随即退出，进程父子链断开）并附 `CREATE_BREAKAWAY_FROM_JOB`
  ——外部按进程树强杀 WebUI（如 `taskkill /F /T`，webui.py stop 即此）不会
  再连带杀掉编辑器；中转不可用时自动回退直启，保底与旧版一致。
- **editor_path 失效自动探测（v1.4.9）**：编辑器发版 exe 改名（如
  `feetaghelper-v2.7.5.exe` → `v2.8.0`）后，旧配置路径失效时启动编辑器会自动
  在同目录扫描 `feetaghelper-v*.exe`、取版本号最新的一个并写回 config——
  发版不再需要手动更新配置（仅同目录生效；新 exe 换了目录仍需手改）。
- **editor.hint 编辑器自荐路径（v1.4.20，X-177 契约）**：FeeTagHelper 编辑器
  在连接「检测」时会把自身 exe 绝对路径写进插件目录根的 `editor.hint` 文件
  （单行文本；编辑器**永不修改插件 config**，单向传值，文件已被 .gitignore
  排除）。启动编辑器时的解析链：**settings.pin 有效 > config 用户值有效 >
  editor.hint 有效 > 同目录扫描最新版 > 原值兜底**——你在插件页面设置的
  路径永远不会被 hint 覆盖；没设置时 hint 直接生效（「立即启动编辑器」/
  启动时自动拉起零配置可用）。hint 档**不写回 config**（与扫描档不同，
  页面文本框仍显示你的原值）；内容缺失 / 空 / 畸形 / 指向不存在的文件时
  自动跳过该档。

## 姊妹项目

同一核心逻辑的 ComfyUI 节点版：
[comfyui-prompt-helper](https://github.com/Policturn/comfyui-prompt-helper)
（"外部提示词注入"节点，每次执行队列时注入）。两个版本同步维护。

## 许可证

[MIT](LICENSE)
