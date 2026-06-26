/**
 * MATE Embeddable Agent Builder Wizard
 *
 * Drop-in script tag for any website. Renders the wizard inline as an iframe.
 *
 * Usage (inline, fills its container or the script's parent):
 *   <div id="mate-wizard"></div>
 *   <script
 *     src="https://your-mate-instance.com/wizard/mate-wizard.js"
 *     data-server="https://your-mate-instance.com"
 *     data-target="mate-wizard"
 *     data-tier=""
 *     data-height="720"
 *   ></script>
 */
(function () {
  "use strict";

  var scripts = document.getElementsByTagName("script");
  var currentScript = document.currentScript || scripts[scripts.length - 1];

  var CONFIG = {
    server: (currentScript.getAttribute("data-server") || "").replace(/\/+$/, ""),
    target: currentScript.getAttribute("data-target") || "",
    tier: currentScript.getAttribute("data-tier") || "",
    lang: currentScript.getAttribute("data-lang") || "",
    currency: currentScript.getAttribute("data-currency") || "",
    partner: currentScript.getAttribute("data-partner") || "",
    contactEmail: currentScript.getAttribute("data-contact-email") || "",
    height: currentScript.getAttribute("data-height") || "720",
  };

  if (!CONFIG.server) {
    console.error("[MateWizard] data-server attribute is required.");
    return;
  }

  function mount() {
    var container = CONFIG.target ? document.getElementById(CONFIG.target) : null;
    if (!container) {
      container = document.createElement("div");
      currentScript.parentNode.insertBefore(container, currentScript);
    }

    var params = [];
    if (CONFIG.tier) params.push("tier=" + encodeURIComponent(CONFIG.tier));
    if (CONFIG.lang) params.push("lang=" + encodeURIComponent(CONFIG.lang));
    if (CONFIG.currency) params.push("currency=" + encodeURIComponent(CONFIG.currency));
    if (CONFIG.partner) params.push("partner=" + encodeURIComponent(CONFIG.partner));
    if (CONFIG.contactEmail) params.push("contact=" + encodeURIComponent(CONFIG.contactEmail));
    var src = CONFIG.server + "/wizard/embed" + (params.length ? "?" + params.join("&") : "");

    var iframe = document.createElement("iframe");
    iframe.src = src;
    iframe.setAttribute("title", "Build your AI agent");
    iframe.style.width = "100%";
    iframe.style.minHeight = CONFIG.height + "px";
    iframe.style.border = "0";
    iframe.style.borderRadius = "12px";
    iframe.allow = "clipboard-write";
    container.appendChild(iframe);

    // Auto-resize from the wizard's postMessage.
    window.addEventListener("message", function (ev) {
      if (!ev.data || ev.data.type !== "mate-wizard:resize") return;
      if (typeof ev.data.height === "number" && ev.data.height > 0) {
        iframe.style.height = ev.data.height + 40 + "px";
      }
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", mount);
  } else {
    mount();
  }
})();
