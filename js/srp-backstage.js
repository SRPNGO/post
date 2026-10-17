/* ============================================================================
 * SRAP Wiki · 内部资料的分层呈现
 * ----------------------------------------------------------------------------
 * 作用：
 *   页面中以 <div class="srp-backstage"> 包裹的内容默认不可见，
 *   仅在以内部视图方式打开页面时展开，并在展开块顶部插入
 *   "AI 整理 · 风险内容" 的说明条。
 *
 * 说明：
 *   参数名、取值的完整用法由维护者单独掌握（见站外维护文档），
 *   不在站点页面与本文档中记录。
 *
 * 注意：
 *   本机制用于让默认视图保持中性；
 *   它 **不是权限控制** —— 相关文本仍然存在于页面源码与仓库中。
 * ==========================================================================*/
(function () {
  "use strict";

  var OFF_VALUES = { "": 1, 0: 1, false: 1, off: 1, no: 1 };
  var ALL_VALUES = { 1: 1, true: 1, on: 1, yes: 1, all: 1 };

  function readParam(name) {
    try {
      var q = new URLSearchParams(window.location.search || "");
      if (q.has(name)) return q.get(name);
    } catch (_) {}
    try {
      var h = String(window.location.hash || "").replace(/^[#?]+/, "");
      if (h && h.indexOf("=") !== -1) {
        var hq = new URLSearchParams(h);
        if (hq.has(name)) return hq.get(name);
      }
    } catch (_) {}
    return null;
  }

  var raw = readParam("backstage");
  var isOff =
    raw === null || OFF_VALUES[String(raw).trim().toLowerCase()] === 1;
  var on = !isOff;
  var layers = null; // null 表示全部层

  if (on) {
    var v = String(raw).trim().toLowerCase();
    if (ALL_VALUES[v] !== 1) {
      layers = v
        .split(/[,，\s]+/)
        .map(function (s) {
          return s.trim();
        })
        .filter(Boolean);
      if (!layers.length) layers = null;
    }
  }

  // 立即挂到全局：主页的搜索逻辑与其它脚本都可直接读取
  window.SRP_BACKSTAGE = {
    on: on,
    raw: raw,
    layers: layers,
    /** 某一块是否应在本层组合下展开 */
    allows: function (layer) {
      if (!on) return false;
      if (!layers) return true;
      var l = String(layer || "")
        .trim()
        .toLowerCase();
      if (!l) return true; // 未标注层的块视为通用后台内容
      return layers.indexOf(l) !== -1;
    },
  };

  var NOTE_HTML =
    "<b>内部资料</b>：以下内容由 AI 依据《星球阁存档计划》原始资料整理与总结，" +
    "因涉及争议、尚未定论或存在风险，默认隐藏，仅在内部视图下显示。" +
    "原文未作删改，内容仅供参考，不代表官方立场，请以原始资料为准。";

  var BANNER_HTML =
    '<span class="srp-backstage-banner-dot"></span>' +
    "<b>backstage 内部视图已开启</b>：本页正在显示默认隐藏的内部资料。" +
    '<a href="' +
    "javascript:void(0)" +
    '" class="srp-backstage-banner-off">关闭（回到公开视图）</a>';

  function layerLabel(block) {
    var l = String(block.getAttribute("data-layer") || "").trim();
    var map = {
      quote: "原文留存",
      eval: "评价与定性",
      private: "个人声明 / 敏感",
    };
    if (!l) return "";
    return l
      .split(/[,，\s]+/)
      .filter(Boolean)
      .map(function (x) {
        return map[x.toLowerCase()] || x;
      })
      .join(" · ");
  }

  function ensureNote(block) {
    var existing = null;
    for (var i = 0; i < block.children.length; i++) {
      if (
        block.children[i].classList &&
        block.children[i].classList.contains("srp-backstage-note")
      ) {
        existing = block.children[i];
        break;
      }
    }
    var label = layerLabel(block);
    var title = String(block.getAttribute("data-title") || "").trim();
    var head =
      (title ? "<b>" + escapeHtml(title) + "</b>" : "") +
      (label
        ? '<span class="srp-backstage-layer">' + escapeHtml(label) + "</span>"
        : "");
    if (existing) {
      if (head) existing.innerHTML = head + "<br />" + NOTE_HTML;
      return;
    }
    var note = document.createElement("div");
    note.className = "srp-backstage-note";
    note.innerHTML = (head ? head + "<br />" : "") + NOTE_HTML;
    block.insertBefore(note, block.firstChild);
  }

  function escapeHtml(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function rewriteLinks() {
    var links = document.querySelectorAll("a[href]");
    for (var i = 0; i < links.length; i++) {
      var a = links[i];
      var href = a.getAttribute("href");
      if (!href) continue;
      // 跳过锚点 / 脚本 / 邮件 / 电话 / data
      if (/^(?:#|javascript:|mailto:|tel:|data:|blob:)/i.test(href)) continue;

      var u;
      try {
        u = new URL(href, document.baseURI); // 兼容 <base> 与相对路径
      } catch (_) {
        continue;
      }
      if (u.origin !== window.location.origin) continue; // 跨站链接不动

      u.searchParams.set("backstage", "1"); // 有就覆盖，没有就新增
      a.setAttribute("href", u.pathname + u.search + u.hash);
    }
  }

  function apply() {
    if (on) document.documentElement.classList.add("backstage-on");

    var blocks = document.querySelectorAll(".srp-backstage");
    for (var i = 0; i < blocks.length; i++) {
      var b = blocks[i];
      if (window.SRP_BACKSTAGE.allows(b.getAttribute("data-layer"))) {
        b.classList.add("bs-on");
        ensureNote(b);
      } else {
        b.classList.remove("bs-on");
      }
    }

    if (!on) return;

    rewriteLinks();

    if (document.getElementById("srpBackstageBanner")) return;
    if (!document.body) return;
    var bar = document.createElement("div");
    bar.id = "srpBackstageBanner";
    bar.className = "srp-backstage-banner";
    bar.innerHTML = BANNER_HTML;
    bar
      .querySelector(".srp-backstage-banner-off")
      .addEventListener("click", function () {
        try {
          var url = new URL(window.location.href);
          url.searchParams.delete("backstage");
          if (/#.*backstage=/.test(url.hash)) url.hash = "";
          window.location.href = url.toString();
        } catch (_) {
          window.location.href = window.location.pathname;
        }
      });
    document.body.insertBefore(bar, document.body.firstChild);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", apply);
  } else {
    apply();
  }
})();