// FeeTagHelper 生成页总线 · 浏览器端（v1.4.13）
//
// 职责：轮询服务端原子消费端点 /feetag/bus/cmd（编辑器写 cmd.json，服务端
//   读取并删除——v1.4.6 起不再经 /file= 直读：单槽文件在多浏览器 / 多页签
//   并存时会被竞态消费，实测用户命令被测试页面抢走），收到触发指令后——
//   1. 点击本插件的隐藏 apply 钮（服务端读 params.json → gr.update 回填界面组件，
//      选择器表与回填逻辑见 scripts/prompt_helper.py）；
//   2. 切换到目标页签（A1111 官方全局函数 switch_to_txt2img / switch_to_img2img）；
//   3. 点击该页生成钮，走 UI 正常队列（未覆盖的参数 = WebUI 界面当前值）。
//
// 复制插件目录（v1.4.17，连接引导方案 B 配套）：文件末尾另有一个完全独立的
//   IIFE——生成页面板「复制插件目录」按钮的委托点击处理器，不依赖 bus.armed /
//   Worker / 轮询链路，与总线逻辑互不影响。
//
// 总开关（v1.4.3）：插件目录下必须存在 bus.armed 标志文件，本脚本才开始轮询；
// 未放置时每 15s 静默探测一次（单个 404 请求），零总线流量。v1.4.17 起探测
//   双向：文件被任何一方（编辑器直写 / 手工 / 生成页面板开关）写删时，开→启
//   轮询、关→停轮询并同步面板 armed 开关显示（服务端本就每请求实时判定，此处
//   只管客户端节拍与回显，显示回显 ≤15s）。放置/删除即生效，无需刷新页面。
//
// Worker 心跳（v1.4.11）：轮询节拍从主线程 setInterval 挪进 Dedicated Worker
//   （Blob URL 内联创建，零新增文件）。v1.4.10 及以前主线程定时器会被 Chromium
//   后台节流——标签页隐藏后定时器最少 1s 一次，隐藏超 5 分钟进入 intensive
//   throttling 最长 1 分钟一次（实测对照见 开发/scripts/scratch/throttle-probe/，
//   后台 250ms 定时器实测退化为 1~60s），WebUI 窗口非活跃时生成指令被延后消费。
//   Worker 有独立事件循环、定时器不受页面可见性节流：Worker 每 POLL_MS
//   postMessage 一次心跳，页面 onmessage 里执行原轮询/消费逻辑（fetch 与
//   DOM 点击仍在主线程，行为不变；message 事件不属定时器，后台页仍即时派发）。
//   Worker/Blob 不可用时自动回退主线程定时器（v1.4.10 行为）；页面
//   pagehide/beforeunload 时 terminate worker。
//
// 指令延迟压缩（v1.4.13）：发布指令到点生成的链路从「轮询发现(≤500ms) +
//   apply 盲等(700ms) + 切页驻留(150ms)」压到平均 ~300ms 内——
//   a) 轮询间隔 500ms→200ms（armed 态；未 armed 的 15s 探测不变，本地空转
//      开销可忽略）；
//   b) apply 盲等 700ms 改为完成信号驱动：服务端 apply handler 末尾向
//      status.json 写 applied_ts（毫秒时间戳，v1.4.13 起），JS 点 apply 后轮询
//      /feetag/bus/status，见 applied_ts > cmd.ts 即参数已在服务端算完回包，
//      小驻留（等 gr.update 客户端落值）后立即点生成；信号超时（600ms，旧版
//      插件无该字段/检测链路异常）回落现行盲等（补足 700ms 总时长再点生成，
//      行为与 v1.4.10~v1.4.12 等价）；
//   c) 切页签提前到 apply 点击后立即执行（纯 DOM 操作，与 apply 事件回包无
//      数据依赖；原实现串行等待后才切）。
//   信号等待为三态门控状态机，由双节拍驱动：50ms 专用 setInterval（前台页
//   细粒度）+ Worker 心跳 onTick（隐藏页仍 200ms 一次不被节流——实测隐藏页里
//   主线程 setTimeout 会被 Chromium 节流到 ≥1s，旧版 700ms+150ms 两级盲等在
//   隐藏页实际退化成 ~3s+，这正是压缩前实测 write→busy 高达 3.5~4.3s 的主因）。
//
// cmd 消费语义（v1.4.6）：每条命令全局恰有一个消费者能取到（服务端先改名再读删，
// 恰一方 200、其余 404）；本地 lastTs（localStorage，跨页签 + 刷新防重放）保留
// 为双保险。任何异常（无命令 / 服务未起 / 组件缺失）一律静默不炸，不影响
// WebUI 自身使用。

