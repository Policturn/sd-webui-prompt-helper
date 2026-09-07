// FeeTagHelper 生成页总线 · 浏览器端（v1.4.3，P2）
//
// 职责：轮询插件目录下的 cmd.json（编辑器写入），收到触发指令后——
//   1. 点击本插件的隐藏 apply 钮（服务端读 params.json → gr.update 回填界面组件，
//      选择器表与回填逻辑见 scripts/prompt_helper.py）；
//   2. 切换到目标页签（A1111 官方全局函数 switch_to_txt2img / switch_to_img2img）；
//   3. 点击该页生成钮，走 UI 正常队列（未覆盖的参数 = WebUI 界面当前值）。
//
// 总开关（v1.4.3）：插件目录下必须存在 bus.armed 标志文件，本脚本才开始轮询；
// 未放置时每 15s 静默探测一次（单个 404 请求），零总线流量。放置/删除即生效，
// 无需刷新页面。
//
// 总线文件经 WebUI 的 /file= 静态路由读取（与 A1111 加载本文件自身同一机制，
// 已实测可达）。任何异常（文件不存在 / 服务未起 / 组件缺失）一律静默不炸，
// 不影响 WebUI 自身使用。

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
    var POLL_MS = 500;       // cmd.json 轮询间隔（已启用态）
    var APPLY_WAIT_MS = 700; // 点 apply 后等 gradio 回填往返的时间
    var SWITCH_WAIT_MS = 150;
    var STORE_KEY = "feetag_last_cmd_ts";

    var armed = false;
    var lastTs = null;
    try {
        lastTs = window.localStorage.getItem(STORE_KEY); // 跨页签去重 + 刷新后不重放旧指令
    } catch (e) { /* localStorage 不可用则仅内存去重 */ }

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
        // 1) 先 apply（服务端读 params.json 回填界面），2) 切页签，3) 点生成
        try {
            clickById("feetag_apply_" + page, "参数回填钮");
        } catch (e) { /* 回填失败不阻断触发 */ }
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
        fetch(busUrl("cmd.json"), { cache: "no-store" })
            .then(function (res) { return res.ok ? res.json() : null; })
            .then(function (cmd) {
                if (!cmd || !cmd.action) return; // 文件不存在 / 空指令：常态，静默
                var ts = String(cmd.ts || "");
                if (!ts || ts === String(lastTs)) return; // 已处理过
                markHandled(ts);
                trigger(cmd);
            })
            .catch(function () { /* 404（尚未配置）/ 服务未起：静默 */ });
    }

    // 总开关探测：bus.armed 存在才开始 cmd 轮询；未启用时仅 15s 一次探测（零总线流量）
    function probe() {
        if (armed) return;
        fetch(busUrl("bus.armed"), { cache: "no-store" })
            .then(function (res) {
                if (res.ok && !armed) {
                    armed = true;
                    console.info("[feetag] 总线已启用（检测到 bus.armed），开始轮询 cmd.json");
                    setInterval(poll, POLL_MS);
                }
            })
            .catch(function () { /* 未启用 / 服务未起：静默 */ });
    }

    probe();
    setInterval(probe, PROBE_MS);
})();

