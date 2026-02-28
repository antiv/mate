/**
 * MATE Embeddable Chat Widget
 *
 * Drop-in script tag for any website. Creates a floating chat button that
 * opens an iframe pointing to the MATE widget chat page.
 *
 * Usage:
 *   <script
 *     src="https://your-mate-instance.com/widget/mate-widget.js"
 *     data-key="wk_abc123..."
 *     data-server="https://your-mate-instance.com"
 *     data-position="bottom-right"
 *     data-theme="auto"
 *     data-button-color="#2563eb"
 *   ></script>
 *
 * JS API:
 *   window.MateWidget.open()
 *   window.MateWidget.close()
 *   window.MateWidget.toggle()
 */
(function () {
  "use strict";

  // --- Read config from script tag -------------------------------------
  var scripts = document.getElementsByTagName("script");
  var currentScript = scripts[scripts.length - 1];
  // Prefer document.currentScript when available
  if (document.currentScript) currentScript = document.currentScript;

  var CONFIG = {
    key: currentScript.getAttribute("data-key") || "",
    server: (currentScript.getAttribute("data-server") || "").replace(/\/+$/, ""),
    position: currentScript.getAttribute("data-position") || "bottom-right",
    theme: currentScript.getAttribute("data-theme") || "auto",
    buttonColor: currentScript.getAttribute("data-button-color") || "#2563eb",
    buttonText: currentScript.getAttribute("data-button-text") || "",
    greeting: currentScript.getAttribute("data-greeting") || "",
    width: currentScript.getAttribute("data-width") || "400",
    height: currentScript.getAttribute("data-height") || "600",
  };

  if (!CONFIG.key || !CONFIG.server) {
    console.error("[MateWidget] data-key and data-server attributes are required.");
    return;
  }

  var isOpen = false;
  var container, iframe, button, badge;

  // --- Styles ----------------------------------------------------------
  function injectStyles() {
    var pos = CONFIG.position === "bottom-left"
      ? "left: 20px; right: auto;"
      : "right: 20px; left: auto;";

    var panelPos = CONFIG.position === "bottom-left"
      ? "left: 20px; right: auto;"
      : "right: 20px; left: auto;";

    var css = [
      "#mate-widget-btn {",
      "  position: fixed; bottom: 20px; " + pos,
      "  z-index: 2147483646;",
      "  width: 56px; height: 56px; border-radius: 50%;",
      "  background: " + CONFIG.buttonColor + ";",
      "  color: #fff; border: none; cursor: pointer;",
      "  box-shadow: 0 4px 12px rgba(0,0,0,0.15);",
      "  display: flex; align-items: center; justify-content: center;",
      "  transition: transform 0.2s, box-shadow 0.2s;",
      "  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;",
      "}",
      "#mate-widget-btn:hover { transform: scale(1.05); box-shadow: 0 6px 20px rgba(0,0,0,0.2); }",
      "#mate-widget-btn svg { width: 28px; height: 28px; fill: currentColor; }",
      "#mate-widget-container {",
      "  position: fixed; bottom: 88px; " + panelPos,
      "  z-index: 2147483647;",
      "  width: " + CONFIG.width + "px; height: " + CONFIG.height + "px;",
      "  max-width: calc(100vw - 40px); max-height: calc(100vh - 120px);",
      "  border-radius: 16px; overflow: hidden;",
      "  box-shadow: 0 8px 30px rgba(0,0,0,0.12), 0 2px 8px rgba(0,0,0,0.08);",
      "  border: 1px solid rgba(0,0,0,0.08);",
      "  display: none; flex-direction: column;",
      "  background: #fff;",
      "}",
      "#mate-widget-container.open { display: flex; animation: mateSlideUp 0.25s ease; }",
      "#mate-widget-iframe { width: 100%; height: 100%; border: none; }",
      "@keyframes mateSlideUp {",
      "  from { opacity: 0; transform: translateY(12px); }",
      "  to { opacity: 1; transform: translateY(0); }",
      "}",
      "@media (max-width: 480px) {",
      "  #mate-widget-container {",
      "    width: calc(100vw - 16px); height: calc(100vh - 80px);",
      "    bottom: 8px; left: 8px; right: 8px; border-radius: 12px;",
      "  }",
      "  #mate-widget-btn { bottom: 12px; }",
      "}",
    ].join("\n");

    var style = document.createElement("style");
    style.id = "mate-widget-styles";
    style.textContent = css;
    document.head.appendChild(style);
  }

  // --- Build DOM -------------------------------------------------------
  function buildWidget() {
    // Chat button
    button = document.createElement("button");
    button.id = "mate-widget-btn";
    button.setAttribute("aria-label", "Open chat");
    button.innerHTML = CONFIG.buttonText
      ? '<span style="font-size:14px;font-weight:600">' + _escapeHtml(CONFIG.buttonText) + '</span>'
      : '<svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">'
        + '<path d="M20 2H4c-1.1 0-2 .9-2 2v18l4-4h14c1.1 0 2-.9 2-2V4c0-1.1-.9-2-2-2zm0 14H5.17L4 17.17V4h16v12z"/>'
        + '<path d="M7 9h10v2H7zm0-3h10v2H7z"/>'
        + '</svg>';
    button.addEventListener("click", toggle);

    // Container + iframe
    container = document.createElement("div");
    container.id = "mate-widget-container";

    var chatUrl = CONFIG.server + "/widget/chat?key=" + encodeURIComponent(CONFIG.key);
    iframe = document.createElement("iframe");
    iframe.id = "mate-widget-iframe";
    iframe.setAttribute("allow", "clipboard-write");
    iframe.setAttribute("loading", "lazy");

    container.appendChild(iframe);
    document.body.appendChild(container);
    document.body.appendChild(button);

    // Lazy-load iframe src on first open to avoid initial load overhead
    iframe._src = chatUrl;
  }

  // --- Public API ------------------------------------------------------
  function open() {
    if (isOpen) return;
    isOpen = true;
    // Load iframe on first open
    if (iframe._src) {
      iframe.src = iframe._src;
      iframe._src = null;
    }
    container.classList.add("open");
    // Send theme to iframe
    if (iframe.contentWindow) {
      var t = CONFIG.theme;
      if (t === "auto") {
        t = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
      }
      setTimeout(function () {
        try { iframe.contentWindow.postMessage({ type: "mate-theme", theme: t }, "*"); } catch (_) {}
      }, 300);
    }
  }

  function close() {
    if (!isOpen) return;
    isOpen = false;
    container.classList.remove("open");
  }

  function toggle() {
    isOpen ? close() : open();
  }

  function _escapeHtml(t) {
    var d = document.createElement("div");
    d.textContent = t;
    return d.innerHTML;
  }

  // --- Init ------------------------------------------------------------
  function init() {
    injectStyles();
    buildWidget();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }

  // Expose public API
  window.MateWidget = { open: open, close: close, toggle: toggle };
})();
