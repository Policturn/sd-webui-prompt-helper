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

## 常见问题

- **路径怎么填**：资源管理器里选中文件，Shift + 右键 → "复制文件地址"，粘贴进来即可。
- **编码**：自动兼容 UTF-8（含 BOM）与 GBK。
- **文件被编辑器占用的瞬间**：自动重试 3 次；仍失败则本次跳过注入并在控制台
  打印原因，不会中断生成。
- **想暂时停用**：取消勾选"启用注入"，或直接在编辑器里清空输出文件。
- **编辑器联动启动**：编辑器以独立进程运行——关闭 WebUI 不会连带关闭它；
  检测到同名进程已在运行时不会重复拉起（和 ComfyUI 版同时开启也只启动一份）。

## 姊妹项目

同一核心逻辑的 ComfyUI 节点版：
[comfyui-prompt-helper](https://github.com/Policturn/comfyui-prompt-helper)
（"外部提示词注入"节点，每次执行队列时注入）。两个版本同步维护。

## 许可证

[MIT](LICENSE)
