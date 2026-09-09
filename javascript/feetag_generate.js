// FeeTagHelper 生成页总线 · 浏览器端（v1.4.11）
//
// 职责：轮询服务端原子消费端点 /feetag/bus/cmd（编辑器写 cmd.json，服务端
//   读取并删除——v1.4.6 起不再经 /file= 直读：单槽文件在多浏览器 / 多页签
//   并存时会被竞态消费，实测用户命令被测试页面抢走），收到触发指令后——
//   1. 点击本插件的隐藏 apply 钮（服务端读 params.json → gr.update 回填界面组件，
//      选择器表与回填逻辑见 scripts/prompt_helper.py）；
//   2. 切换到目标页签（A1111 官方全局函数 switch_to_txt2img / switch_to_img2img）；
//   3. 点击该页生成钮，走 UI 正常队列（未覆盖的参数 = WebUI 界面当前值）。
//
// 总开关（v1.4.3）：插件目录下必须存在 bus.armed 标志文件，本脚本才开始轮询；
// 未放置时每 15s 静默探测一次（单个 404 请求），零总线流量。放置/删除即生效，
// 无需刷新页面。
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

    var PROBE_MS = 15000;    // bus.armed 探测间隔（未启用态）
    var POLL_MS = 500;       // cmd 轮询 / Worker 心跳间隔（已启用态）
    var APPLY_WAIT_MS = 700; // 点 apply 后等 gradio 回填往返的时间
    var SWITCH_WAIT_MS = 150;
    var STORE_KEY = "feetag_last_cmd_ts";

    var armed = false;
    var lastTs = null;
    try {
        lastTs = window.localStorage.getItem(STORE_KEY); // 跨页签去重 + 刷新后不重放旧指令
    } catch (e) { /* localStorage 不可用则仅内存去重 */ }

    var worker = null;      // v1.4.11 心跳 Worker
    var mainTimer = null;   // 回退态的主线程节拍定时器
    var probeCounter = 0;   // 未启用态按节拍折算 15s 探测

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

    function trigger(cmd) {
        var page = cmd.page === "img2img" ? "img2img" : null;
        if (cmd.page === "txt2img") {
            page = "txt2img";
        }
        if (!page) {
            console.info("[feetag] 忽略暂不支持的目标页：" + cmd.page);
            return;
        }
        // 1) 先 apply（服务端读 params.json 回填界面）+ ADetailer infotext 回填，2) 切页签，3) 点生成
        try {
            clickById("feetag_apply_" + page, "参数回填钮");
        } catch (e) { /* 回填失败不阻断触发 */ }
        try {
            clickById("feetag_adetailer_apply_" + page, "ADetailer 回填钮");
        } catch (e) { /* 未安装 ADetailer 时静默 */ }
        setTimeout(function () {
            try {
                var sw = window["switch_to_" + page];
                if (typeof sw === "function") {
                    sw();
                }
            } catch (e) { /* 已在目标页或切换失败，不阻断 */ }
            setTimeout(function () {
                try {
                    clickById(page + "_generate", "生成钮");
                } catch (e) {
                    console.info("[feetag] 触发生成失败：" + e);
                }
            }, SWITCH_WAIT_MS);
        }, APPLY_WAIT_MS);
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

    // 总开关探测：bus.armed 存在才开始 cmd 轮询；未启用时仅 15s 一次探测（零总线流量）
    function probe() {
        if (armed) return;
        fetch(busUrl("bus.armed"), { cache: "no-store" })
            .then(function (res) {
                if (res.ok && !armed) {
                    armed = true;
                    console.info("[feetag] 总线已启用（检测到 bus.armed），开始轮询 /feetag/bus/cmd（Worker 心跳驱动）");
                }
            })
            .catch(function () { /* 未启用 / 服务未起：静默 */ });
    }

    // 统一节拍：armed 走高频轮询，未 armed 按节拍折算 15s 探测（语义与 v1.4.10 一致）
    function onTick() {
        if (armed) {
            poll();
            return;
        }
        probeCounter += 1;
        if (probeCounter * POLL_MS >= PROBE_MS) {
            probeCounter = 0;
            probe();
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
        // 页面卸载/跳转时终止 Worker，不悬挂线程
        window.addEventListener("pagehide", stopWorker);
        window.addEventListener("beforeunload", stopWorker);
    }

    startWorker();
})();
