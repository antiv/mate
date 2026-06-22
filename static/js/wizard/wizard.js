/**
 * Agent Builder Wizard — front-end state machine (multilingual: en, sr).
 *
 * Steps: choose tier -> configure -> (provision + test) -> lead -> done.
 * Provisionable tiers spin up a live trial agent (embedded widget chat) before the lead step;
 * non-provisionable tiers collect a request and go straight to the lead step.
 *
 * Language and contact email are passed in by the embedding iframe (data attributes / query).
 */
(function () {
  "use strict";

  var app = document.getElementById("wizard-app");
  var PROMPT_LIMIT = 6;

  var STRINGS = {
    en: {
      step_choose: "1. Choose", step_configure: "2. Configure", step_test: "3. Test", step_start: "4. Get started",
      tier_h1: "Build your AI agent", tier_sub: "Pick what you need. You can test it before deciding.",
      loading: "Loading…", back: "Back",
      cfg_site_label: "Your website URL",
      cfg_site_help: "The agent reads this site to answer questions.",
      cfg_instructions_label: "Extra instructions (optional)",
      cfg_request_label: "Tell us what you need",
      continue: "Continue",
      ph_instructions: "e.g. Always offer code WELCOME. Hours 9–17.",
      ph_request: "Describe what the agent should do, which systems it connects to, etc.",
      test_h2: "Try your agent",
      test_sub: "Ask it up to 6 questions (optional). Confirm the agent whenever you're ready to finish the test.",
      test_loading: "Reading your website and building your agent…",
      counter_label: "Test questions used",
      done_note: "You've used all 6 test questions. Confirm the agent to finish the test.",
      confirm_finish: "Confirm agent & finish test",
      lead_h2: "Get your agent activated",
      lead_sub: "Leave your details and we'll get in touch to activate it.",
      ph_name: "Your name", ph_email: "Email *", ph_company: "Company", ph_phone: "Phone",
      submit: "Submit",
      done_h2: "Thank you!",
      done_p1: "We've received your details and will contact you shortly.",
      done_p2_prefix: "Questions? Email",
      err_site: "Please enter your website URL.",
      err_email: "Please enter a valid email.",
      err_tiers: "Could not load options. Please refresh.",
      err_build: "Sorry, we couldn't build the trial agent: ",
      lock_msg: "Test finished — confirm your agent to continue.",
      price_tmpl: "{label} — estimated {price}/month. No charge now; we'll confirm details with you.",
    },
    sr: {
      step_choose: "1. Izbor", step_configure: "2. Podešavanje", step_test: "3. Test", step_start: "4. Početak",
      tier_h1: "Napravite svog AI agenta", tier_sub: "Izaberite šta vam treba. Možete ga isprobati pre odluke.",
      loading: "Učitavanje…", back: "Nazad",
      cfg_site_label: "URL vašeg sajta",
      cfg_site_help: "Agent čita ovaj sajt da bi odgovarao na pitanja.",
      cfg_instructions_label: "Dodatne instrukcije (opciono)",
      cfg_request_label: "Recite nam šta vam treba",
      continue: "Nastavi",
      ph_instructions: "npr. Uvek ponudi kod WELCOME. Radno vreme 9–17.",
      ph_request: "Opišite šta agent treba da radi, sa kojim sistemima se povezuje, itd.",
      test_h2: "Isprobajte agenta",
      test_sub: "Postavite mu do 6 pitanja (opciono). Potvrdite agenta kada budete spremni da završite test.",
      test_loading: "Čitam vaš sajt i pravim agenta…",
      counter_label: "Iskorišćeno pitanja",
      done_note: "Iskoristili ste svih 6 pitanja. Potvrdite agenta da završite test.",
      confirm_finish: "Potvrdi agenta i završi test",
      lead_h2: "Aktivirajte svog agenta",
      lead_sub: "Ostavite svoje podatke i javićemo vam se da ga aktiviramo.",
      ph_name: "Vaše ime", ph_email: "Email *", ph_company: "Firma", ph_phone: "Telefon",
      submit: "Pošalji",
      done_h2: "Hvala!",
      done_p1: "Primili smo vaše podatke i uskoro ćemo vas kontaktirati.",
      done_p2_prefix: "Pitanja? Email",
      err_site: "Unesite URL vašeg sajta.",
      err_email: "Unesite ispravan email.",
      err_tiers: "Nije moguće učitati opcije. Osvežite stranicu.",
      err_build: "Nažalost, nismo uspeli da napravimo probnog agenta: ",
      lock_msg: "Test završen — potvrdite agenta da nastavite.",
      price_tmpl: "{label} — procena {price}/mesec. Bez naplate sada; potvrdićemo detalje sa vama.",
    },
  };

  function _initialLang() {
    var l = (app.getAttribute("data-lang") || "en").toLowerCase();
    return STRINGS[l] ? l : "en";
  }

  var state = {
    lang: _initialLang(),
    contactEmail: app.getAttribute("data-contact-email") || "",
    token: null,
    tier: null,
    tierMeta: null,
    tiers: [],
    promptCount: 0,
  };

  function t(key) {
    return (STRINGS[state.lang] && STRINGS[state.lang][key]) || STRINGS.en[key] || key;
  }

  // --- Resume-after-refresh persistence (per-tab sessionStorage) --------
  var SS_TOKEN = "mate_wiz_token", SS_STEP = "mate_wiz_step";
  function ssGet(k) { try { return sessionStorage.getItem(k); } catch (e) { return null; } }
  function ssSet(k, v) { try { sessionStorage.setItem(k, v); } catch (e) {} }
  function ssClear() { try { sessionStorage.removeItem(SS_TOKEN); sessionStorage.removeItem(SS_STEP); } catch (e) {} }

  // --- Helpers ---------------------------------------------------------
  function api(path, body) {
    return fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    }).then(function (r) {
      return r.json().then(function (data) {
        if (!r.ok) throw new Error(data.detail || "Request failed");
        return data;
      });
    });
  }

  function applyI18n() {
    document.querySelectorAll("[data-i18n]").forEach(function (el) {
      var v = t(el.getAttribute("data-i18n"));
      if (v) el.textContent = v;
    });
    document.querySelectorAll("[data-i18n-ph]").forEach(function (el) {
      var v = t(el.getAttribute("data-i18n-ph"));
      if (v) el.placeholder = v;
    });
    document.documentElement.setAttribute("lang", state.lang);
    document.querySelectorAll("[data-setlang]").forEach(function (b) {
      b.classList.toggle("active", b.getAttribute("data-setlang") === state.lang);
    });
  }

  function show(stepName) {
    document.querySelectorAll(".wz-step").forEach(function (el) {
      el.classList.toggle("active", el.getAttribute("data-step") === stepName);
    });
    document.querySelectorAll("[data-stepdot]").forEach(function (el) {
      var on = el.getAttribute("data-stepdot") === stepName;
      el.classList.toggle("text-blue-600", on);
      el.classList.toggle("text-slate-400", !on);
    });
    ssSet(SS_STEP, stepName);
    notifyResize();
  }

  function notifyResize() {
    try {
      parent.postMessage({ type: "mate-wizard:resize", height: document.body.scrollHeight }, "*");
    } catch (e) {}
  }

  function showError(id, msg) {
    var el = document.getElementById(id);
    el.textContent = msg;
    el.classList.remove("hidden");
  }
  function clearError(id) { document.getElementById(id).classList.add("hidden"); }

  function tierById(id) {
    return state.tiers.filter(function (x) { return x.id === id; })[0] || null;
  }

  function ensureSession() {
    if (state.token) return Promise.resolve(state.token);
    return api("/wizard/api/session/start", { tier: state.tier }).then(function (d) {
      state.token = d.session_token;
      ssSet(SS_TOKEN, state.token);
      return state.token;
    });
  }

  // --- Step 1: tiers ---------------------------------------------------
  function fetchTiers(cb) {
    fetch("/wizard/api/tiers?lang=" + encodeURIComponent(state.lang))
      .then(function (r) { return r.json(); })
      .then(function (d) {
        state.tiers = d.tiers || [];
        if (!state.contactEmail) state.contactEmail = d.contact_email || "";
        if (cb) cb();
      })
      .catch(function () {
        document.getElementById("tierList").innerHTML =
          '<div class="text-red-600 text-sm">' + escapeHtml(t("err_tiers")) + "</div>";
      });
  }

  function renderTiers() {
    var list = document.getElementById("tierList");
    list.innerHTML = "";
    state.tiers.forEach(function (tier) {
      var card = document.createElement("button");
      card.className =
        "tier-card text-left border border-slate-200 rounded-xl p-4 bg-white hover:border-blue-400 transition";
      if (state.tier === tier.id) card.className += " selected";
      card.innerHTML =
        '<div class="flex items-baseline justify-between mb-1">' +
          '<span class="font-semibold">' + escapeHtml(tier.label) + "</span>" +
          '<span class="text-blue-600 font-bold text-sm">' + escapeHtml(tier.monthly_estimate || "") + "</span>" +
        "</div>" +
        '<p class="text-sm text-slate-500 mb-2">' + escapeHtml(tier.description || "") + "</p>" +
        '<ul class="text-xs text-slate-400 space-y-0.5">' +
          (tier.features || []).map(function (f) {
            return '<li><i class="fas fa-check text-green-500 mr-1"></i>' + escapeHtml(f) + "</li>";
          }).join("") +
        "</ul>";
      card.addEventListener("click", function () { selectTier(tier); });
      list.appendChild(card);
    });
  }

  function selectTier(tier) {
    state.tier = tier.id;
    state.tierMeta = tier;
    document.querySelectorAll(".tier-card").forEach(function (c) { c.classList.remove("selected"); });
    ensureSession()
      .then(function () { return api("/wizard/api/session/step", { session_token: state.token, tier: tier.id, data: {} }); })
      .then(function () { goConfig(); })
      .catch(function (e) { alert(e.message); });
  }

  // --- Step 2: configure ----------------------------------------------
  function goConfig() {
    var tm = state.tierMeta;
    document.getElementById("configTitle").textContent = tm.label;
    document.getElementById("configSubtitle").textContent = tm.description || "";
    document.getElementById("configWebsite").classList.toggle("hidden", !tm.provisionable);
    document.getElementById("configRequest").classList.toggle("hidden", !!tm.provisionable);
    clearError("configError");
    show("config");
  }

  document.getElementById("configNext").addEventListener("click", function () {
    clearError("configError");
    if (state.tierMeta.provisionable) {
      var url = document.getElementById("cfgSiteUrl").value.trim();
      if (!url) { showError("configError", t("err_site")); return; }
      saveStepThen({ site_url: url, instructions: document.getElementById("cfgInstructions").value.trim() }, goTestProvision);
    } else {
      saveStepThen({ message: document.getElementById("cfgRequest").value.trim() }, goLead);
    }
  });

  function saveStepThen(data, next) {
    api("/wizard/api/session/step", { session_token: state.token, tier: state.tier, data: data })
      .then(next)
      .catch(function (e) { showError("configError", e.message); });
  }

  // --- Step 3: provision + test ---------------------------------------
  function goTestProvision() {
    show("test");
    state.promptCount = 0;
    updateCounter();
    document.getElementById("testLoading").classList.remove("hidden");
    document.getElementById("testChatWrap").classList.add("hidden");
    document.getElementById("testNext").classList.add("hidden");
    document.getElementById("testDoneNote").classList.add("hidden");
    clearError("testError");

    api("/wizard/api/session/provision", { session_token: state.token })
      .then(function (d) {
        var iframe = document.getElementById("testChat");
        iframe.src = d.chat_url;
        iframe.addEventListener("load", function () {
          try { iframe.contentWindow.postMessage({ type: "mate-lang", lang: state.lang }, "*"); } catch (e) {}
        });
        document.getElementById("testLoading").classList.add("hidden");
        document.getElementById("testChatWrap").classList.remove("hidden");
        // Confirm is available immediately — the visitor may send 0 up to 6 prompts.
        document.getElementById("testNext").classList.remove("hidden");
        notifyResize();
      })
      .catch(function (e) {
        document.getElementById("testLoading").classList.add("hidden");
        showError("testError", t("err_build") + e.message);
      });
  }

  function updateCounter() {
    var used = Math.min(state.promptCount, PROMPT_LIMIT);
    document.getElementById("testCounter").textContent = used + " / " + PROMPT_LIMIT;
    document.getElementById("testCounterBar").style.width = (used / PROMPT_LIMIT * 100) + "%";
  }

  function onUserPrompt() {
    state.promptCount += 1;
    updateCounter();
    if (state.promptCount >= PROMPT_LIMIT) {
      var iframe = document.getElementById("testChat");
      try {
        iframe.contentWindow.postMessage({ type: "mate-lock-input", message: t("lock_msg") }, "*");
      } catch (e) {}
      document.getElementById("testDoneNote").classList.remove("hidden");
      document.getElementById("testNext").classList.remove("hidden");
      notifyResize();
    }
  }

  document.getElementById("testNext").addEventListener("click", goLead);

  // --- Step 4: lead ----------------------------------------------------
  function refreshPriceBox() {
    var tm = state.tierMeta;
    var box = document.getElementById("leadPrice");
    if (tm && tm.monthly_estimate) {
      box.innerHTML = t("price_tmpl")
        .replace("{label}", "<strong>" + escapeHtml(tm.label) + "</strong>")
        .replace("{price}", escapeHtml(tm.monthly_estimate));
      box.classList.remove("hidden");
    } else {
      box.classList.add("hidden");
    }
  }

  function goLead() {
    refreshPriceBox();
    clearError("leadError");
    show("lead");
  }

  document.getElementById("leadSubmit").addEventListener("click", function () {
    clearError("leadError");
    var email = document.getElementById("leadEmail").value.trim();
    if (!email || email.indexOf("@") < 0) { showError("leadError", t("err_email")); return; }
    var payload = {
      session_token: state.token,
      tier: state.tier,
      name: document.getElementById("leadName").value.trim(),
      email: email,
      company: document.getElementById("leadCompany").value.trim(),
      phone: document.getElementById("leadPhone").value.trim(),
    };
    if (state.tierMeta && !state.tierMeta.provisionable) {
      payload.requirements = { message: document.getElementById("cfgRequest").value.trim() };
    }
    api("/wizard/api/lead", payload)
      .then(function (d) {
        if (!state.contactEmail) state.contactEmail = d.contact_email || "";
        ssClear();  // flow complete — a fresh visit/refresh starts over
        showDone();
      })
      .catch(function (e) { showError("leadError", e.message); });
  });

  function showDone() {
    var mail = state.contactEmail || "";
    var link = document.getElementById("doneEmail");
    link.textContent = mail;
    link.href = "mailto:" + mail;
    show("done");
  }

  // --- Resume a session after a page refresh ---------------------------
  function restoreTest(chatUrl) {
    show("test");
    state.promptCount = 0;
    updateCounter();
    document.getElementById("testLoading").classList.add("hidden");
    document.getElementById("testDoneNote").classList.add("hidden");
    var iframe = document.getElementById("testChat");
    iframe.src = chatUrl;
    iframe.addEventListener("load", function () {
      try { iframe.contentWindow.postMessage({ type: "mate-lang", lang: state.lang }, "*"); } catch (e) {}
    });
    document.getElementById("testChatWrap").classList.remove("hidden");
    document.getElementById("testNext").classList.remove("hidden");
    notifyResize();
  }

  function resumeSession(token) {
    fetch("/wizard/api/session/" + encodeURIComponent(token))
      .then(function (r) { if (!r.ok) throw new Error("invalid"); return r.json(); })
      .then(function (d) {
        state.token = token;
        state.tier = d.tier;
        state.tierMeta = d.tier ? tierById(d.tier) : null;
        var sd = d.step_data || {};
        if (sd.site_url) document.getElementById("cfgSiteUrl").value = sd.site_url;
        if (sd.instructions) document.getElementById("cfgInstructions").value = sd.instructions;
        if (sd.message) document.getElementById("cfgRequest").value = sd.message;

        if (d.status === "lead_submitted") { ssClear(); showDone(); return; }
        if (!state.tierMeta) { show("tier"); return; }

        var savedStep = ssGet(SS_STEP) || "tier";
        if (savedStep === "test" && d.widget_api_key) {
          restoreTest(d.chat_url);
        } else if (savedStep === "lead") {
          goLead();
        } else if (savedStep === "config" || savedStep === "test") {
          goConfig();
        } else {
          show("tier");
        }
      })
      .catch(function () {
        ssClear();
        var pre = (app.getAttribute("data-preselect") || "").trim();
        if (pre) { var tm = tierById(pre); if (tm) selectTier(tm); }
      });
  }

  // --- Language switch -------------------------------------------------
  function setLang(lang) {
    if (!STRINGS[lang] || lang === state.lang) return;
    state.lang = lang;
    applyI18n();
    fetchTiers(function () {
      if (state.tier) {
        var tm = tierById(state.tier);
        if (tm) state.tierMeta = tm;
      }
      renderTiers();
      if (state.tierMeta) {
        document.getElementById("configTitle").textContent = state.tierMeta.label;
        document.getElementById("configSubtitle").textContent = state.tierMeta.description || "";
        refreshPriceBox();
      }
    });
    var iframe = document.getElementById("testChat");
    if (iframe && iframe.src) {
      try { iframe.contentWindow.postMessage({ type: "mate-lang", lang: lang }, "*"); } catch (e) {}
    }
  }

  document.querySelectorAll("[data-setlang]").forEach(function (btn) {
    btn.addEventListener("click", function () { setLang(btn.getAttribute("data-setlang")); });
  });

  // --- Back buttons ----------------------------------------------------
  document.querySelectorAll("[data-back]").forEach(function (btn) {
    btn.addEventListener("click", function () { show(btn.getAttribute("data-back")); });
  });

  // --- Messages from the embedded widget chat (nested iframe) ----------
  window.addEventListener("message", function (e) {
    if (e.data && e.data.type === "mate-user-message") onUserPrompt();
  });

  // --- Util ------------------------------------------------------------
  function escapeHtml(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  // --- Boot ------------------------------------------------------------
  applyI18n();
  fetchTiers(function () {
    renderTiers();
    var savedToken = ssGet(SS_TOKEN);
    if (savedToken) {
      resumeSession(savedToken);
      return;
    }
    var pre = (app.getAttribute("data-preselect") || "").trim();
    if (pre) {
      var tm = tierById(pre);
      if (tm) selectTier(tm);
    }
  });
  window.addEventListener("resize", notifyResize);
})();
