/*
 * The wizard demo page (static/wizard-demo.html, served at /wizard/demo).
 * A file rather than an inline script, which the Content-Security-Policy
 * would block on a static page that cannot carry the response's nonce.
 */
var SERVER = window.location.origin; // demo se servira sa MATE servera

var PRESETS = [
  { name: "Birač — intro (SR, RSD)", cfg: { lang: "sr", currency: "RSD", contact: "prodaja@tvojafirma.rs", fresh: "1" } },
  { name: "AI podrška (Tier 1)", cfg: { tier: "tier1", lang: "sr", currency: "RSD" } },
  { name: "Podrška + zakazivanje (Tier 2)", cfg: { tier: "tier2", lang: "sr", currency: "RSD" } },
  { name: "AI prodavac (Tier 3)", cfg: { tier: "tier3", lang: "sr", currency: "RSD" } },
  { name: "Enterprise (Tier 4)", cfg: { tier: "tier4", lang: "sr" } },
  { name: "Partner: salon-a (EN, EUR)", cfg: { partner: "salon-a", lang: "en", currency: "EUR" } },
];

function buildQuery(cfg) {
  var p = [];
  if (cfg.tier) p.push("tier=" + encodeURIComponent(cfg.tier));
  if (cfg.lang) p.push("lang=" + encodeURIComponent(cfg.lang));
  if (cfg.currency) p.push("currency=" + encodeURIComponent(cfg.currency));
  if (cfg.partner) p.push("partner=" + encodeURIComponent(cfg.partner));
  if (cfg.contact) p.push("contact=" + encodeURIComponent(cfg.contact));
  if (cfg.fresh) p.push("fresh=1");
  return p.join("&");
}

function snippetFor(cfg) {
  var attrs = ['src="' + SERVER + '/wizard/mate-wizard.js"', 'data-server="' + SERVER + '"', 'data-target="mate-wizard"'];
  ["tier", "lang", "currency", "partner", "contact"].forEach(function (k) {
    if (cfg[k]) attrs.push("data-" + (k === "contact" ? "contact-email" : k) + '="' + cfg[k] + '"');
  });
  return '<div id="mate-wizard"></div>\n<script ' + attrs.join("\n        ") + "><\/script>";
}

var HINTS = {
  tier1: { sr: ["Koje su vam cene?", "Gde se nalazite?", "Radno vreme?", "Kako da vas kontaktiram?"],
           en: ["What are your prices?", "Where are you located?", "Opening hours?", "How do I contact you?"] },
  tier2: { sr: ["Imate li slobodan termin sutra?", "Zakaži šišanje u petak u 10h", "Šta je zakazano sutra?", "Otkaži moj termin", "Pomeri termin na ponedeljak"],
           en: ["Any free slot tomorrow?", "Book a haircut Friday at 10:00", "What's scheduled tomorrow?", "Cancel my appointment", "Move it to Monday"] },
  tier3: { sr: ["Pokaži proizvode", "Dodaj hoodie u korpu", "Prikaži korpu", "Ukloni majicu", "Završi porudžbinu"],
           en: ["Show me products", "Add the hoodie to cart", "Show my cart", "Remove the t-shirt", "Checkout"] },
};

function renderHints(cfg) {
  var box = document.getElementById("hintsPanel");
  var h = HINTS[cfg.tier];
  if (!h) { box.style.display = "none"; return; }
  var arr = h[cfg.lang] || h.en || h.sr;
  document.getElementById("hints").innerHTML = arr.map(function (s) {
    return '<span class="chip">' + s.replace(/&/g, "&amp;").replace(/</g, "&lt;") + "</span>";
  }).join("");
  box.style.display = "block";
}

function apply(cfg) {
  document.getElementById("wizardFrame").src = SERVER + "/wizard/embed" + (buildQuery(cfg) ? "?" + buildQuery(cfg) : "");
  document.getElementById("cTier").value = cfg.tier || "";
  document.getElementById("cLang").value = cfg.lang || "sr";
  document.getElementById("cCur").value = cfg.currency || "";
  document.getElementById("cPartner").value = cfg.partner || "";
  document.getElementById("cContact").value = cfg.contact || "";
  document.getElementById("snippet").textContent = snippetFor(cfg);
  renderHints(cfg);
}

function applyFromControls() {
  apply({
    tier: document.getElementById("cTier").value,
    lang: document.getElementById("cLang").value,
    currency: document.getElementById("cCur").value,
    partner: document.getElementById("cPartner").value.trim(),
    contact: document.getElementById("cContact").value.trim(),
  });
}

document.getElementById("applyBtn").addEventListener("click", applyFromControls);

// Render preset buttons
var pc = document.getElementById("presets");
PRESETS.forEach(function (preset) {
  var b = document.createElement("button");
  b.className = "preset";
  b.textContent = preset.name;
  b.addEventListener("click", function () { apply(preset.cfg); });
  pc.appendChild(b);
});

// Auto-resize the iframe from the wizard's postMessage
window.addEventListener("message", function (ev) {
  if (ev.data && ev.data.type === "mate-wizard:resize" && typeof ev.data.height === "number" && ev.data.height > 0) {
    document.getElementById("wizardFrame").style.height = (ev.data.height + 40) + "px";
  }
});

// On page load: resume existing session (if any) so the trial limit is preserved
// across page refreshes. If there is no saved session, apply the first preset (fresh start).
if (localStorage.getItem("mate_wiz_ls_token")) {
  apply({ lang: "sr", currency: "RSD" });
} else {
  apply(PRESETS[0].cfg);
}
