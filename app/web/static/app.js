/* BiliBot 本地控制台前端 */
(() => {
  "use strict";
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  let STATUS = null;
  let CFG = null;
  let loginKey = "";
  let loginTimer = null;
  let logsTimer = null;
  let refreshing = false;
  let currentTab = "overview";

  // ------------------------------------------------------------------ 工具
  async function api(path, opts = {}) {
    const init = { method: opts.method || "GET", headers: {} };
    if (opts.body !== undefined) {
      init.headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(opts.body);
    }
    const res = await fetch(path, init);
    let data;
    try { data = await res.json(); } catch (e) { throw new Error(`HTTP ${res.status}`); }
    if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
    return data;
  }

  function toast(msg, kind = "") {
    const el = $("#toast");
    el.textContent = msg;
    el.className = "toast " + kind;
    el.hidden = false;
    clearTimeout(toast._t);
    toast._t = setTimeout(() => { el.hidden = true; }, 3600);
  }

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  function short(v, n = 60) {
    let s = typeof v === "string" ? v : JSON.stringify(v);
    s = s == null ? "" : s;
    return s.length > n ? s.slice(0, n) + "…" : s;
  }

  function fmtTs(ts) {
    if (!ts) return "-";
    const d = new Date(Number(ts) * 1000);
    const p = (n) => String(n).padStart(2, "0");
    return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
  }

  function fmtBytes(n) {
    n = Number(n) || 0;
    if (n < 1024) return n + " B";
    if (n < 1024 * 1024) return (n / 1024).toFixed(1) + " KB";
    if (n < 1024 * 1024 * 1024) return (n / 1024 / 1024).toFixed(1) + " MB";
    return (n / 1024 / 1024 / 1024).toFixed(2) + " GB";
  }

  function parseList(text) {
    return String(text || "").split(/[,，\n]/).map(s => s.trim()).filter(Boolean);
  }

  function splitNums(text) {
    return parseList(text).map(s => Number(s)).filter(n => Number.isFinite(n) && n > 0);
  }

  function setPill(el, cls, text) {
    if (!el) return;
    el.className = "pill " + (cls || "idle");
    el.textContent = text;
  }

  // ------------------------------------------------------------------ 状态
  async function refreshStatus() {
    if (refreshing) return;
    refreshing = true;
    try {
      const s = await api("/api/status");
      STATUS = s;
      renderStatus(s);
    } catch (e) {
      setPill($("#pill"), "error", "离线");
    } finally {
      refreshing = false;
    }
  }

  function renderStatus(s) {
    const running = !!s.running;
    const paused = !!s.paused;
    let cls = "idle", text = "未启动";
    if (paused) { cls = "paused"; text = "已暂停"; }
    else if (s.state === "dry-run") { cls = "dry"; text = "dry-run"; }
    else if (running) { cls = "running"; text = "运行中"; }
    else if (s.last_error) { cls = "error"; text = "异常"; }
    setPill($("#pill"), cls, text);
    setPill($("#run-state"), cls, s.state || "idle");

    const login = s.login || {};
    $("#head-user").textContent = login.isLogin
      ? (login.uname === "登录中…" ? "登录中…" : `${login.uname || "已登录"} · ${login.mid || ""}`)
      : "未登录";
    const llm = s.llm || {};
    $("#head-model").textContent = llm.message === "检测中" ? "检测中…" : (llm.ok ? "模型在线" : "模型离线");
    $("#head-model").title = llm.message || "";

    $("#stat-comment").textContent = (s.counts && s.counts.today_comment) || 0;
    $("#stat-nested").textContent = (s.counts && s.counts.today_nested) || 0;
    $("#stat-dm").textContent = (s.counts && s.counts.today_dm) || 0;
    $("#stat-seen").textContent = (s.counts && s.counts.seen_videos) || 0;

    $("#run-current").textContent = s.current || (running ? "运行中" : "等待启动");
    const err = $("#run-error");
    err.textContent = s.last_error || "";
    err.hidden = !s.last_error;

    const c = s.circuit || {};
    $("#run-circuit").textContent = c.open
      ? `熔断中：${c.reason || ""}（恢复约 ${fmtTs(c.until)}，连续失败 ${c.failures || 0}）`
      : `风控状态：正常（连续失败 ${c.failures || 0}）`;
    $("#run-llm").textContent = `模型：${llm.base_url || "-"} / ${llm.model || "-"} · ${llm.message || ""}`;

    const limits = s.limits || {};
    const label = { comment: "评论", nested: "楼中楼", dm: "私信" };
    const parts = [];
    ["comment", "nested", "dm"].forEach(k => {
      const L = limits[k] || {};
      const cap = Number(L.cap || 0);
      parts.push(`${label[k]} ${L.used || 0}/${cap > 0 ? cap : "∞"}`);
    });
    const blocked = Object.values(limits).find(L => L && L.can === false);
    $("#quota-line").textContent = `今日配额：${parts.join(" · ")}${blocked ? `（${blocked.why}）` : ""}`;

    const skip = s.last_skip || "";
    $("#last-skip").textContent = skip
      ? `最近跳过：${skip}${s.last_skip_ts ? `（${fmtTs(s.last_skip_ts)}）` : ""}`
      : "";

    $("#btn-start").disabled = running;
    $("#btn-pause").disabled = !running || paused;
    $("#btn-resume").disabled = !running || !paused;
    $("#btn-stop").disabled = !running;
    $("#btn-once").disabled = running;
    $("#btn-once-real").disabled = running;
    if ($("#btn-test-bv")) $("#btn-test-bv").disabled = running;

    const body = $("#actions-body");
    const acts = s.recent_actions || [];
    if (!acts.length) {
      body.innerHTML = `<tr><td colspan="4" class="muted">暂无</td></tr>`;
    } else {
      body.innerHTML = acts.map(a => {
        const ok = a.ok === 1 || a.ok === true || a.ok === undefined;
        const detail = a.error ? a.error : short(a.detail, 52);
        return `<tr>
          <td class="muted">${esc(fmtTs(a.created_at))}</td>
          <td>${esc(a.kind || "")}</td>
          <td>${esc(short(a.target, 34))}</td>
          <td class="${ok ? "ok" : "bad"}">${esc(detail)}</td>
        </tr>`;
      }).join("");
    }
  }

  // ------------------------------------------------------------------ 账号
  function renderLogin(login) {
    login = login || {};
    const box = $("#login-account");
    if (login.isLogin) {
      box.innerHTML = `<div class="name">${esc(login.uname || "已登录")}</div>
        <div class="muted">UID ${esc(login.mid || "")} · 大会员 ${login.vip ? "是" : "否"}</div>`;
    } else {
      box.innerHTML = `<div class="name">未登录</div>
        <div class="muted">${esc(login.error || "扫码后这里会显示账号")}</div>`;
    }
    $("#login-info").textContent = login.isLogin
      ? `当前账号：${login.uname || ""}（${login.mid || ""}）`
      : (login.error || "");
  }

  async function refreshAccount() {
    try {
      const r = await api("/api/login/status");
      renderLogin(r.login);
    } catch (e) { toast(e.message, "bad"); }
  }

  async function startLogin() {
    try {
      const r = await api("/api/login/qr", { method: "POST", body: {} });
      if (!r.ok) { toast(r.error || "生成失败", "bad"); return; }
      loginKey = r.key;
      const box = $("#qrbox");
      box.innerHTML = "";
      try {
        new QRCode(box, {
          text: r.url, width: 220, height: 220,
          correctLevel: QRCode.CorrectLevel.M,
        });
      } catch (e) {
        box.innerHTML = `<div style="padding:12px;color:#111;font-size:11px;word-break:break-all">${esc(r.url)}</div>`;
      }
      $("#login-status").textContent = "请用 B 站 App 扫码";
      $("#btn-qr").disabled = true;
      clearInterval(loginTimer);
      loginTimer = setInterval(pollLogin, 2000);
    } catch (e) { toast(e.message, "bad"); }
  }

  async function pollLogin() {
    if (!loginKey) return;
    try {
      const r = await api("/api/login/poll?key=" + encodeURIComponent(loginKey));
      if (r.status === "ok") {
        clearInterval(loginTimer);
        loginTimer = null;
        loginKey = "";
        $("#btn-qr").disabled = false;
        $("#login-status").textContent = "登录成功";
        toast(`登录成功：${(r.user && r.user.uname) || ""}`, "ok");
        renderLogin(r.user || {});
        refreshStatus();
      } else if (r.status === "expired") {
        clearInterval(loginTimer);
        loginTimer = null;
        loginKey = "";
        $("#btn-qr").disabled = false;
        $("#login-status").textContent = "二维码过期，请重新生成";
      } else if (r.status === "scanned") {
        $("#login-status").textContent = "已扫码，请在手机上确认";
      } else if (r.status === "error") {
        clearInterval(loginTimer);
        loginTimer = null;
        loginKey = "";
        $("#btn-qr").disabled = false;
        $("#login-status").textContent = r.message || "登录失败";
        toast(r.message || "登录失败", "bad");
      } else {
        $("#login-status").textContent = r.message || "等待扫码…";
      }
    } catch (e) {
      clearInterval(loginTimer);
      loginTimer = null;
      loginKey = "";
      $("#btn-qr").disabled = false;
      toast(e.message, "bad");
    }
  }

  // ------------------------------------------------------------------ 人设
  async function loadPersona() {
    try {
      const r = await api("/api/persona");
      $("#persona-name").textContent = (r.persona && r.persona.name) || "-";
      $("#persona-json").value = JSON.stringify(r.persona, null, 2);
    } catch (e) { toast(e.message, "bad"); }
  }

  async function savePersona() {
    const raw = ($("#persona-json").value || "").trim();
    if (!raw) { toast("人设内容为空", "bad"); return; }
    let p;
    try { p = JSON.parse(raw); }
    catch (e) { toast("人设 JSON 格式不对：" + e.message, "bad"); return; }
    if (!p || typeof p !== "object" || Array.isArray(p)) {
      toast("人设必须是一个 JSON 对象（不能是数组/字符串）", "bad"); return;
    }
    if (p.persona && typeof p.persona === "object" && !p.name) p = p.persona;
    if (!String(p.name || "").trim()) { toast("人设缺少 name，名字不能为空", "bad"); return; }
    try {
      const r = await api("/api/persona", { method: "POST", body: p });
      $("#persona-name").textContent = (r.persona && r.persona.name) || "-";
      toast("人设已保存", "ok");
      refreshStatus();
    } catch (e) { toast(e.message, "bad"); }
  }

  // ------------------------------------------------------------------ 配置
  function fillConfig(c) {
    CFG = c;
    const llm = c.llm || {}, bot = c.bot || {}, at = bot.at_master || {}, dm = bot.dm || {};
    $("#llm-base").value = llm.base_url || "";
    $("#llm-model").value = llm.model || "";
    $("#llm-key").value = llm.api_key || "";
    $("#q-comment").value = bot.comment_per_day ?? 8;
    $("#q-nested").value = bot.nested_per_day ?? 15;
    $("#q-dm").value = bot.dm_per_day ?? 5;
    $("#q-min").value = bot.min_interval_min ?? 20;
    $("#q-max").value = bot.max_interval_min ?? 60;
    $("#q-prob").value = bot.comment_probability ?? 0.55;
    $("#q-approval").value = bot.approval_mode || "auto";
    $("#q-hours").value = (bot.active_hours || []).join(",");
    $("#q-at-mode").value = at.mode || "taste";
    $("#q-at-uid").value = at.uid || "";
    $("#q-at-name").value = at.name || "";
    $("#q-at-taste").value = (at.taste || []).join(",");
    $("#q-at-daily").value = at.daily_limit ?? 8;
    $("#q-dm-mode").value = dm.proactive || "whitelist";
    $("#q-dm-whitelist").value = (dm.whitelist || []).map(x => typeof x === "object" ? (x.uid || "") : x).join(",");
    $("#config-json").value = JSON.stringify(c, null, 2);
  }

  async function loadConfig() {
    try {
      const r = await api("/api/config");
      fillConfig(r.config || {});
    } catch (e) { toast(e.message, "bad"); }
  }

  function num(id, fallback) {
    const raw = String($(id).value ?? "").trim();
    if (raw === "") return fallback;
    const v = Number(raw);
    return Number.isFinite(v) ? v : fallback;
  }

  async function saveQuick() {
    const patch = {
      llm: {
        base_url: $("#llm-base").value.trim(),
        model: $("#llm-model").value.trim(),
        api_key: $("#llm-key").value,
      },
      bot: {
        comment_per_day: num("#q-comment", 8),
        nested_per_day: num("#q-nested", 15),
        dm_per_day: num("#q-dm", 5),
        min_interval_min: num("#q-min", 20),
        max_interval_min: num("#q-max", 60),
        comment_probability: num("#q-prob", 0.55),
        approval_mode: $("#q-approval").value,
        active_hours: parseList($("#q-hours").value),
        at_master: {
          mode: $("#q-at-mode").value,
          uid: Number($("#q-at-uid").value) || 0,
          name: $("#q-at-name").value.trim(),
          taste: parseList($("#q-at-taste").value),
          daily_limit: num("#q-at-daily", 8),
        },
        dm: {
          proactive: $("#q-dm-mode").value,
          whitelist: splitNums($("#q-dm-whitelist").value),
        },
      },
    };
    try {
      const r = await api("/api/config", { method: "POST", body: patch });
      fillConfig(r.config || {});
      toast("设置已保存", "ok");
      refreshStatus();
    } catch (e) { toast(e.message, "bad"); }
  }

  async function saveConfigJson() {
    let p;
    try { p = JSON.parse($("#config-json").value); }
    catch (e) { toast("配置 JSON 格式不对：" + e.message, "bad"); return; }
    try {
      const r = await api("/api/config", { method: "POST", body: p });
      fillConfig(r.config || {});
      toast("配置已保存", "ok");
      refreshStatus();
    } catch (e) { toast(e.message, "bad"); }
  }

  async function testModel() {
    $("#model-test-result").textContent = "测试中…";
    try {
      const r = await api("/api/model/test", { method: "POST", body: {} });
      $("#model-test-result").textContent = r.message || (r.ok ? "连接成功" : "连接失败");
      toast(r.message || (r.ok ? "模型连接成功" : "模型连接失败"), r.ok ? "ok" : "bad");
      refreshStatus();
    } catch (e) {
      $("#model-test-result").textContent = e.message;
      toast(e.message, "bad");
    }
  }

  async function loadCache() {
    try {
      const r = await api("/api/cache");
      $("#cache-info").textContent =
        `数据 ${fmtBytes(r.data_bytes)} · 缓存 ${fmtBytes(r.cache_bytes)} · 日志 ${fmtBytes(r.logs_bytes)} · 数据库 ${fmtBytes(r.db_bytes)}`;
    } catch (e) { /* 忽略 */ }
  }

  async function cleanCache(kind) {
    try {
      const r = await api("/api/cache/clean", { method: "POST", body: { kind } });
      toast(`已清理，释放 ${fmtBytes(r.freed)}` + (r.errors && r.errors.length ? "（部分文件占用）" : ""), "ok");
      loadCache();
    } catch (e) { toast(e.message, "bad"); }
  }

  // ------------------------------------------------------------------ 语音
  async function loadVoice() {
    try {
      const r = await api("/api/voice/status");
      const asr = r.asr || {};
      $("#voice-info").textContent = `本地语音识别：${asr.ok ? "就绪" : "不可用"} ｜ ${asr.message || "-"}`;
    } catch (e) {
      $("#voice-info").textContent = "语音状态读取失败：" + e.message;
    }
  }

  async function voiceAsr() {
    const bvid = ($("#voice-bvid").value || "").trim();
    if (!bvid) { toast("先填 BV 号", "bad"); return; }
    const btn = $("#btn-voice-asr");
    const out = $("#voice-asr-out");
    btn.disabled = true;
    out.textContent = "抓音轨 → 本地识别中…";
    try {
      const r = await api("/api/voice/asr", {
        method: "POST",
        body: { bvid, seconds: Number($("#voice-seconds").value) || 120 },
      });
      if (!r.ok) throw new Error(r.error || "识别失败");
      out.textContent = `${r.text || "（没有识别到语音）"}\n\n音频时长 ${r.duration || 0}s · 截取 ${r.seconds || 0}s`;
    } catch (e) {
      out.textContent = "失败：" + e.message;
    } finally {
      btn.disabled = false;
    }
  }

  // ------------------------------------------------------------------ 草稿
  async function loadDrafts() {
    try {
      const r = await api("/api/drafts");
      const rows = r.drafts || [];
      const body = $("#drafts-body");
      if (!rows.length) {
        body.innerHTML = `<tr><td colspan="5" class="muted">暂无草稿</td></tr>`;
        return;
      }
      body.innerHTML = rows.map(d => {
        const link = d.bvid ? `https://www.bilibili.com/video/${esc(d.bvid)}` : "#";
        const status = d.status || "pending";
        const ops = status === "pending"
          ? `<button class="btn small primary btn-approve" data-id="${esc(d.id)}">发布</button>
             <button class="btn small btn-reject" data-id="${esc(d.id)}">忽略</button>`
          : `<span class="muted">${esc(status)}</span>`;
        return `<tr>
          <td class="muted">${esc(fmtTs(d.created_at))}</td>
          <td>${esc(d.kind || "")}</td>
          <td><a href="${link}" target="_blank" rel="noreferrer">${esc(d.bvid || "")}</a></td>
          <td class="msg">${esc(d.message || "")}</td>
          <td>${ops}</td>
        </tr>`;
      }).join("");
    } catch (e) { toast(e.message, "bad"); }
  }

  async function draftAction(id, action) {
    if (action === "approve" && !confirm("确认发布这条评论到 B 站？")) return;
    try {
      const r = await api(`/api/drafts/${id}`, { method: "POST", body: { action } });
      toast(r.message || (action === "approve" ? "已发布" : "已忽略"), r.ok ? "ok" : "bad");
      loadDrafts();
      refreshStatus();
    } catch (e) { toast(e.message, "bad"); }
  }

  // ------------------------------------------------------------------ 日志
  async function loadLogs() {
    try {
      const r = await api("/api/logs?n=300");
      const box = $("#logbox");
      const atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 60;
      box.textContent = (r.lines || []).join("\n") || "（暂无日志）";
      if (atBottom) box.scrollTop = box.scrollHeight;
    } catch (e) { /* 忽略 */ }
  }

  function startLogs() {
    clearInterval(logsTimer);
    logsTimer = setInterval(() => { if (currentTab === "logs") loadLogs(); }, 5000);
  }

  function stopLogs() {
    clearInterval(logsTimer);
    logsTimer = null;
  }

  // ------------------------------------------------------------------ 运行控制
  async function botAction(action) {
    try {
      const r = await api("/api/bot/" + action, { method: "POST", body: {} });
      toast(r.message || (r.ok ? "已执行" : r.error || "失败"), r.ok ? "ok" : "bad");
      refreshStatus();
    } catch (e) { toast(e.message, "bad"); }
  }

  async function runOnce(real) {
    const btn = real ? $("#btn-once-real") : $("#btn-once");
    btn.disabled = true;
    const out = $("#once-result");
    out.hidden = false;
    out.textContent = real
      ? "真实单跑：选视频 → 解析 → 生成；审核模式会停在草稿箱…"
      : "单跑中：刷推荐 → 选视频 → 解析 → 生成草稿…";
    try {
      const r = await api("/api/bot/once", { method: "POST", body: { dry_run: !real, force: !!real } });
      out.textContent = JSON.stringify(r, null, 2);
      let msg = r.ok ? "完成" : (r.error || "失败");
      if (r.ok && r.review) msg = "已生成，等待你在草稿箱审核";
      else if (r.ok && r.dry_run) msg = "dry-run 完成，草稿已生成";
      else if (r.ok && r.posted) msg = "已提交 B 站；若几分钟后仍看不到，可能被审核/仅自己可见（看日志 shadowban）";
      else if (r.ok && r.skipped) msg = "本轮跳过：" + r.skipped;
      toast(msg, r.ok ? "ok" : "bad");
      loadDrafts();
      refreshStatus();
    } catch (e) {
      out.textContent = "失败：" + e.message;
      toast(e.message, "bad");
    } finally {
      btn.disabled = false;
    }
  }

  async function testBv() {
    const bvid = ($("#test-bvid").value || "").trim();
    if (!/^BV[0-9A-Za-z]{10}$/.test(bvid)) {
      toast("请输入正确的 BV 号（BV + 10 位）", "bad");
      return;
    }
    const btn = $("#btn-test-bv");
    const out = $("#test-bv-result");
    btn.disabled = true;
    out.hidden = false;
    out.textContent = `测试 ${bvid}：读取原视频 → 标签/字幕/弹幕/热评 → 生成草稿（不发布）…`;
    try {
      const r = await api("/api/bot/once", { method: "POST", body: { bvid, deep: true } });
      out.textContent = JSON.stringify(r, null, 2);
      toast(r.ok ? `已生成草稿 #${r.draft_id || "?"}，未发布` : (r.error || "测试失败"), r.ok ? "ok" : "bad");
      loadDrafts();
      refreshStatus();
    } catch (e) {
      out.textContent = "失败：" + e.message;
      toast(e.message, "bad");
    } finally {
      btn.disabled = false;
    }
  }

  async function refreshFeed() {
    const list = $("#feed-list");
    list.innerHTML = `<p class="muted">刷推荐中…</p>`;
    try {
      const r = await api("/api/feed?limit=10");
      const vids = r.videos || [];
      if (!vids.length) {
        list.innerHTML = `<p class="muted">没有候选视频（可能未登录或被风控）。</p>`;
        return;
      }
      list.innerHTML = vids.map(v => {
        const dur = Number(v.duration) || 0;
        const mm = String(Math.floor(dur / 60)).padStart(2, "0");
        const ss = String(dur % 60).padStart(2, "0");
        return `<div class="feed-item">
          <span class="t">${esc(v.title || v.bvid)}</span>
          <span class="meta">${esc(v.owner || "")} · ${mm}:${ss} · ${esc(v.source || "")}</span>
        </div>`;
      }).join("");
    } catch (e) {
      list.innerHTML = `<p class="muted">失败：${esc(e.message)}</p>`;
    }
  }

  // ------------------------------------------------------------------ 标签页
  function switchTab(name) {
    currentTab = name;
    if (location.hash !== "#" + name) history.replaceState(null, "", "#" + name);
    $$("#tabs button").forEach(b => b.classList.toggle("active", b.dataset.tab === name));
    $$(".tab-panel").forEach(p => p.classList.toggle("active", p.id === "tab-" + name));
    if (name === "logs") { loadLogs(); startLogs(); } else stopLogs();
    if (name === "drafts") loadDrafts();
    if (name === "settings") { loadConfig(); loadCache(); }
    if (name === "persona") loadPersona();
    if (name === "voice") loadVoice();
    if (name === "login") refreshAccount();
  }

  // ------------------------------------------------------------------ 绑定
  function bind() {
    $$("#tabs button").forEach(b => b.addEventListener("click", () => switchTab(b.dataset.tab)));

    $("#btn-start").addEventListener("click", () => {
      if (STATUS && STATUS.login && !STATUS.login.isLogin) {
        toast("先扫码登录", "bad"); switchTab("login"); return;
      }
      if (STATUS && STATUS.llm && !STATUS.llm.ok) {
        toast("模型不在线，先在设置里测试连接", "bad"); switchTab("settings"); return;
      }
      if (!confirm("开始自动运行？机器人会真实发评论/回复/私信，受频率限制。")) return;
      botAction("start");
    });
    $("#btn-pause").addEventListener("click", () => botAction("pause"));
    $("#btn-resume").addEventListener("click", () => botAction("resume"));
    $("#btn-stop").addEventListener("click", () => {
      if (confirm("停止自动运行？")) botAction("stop");
    });
    $("#btn-once").addEventListener("click", () => runOnce(false));
    $("#btn-once-real").addEventListener("click", () => runOnce(true));
    $("#btn-test-bv").addEventListener("click", () => testBv());
    $("#btn-reset-quota").addEventListener("click", async () => {
      if (!confirm("把今日评论/楼中楼/私信计数清零，用于测试？")) return;
      try {
        const r = await api("/api/bot/reset_quota", { method: "POST", body: {} });
        toast(r.message || "已重置");
        refreshStatus();
      } catch (e) { toast(`重置失败：${e.message}`, "bad"); }
    });

    $("#btn-feed").addEventListener("click", refreshFeed);
    $("#btn-qr").addEventListener("click", startLogin);
    $("#btn-persona-load").addEventListener("click", loadPersona);
    $("#btn-persona-save").addEventListener("click", savePersona);
    $("#btn-config-load").addEventListener("click", loadConfig);
    $("#btn-config-save").addEventListener("click", saveConfigJson);
    $("#btn-model-test").addEventListener("click", testModel);
    $("#btn-save-quick").addEventListener("click", saveQuick);
    $("#btn-cache").addEventListener("click", () => cleanCache("cache"));
    $("#btn-cache-logs").addEventListener("click", () => cleanCache("logs"));
    $("#btn-voice-asr").addEventListener("click", voiceAsr);
    $("#btn-drafts").addEventListener("click", loadDrafts);
    $("#btn-logs").addEventListener("click", loadLogs);

    $("#drafts-body").addEventListener("click", (e) => {
      const b = e.target.closest("button");
      if (!b) return;
      const id = b.dataset.id;
      if (b.classList.contains("btn-approve")) draftAction(id, "approve");
      if (b.classList.contains("btn-reject")) draftAction(id, "reject");
    });
  }

  // ------------------------------------------------------------------ 首启向导
  let wzStep = 1;
  const WZ_TOTAL = 5;

  function wzOpen() {
    $("#wizard").hidden = false;
    wzStep = 1;
    wzRender();
    wzCheckEnv();
  }

  function wzClose() {
    $("#wizard").hidden = true;
  }

  function wzRender() {
    $$(".wz-step").forEach(el => { el.hidden = Number(el.dataset.wz) !== wzStep; });
    $("#wz-progress").textContent = `${wzStep} / ${WZ_TOTAL}`;
    $("#wz-prev").disabled = wzStep === 1;
    $("#wz-next").textContent = wzStep === WZ_TOTAL ? "完成" : "下一步";
    if (wzStep === 2) {
      const ok = STATUS && STATUS.login && STATUS.login.isLogin;
      $("#wz-login-state").textContent = ok
        ? `已登录：${STATUS.login.uname || STATUS.login.mid || ""}`
        : "未登录";
    }
    if (wzStep === 3) {
      const p = (STATUS && STATUS.persona) || {};
      if (!$("#wz-name").value) $("#wz-name").value = p.name || "";
      if (!$("#wz-identity").value) $("#wz-identity").value = p.identity || "";
      if (!$("#wz-interests").value) $("#wz-interests").value = (p.interests || []).join(",");
    }
    if (wzStep === 4 && CFG) {
      const at = (CFG.bot || {}).at_master || {};
      if (!$("#wz-at-uid").value) $("#wz-at-uid").value = at.uid || "";
      if (!$("#wz-at-name").value) $("#wz-at-name").value = at.name || "";
      if (!$("#wz-taste").value) $("#wz-taste").value = (at.taste || []).join(",");
    }
    if (wzStep === WZ_TOTAL) {
      const at = (CFG && CFG.bot && CFG.bot.at_master) || {};
      $("#wz-summary").textContent =
        `人设：${$("#wz-name").value || "默认"}\n` +
        `主人：${$("#wz-at-name").value || "未设置"} ${$("#wz-at-uid").value ? "(UID " + $("#wz-at-uid").value + ")" : ""}\n` +
        `节奏：${$("#wz-preset").selectedOptions[0].textContent}\n` +
        `@主人：${at.mode || "taste"}`;
    }
  }

  async function wzCheckEnv() {
    const box = $("#wz-checks");
    box.innerHTML = "<div class='wz-check'>检查中…</div>";
    try {
      const r = await Promise.race([
        api("/api/status"),
        new Promise((_, reject) => setTimeout(() => reject(new Error("状态读取超时")), 12000)),
      ]);
      STATUS = r;
      const login = r.login || {}, llm = r.llm || {}, voice = r.voice || {};
      const rows = [
        ["登录", login.isLogin, login.isLogin ? (login.uname || login.mid || "") : "未登录"],
        ["模型", llm.ok, llm.message || ""],
        ["语音 ASR", voice.asr && voice.asr.ok, (voice.asr && voice.asr.message) || ""],
      ];
      box.innerHTML = rows.map(([name, ok, msg]) =>
        `<div class="wz-check ${ok ? "ok" : "bad"}">${ok ? "✅" : "❌"} ${esc(name)}：${esc(String(msg || (ok ? "正常" : "未就绪")))}</div>`
      ).join("");
    } catch (e) {
      box.innerHTML = `<div class="wz-check bad">❌ 读取状态失败：${esc(e.message)}</div>`;
    }
  }

  async function wzNext() {
    if (wzStep >= WZ_TOTAL) {
      await wzFinish();
      return;
    }
    if (wzStep === 3) {
      const name = $("#wz-name").value.trim();
      if (name) {
        try {
          const r = await api("/api/persona");
          const p = r.persona || {};
          p.name = name;
          p.identity = $("#wz-identity").value.trim() || p.identity || "";
          p.interests = parseList($("#wz-interests").value);
          await api("/api/persona", { method: "POST", body: p });
          STATUS = STATUS || {}; STATUS.persona = p;
        } catch (e) { toast("保存人设失败：" + e.message, "bad"); return; }
      }
    }
    if (wzStep === 4) {
      const preset = $("#wz-preset").value;
      const quotas = {
        safe: [3, 5, 2], normal: [8, 15, 5], active: [15, 25, 8],
      }[preset] || [8, 15, 5];
      const patch = {
        bot: {
          comment_per_day: quotas[0],
          nested_per_day: quotas[1],
          dm_per_day: quotas[2],
          at_master: {
            mode: $("#wz-at-uid").value ? "taste" : "off",
            uid: Number($("#wz-at-uid").value) || 0,
            name: $("#wz-at-name").value.trim(),
            taste: parseList($("#wz-taste").value),
          },
        },
      };
      try {
        const r = await api("/api/config", { method: "POST", body: patch });
        CFG = r.config || CFG;
      } catch (e) { toast("保存设置失败：" + e.message, "bad"); return; }
    }
    wzStep += 1;
    wzRender();
  }

  async function wzFinish() {
    localStorage.setItem("biliBotWizardDone", "1");
    if ($("#wz-autostart").checked) {
      try { await api("/api/bot/start", { method: "POST", body: {} }); } catch (e) { toast(e.message, "bad"); }
    }
    wzClose();
    refreshStatus();
    toast("向导完成，建议先单跑一轮 dry-run", "ok");
  }

  function bindWizard() {
    $("#btn-wizard").addEventListener("click", wzOpen);
    $("#wz-close").addEventListener("click", wzClose);
    $("#wz-next").addEventListener("click", wzNext);
    $("#wz-prev").addEventListener("click", () => { if (wzStep > 1) { wzStep -= 1; wzRender(); } });
    $("#wz-go-login").addEventListener("click", () => { wzClose(); switchTab("login"); });
  }

  // ------------------------------------------------------------------ 初始化
  function init() {
    bind();
    bindWizard();
    setInterval(refreshStatus, 5000);
    setInterval(() => {
      const d = new Date();
      const p = (n) => String(n).padStart(2, "0");
      $("#footer-time").textContent = `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
    }, 1000);
    refreshStatus();
    loadConfig();
    loadPersona();
    loadDrafts();
    refreshAccount();
    loadCache();
    loadVoice();
    const hash = (location.hash || "").replace("#", "");
    switchTab(hash && $("#tab-" + hash) ? hash : "overview");
    const params = new URLSearchParams(location.search || "");
    if (!localStorage.getItem("biliBotWizardDone") || params.has("wizard")) {
      setTimeout(wzOpen, 700);
    }
  }

  document.addEventListener("DOMContentLoaded", init);
})();
