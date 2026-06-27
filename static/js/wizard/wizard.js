/**
 * Agent Builder Wizard — front-end state machine (multilingual: en, sr).
 *
 * Steps: intro → choose tier → configure (or t4-industry → t4-goals) → (provision + test) → lead → done.
 * Provisionable tiers (1-3) spin up a live trial agent (embedded widget chat) before the lead step;
 * Tier 4 runs a 2-step industry/goals discovery flow and goes straight to the lead step.
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

      // Intro / hero screen
      intro_h1: "Your AI agent in 5 minutes.",
      intro_sub: "Test it free on your website. Answers customers 24/7. We set everything up for you.",
      intro_stat1: "85% lower support costs",
      intro_stat2: "Available 24/7 without breaks",
      intro_stat3: "We handle the full setup",
      intro_cta: "Try for free →",
      intro_note: "No credit card. No charge during the test.",

      // Tier selection
      tier_h1: "Your AI agent in 5 minutes",
      tier_sub: "Pick a type and test it for free — no credit card, no charge during the test.",
      free_trial_badge: "Free test",

      loading: "Loading…", back: "Back",
      cfg_site_label: "Your website URL",
      cfg_site_help: "The agent reads this site to answer questions.",
      cfg_instructions_label: "Extra instructions (optional)",
      cfg_request_label: "Tell us what you need",
      cfg_caps_intro: "MATE can do a lot — pick what you're interested in:",
      continue: "Continue",
      ph_instructions: "e.g. Always offer code WELCOME. Hours 9–17.",
      ph_request: "Describe what the agent should do, which systems it connects to, etc.",

      // Appearance panel
      appear_toggle: "Customize appearance",
      appear_title_label: "Widget title",
      appear_greeting_label: "Greeting message",
      appear_color_label: "Button color",
      appear_theme_label: "Theme",
      appear_theme_light: "Light",
      appear_theme_dark: "Dark",
      appear_theme_auto: "Auto",
      appear_apply: "Apply",
      appear_applying: "Applying…",
      appear_applied: "Appearance updated",
      appear_error: "Error saving appearance",

      // Test step
      test_h2: "Your agent is ready — talk to it",
      test_sub: "This is your agent, trained on your website. Ask it what a customer would ask.",
      test_loading: "Reading your website and building your agent…",
      counter_label: "Test questions used",
      done_note: "You've used all 6 test questions. Confirm the agent to finish the test.",
      confirm_finish: "Confirm agent & finish test",

      // Lead step (tiers 1-3)
      lead_h2: "Activate your agent — we handle the setup",
      lead_sub: "Leave your contact and our team will activate the agent within 24h. No charge until confirmation.",
      // Lead step override for tier 4
      lead_t4_h2: "Schedule a free consultation",
      lead_t4_sub: "Our team will analyze your needs and send a proposal within 24h.",

      ph_name: "Your name", ph_email: "Email *", ph_company: "Company", ph_phone: "Phone",
      submit: "Submit",
      done_h2: "Request received!",
      done_p1: "We'll be in touch within 24h to set everything up. Check your inbox.",
      done_p2_prefix: "Questions? Email",
      done_order_label: "Your order",
      done_contact_label: "Your details",
      done_per_month: "month",
      done_industry_label: "Industry",
      done_goals_label: "Goals",
      err_site: "Please enter your website URL.",
      err_email: "Please enter a valid email.",
      err_tiers: "Could not load options. Please refresh.",
      err_build: "Sorry, we couldn't build the trial agent: ",
      lock_msg: "Test finished — confirm your agent to continue.",
      price_tmpl: "{label} — {price}/month. We handle setup. No charge during the test.",

      // Tier 4 discovery
      t4_ind_h2: "What industry are you in?",
      t4_ind_sub: "We'll tailor the solution to your business type.",
      t4_ind_err: "Please select your industry.",
      t4_goals_h2: "What do you want to automate?",
      t4_goals_sub: "Select all that apply — we'll design the right setup.",
      t4_goals_err: "Please select at least one goal.",
      t4_goals_cta: "Continue to consultation →",
    },
    sr: {
      step_choose: "1. Izbor", step_configure: "2. Podešavanje", step_test: "3. Test", step_start: "4. Početak",

      // Intro / hero screen
      intro_h1: "Vaš AI agent za 5 minuta.",
      intro_sub: "Testirajte besplatno na vašem sajtu. Odgovara kupcima 24/7. Mi sve podešavamo.",
      intro_stat1: "85% manji troškovi podrške",
      intro_stat2: "Dostupan 24/7 bez pauze",
      intro_stat3: "Mi radimo kompletno podešavanje",
      intro_cta: "Isprobaj besplatno →",
      intro_note: "Bez kreditne kartice. Bez naplate tokom testa.",

      // Tier selection
      tier_h1: "Vaš AI agent za 5 minuta",
      tier_sub: "Izaberite tip i isprobajte besplatno — bez kartice, bez naplate tokom testa.",
      free_trial_badge: "Besplatno testiranje",

      loading: "Učitavanje…", back: "Nazad",
      cfg_site_label: "URL vašeg sajta",
      cfg_site_help: "Agent čita ovaj sajt da bi odgovarao na pitanja.",
      cfg_instructions_label: "Dodatne instrukcije (opciono)",
      cfg_request_label: "Recite nam šta vam treba",
      cfg_caps_intro: "MATE može mnogo — izaberite šta vas zanima:",
      continue: "Nastavi",
      ph_instructions: "npr. Uvek ponudi kod WELCOME. Radno vreme 9–17.",
      ph_request: "Opišite šta agent treba da radi, sa kojim sistemima se povezuje, itd.",

      // Appearance panel
      appear_toggle: "Prilagodite izgled",
      appear_title_label: "Naziv widgeta",
      appear_greeting_label: "Pozdravna poruka",
      appear_color_label: "Boja dugmeta",
      appear_theme_label: "Tema",
      appear_theme_light: "Svetla",
      appear_theme_dark: "Tamna",
      appear_theme_auto: "Auto",
      appear_apply: "Primeni",
      appear_applying: "Primenjujem…",
      appear_applied: "Izgled ažuriran",
      appear_error: "Greška pri čuvanju izgleda",

      // Test step
      test_h2: "Vaš agent je spreman — razgovarajte s njim",
      test_sub: "Ovo je vaš agent, treniran na vašem sajtu. Pitajte ga šta bi kupac pitao.",
      test_loading: "Čitam vaš sajt i pravim agenta…",
      counter_label: "Iskorišćeno pitanja",
      done_note: "Iskoristili ste svih 6 pitanja. Potvrdite agenta da završite test.",
      confirm_finish: "Potvrdi agenta i završi test",

      // Lead step (tiers 1-3)
      lead_h2: "Aktivirajte agenta — mi sve podešavamo",
      lead_sub: "Ostavite kontakt i naš tim aktivira agenta u roku od 24h. Bez naplate do potvrde.",
      // Lead step override for tier 4
      lead_t4_h2: "Zakažite besplatnu konsultaciju",
      lead_t4_sub: "Naš tim analizira vaše potrebe i priprema predlog u roku od 24h.",

      ph_name: "Vaše ime", ph_email: "Email *", ph_company: "Firma", ph_phone: "Telefon",
      submit: "Pošalji",
      done_h2: "Zahtev primljen!",
      done_p1: "Javićemo vam se u roku od 24h da sve podesimo. Proverite inbox.",
      done_p2_prefix: "Pitanja? Email",
      done_order_label: "Vaša narudžbina",
      done_contact_label: "Vaši podaci",
      done_per_month: "mesec",
      done_industry_label: "Industrija",
      done_goals_label: "Ciljevi",
      err_site: "Unesite URL vašeg sajta.",
      err_email: "Unesite ispravan email.",
      err_tiers: "Nije moguće učitati opcije. Osvežite stranicu.",
      err_build: "Nažalost, nismo uspeli da napravimo probnog agenta: ",
      lock_msg: "Test završen — potvrdite agenta da nastavite.",
      price_tmpl: "{label} — {price}/mesec. Mi podešavamo sve. Bez naplate tokom testa.",

      // Tier 4 discovery
      t4_ind_h2: "U kojoj industriji poslujete?",
      t4_ind_sub: "Prilagodićemo rešenje tipu vašeg poslovanja.",
      t4_ind_err: "Molimo izaberite industriju.",
      t4_goals_h2: "Šta želite da automatizujete?",
      t4_goals_sub: "Izaberite sve što se odnosi na vas — osmislićemo pravo rešenje.",
      t4_goals_err: "Izaberite bar jedan cilj.",
      t4_goals_cta: "Nastavi ka konsultaciji →",
    },
  };

  // Tier 4 discovery data — industries and goals (not fetched from server, locale-only UI)
  var T4_INDUSTRIES = {
    en: [
      { id: "ecommerce",   label: "E-commerce",      icon: "fas fa-shopping-cart" },
      { id: "services",    label: "Services",         icon: "fas fa-briefcase" },
      { id: "health",      label: "Healthcare",       icon: "fas fa-heartbeat" },
      { id: "realestate",  label: "Real estate",      icon: "fas fa-building" },
      { id: "education",   label: "Education",        icon: "fas fa-graduation-cap" },
      { id: "hospitality", label: "Hospitality",      icon: "fas fa-utensils" },
      { id: "legal",       label: "Legal services",   icon: "fas fa-balance-scale" },
      { id: "other",       label: "Other",            icon: "fas fa-ellipsis-h" },
    ],
    sr: [
      { id: "ecommerce",   label: "E-commerce",        icon: "fas fa-shopping-cart" },
      { id: "services",    label: "Usluge",             icon: "fas fa-briefcase" },
      { id: "health",      label: "Zdravstvo",          icon: "fas fa-heartbeat" },
      { id: "realestate",  label: "Nekretnine",         icon: "fas fa-building" },
      { id: "education",   label: "Obrazovanje",        icon: "fas fa-graduation-cap" },
      { id: "hospitality", label: "Ugostiteljstvo",     icon: "fas fa-utensils" },
      { id: "legal",       label: "Pravne usluge",      icon: "fas fa-balance-scale" },
      { id: "other",       label: "Ostalo",             icon: "fas fa-ellipsis-h" },
    ],
  };

  var T4_GOALS = {
    en: [
      { id: "support",     label: "Customer support" },
      { id: "sales",       label: "Sales & lead generation" },
      { id: "scheduling",  label: "Appointment scheduling" },
      { id: "knowledge",   label: "Document & knowledge Q&A" },
      { id: "crm",         label: "CRM / ERP integration" },
      { id: "other",       label: "Something else" },
    ],
    sr: [
      { id: "support",     label: "Korisnička podrška" },
      { id: "sales",       label: "Prodaja i generisanje potencijalnih klijenata" },
      { id: "scheduling",  label: "Zakazivanje termina" },
      { id: "knowledge",   label: "Odgovori iz dokumenata i baze znanja" },
      { id: "crm",         label: "Integracija sa CRM / ERP sistemom" },
      { id: "other",       label: "Nešto drugo" },
    ],
  };

  // Maps internal step names to the stepdot they should highlight in the progress bar.
  var STEP_DOT_MAP = { "t4-industry": "config", "t4-goals": "config" };

  function _initialLang() {
    var l = (app.getAttribute("data-lang") || "en").toLowerCase();
    return STRINGS[l] ? l : "en";
  }

  var state = {
    lang: _initialLang(),
    currency: (app.getAttribute("data-currency") || "").toUpperCase(),
    partner: app.getAttribute("data-partner") || "",
    contactEmail: app.getAttribute("data-contact-email") || "",
    token: null,
    tier: null,
    tierMeta: null,
    tiers: [],
    capabilities: [],
    promptCount: 0,
    visited: {},
    t4Industry: null,
    t4Goals: [],
    trialWidgetKey: null,
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
  }

  function show(stepName) {
    if (stepName !== "done") state.visited[stepName] = true;
    document.querySelectorAll(".wz-step").forEach(function (el) {
      el.classList.toggle("active", el.getAttribute("data-step") === stepName);
    });

    // Hide progress bar on intro screen (it's a pre-wizard landing page)
    var progress = document.getElementById("wzProgress");
    if (progress) progress.classList.toggle("hidden", stepName === "intro");

    var dotStep = STEP_DOT_MAP[stepName] || stepName;
    document.querySelectorAll("[data-stepdot]").forEach(function (el) {
      var s = el.getAttribute("data-stepdot");
      var on = s === dotStep;
      el.classList.toggle("text-blue-600", on);
      el.classList.toggle("text-slate-400", !on);
      var canNav = !!state.visited[s] && stepName !== "done";
      el.classList.toggle("cursor-pointer", canNav);
      el.classList.toggle("underline", canNav && !on);
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
    return api("/wizard/api/session/start", { tier: state.tier, partner: state.partner }).then(function (d) {
      state.token = d.session_token;
      ssSet(SS_TOKEN, state.token);
      return state.token;
    });
  }

  // --- Step: intro -------------------------------------------------------
  document.getElementById("introCta").addEventListener("click", function () {
    show("tier");
  });

  // --- Step: tiers ---------------------------------------------------
  function fetchTiers(cb) {
    var url = "/wizard/api/tiers?lang=" + encodeURIComponent(state.lang)
            + (state.currency ? "&currency=" + encodeURIComponent(state.currency) : "")
            + (state.partner ? "&partner=" + encodeURIComponent(state.partner) : "");
    fetch(url)
      .then(function (r) { return r.json(); })
      .then(function (d) {
        state.tiers = d.tiers || [];
        state.capabilities = d.capabilities || [];
        if (d.currency) state.currency = d.currency;
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

      var badgeHtml = tier.provisionable
        ? '<span class="inline-block text-xs font-semibold text-green-700 bg-green-100 rounded px-1.5 py-0.5 mb-1">' +
          '<i class="fas fa-vial mr-1"></i>' + escapeHtml(t("free_trial_badge")) + '</span>'
        : '';

      card.innerHTML =
        badgeHtml +
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
      .then(function () {
        if (!tier.provisionable) {
          goT4Industry();
        } else {
          goConfig();
        }
      })
      .catch(function (e) { alert(e.message); });
  }

  // --- Step: configure (tiers 1-3 only) -------------------------------
  function goConfig() {
    var tm = state.tierMeta;
    document.getElementById("configTitle").textContent = tm.label;
    document.getElementById("configSubtitle").textContent = tm.description || "";
    document.getElementById("configWebsite").classList.remove("hidden");
    document.getElementById("configRequest").classList.add("hidden");
    clearError("configError");
    show("config");
  }

  function renderCapabilities() {
    var list = document.getElementById("capabilitiesList");
    if (!list || list.childElementCount) return;
    list.innerHTML = state.capabilities.map(function (c) {
      return '<label class="flex items-start gap-2 text-sm text-slate-700 cursor-pointer">' +
        '<input type="checkbox" class="wz-cap mt-0.5" value="' + escapeHtml(c.id) + '" data-label="' + escapeHtml(c.label) + '">' +
        '<span>' + escapeHtml(c.label) + '</span></label>';
    }).join("");
    if (state._restoreCaps && state._restoreCaps.length) {
      var set = {};
      state._restoreCaps.forEach(function (l) { set[l] = 1; });
      list.querySelectorAll(".wz-cap").forEach(function (cb) { if (set[cb.getAttribute("data-label")]) cb.checked = true; });
      state._restoreCaps = null;
    }
  }

  function collectCapabilities() {
    var out = [];
    document.querySelectorAll("#capabilitiesList .wz-cap:checked").forEach(function (cb) {
      out.push(cb.getAttribute("data-label"));
    });
    return out;
  }

  document.getElementById("configNext").addEventListener("click", function () {
    clearError("configError");
    var url = document.getElementById("cfgSiteUrl").value.trim();
    if (!url) { showError("configError", t("err_site")); return; }
    var data = { site_url: url, instructions: document.getElementById("cfgInstructions").value.trim() };
    var changed = !!state.provisionedTier &&
      (state.provisionedTier !== state.tier || JSON.stringify(data) !== state.provisionedConfig);
    saveStepThen(data, function () { goTestProvision(changed); });
  });

  function saveStepThen(data, next) {
    api("/wizard/api/session/step", { session_token: state.token, tier: state.tier, data: data })
      .then(next)
      .catch(function (e) { showError("configError", e.message); });
  }

  // --- Step: Tier 4 — Industry ----------------------------------------
  function goT4Industry() {
    renderT4Industry();
    clearError("t4IndError");
    show("t4-industry");
  }

  function renderT4Industry() {
    var list = document.getElementById("industryList");
    if (list.childElementCount) return;  // already rendered
    var industries = T4_INDUSTRIES[state.lang] || T4_INDUSTRIES.en;
    list.innerHTML = industries.map(function (ind) {
      return '<button type="button" class="industry-card border border-slate-200 rounded-xl p-3 bg-white hover:border-blue-400 transition text-center" data-id="' + escapeHtml(ind.id) + '">' +
        '<i class="' + escapeHtml(ind.icon) + ' text-blue-500 text-xl mb-1 block"></i>' +
        '<span class="text-sm font-medium text-slate-700">' + escapeHtml(ind.label) + '</span>' +
        '</button>';
    }).join("");
    list.querySelectorAll(".industry-card").forEach(function (btn) {
      btn.addEventListener("click", function () {
        list.querySelectorAll(".industry-card").forEach(function (b) { b.classList.remove("selected"); });
        btn.classList.add("selected");
        state.t4Industry = btn.getAttribute("data-id");
        clearError("t4IndError");
      });
      // Restore previously selected
      if (state.t4Industry && btn.getAttribute("data-id") === state.t4Industry) {
        btn.classList.add("selected");
      }
    });
  }

  document.getElementById("t4IndNext").addEventListener("click", function () {
    if (!state.t4Industry) { showError("t4IndError", t("t4_ind_err")); return; }
    goT4Goals();
  });

  // --- Step: Tier 4 — Goals -------------------------------------------
  function goT4Goals() {
    renderT4Goals();
    clearError("t4GoalsError");
    show("t4-goals");
  }

  function renderT4Goals() {
    var list = document.getElementById("goalsList");
    if (list.childElementCount) return;  // already rendered
    var goals = T4_GOALS[state.lang] || T4_GOALS.en;
    list.innerHTML = goals.map(function (g) {
      return '<button type="button" class="goal-card border border-slate-200 rounded-lg px-4 py-3 bg-white hover:border-blue-400 transition text-left text-sm font-medium text-slate-700" data-id="' + escapeHtml(g.id) + '">' +
        '<i class="fas fa-square-check mr-2 text-slate-300 goal-icon"></i>' + escapeHtml(g.label) +
        '</button>';
    }).join("");
    list.querySelectorAll(".goal-card").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var id = btn.getAttribute("data-id");
        var idx = state.t4Goals.indexOf(id);
        if (idx === -1) {
          state.t4Goals.push(id);
          btn.classList.add("selected");
          btn.querySelector(".goal-icon").classList.replace("text-slate-300", "text-blue-500");
        } else {
          state.t4Goals.splice(idx, 1);
          btn.classList.remove("selected");
          btn.querySelector(".goal-icon").classList.replace("text-blue-500", "text-slate-300");
        }
        clearError("t4GoalsError");
      });
      // Restore previously selected
      if (state.t4Goals.indexOf(btn.getAttribute("data-id")) !== -1) {
        btn.classList.add("selected");
        btn.querySelector(".goal-icon").classList.replace("text-slate-300", "text-blue-500");
      }
    });
  }

  document.getElementById("t4GoalsNext").addEventListener("click", function () {
    if (!state.t4Goals.length) { showError("t4GoalsError", t("t4_goals_err")); return; }
    // Save industry + goals to session, then go to lead
    api("/wizard/api/session/step", {
      session_token: state.token,
      tier: state.tier,
      data: { industry: state.t4Industry, capabilities: state.t4Goals },
    })
      .then(goLead)
      .catch(function (e) { showError("t4GoalsError", e.message); });
  });

  // --- Step: provision + test (tiers 1-3) ----------------------------
  function goTestProvision(reprovision) {
    show("test");
    state.promptCount = 0;
    updateCounter();
    document.getElementById("testLoading").classList.remove("hidden");
    document.getElementById("testChatWrap").classList.add("hidden");
    document.getElementById("testNext").classList.add("hidden");
    document.getElementById("testDoneNote").classList.add("hidden");
    clearError("testError");

    api("/wizard/api/session/provision", { session_token: state.token, reprovision: !!reprovision })
      .then(function (d) {
        state.provisionedTier = state.tier;
        state.provisionedConfig = JSON.stringify({
          site_url: document.getElementById("cfgSiteUrl").value.trim(),
          instructions: document.getElementById("cfgInstructions").value.trim(),
        });
        state.trialWidgetKey = d.widget_api_key || null;
        initAppearancePanel(d.widget_api_key);
        var iframe = document.getElementById("testChat");
        iframe.src = d.chat_url;
        iframe.addEventListener("load", function () {
          try {
            iframe.contentWindow.postMessage({ type: "mate-lang", lang: state.lang }, "*");
            iframe.contentWindow.postMessage({ type: "mate-theme", theme: "light" }, "*");
          } catch (e) {}
        });
        document.getElementById("testLoading").classList.add("hidden");
        document.getElementById("testChatWrap").classList.remove("hidden");
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

  // --- Step: lead capture ---------------------------------------------
  function refreshPriceBox() {
    var tm = state.tierMeta;
    var box = document.getElementById("leadPrice");
    if (tm && tm.monthly_estimate) {
      var tmpl = t("price_tmpl");
      if (tm.monthly_estimate === "Custom pricing") {
        tmpl = tmpl.replace("{price}/month", "{price}").replace("{price}/mesec", "{price}");
      }
      box.innerHTML = tmpl
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
    // Apply tier4-specific headings
    var isTier4 = state.tierMeta && !state.tierMeta.provisionable;
    document.getElementById("leadHeading").textContent = t(isTier4 ? "lead_t4_h2" : "lead_h2");
    document.getElementById("leadSubtitle").textContent = t(isTier4 ? "lead_t4_sub" : "lead_sub");
    show("lead");
  }

  document.getElementById("leadSubmit").addEventListener("click", function () {
    clearError("leadError");
    var email = document.getElementById("leadEmail").value.trim();
    if (!email || email.indexOf("@") < 0) { showError("leadError", t("err_email")); return; }
    var payload = {
      session_token: state.token,
      tier: state.tier,
      currency: state.currency,
      partner: state.partner,
      name: document.getElementById("leadName").value.trim(),
      email: email,
      company: document.getElementById("leadCompany").value.trim(),
      phone: document.getElementById("leadPhone").value.trim(),
    };
    if (state.tierMeta && !state.tierMeta.provisionable) {
      payload.requirements = { industry: state.t4Industry || "", goals: state.t4Goals };
    }
    api("/wizard/api/lead", payload)
      .then(function (d) {
        if (!state.contactEmail) state.contactEmail = d.contact_email || "";
        ssClear();
        showDone();
      })
      .catch(function (e) { showError("leadError", e.message); });
  });

  function showDone() {
    // Contact email for questions
    var mail = state.contactEmail || "";
    var link = document.getElementById("doneEmail");
    link.href = mail ? "mailto:" + mail : "#";
    link.style.display = mail ? "" : "none";
    var emailText = document.getElementById("doneEmailText");
    if (emailText) emailText.textContent = mail;

    // Order summary — tier
    var tm = state.tierMeta;
    if (tm) {
      document.getElementById("doneTierLabel").textContent = tm.label || "";
      document.getElementById("doneTierDesc").textContent = tm.description || "";
      var priceEl = document.getElementById("doneTierPrice");
      priceEl.textContent = tm.monthly_estimate
        ? (tm.monthly_estimate === "Custom pricing" ? tm.monthly_estimate : tm.monthly_estimate + "/" + t("done_per_month"))
        : "";

      // Features list
      var featEl = document.getElementById("doneTierFeatures");
      featEl.innerHTML = "";
      (tm.features || []).forEach(function (f) {
        var li = document.createElement("li");
        li.className = "flex items-start gap-2";
        li.innerHTML = '<i class="fas fa-check text-green-500 mt-0.5 flex-shrink-0 text-xs"></i><span>' + escapeHtml(f) + "</span>";
        featEl.appendChild(li);
      });

      // Tier 4 — show selected industry and goals instead of features
      var t4el = document.getElementById("doneT4Summary");
      if (!tm.provisionable) {
        t4el.classList.remove("hidden");
        var langs = T4_INDUSTRIES[state.lang] || T4_INDUSTRIES.en;
        var glangs = T4_GOALS[state.lang] || T4_GOALS.en;
        if (state.t4Industry) {
          var ind = langs.find(function (x) { return x.id === state.t4Industry; });
          document.getElementById("doneT4Industry").textContent =
            t("done_industry_label") + ": " + (ind ? ind.label : state.t4Industry);
        }
        if (state.t4Goals.length) {
          var goalLabels = state.t4Goals.map(function (gid) {
            var g = glangs.find(function (x) { return x.id === gid; });
            return g ? g.label : gid;
          });
          document.getElementById("doneT4Goals").textContent =
            t("done_goals_label") + ": " + goalLabels.join(", ");
        }
      } else {
        t4el.classList.add("hidden");
      }
    }

    // Contact details the user filled in
    var name = document.getElementById("leadName").value.trim();
    var email = document.getElementById("leadEmail").value.trim();
    var company = document.getElementById("leadCompany").value.trim();
    document.getElementById("doneContactName").textContent = name;
    document.getElementById("doneContactEmail").textContent = email;
    var compEl = document.getElementById("doneContactCompany");
    compEl.textContent = company;
    compEl.classList.toggle("hidden", !company);

    show("done");
  }

  // Back from lead: tier4 → goals, provisionable → test, else → config
  document.getElementById("leadBack").addEventListener("click", function () {
    if (state.tierMeta && !state.tierMeta.provisionable) {
      show("t4-goals");
    } else {
      show(state.tierMeta && state.tierMeta.provisionable ? "test" : "config");
    }
  });

  // --- Resume a session after a page refresh ---------------------------
  function restoreTest(chatUrl, widgetKey) {
    state.trialWidgetKey = widgetKey || state.trialWidgetKey;
    if (widgetKey) initAppearancePanel(widgetKey);
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
        if (sd.capabilities) state._restoreCaps = sd.capabilities;
        if (sd.industry) state.t4Industry = sd.industry;
        if (sd.goals && sd.goals.length) state.t4Goals = sd.goals;

        if (d.status === "lead_submitted") { ssClear(); showDone(); return; }
        if (!state.tierMeta) { show("tier"); return; }

        state.visited.tier = true;
        state.visited.config = true;
        if (d.widget_api_key) state.visited.test = true;

        var savedStep = ssGet(SS_STEP) || "tier";
        if (savedStep === "test" && d.widget_api_key) {
          restoreTest(d.chat_url, d.widget_api_key);
        } else if (savedStep === "lead") {
          goLead();
        } else if (savedStep === "t4-industry" || savedStep === "t4-goals") {
          goT4Industry();
        } else if (savedStep === "config" || savedStep === "test") {
          goConfig();
        } else {
          show("tier");
        }
      })
      .catch(function () {
        ssClear();
        var pre = (app.getAttribute("data-preselect") || "").trim();
        if (pre) { var tm = tierById(pre); if (tm) { selectTier(tm); return; } }
        show("intro");
      });
  }

  // --- Language switch -------------------------------------------------
  function setLang(lang) {
    if (!STRINGS[lang] || lang === state.lang) return;
    state.lang = lang;
    // Re-render tier4 discovery panels in new language
    document.getElementById("industryList").innerHTML = "";
    document.getElementById("goalsList").innerHTML = "";
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
      // Re-render discovery panels if currently on those steps
      var active = document.querySelector(".wz-step.active");
      var activeStep = active ? active.getAttribute("data-step") : null;
      if (activeStep === "t4-industry") renderT4Industry();
      if (activeStep === "t4-goals") { renderT4Industry(); renderT4Goals(); }
    });
    var iframe = document.getElementById("testChat");
    if (iframe && iframe.src) {
      try { iframe.contentWindow.postMessage({ type: "mate-lang", lang: lang }, "*"); } catch (e) {}
    }
  }

  // --- Back navigation (static data-back buttons) ---------------------
  document.querySelectorAll("[data-back]").forEach(function (btn) {
    btn.addEventListener("click", function () { show(btn.getAttribute("data-back")); });
  });

  // Click a completed step in the progress bar to jump back to it.
  document.querySelectorAll("[data-stepdot]").forEach(function (el) {
    el.addEventListener("click", function () {
      var s = el.getAttribute("data-stepdot");
      if (state.visited[s]) show(s);
    });
  });

  // --- Messages from the embedded widget chat (nested iframe) ----------
  window.addEventListener("message", function (e) {
    if (!e.data) return;
    if (e.data.type === "mate-user-message") onUserPrompt();
    if (e.data.type === "mate-set-lang" && e.data.lang) setLang(e.data.lang);
  });

  // --- Appearance panel (test step) -----------------------------------
  function setActiveThemeBtn(theme) {
    document.querySelectorAll(".appear-theme-btn").forEach(function (btn) {
      var active = btn.getAttribute("data-theme") === theme;
      btn.classList.toggle("border-blue-500", active);
      btn.classList.toggle("bg-blue-50", active);
      btn.classList.toggle("text-blue-700", active);
      btn.classList.toggle("font-semibold", active);
      btn.classList.toggle("border-slate-200", !active);
    });
  }

  function initAppearancePanel(widgetKey) {
    if (!widgetKey) return;
    var panel = document.getElementById("appearancePanel");
    if (!panel) return;
    panel.classList.remove("hidden");
    // Pre-populate from current config
    fetch("/widget/api/widget-config", { headers: { "X-Widget-Key": widgetKey } })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        var cfg = d.widget_config || {};
        document.getElementById("appearTitle").value = cfg.title || "";
        document.getElementById("appearGreeting").value = cfg.greeting || "";
        document.getElementById("appearColor").value = cfg.button_color || "#2563eb";
        setActiveThemeBtn(cfg.theme || "light");
      })
      .catch(function () { setActiveThemeBtn("light"); });
    // Theme buttons — live preview via postMessage, no save needed
    document.querySelectorAll(".appear-theme-btn").forEach(function (btn) {
      btn.onclick = function () {
        var theme = btn.getAttribute("data-theme");
        setActiveThemeBtn(theme);
        var iframe = document.getElementById("testChat");
        try { iframe.contentWindow.postMessage({ type: "mate-theme", theme: theme }, "*"); } catch (e) {}
      };
    });
    // Apply button
    document.getElementById("appearApply").onclick = function () { applyAppearance(widgetKey); };
    // i18n labels
    document.getElementById("appearToggleLabel").textContent = t("appear_toggle");
    document.getElementById("appearTitleLabel").textContent = t("appear_title_label");
    document.getElementById("appearGreetingLabel").textContent = t("appear_greeting_label");
    document.getElementById("appearColorLabel").textContent = t("appear_color_label");
    document.getElementById("appearThemeLabel").textContent = t("appear_theme_label");
    document.getElementById("themeLight").textContent = "☀ " + t("appear_theme_light");
    document.getElementById("themeDark").textContent = "☾ " + t("appear_theme_dark");
    document.getElementById("themeAuto").textContent = "⬡ " + t("appear_theme_auto");
    document.getElementById("appearApply").textContent = t("appear_apply");
  }

  function applyAppearance(widgetKey) {
    var btn = document.getElementById("appearApply");
    btn.textContent = t("appear_applying");
    btn.disabled = true;
    var activeThemeBtn = document.querySelector(".appear-theme-btn.border-blue-500");
    var payload = {
      title: document.getElementById("appearTitle").value.trim(),
      greeting: document.getElementById("appearGreeting").value.trim(),
      button_color: document.getElementById("appearColor").value.trim(),
      theme: activeThemeBtn ? activeThemeBtn.getAttribute("data-theme") : "light",
    };
    fetch("/widget/api/widget-config", {
      method: "PUT",
      headers: { "Content-Type": "application/json", "X-Widget-Key": widgetKey },
      body: JSON.stringify(payload),
    })
      .then(function (r) { if (!r.ok) throw new Error(); return r.json(); })
      .then(function () {
        btn.textContent = t("appear_applied");
        // Reload iframe to pick up new config
        var iframe = document.getElementById("testChat");
        var src = iframe.src;
        iframe.src = "";
        iframe.src = src;
        setTimeout(function () {
          btn.textContent = t("appear_apply");
          btn.disabled = false;
        }, 2000);
      })
      .catch(function () {
        btn.textContent = t("appear_error");
        btn.disabled = false;
      });
  }

  // --- Util ------------------------------------------------------------
  function escapeHtml(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  // --- Boot ------------------------------------------------------------
  // Clear saved session when the embedding page requests a fresh start (fresh=1 URL param).
  if (app.getAttribute("data-fresh") === "true") { ssClear(); }
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
      if (tm) { selectTier(tm); return; }
    }
    // First visit: show the intro/hero screen
    show("intro");
  });
  window.addEventListener("resize", notifyResize);
})();