(function () {
    "use strict";

    // 本扩展目录（从自身 script src 的 /file= 路径反解，防目录改名；失败用默认名）
    var BASE_DIR = "extensions/sd-webui-prompt-helper";
    try {
        var src = (document.currentScript && document.currentScript.src) || "";
        var m = src.match(/file=([^?]+)\/javascript\//);
        if (m && m[1].indexOf("extensions/") === 0) {
            BASE_DIR = m[1];
        }
    } catch (e) { /* 保持默认 */ }

    var PROBE_MS = 15000;              // bus.armed 探测间隔（未启用态，v1.4.13 不变）
    var POLL_MS = 200;                 // cmd 轮询 / Worker 心跳间隔（v1.4.13：500→200）
    var APPLY_SIGNAL_POLL_MS = 50;     // apply 完成信号轮询细粒度节拍（前台页）
    var APPLY_SIGNAL_TIMEOUT_MS = 600; // 信号等待超时：回落现行盲等
    var APPLY_WAIT_MS = 700;           // 信号失败回落盲等的总时长（对齐旧版 700ms）
    var APPLY_SETTLE_MS = 100;         // 信号命中后等 gr.update 客户端落值的驻留
    var STORE_KEY = "feetag_last_cmd_ts";

    var armed = false;
    var lastTs = null;
    try {
        lastTs = window.localStorage.getItem(STORE_KEY); // 跨页签去重 + 刷新后不重放旧指令
    } catch (e) { /* localStorage 不可用则仅内存去重 */ }

    var worker = null;      // v1.4.11 心跳 Worker
    var mainTimer = null;   // 回退态的主线程节拍定时器
    var probeCounter = 0;   // 未启用态按节拍折算 15s 探测
    var applyGate = null;   // v1.4.13 进行中的 apply→generate 门控（单活）

    function busUrl(name) {
        return "/file=" + BASE_DIR + "/" + name + "?t=" + Date.now();
    }

    function markHandled(ts) {
        lastTs = String(ts);
        try {
            window.localStorage.setItem(STORE_KEY, lastTs);
        } catch (e) { /* 内存去重兜底 */ }
    }

    function clickById(id, what) {
        var el = window.gradioApp().querySelector("#" + id);
        if (!el) {
            console.info("[feetag] 找不到组件，跳过：" + what + " (#" + id + ")");
            return false;
        }
        el.click();
        return true;
    }

    function clickGenerate(page) {
        try {
            clickById(page + "_generate", "生成钮");
        } catch (e) {
            console.info("[feetag] 触发生成失败：" + e);
        }
    }

    function disposeApplyGate() {
        if (applyGate) {
            applyGate.dispose();
            applyGate = null;
        }
    }

    // v1.4.13 apply→生成三态门控：0=等完成信号 → 1=驻留（截止点点生成）→ 2=完。
    // 双节拍驱动：50ms setInterval（前台细粒度）+ Worker 心跳 onTick（隐藏页
    // 不被节流）。信号 = /feetag/bus/status 的 applied_ts > cmd.ts（服务端 apply
    // handler 完成时写入的毫秒时间戳，编辑器与服务端同机同系统钟，且上一轮
    // 旧值必小于本轮 cmd.ts，不会误触发）。任何检测失败（字段缺失/请求异常/
    // 半截 JSON）静默等下一拍；超时回落盲等（settleAt 推到 t0+700ms，页签已
    // 提前切换，与旧版固定盲等等价）。
    function startApplyGate(cmdTs, page) {
        disposeApplyGate(); // 上一门控未完又来新指令：以后者为准（罕见，快速连发）
        var t0 = Date.now();
        var phase = 0;
        var settleAt = 0;
        var timer = null;

        function dispose() {
            phase = 2;
            if (timer) {
                clearInterval(timer);
                timer = null;
            }
        }

        function advance() {
            if (phase >= 2) return;
            var now = Date.now();
            if (phase === 0) {
                if (now - t0 >= APPLY_SIGNAL_TIMEOUT_MS) {
                    console.info("[feetag] apply 完成信号超时，回落盲等（总时长 " + APPLY_WAIT_MS + "ms）");
                    phase = 1;
                    settleAt = t0 + APPLY_WAIT_MS;
                    return;
                }
                fetch("/feetag/bus/status?t=" + Date.now(), { cache: "no-store" })
                    .then(function (res) { return res.ok ? res.json() : null; })
                    .then(function (st) {
                        if (phase === 0 && st && Number(st.applied_ts) > cmdTs) {
                            phase = 1;
                            settleAt = Date.now() + APPLY_SETTLE_MS;
                        }
                    })
                    .catch(function () { /* 半截 JSON / 服务未起：下一拍再查 */ });
                return;
            }
            if (now >= settleAt) {
                dispose();
                clickGenerate(page);
            }
        }

        applyGate = { advance: advance, dispose: dispose };
        timer = setInterval(advance, APPLY_SIGNAL_POLL_MS);
    }

    function trigger(cmd) {
        var page = cmd.page === "img2img" ? "img2img" : null;
        if (cmd.page === "txt2img") {
            page = "txt2img";
        }
        if (!page) {
            console.info("[feetag] 忽略暂不支持的目标页：" + cmd.page);
            return;
        }
        // 1) 点 apply（服务端读 params.json 回填界面 + ADetailer infotext 段）+
        //    ADetailer 独立钮兼容点击；2) 立即切页签（纯 DOM 操作，与 apply
        //    事件回包无数据依赖，在等待期内完成）；3) 门控等 apply 完成信号，
        //    命中即驻留后点生成，超时回落盲等。
        try {
            clickById("feetag_apply_" + page, "参数回填钮");
        } catch (e) { /* 回填失败不阻断触发 */ }
        try {
            clickById("feetag_adetailer_apply_" + page, "ADetailer 回填钮");
        } catch (e) { /* 未安装 ADetailer 时静默 */ }
        try {
            var sw = window["switch_to_" + page];
            if (typeof sw === "function") {
                sw();
            }
        } catch (e) { /* 已在目标页或切换失败，不阻断 */ }
        startApplyGate(Number(cmd.ts) || 0, page);
    }

    function poll() {
        // v1.4.6：改调服务端原子消费端点（同源，无 CORS 问题）；恰一方取到命令，
        // 其余消费者 404——多浏览器并存不再单槽竞态
        fetch("/feetag/bus/cmd?t=" + Date.now(), { cache: "no-store" })
            .then(function (res) { return res.ok ? res.json() : null; })
            .then(function (cmd) {
                if (!cmd || !cmd.action) return; // 404（无命令/已被取走）：常态，静默
                var ts = String(cmd.ts || "");
                if (!ts || ts === String(lastTs)) return; // 双保险：lastTs 防重放
                markHandled(ts);
                trigger(cmd);
            })
            .catch(function () { /* 无命令 / 服务未起：静默 */ });
    }

    // 总开关探测（v1.4.17 起双向，v1.4.18 扩到 bus.direct）：bus.armed 存在才
    // 开始 cmd 轮询；文件被任何一方（编辑器直写 / 手工 / 面板开关）写删时，
    // 服务端本就每请求实时判定，此处 15s 一探双向感知——开→启轮询、关→停
    // 轮询，并同步面板开关显示（syncCheckbox：程序化 dispatch change 让
    // gradio 拾取，触发的服务端写与文件现值幂等，无副作用）。bus.direct
    // 同款探测：面板「使用网页端插件」勾选态 = 文件不存在（勾选=页面链路）。
    // 直发模式下本脚本的 cmd 轮询恒 404（服务端消费线程自取），不执行。
    function syncCheckbox(elemId, state) {
        try {
            var box = document.querySelector("#" + elemId + " input[type=checkbox]");
            if (box && state !== null && box.checked !== state) {
                box.checked = state;
                box.dispatchEvent(new Event("change", { bubbles: true }));
            }
        } catch (e) { /* 显示同步尽力而为，不影响轮询 */ }
    }

    function fetchFlag(name) {
        // 探测插件目录标志文件（bus.armed / bus.direct）；服务未起返回 null（未知，不动状态）
        return fetch(busUrl(name), { cache: "no-store" })
            .then(function (res) { return res.ok; })
            .catch(function () { return null; });
    }

    function probe() {
        fetchFlag("bus.armed").then(function (nowArmed) {
            if (nowArmed === null) return;
            if (nowArmed && !armed) {
                armed = true;
                console.info("[feetag] 总线已启用（检测到 bus.armed），开始轮询 /feetag/bus/cmd（Worker 心跳驱动）");
            } else if (!nowArmed && armed) {
                armed = false;
                console.info("[feetag] 总线已停用（bus.armed 已移除）——恢复 15s 探测");
            }
            syncCheckbox("feetag_bus_armed", nowArmed);
        });
        fetchFlag("bus.direct").then(function (nowDirect) {
            if (nowDirect === null) return;
            // 勾选 = 页面链路 = bus.direct 不存在
            syncCheckbox("feetag_bus_direct", !nowDirect);
        });
    }

    // 统一节拍：15s 一次双向探测（armed 与否都探——开关显示与轮询启停随外部
    // 写入同步）；armed 态叠加 200ms cmd 轮询；v1.4.13：节拍同时驱动 apply 门控
    // （隐藏页里专用 setInterval 会被节流，Worker 心跳的 message 事件不受页面
    // 可见性影响，信号检测不因页签隐藏退化）
    function onTick() {
        if (applyGate) {
            applyGate.advance();
        }
        probeCounter += 1;
        if (probeCounter * POLL_MS >= PROBE_MS) {
            probeCounter = 0;
            probe();
        }
        if (armed) {
            poll();
        }
    }

    function stopWorker() {
        if (!worker) return;
        try { worker.terminate(); } catch (e) { /* 已终止 */ }
        worker = null;
    }

    // Worker/Blob 不可用（极老浏览器等）→ 主线程定时器兜底（v1.4.10 行为，
    // 后台标签页可能被浏览器节流，属降级而非失效）
    function fallbackToMainThread() {
        if (mainTimer || worker) return;
        mainTimer = setInterval(onTick, POLL_MS);
        console.info("[feetag] Worker 不可用，已回退主线程定时器");
    }

    function startWorker() {
        var heartbeat = "setInterval(function(){ postMessage(1); }, " + POLL_MS + ");";
        try {
            var blob = new Blob([heartbeat], { type: "application/javascript" });
            worker = new Worker(URL.createObjectURL(blob));
            worker.onmessage = onTick;
            worker.onerror = function () { stopWorker(); fallbackToMainThread(); };
        } catch (e) {
            fallbackToMainThread();
            return;
        }
        // 页面卸载/跳转时终止 Worker 与门控定时器，不悬挂线程
        window.addEventListener("pagehide", stopWorker);
        window.addEventListener("pagehide", disposeApplyGate);
        window.addEventListener("beforeunload", stopWorker);
        window.addEventListener("beforeunload", disposeApplyGate);
    }

    startWorker();
})();

// ---------------------------------------------------------------------------
// 复制插件目录（v1.4.17 连接引导；v1.4.18 起两处按钮并存）：生成页面板
// 「复制插件目录」（#feetag_copy_dir，服务新用户引导）与设置手风琴编辑器组
// 「复制插件路径」（#feetag_copy_dir_settings，服务分组收纳）——同一委托
// 处理器、同一取值逻辑（EXT_DIR 实时计算，静态值无漂移），不依赖
// bus.armed / Worker / 轮询链路，与总线逻辑互不影响。
// ---------------------------------------------------------------------------
(function () {
    "use strict";
    if (window.__feetagCopyDirBound) return;
    window.__feetagCopyDirBound = true;

    function flashButton(btn) {
        var original = btn.textContent;
        btn.textContent = "已复制 ✓";
        setTimeout(function () { btn.textContent = original; }, 1600);
    }

    function fallbackCopy(text, btn) {
        try {
            var ta = document.createElement("textarea");
            ta.value = text;
            ta.setAttribute("readonly", "");
            ta.style.position = "fixed";
            ta.style.opacity = "0";
            document.body.appendChild(ta);
            ta.focus();
            ta.select();
            document.execCommand("copy");
            document.body.removeChild(ta);
            flashButton(btn);
        } catch (e) {
            console.info("[feetag] 复制失败（路径已显示在旁，可手动选择复制）：" + e);
        }
    }

    function resolveDirText() {
        // 两处路径源（elem_id 挂在 gr.HTML 外层包装、data-dir 在内层 code 上）：
        // 生成页面板 + 设置区编辑器组；同源 EXT_DIR，取任一在场的即可
        var node = document.querySelector(
            "#feetag_plugin_dir [data-dir], #feetag_plugin_dir_settings [data-dir]")
            || document.querySelector("#feetag_plugin_dir code, #feetag_plugin_dir_settings code");
        if (!node) return "";
        var attr = node.getAttribute("data-dir");
        return (attr || node.textContent || "").trim();
    }

    document.addEventListener("click", function (ev) {
        var target = ev.target;
        var btn = target && target.closest
            ? target.closest("#feetag_copy_dir, #feetag_copy_dir_settings")
            : null;
        if (!btn) return;
        var text = resolveDirText();
        if (!text) {
            console.info("[feetag] 插件目录元素缺失，跳过复制");
            return;
        }
        if (navigator.clipboard && navigator.clipboard.writeText) {
            navigator.clipboard.writeText(text).then(
                function () { flashButton(btn); },
                function () { fallbackCopy(text, btn); }
            );
        } else {
            fallbackCopy(text, btn);
        }
    });
})();

// ---------------------------------------------------------------------------
// 页面状态快照/恢复（v1.4.18 Wave B，页面链路模式的持久化，best-effort 档、
// gradio 版本敏感——程序化设值依赖 dispatch input/change 让 gradio 拾取
// （A1111 updateInput 同款技巧），gradio 升级改 DOM 结构时可能部分失效，
// 失效仅影响恢复、不影响生成）。
//   采集：已接线字段表 elem_id 组件当前值（服务端 GET 下发清单）+
//         {page}_script_container 内未接线脚本输入的通用 DOM 扫描（按索引）；
//   触发：输入防抖 1.2s + pagehide keepalive 兜底；
//   回放：页面加载时 GET /feetag/bus/page-state 尽力回填——恢复只在加载时，
//         生成时总线 apply 对受控字段的写入照常覆盖（受控字段编辑器赢、
//         其余恢复用户值）；回放完成后才开始采集（防回放本身触发首轮快照）。
//   开关：插件目录放置 bus.page_state.disabled 即整体关闭（GET 的
//         enabled=false / POST 403，本 IIFE 静默退出）。与总线轮询逻辑
//   完全独立（不依赖 bus.armed 的内存态；端点侧 armed 门控）。
// ---------------------------------------------------------------------------
(function () {
    "use strict";
    if (window.__feetagPageStateBound) return;
    window.__feetagPageStateBound = true;

    var SAVE_DEBOUNCE_MS = 1200;
    var PAGES = ["txt2img", "img2img"];
    var fields = null;      // 服务端下发的已知字段清单 {txt2img: [elem_id...], ...}
    var saveTimer = null;

    function readValue(root) {
        try {
            var cb = root.querySelector("input[type=checkbox]");
            if (cb) return { checkbox: cb.checked };
            var inp = root.querySelector("input, textarea");
            if (inp) return { text: inp.value };
            var label = root.querySelector(".secondary-inner, .token-inner, span");
            var text = label ? (label.textContent || "").trim() : "";
            return text ? { label: text } : null;  // 下拉/单选：只采集展示文本，恢复跳过
        } catch (e) { return null; }
    }

    function collect() {
        var state = {};
        if (fields) {
            Object.keys(fields).forEach(function (page) {
                (fields[page] || []).forEach(function (id) {
                    var root = document.getElementById(id);
                    if (!root) return;               // 未装扩展的可选组组件：跳过
                    var v = readValue(root);
                    if (v) state[id] = v;
                });
            });
        }
        // 未接线脚本组件的通用 DOM 尽力扫描（脚本容器内按索引，回放尽力对位）
        PAGES.forEach(function (page) {
            var box = document.getElementById(page + "_script_container");
            if (!box) return;
            var inputs = box.querySelectorAll("input, textarea");
            var generic = [];
            for (var i = 0; i < inputs.length; i += 1) {
                var el = inputs[i];
                if (el.type === "checkbox") generic.push({ checkbox: el.checked });
                else if (typeof el.value === "string") generic.push({ text: el.value });
            }
            if (generic.length) state["g:" + page] = generic;
        });
        return state;
    }

    function postSave() {
        try {
            fetch("/feetag/bus/page-state", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(collect()),
                keepalive: true
            }).catch(function () { /* best-effort：未 armed / 网络断：静默 */ });
        } catch (e) { /* ignore */ }
    }

    // v1.4.18 追补：同步冲刷钩子——编辑器「藏回去」移窗前主动调它，立即执行
    // 一次采集+POST（不等 1.2s 防抖；防「改完立刻藏、防抖没来得及存」的边缘
    // 丢档）。失败静默，防抖兜底链路不变；字段清单未就绪时只采通用扫描区。
    window.__feetagFlushPageState = function () {
        try {
            postSave();
        } catch (e) { /* 冲刷失败静默：防抖 / 后续输入仍会补存 */ }
    };

    function scheduleSave() {
        if (saveTimer) clearTimeout(saveTimer);
        saveTimer = setTimeout(function () {
            saveTimer = null;
            postSave();
        }, SAVE_DEBOUNCE_MS);
    }

    function applyValue(id, v) {
        var root = document.getElementById(id);
        if (!root) return;
        try {
            var cb = root.querySelector("input[type=checkbox]");
            if (cb && typeof v.checkbox === "boolean") {
                if (cb.checked !== v.checkbox) {
                    cb.checked = v.checkbox;
                    cb.dispatchEvent(new Event("change", { bubbles: true }));
                }
                return;
            }
            var inp = root.querySelector("input, textarea");
            if (inp && typeof v.text === "string") {
                if (inp.value !== v.text) {
                    inp.value = v.text;
                    inp.dispatchEvent(new Event("input", { bubbles: true }));
                    inp.dispatchEvent(new Event("change", { bubbles: true }));
                }
            }
            // {label: ...}（下拉等非原生表单）：恢复跳过（采集只为留档）
        } catch (e) { /* 单项恢复失败跳过 */ }
    }

    fetch("/feetag/bus/page-state", { cache: "no-store" })
        .then(function (res) { return res.ok ? res.json() : null; })
        .then(function (doc) {
            if (!doc || !doc.enabled) return;   // 未 armed（404→null）或功能已关
            fields = doc.fields || {};
            var state = doc.state || {};
            Object.keys(state).forEach(function (id) {
                if (id.indexOf("g:") === 0) {
                    // 通用扫描区：尽力对位回放（脚本容器内输入按索引）
                    var page = id.slice(2);
                    var box = document.getElementById(page + "_script_container");
                    var list = state[id];
                    if (!box || !Array.isArray(list)) return;
                    var inputs = box.querySelectorAll("input, textarea");
                    for (var i = 0; i < list.length && i < inputs.length; i += 1) {
                        applyValueToInput(inputs[i], list[i]);
                    }
                    return;
                }
                applyValue(id, state[id]);
            });
            // 回放完成后才开始采集：回放本身触发的事件不计为用户输入
            document.addEventListener("input", scheduleSave, true);
            document.addEventListener("change", scheduleSave, true);
            window.addEventListener("pagehide", postSave);
        })
        .catch(function () { /* 服务未起：功能静默 */ });

    function applyValueToInput(el, v) {
        try {
            if (el.type === "checkbox" && typeof v.checkbox === "boolean") {
                if (el.checked !== v.checkbox) {
                    el.checked = v.checkbox;
                    el.dispatchEvent(new Event("change", { bubbles: true }));
                }
            } else if (typeof v.text === "string" && el.value !== v.text) {
                el.value = v.text;
                el.dispatchEvent(new Event("input", { bubbles: true }));
                el.dispatchEvent(new Event("change", { bubbles: true }));
            }
        } catch (e) { /* 尽力恢复 */ }
    }
})();
