/**
 * MATE Widget Chat — client-side logic.
 *
 * Expects three globals injected by the template:
 *   WIDGET_API_KEY, WIDGET_AGENT_NAME, WIDGET_CONFIG
 */
(function () {
  "use strict";

  // --- Config ----------------------------------------------------------
  const API_KEY = window.WIDGET_API_KEY || "";
  const AGENT_NAME = window.WIDGET_AGENT_NAME || "agent";
  const CFG = window.WIDGET_CONFIG || {};
  const BASE = window.location.origin;

  const STORAGE_PREFIX = `mate_widget_${API_KEY.slice(0, 8)}`;

  // --- State -----------------------------------------------------------
  let sessionId = localStorage.getItem(`${STORAGE_PREFIX}_sid`) || "";
  let userId = localStorage.getItem(`${STORAGE_PREFIX}_uid`) || _generateId();
  localStorage.setItem(`${STORAGE_PREFIX}_uid`, userId);

  let sending = false;
  let forceNewSession = false;
  let pendingFiles = []; // [{dataUrl, mimeType, base64, name}]
  let pageContext = null; // {url, title, description, lang} from parent page via postMessage
  let currentLang = "en";
  let abortController = null;
  let debugMode = false;
  let activeAgentEl = null;
  // Locked by wizard via postMessage when trial prompt limit is reached. Persisted in
  // localStorage so the lock survives widget iframe reloads (iframe destruction clears sessionStorage).
  const _LOCK_KEY = `${STORAGE_PREFIX}_locked`;
  let _locked = localStorage.getItem(_LOCK_KEY) === "1";
  let activeAgentText = "";
  let activeAgentAuthor = "";
  let activeAgentImages = [];
  const artifactCache = {};
  const IMAGE_EXTENSIONS = [".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp"];

  // UI string translations — placeholder, send button, new-chat button, stop button, interrupted message
  const UI_STRINGS = {
    en: { placeholder: "Type a message…", send: "Send", newChat: "New Chat", stop: "Stop", interrupted: "Response interrupted", copy: "Copy", copied: "Copied!", download: "Download", access_denied: "You don't have permission to use this agent. Please contact the administrator.", error_occurred: "Hmm, I couldn't quite process that. Could you say it again?", endChat: "End chat", endConfirm: "End this conversation? Your chat will be cleared.", endYes: "Yes, end", endNo: "No", confirmTitle: "Approval required", confirmApprove: "Approve", confirmReject: "Reject", confirmApproved: "Approved", confirmRejected: "Rejected" },
    sr: { placeholder: "Unesite poruku…", send: "Pošalji", newChat: "Nov razgovor", stop: "Prekini", interrupted: "Odgovor je prekinut", copy: "Kopiraj", copied: "Kopirano!", download: "Preuzmi", access_denied: "Nemate pristup ovom agentu. Molimo kontaktirajte administratora.", error_occurred: "Hm, nisam uspeo to da obradim. Možete li da ponovite?", endChat: "Završi", endConfirm: "Završiti razgovor? Vaš chat će biti obrisan.", endYes: "Da, završi", endNo: "Ne", confirmTitle: "Potrebna je potvrda", confirmApprove: "Potvrdi", confirmReject: "Odbaci", confirmApproved: "Potvrđeno", confirmRejected: "Odbačeno" },
    hr: { placeholder: "Unesite poruku…", send: "Pošalji", newChat: "Novi razgovor", stop: "Prekini", interrupted: "Odgovor je prekinut", copy: "Kopiraj", copied: "Kopirano!", download: "Preuzmi", access_denied: "Nemate pristup ovom agentu. Kontaktirajte administratora.", error_occurred: "Hm, nisam uspio to obraditi. Možete li ponoviti?" },
    bs: { placeholder: "Unesite poruku…", send: "Pošalji", newChat: "Novi razgovor", stop: "Prekini", interrupted: "Odgovor je prekinut", copy: "Kopiraj", copied: "Kopirano!", download: "Preuzmi", access_denied: "Nemate pristup ovom agentu. Kontaktirajte administratora.", error_occurred: "Hm, nisam uspio to obraditi. Možete li ponoviti?" },
    de: { placeholder: "Nachricht eingeben…", send: "Senden", newChat: "Neuer Chat", stop: "Stoppen", interrupted: "Antwort unterbrochen", copy: "Kopieren", copied: "Kopiert!", download: "Herunterladen", access_denied: "Sie haben keinen Zugriff auf diesen Agenten. Bitte kontaktieren Sie den Administrator.", error_occurred: "Hmm, das konnte ich nicht verarbeiten. Können Sie es wiederholen?" },
    fr: { placeholder: "Écrivez un message…", send: "Envoyer", newChat: "Nouveau chat", stop: "Arrêter", interrupted: "Réponse interrompue", copy: "Copier", copied: "Copié !", download: "Télécharger", access_denied: "Vous n'avez pas accès à cet agent. Veuillez contacter l'administrateur.", error_occurred: "Hmm, je n'ai pas réussi à traiter cela. Pouvez-vous répéter ?" },
    es: { placeholder: "Escribe un mensaje…", send: "Enviar", newChat: "Nueva conversación", stop: "Detener", interrupted: "Respuesta interrumpida", access_denied: "No tienes permiso para usar este agente. Contacta al administrador.", error_occurred: "Mmm, no pude procesar eso. ¿Puedes repetirlo?" },
    it: { placeholder: "Scrivi un messaggio…", send: "Invia", newChat: "Nuova chat", stop: "Interrompi", interrupted: "Risposta interrotta", access_denied: "Non hai accesso a questo agente. Contatta l'amministratore.", error_occurred: "Hmm, non sono riuscito a elaborarlo. Puoi ripetere?" },
    pt: { placeholder: "Escreva uma mensagem…", send: "Enviar", newChat: "Nova conversa", stop: "Parar", interrupted: "Resposta interrompida", access_denied: "Você não tem acesso a este agente. Contacte o administrador.", error_occurred: "Hmm, não consegui processar isso. Pode repetir?" },
    nl: { placeholder: "Typ een bericht…", send: "Versturen", newChat: "Nieuw gesprek", stop: "Stoppen", interrupted: "Reactie onderbroken", access_denied: "U heeft geen toegang tot deze agent. Neem contact op met de beheerder.", error_occurred: "Hmm, ik kon dat niet verwerken. Kunt u het herhalen?" },
    pl: { placeholder: "Wpisz wiadomość…", send: "Wyślij", newChat: "Nowy czat", stop: "Zatrzymaj", interrupted: "Odpowiedź przerwana", access_denied: "Nie masz dostępu do tego agenta. Skontaktuj się z administratorem.", error_occurred: "Hmm, nie udało mi się tego przetworzyć. Czy możesz powtórzyć?" },
    ru: { placeholder: "Введите сообщение…", send: "Отправить", newChat: "Новый чат", stop: "Остановить", interrupted: "Ответ прерван", access_denied: "У вас нет доступа к этому агенту. Свяжитесь с администратором.", error_occurred: "Хм, мне не удалось это обработать. Пожалуйста, повторите." },
    zh: { placeholder: "输入消息…", send: "发送", newChat: "新对话", stop: "停止", interrupted: "回答被中断", access_denied: "您没有访问此代理的权限。请联系管理员。", error_occurred: "嗯，我没能理解那条信息。能再说一遍吗？" },
    ja: { placeholder: "メッセージを入力…", send: "送信", newChat: "新しいチャット", stop: "停止", interrupted: "回答が中断されました", access_denied: "このエージェントへのアクセス権がありません。管理者にお問い合わせください。", error_occurred: "うーん、うまく処理できませんでした。もう一度お願いできますか？" },
    ar: { placeholder: "اكتب رسالة…", send: "إرسال", newChat: "محادثة جديدة", stop: "إيقاف", interrupted: "تم مقاطعة الإجابة", access_denied: "ليس لديك صلاحية الوصول إلى هذا الوكيل. يرجى التواصل مع المسؤول.", error_occurred: "لم أتمكن من معالجة ذلك. هل يمكنك تكرارها؟" },
    he: { placeholder: "כתוב הודעה…", send: "שלח", newChat: "שיחה חדשה", stop: "עצור", interrupted: "התשובה הופסקה", access_denied: "אין לך הרשאה לשימוש בסוכן זה. אנא פנה למנהל המערכת.", error_occurred: "לא הצלחתי לעבד את זה. תוכל לחזור על כך?" },
    tr: { placeholder: "Mesaj yazın…", send: "Gönder", newChat: "Yeni Sohbet", stop: "Durdur", interrupted: "Yanıt yarıda kesildi", access_denied: "Bu ajana erişim izniniz yok. Lütfen yönetici ile iletişime geçin.", error_occurred: "Hmm, bunu işleyemedim. Tekrar eder misiniz?" },
  };
  const RTL_LANGS = ["ar", "he", "fa", "ur"];

  function _darkenHex(hex, amount) {
    var c = hex.replace("#", "");
    if (c.length === 3) c = c[0]+c[0]+c[1]+c[1]+c[2]+c[2];
    var r = Math.max(0, Math.round(parseInt(c.slice(0,2),16) * (1-amount)));
    var g = Math.max(0, Math.round(parseInt(c.slice(2,4),16) * (1-amount)));
    var b = Math.max(0, Math.round(parseInt(c.slice(4,6),16) * (1-amount)));
    return "#" + [r,g,b].map(function(v){ return v.toString(16).padStart(2,"0"); }).join("");
  }

  function _updateSendBtnState() {
    if (!sendBtn) return;
    var s = UI_STRINGS[currentLang] || UI_STRINGS["en"];
    if (sending) {
      sendBtn.textContent = s.stop || "Stop";
      sendBtn.classList.add("widget-stop-btn");
      sendBtn.disabled = false;
    } else {
      sendBtn.textContent = s.send || "Send";
      sendBtn.classList.remove("widget-stop-btn");
      sendBtn.disabled = false;
    }
  }

  function _stopGeneration() {
    if (abortController) {
      abortController.abort();
      abortController = null;
    }
  }

  function _applyLang(lang) {
    var s = UI_STRINGS[lang] || UI_STRINGS["en"];
    if (inputEl) inputEl.placeholder = s.placeholder;
    _updateSendBtnState();
    if (newChatBtn) newChatBtn.textContent = s.endChat || UI_STRINGS["en"].endChat;
    if (endConfirmText) endConfirmText.textContent = s.endConfirm || UI_STRINGS["en"].endConfirm;
    if (endConfirmYes) endConfirmYes.textContent = s.endYes || UI_STRINGS["en"].endYes;
    if (endConfirmNo) endConfirmNo.textContent = s.endNo || UI_STRINGS["en"].endNo;
    // RTL support
    var dir = RTL_LANGS.indexOf(lang) !== -1 ? "rtl" : "ltr";
    document.documentElement.setAttribute("dir", dir);
  }

  // --- DOM refs --------------------------------------------------------
  const messagesEl = document.getElementById("widgetMessages");
  const inputEl = document.getElementById("widgetInput");
  const sendBtn = document.getElementById("widgetSendBtn");
  const typingEl = document.getElementById("widgetTyping");
  const newChatBtn = document.getElementById("widgetNewChat");
  const minimizeBtn = document.getElementById("widgetMinimize");
  const endConfirm = document.getElementById("widgetEndConfirm");
  const endConfirmText = document.getElementById("widgetEndConfirmText");
  const endConfirmYes = document.getElementById("widgetEndConfirmYes");
  const endConfirmNo = document.getElementById("widgetEndConfirmNo");
  const greetingEl = document.getElementById("widgetGreeting");
  const headerTitle = document.getElementById("widgetHeaderTitle");
  const attachBtn = document.getElementById("widgetAttachBtn");
  const fileInput = document.getElementById("widgetFileInput");
  const imagePreview = document.getElementById("widgetImagePreview");

  // Re-apply lock from previous session if the widget was locked before iframe reload.
  if (_locked) {
    if (inputEl) { inputEl.disabled = true; inputEl.placeholder = "Test finished"; }
    if (sendBtn) sendBtn.disabled = true;
  }

  // Delegated handler for card actions (book a slot, add to cart, download .ics, ...).
  if (messagesEl) {
    messagesEl.addEventListener("click", function (e) {
      // Human-in-the-loop tool confirmation buttons
      var cbtn = e.target.closest ? e.target.closest(".mate-confirm-act") : null;
      if (cbtn && !cbtn.disabled) {
        var card = cbtn.closest(".mate-confirm-card");
        var fcId = card ? card.getAttribute("data-fc-id") : null;
        if (fcId) _sendConfirmation(fcId, cbtn.getAttribute("data-confirmed") === "1", card);
        return;
      }
      var btn = e.target.closest ? e.target.closest(".wz-card-act") : null;
      if (!btn) return;
      var kind = btn.getAttribute("data-kind");
      if (kind === "ics") {
        _downloadIcs({
          summary: btn.getAttribute("data-summary"), start: btn.getAttribute("data-start"),
          end: btn.getAttribute("data-end"), desc: btn.getAttribute("data-desc"), loc: btn.getAttribute("data-loc"),
        });
      } else if (kind === "message") {
        var v = btn.getAttribute("data-value");
        if (v) _sendText(v);
      }
    });
  }

  // Send a message on the visitor's behalf (used by card action buttons).
  function _sendText(text) {
    if (!inputEl || _locked || sending) return;
    inputEl.value = text;
    _send();
  }

  // --- Init ------------------------------------------------------------
  function init() {
    if (CFG.title) headerTitle.textContent = CFG.title;
    if (CFG.greeting && greetingEl) greetingEl.textContent = CFG.greeting;

    // Theme
    const theme = CFG.theme || "auto";
    if (theme === "dark") {
      document.documentElement.setAttribute("data-theme", "dark");
    } else if (theme === "light") {
      document.documentElement.removeAttribute("data-theme");
    } else {
      // auto — follow parent page preference via message or media query
      if (window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches) {
        document.documentElement.setAttribute("data-theme", "dark");
      }
    }

    // Restore chat history from sessionStorage
    const saved = sessionStorage.getItem(`${STORAGE_PREFIX}_msgs`);
    if (saved) {
      try {
        const msgs = JSON.parse(saved);
        msgs.forEach(function (m) { _appendMessage(m.role, m.text, true, null, m.author); });
        if (greetingEl) greetingEl.style.display = "none";
      } catch (_) {}
    }

    sendBtn.addEventListener("click", _send);
    inputEl.addEventListener("keydown", function (e) {
      if (e.key === "Enter" && !e.shiftKey) { 
        e.preventDefault(); 
        if (!sending) _send(); 
      }
    });
    newChatBtn.addEventListener("click", function () {
      if (endConfirm) endConfirm.style.display = "flex";
    });
    if (minimizeBtn) {
      if (window.parent === window) {
        // Not embedded in the launcher iframe — nothing to minimize
        minimizeBtn.style.display = "none";
      } else {
        minimizeBtn.addEventListener("click", function () {
          try { window.parent.postMessage({ type: "mate-close" }, "*"); } catch (_) {}
        });
      }
    }
    if (endConfirmNo) endConfirmNo.addEventListener("click", function () {
      if (endConfirm) endConfirm.style.display = "none";
    });
    if (endConfirmYes) endConfirmYes.addEventListener("click", function () {
      if (endConfirm) endConfirm.style.display = "none";
      _endConversation();
    });
    attachBtn.addEventListener("click", function () { fileInput.click(); });
    fileInput.addEventListener("change", _handleFileSelect);

    // Auto-resize textarea
    inputEl.addEventListener("input", function () {
      inputEl.style.height = "auto";
      inputEl.style.height = Math.min(inputEl.scrollHeight, 120) + "px";
    });

    // Attachment button visibility
    if (CFG.show_attachments === false) {
      if (attachBtn) attachBtn.style.display = "none";
    }

    // Apply button/accent color from widget config
    if (CFG.button_color && /^#[0-9a-f]{3,6}$/i.test(CFG.button_color)) {
      var color = CFG.button_color;
      var colorDark = _darkenHex(color, 0.12);
      document.documentElement.style.setProperty("--w-primary", color);
      document.documentElement.style.setProperty("--w-primary-hover", colorDark);
      document.documentElement.style.setProperty("--w-user-bubble", color);
    }

    if (CFG.icon_url) {
      const headerIcon = document.getElementById("widgetHeaderIcon");
      if (headerIcon) {
        headerIcon.src = CFG.icon_url;
        headerIcon.style.display = "block";
      }
    }

    // Notify parent so it can update the floating button color and icon
    try {
      window.parent.postMessage({
        type: "mate-config",
        button_color: CFG.button_color || "",
        icon_url: CFG.icon_url || ""
      }, "*");
    } catch (_) {}

    // Listen for messages from parent page (theme, page context, language)
    window.addEventListener("message", function (e) {
      if (!e.data) return;
      if (e.data.type === "mate-theme") {
        // Only apply parent-page theme when widget config is set to "auto"
        if (!CFG.theme || CFG.theme === "auto") {
          document.documentElement.setAttribute("data-theme", e.data.theme === "dark" ? "dark" : "");
        }
      }
      if (e.data.type === "mate-context") {
        var lang = (e.data.lang || "en").split("-")[0].toLowerCase();
        pageContext = {
          url: e.data.url || "",
          title: e.data.title || "",
          description: e.data.description || "",
          lang: lang,
        };
        if (lang && lang !== currentLang) {
          currentLang = lang;
          _applyLang(lang);
        }
      }
      if (e.data.type === "mate-lang") {
        var lang = (e.data.lang || "en").split("-")[0].toLowerCase();
        if (lang !== currentLang) {
          currentLang = lang;
          if (pageContext) pageContext.lang = lang;
          _applyLang(lang);
        }
      }
      if (e.data.type === "mate-color") {
        var color = e.data.button_color;
        if (color && /^#[0-9a-f]{3,6}$/i.test(color)) {
          document.documentElement.style.setProperty("--w-primary", color);
          document.documentElement.style.setProperty("--w-primary-hover", _darkenHex(color, 0.12));
          document.documentElement.style.setProperty("--w-user-bubble", color);
        }
      }
      if (e.data.type === "mate-lock-input") {
        _locked = true;
        localStorage.setItem(_LOCK_KEY, "1");
        if (inputEl) {
          inputEl.disabled = true;
          inputEl.placeholder = e.data.message || "Test finished";
        }
        if (sendBtn) sendBtn.disabled = true;
      }
    });
  }

  // --- Send message ----------------------------------------------------
  function _send() {
    if (sending) {
      _stopGeneration();
      return;
    }
    if (_locked) return;
    const text = inputEl.value.trim();
    const files = pendingFiles.slice();
    if (!text && !files.length) return;

    if (greetingEl) greetingEl.style.display = "none";
    _appendMessage("user", text, false, files, "user");
    // Notify parent (wizard) that the visitor sent a prompt — used for the trial prompt limit.
    try { window.parent.postMessage({ type: "mate-user-message" }, "*"); } catch (_) {}
    inputEl.value = "";
    inputEl.style.height = "auto";
    _clearPendingFiles();
    sending = true;
    _updateSendBtnState();
    _showTyping(true);

    // Build parts array
    const parts = [];
    files.forEach(function (f) {
      parts.push({ 
        inline_data: { mime_type: f.mimeType, data: f.base64 },
        filename: f.name
      });
    });
    if (text) parts.push({ text: text });

    const payload = { message: text, parts: parts, user_id: userId, session_id: sessionId, new_session: forceNewSession };
    if (pageContext) payload.page_context = pageContext;
    if (currentLang) payload.lang = currentLang;
    forceNewSession = false;

    abortController = new AbortController();

    fetch(`${BASE}/widget/api/chat`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Widget-Key": API_KEY,
      },
      body: JSON.stringify(payload),
      signal: abortController.signal
    })
      .then(function (res) {
        if (!res.ok) {
          return res.json()
            .catch(function () { return {}; })
            .then(function (errData) {
              throw new Error(errData.detail || errData.error || "Chat request failed: " + res.status);
            });
        }
        return _readSSE(res);
      })
      .catch(function (err) {
        _showTyping(false);
        if (err.name === "AbortError") {
          console.log("Generation interrupted by user.");
          var s = UI_STRINGS[currentLang] || UI_STRINGS["en"];
          var interruptText = " [" + (s.interrupted || "Response interrupted") + "]";
          if (activeAgentEl) {
            activeAgentText += interruptText;
            _updateMessage(activeAgentEl, activeAgentText);
            _addMessageActions(activeAgentEl);
          } else {
            _appendMessage("agent", s.interrupted || "Response interrupted", false, null, activeAgentAuthor || "agent");
          }
          _saveHistory();
          return;
        }
        var s = UI_STRINGS[currentLang] || UI_STRINGS["en"];
        var friendlyMsg = s.error_occurred || UI_STRINGS["en"].error_occurred;
        if (debugMode && err.message) friendlyMsg += "\n\n`" + err.message.replace(/^Error:\s*/i, "") + "`";
        _appendMessage("agent", friendlyMsg, false, null, "agent");
        console.error("Widget chat error:", err);
      })
      .finally(function () {
        sending = false;
        abortController = null;
        _updateSendBtnState();
      });
  }

  // --- Human-in-the-loop tool confirmation ------------------------------
  // ADK pauses a require_confirmation tool by emitting a functionCall named
  // "adk_request_confirmation". We render an approve/reject card; the answer
  // goes back as a functionResponse with the same id and {confirmed: bool}.
  function _confirmationCardHtml(fc) {
    function esc(s) { return String(s == null ? "" : s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;"); }
    var args = fc.args || {};
    var orig = args.originalFunctionCall || args.original_function_call || {};
    var conf = args.toolConfirmation || args.tool_confirmation || {};
    var s = UI_STRINGS[currentLang] || UI_STRINGS.en;
    var en = UI_STRINGS.en;
    var argsJson = "";
    try { argsJson = JSON.stringify(orig.args || {}); } catch (e) { argsJson = ""; }
    if (argsJson.length > 220) argsJson = argsJson.slice(0, 220) + "…";
    var html = '<div class="mate-confirm-card" data-fc-id="' + esc(fc.id) + '" style="border:1px solid #f59e0b;border-radius:12px;padding:12px;background:#fffbeb">';
    html += '<div style="font-size:12px;color:#b45309;font-weight:700">⚠ ' + esc(s.confirmTitle || en.confirmTitle) + '</div>';
    html += '<div style="font-weight:600;margin-top:4px;color:#0f172a;font-family:monospace;font-size:13px">' + esc(orig.name || "tool") + '</div>';
    if (conf.hint) html += '<div style="font-size:13px;color:#475569;margin-top:2px">' + esc(conf.hint) + '</div>';
    if (argsJson && argsJson !== "{}") html += '<div style="font-size:12px;color:#64748b;margin-top:4px;font-family:monospace;word-break:break-all">' + esc(argsJson) + '</div>';
    html += '<div style="display:flex;gap:10px;margin-top:10px">';
    html += '<button type="button" class="mate-confirm-act" data-confirmed="1" style="background:#16a34a;color:#fff;border:0;border-radius:8px;padding:6px 14px;font-size:13px;cursor:pointer;font-weight:600">' + esc(s.confirmApprove || en.confirmApprove) + '</button>';
    html += '<button type="button" class="mate-confirm-act" data-confirmed="0" style="background:#dc2626;color:#fff;border:0;border-radius:8px;padding:6px 14px;font-size:13px;cursor:pointer;font-weight:600">' + esc(s.confirmReject || en.confirmReject) + '</button>';
    html += '</div></div>';
    return html;
  }

  function _lockConfirmationCard(cardEl, confirmed) {
    var btns = cardEl.querySelectorAll(".mate-confirm-act");
    for (var i = 0; i < btns.length; i++) {
      btns[i].disabled = true;
      btns[i].style.opacity = "0.5";
      btns[i].style.cursor = "default";
    }
    var s = UI_STRINGS[currentLang] || UI_STRINGS.en;
    var en = UI_STRINGS.en;
    var status = document.createElement("div");
    status.style.cssText = "font-size:12px;margin-top:8px;font-weight:600;color:" + (confirmed ? "#16a34a" : "#dc2626");
    status.textContent = confirmed ? (s.confirmApproved || en.confirmApproved) : (s.confirmRejected || en.confirmRejected);
    cardEl.appendChild(status);
  }

  function _sendConfirmation(fcId, confirmed, cardEl) {
    if (sending || !sessionId) return;
    if (cardEl) _lockConfirmationCard(cardEl, confirmed);
    sending = true;
    _updateSendBtnState();
    _showTyping(true);
    abortController = new AbortController();

    var payload = {
      message: "",
      parts: [{
        function_response: {
          id: fcId,
          name: "adk_request_confirmation",
          response: { confirmed: !!confirmed },
        },
      }],
      user_id: userId,
      session_id: sessionId,
      new_session: false,
    };

    fetch(`${BASE}/widget/api/chat`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Widget-Key": API_KEY,
      },
      body: JSON.stringify(payload),
      signal: abortController.signal
    })
      .then(function (res) {
        if (!res.ok) throw new Error("Confirmation request failed: " + res.status);
        return _readSSE(res);
      })
      .catch(function (err) {
        _showTyping(false);
        if (err.name === "AbortError") return;
        var s = UI_STRINGS[currentLang] || UI_STRINGS["en"];
        _appendMessage("agent", s.error_occurred || UI_STRINGS["en"].error_occurred, false, null, "agent");
        console.error("Confirmation error:", err);
      })
      .finally(function () {
        sending = false;
        abortController = null;
        _updateSendBtnState();
      });
  }

  // --- Read SSE stream -------------------------------------------------
  // ADK streams events from every agent in the chain. Events contain:
  //   author        — which agent produced this event
  //   content.parts — text, functionCall, or functionResponse objects
  //   actions       — transfer_to_agent, escalate, etc.
  //
  // Strategy for a clean end-user experience:
  //   1. Skip transfer/routing actions entirely.
  //   2. When the author changes, reset — only show the latest agent.
  //   3. When a functionCall or functionResponse part appears, the agent
  //      is using tools. Discard any narration text accumulated so far
  //      ("Let me search…") and show a thinking indicator instead.
  //   4. Text arriving after tools finish is the real answer.
  //   5. De-duplicate: ADK often sends partial + complete events.

  function _readSSE(response) {
    var reader = response.body.getReader();
    var decoder = new TextDecoder();
    var buffer = "";
    var segmentText = "";
    var confirmationShown = false;
    activeAgentText = "";
    activeAgentEl = null;
    activeAgentAuthor = "";
    activeAgentImages = [];

    var THINKING_HTML = '<span class="widget-thinking-inline">' +
      '<span class="widget-thinking-dot"></span>' +
      '<span class="widget-thinking-dot"></span>' +
      '<span class="widget-thinking-dot"></span>' +
      ' Thinking\u2026</span>';

    function _ensureBubble() {
      if (!activeAgentEl) {
        var wrapper = document.createElement("div");
        wrapper.className = "widget-message-wrapper agent-message";
        
        var avatarColor = getAgentColor(activeAgentAuthor);
        var initials = getAgentInitials(activeAgentAuthor);
        
        var avatarEl = document.createElement("div");
        avatarEl.className = "widget-agent-avatar";
        if (CFG.icon_url) {
          avatarEl.innerHTML = '<img src="' + CFG.icon_url + '" style="width:100%;height:100%;object-fit:cover;border-radius:50%">';
          avatarEl.style.backgroundColor = "transparent";
        } else {
          avatarEl.style.backgroundColor = avatarColor;
          avatarEl.textContent = initials;
        }
        avatarEl.title = activeAgentAuthor;
        
        activeAgentEl = document.createElement("div");
        activeAgentEl.className = "widget-message agent";
        activeAgentEl.setAttribute("data-author", activeAgentAuthor);
        activeAgentEl.innerHTML = THINKING_HTML;
        
        wrapper.appendChild(avatarEl);
        wrapper.appendChild(activeAgentEl);
        messagesEl.appendChild(wrapper);
        _scrollToBottom();
      }
    }

    function processLine(line) {
      if (!line.startsWith("data: ")) return;
      var raw = line.slice(6);
      if (raw === "[DONE]") return;

      try {
        var evt = JSON.parse(raw);

        if (evt.session_id) {
          sessionId = evt.session_id;
          localStorage.setItem(STORAGE_PREFIX + "_sid", sessionId);
          if (evt.debug_mode !== undefined) debugMode = !!evt.debug_mode;
        }

        // --- Handle error events (e.g. RBAC access denied) ---
        if (evt.error_code) {
          var s = UI_STRINGS[currentLang] || UI_STRINGS["en"];
          var errText = evt.error_code === "RBAC_ACCESS_DENIED"
            ? (s.access_denied || UI_STRINGS["en"].access_denied)
            : (s.error_occurred || UI_STRINGS["en"].error_occurred);
          if (debugMode && evt.error_message) errText += "\n\n`" + evt.error_message + "`";
          _showTyping(false);
          _ensureBubble();
          activeAgentText = errText;
          _updateMessage(activeAgentEl, activeAgentText);
          return;
        }

        var actions = evt.actions || {};
        if (actions.transfer_to_agent || actions.escalate) return;

        // --- Handle artifact delta (image artifacts) ---
        var artifactDelta = actions.artifactDelta || actions.artifact_delta;
        if (artifactDelta && typeof artifactDelta === "object") {
          var filenames = Object.keys(artifactDelta);
          for (var ai = 0; ai < filenames.length; ai++) {
            var artFilename = filenames[ai];
            var artVersion = artifactDelta[artFilename];
            var lowerName = artFilename.toLowerCase();
            var isImage = IMAGE_EXTENSIONS.some(function (ext) {
              return lowerName.endsWith(ext);
            });
            if (isImage) {
              _showTyping(false);
              _ensureBubble();
              
              var publicUrl = BASE + "/api/widget/artifacts/" + AGENT_NAME + "/" + userId + "/" + sessionId + "/" + artFilename + "/" + artVersion;
              var alreadyAdded = activeAgentImages.some(function(img) { return img.url === publicUrl; });
              if (!alreadyAdded) {
                var imgHtml = '<img class="widget-msg-image widget-generated-image art-lazy-load" data-art-url="' + publicUrl + '" alt="' + artFilename + '">';
                activeAgentImages.push({ url: publicUrl, html: imgHtml });
              }
              _updateMessage(activeAgentEl, activeAgentText);
            }
          }
        }

        var author = evt.author || "";
        if (author && author !== activeAgentAuthor) {
          activeAgentAuthor = author;
          activeAgentText = "";
          segmentText = "";
          // When author changes, start a new bubble instead of overwriting/resetting the existing one.
          activeAgentEl = null;
        }

        var parts = (evt.content && evt.content.parts) || [];
        if (!parts.length) return;

        // Human-in-the-loop: tool confirmation request → approve/reject card
        for (var ci = 0; ci < parts.length; ci++) {
          var cfc = parts[ci].functionCall || parts[ci].function_call;
          if (cfc && cfc.name === "adk_request_confirmation") {
            _showTyping(false);
            _ensureBubble();
            activeAgentEl.innerHTML = _confirmationCardHtml(cfc);
            confirmationShown = true;
            activeAgentEl = null;
            activeAgentText = "";
            segmentText = "";
            _scrollToBottom();
            return;
          }
        }

        var hasToolPart = false;
        for (var i = 0; i < parts.length; i++) {
          if (parts[i].functionCall || parts[i].functionResponse ||
              parts[i].function_call || parts[i].function_response) {
            hasToolPart = true;
            break;
          }
        }

        if (hasToolPart) {
          _showTyping(true);
          segmentText = "";
          return;
        }

        for (var j = 0; j < parts.length; j++) {
          // Skip model reasoning ("thought") parts — internal, never shown to the user
          if (parts[j].thought) continue;

          // Handle inline image data (generated artifacts)
          var inlineData = parts[j].inline_data || parts[j].inlineData;
          if (inlineData && inlineData.mime_type && inlineData.mime_type.indexOf('image/') === 0) {
            _showTyping(false);
            _ensureBubble();
            var cleanBase64 = inlineData.data.replace(/\s+/g, '').replace(/-/g, '+').replace(/_/g, '/');
            var imgSrc = 'data:' + inlineData.mime_type + ';base64,' + cleanBase64;

            var exists = activeAgentImages.some(function(img) { return img.src === imgSrc; });
            if (!exists) {
              var imgHtml = '<img src="' + imgSrc + '" class="widget-msg-image widget-generated-image" alt="Generated image">';
              activeAgentImages.push({ type: "inline", src: imgSrc, html: imgHtml });
            }
            _updateMessage(activeAgentEl, activeAgentText);
            continue;
          }

          var t = parts[j].text;
          if (!t) continue;

          _showTyping(false);
          _ensureBubble();

          var delta = "";
          if (segmentText && t.indexOf(segmentText) === 0) {
            delta = t.slice(segmentText.length);
            segmentText = t;
          } else if (segmentText && segmentText.indexOf(t) === 0) {
            continue;
          } else {
            delta = t;
            segmentText = segmentText ? (segmentText + t) : t;
          }

          if (delta) {
            activeAgentText += delta;
            _updateMessage(activeAgentEl, activeAgentText);
          }
        }
      } catch (_) {}
    }

    return reader.read().then(function pump(result) {
      if (result.done) {
        _showTyping(false);
        if (!activeAgentText && activeAgentEl && !confirmationShown) {
          activeAgentEl.innerHTML = _renderMarkdown("(no response)");
          _addMessageActions(activeAgentEl);
        } else if (!activeAgentText && !activeAgentEl && !confirmationShown) {
          _appendMessage("agent", "(no response)", false, null, "agent");
        } else if (activeAgentEl) {
          _addMessageActions(activeAgentEl);
        }
        _saveHistory();
        return;
      }
      buffer += decoder.decode(result.value, { stream: true });
      var lines = buffer.split("\n");
      buffer = lines.pop();
      lines.forEach(processLine);
      return reader.read().then(pump);
    });
  }

  // --- File upload helpers --------------------------------------------
  const MAX_FILE_SIZE = 10 * 1024 * 1024; // 10 MB (images and PDFs)
  const MAX_TEXT_FILE_SIZE = 5 * 1024 * 1024; // 5 MB (text files)
  const MAX_DIMENSION = 2048;
  const SUPPORTED_TEXT_EXTS = [
    "txt", "md", "markdown", "json", "js", "ts", "py", "css", "csv", "html", "xml", "yaml", "yml", "ini", "log", "sql", "sh", "bat"
  ];

  function isSupportedFile(file) {
    if (!file) return false;
    var type = file.type || "";
    if (type.indexOf("image/") === 0) return true;
    if (type === "application/pdf") return true;
    if (type.indexOf("text/") === 0) return true;
    
    var ext = file.name.split(".").pop().toLowerCase();
    if (SUPPORTED_TEXT_EXTS.indexOf(ext) !== -1) return true;
    
    return false;
  }

  function _getMimeFromExtension(filename) {
    var ext = filename.split(".").pop().toLowerCase();
    var mimes = {
      pdf: "application/pdf",
      json: "application/json",
      js: "text/javascript",
      ts: "text/typescript",
      py: "text/x-python",
      css: "text/css",
      csv: "text/csv",
      html: "text/html",
      xml: "text/xml",
      yaml: "text/yaml",
      yml: "text/yaml",
      md: "text/markdown",
      txt: "text/plain"
    };
    return mimes[ext] || "application/octet-stream";
  }

  function _handleFileSelect(e) {
    const files = e.target.files;
    if (!files || !files.length) return;
    for (let i = 0; i < files.length; i++) {
      (function (file) {
        if (!isSupportedFile(file)) {
          alert("Unsupported file type: " + file.name + "\nSupported formats: Images, PDFs, and text files (.txt, .md, .json, .py, etc.)");
          return;
        }

        var isImg = file.type.indexOf("image/") === 0;
        var maxSize = isImg ? MAX_FILE_SIZE : (file.type === "application/pdf" ? MAX_FILE_SIZE : MAX_TEXT_FILE_SIZE);
        
        if (file.size > maxSize) {
          var sizeMB = Math.round(maxSize / (1024 * 1024));
          alert("File too large (max " + sizeMB + " MB): " + file.name);
          return;
        }

        if (isImg) {
          _readAndResizeImage(file, function (result) {
            result.name = file.name;
            pendingFiles.push(result);
            _renderPreviews();
          });
        } else {
          _readAttachment(file, function (result) {
            pendingFiles.push(result);
            _renderPreviews();
          });
        }
      })(files[i]);
    }
    fileInput.value = "";
  }

  function _readAttachment(file, cb) {
    var reader = new FileReader();
    reader.onload = function (e) {
      var dataUrl = e.target.result;
      var base64 = dataUrl.split(",")[1];
      cb({
        dataUrl: dataUrl,
        mimeType: file.type || _getMimeFromExtension(file.name),
        base64: base64,
        name: file.name
      });
    };
    reader.readAsDataURL(file);
  }

  function _readAndResizeImage(file, cb) {
    const reader = new FileReader();
    reader.onload = function (e) {
      const img = new Image();
      img.onload = function () {
        let w = img.width, h = img.height;
        if (w > MAX_DIMENSION || h > MAX_DIMENSION) {
          const scale = MAX_DIMENSION / Math.max(w, h);
          w = Math.round(w * scale);
          h = Math.round(h * scale);
        }
        const canvas = document.createElement("canvas");
        canvas.width = w;
        canvas.height = h;
        const ctx = canvas.getContext("2d");
        ctx.drawImage(img, 0, 0, w, h);
        const mimeType = file.type === "image/png" ? "image/png" : "image/jpeg";
        const quality = mimeType === "image/jpeg" ? 0.85 : undefined;
        const dataUrl = canvas.toDataURL(mimeType, quality);
        const base64 = dataUrl.split(",")[1];
        cb({ dataUrl: dataUrl, mimeType: mimeType, base64: base64, name: file.name });
      };
      img.src = e.target.result;
    };
    reader.readAsDataURL(file);
  }

  function _renderPreviews() {
    imagePreview.innerHTML = "";
    pendingFiles.forEach(function (f, idx) {
      const item = document.createElement("div");
      item.className = "widget-preview-item";
      
      const isImage = f.mimeType && f.mimeType.indexOf("image/") === 0;
      if (isImage) {
        const imgEl = document.createElement("img");
        imgEl.src = f.dataUrl;
        item.appendChild(imgEl);
      } else {
        const badgeEl = document.createElement("div");
        badgeEl.className = "widget-preview-file-badge";
        
        const ext = f.name.split(".").pop().toUpperCase();
        var icon = "📄";
        if (ext === "PDF") icon = "📕";
        else if (["JSON", "PY", "JS", "TS", "HTML", "CSS", "YAML", "YML"].indexOf(ext) !== -1) icon = "💻";
        
        badgeEl.innerHTML = '<span class="file-icon">' + icon + '</span><span class="file-name">' + f.name + '</span>';
        item.appendChild(badgeEl);
      }

      const removeBtn = document.createElement("button");
      removeBtn.className = "widget-preview-remove";
      removeBtn.textContent = "\u00D7";
      removeBtn.onclick = function () {
        pendingFiles.splice(idx, 1);
        _renderPreviews();
      };
      item.appendChild(removeBtn);
      imagePreview.appendChild(item);
    });
    imagePreview.classList.toggle("active", pendingFiles.length > 0);
  }

  function _clearPendingFiles() {
    pendingFiles = [];
    imagePreview.innerHTML = "";
    imagePreview.classList.remove("active");
  }

  // --- DOM helpers -----------------------------------------------------
  function _appendMessage(role, text, skipSave, files, author) {
    var el = document.createElement("div");
    if (role === "agent") {
      var wrapper = document.createElement("div");
      wrapper.className = "widget-message-wrapper agent-message";
      
      var avatarColor = getAgentColor(author);
      var initials = getAgentInitials(author);
      
      var avatarEl = document.createElement("div");
      avatarEl.className = "widget-agent-avatar";
      if (CFG.icon_url) {
        avatarEl.innerHTML = '<img src="' + CFG.icon_url + '" style="width:100%;height:100%;object-fit:cover;border-radius:50%">';
        avatarEl.style.backgroundColor = "transparent";
      } else {
        avatarEl.style.backgroundColor = avatarColor;
        avatarEl.textContent = initials;
      }
      avatarEl.title = author || "agent";
      
      el.className = "widget-message agent";
      el.setAttribute("data-author", author || "");
      el._rawMarkdown = text;
      el.innerHTML = _renderMessageHtml(text);

      wrapper.appendChild(avatarEl);
      wrapper.appendChild(el);
      messagesEl.appendChild(wrapper);
      _addMessageActions(el);
    } else {
      el.className = "widget-message " + role;
      el.setAttribute("data-author", author || "");
      // Show attached files in user bubble
      if (files && files.length) {
        files.forEach(function (fileObj) {
          var isImg = fileObj.mimeType && fileObj.mimeType.indexOf("image/") === 0;
          if (isImg) {
            var imgEl = document.createElement("img");
            imgEl.src = fileObj.dataUrl;
            imgEl.className = "widget-msg-image";
            el.appendChild(imgEl);
          } else {
            var fileLink = document.createElement("div");
            fileLink.className = "widget-msg-file-attachment";
            
            var ext = fileObj.name.split(".").pop().toUpperCase();
            var icon = "📄";
            if (ext === "PDF") icon = "📕";
            
            fileLink.innerHTML = '<span class="file-icon">' + icon + '</span><span class="file-name">' + fileObj.name + '</span>';
            el.appendChild(fileLink);
          }
        });
      }
      if (text) {
        var textNode = document.createElement("span");
        textNode.textContent = text;
        el.appendChild(textNode);
      }
      messagesEl.appendChild(el);
    }
    _scrollToBottom();
    _loadLazyArtifacts();
    if (!skipSave) _saveHistory();
    return el;
  }

  function _updateMessage(el, text) {
    el._rawMarkdown = text;
    var html = _renderMessageHtml(text);
    if (activeAgentImages && activeAgentImages.length) {
      activeAgentImages.forEach(function(img) {
        if (img.type === "inline") {
          html += img.html;
        } else {
          if (html.indexOf(img.url) === -1) {
            html += img.html;
          }
        }
      });
    }
    el.innerHTML = html;
    _scrollToBottom();
    _loadLazyArtifacts();
  }

  // --- Generic rich cards in chat (any agent) --------------------------
  // Agents emit one or more markers: [[CARD]]{...} (generic) or [[APPOINTMENT]]{...} (shortcut).
  // A card: {type, badge, title, subtitle, lines:[...], image, location, ics:{...},
  //          actions:[{label, kind:"message"|"link"|"ics", value}]}.
  function _balancedEnd(text, start) {
    var depth = 0, inStr = false, esc = false;
    for (var i = start; i < text.length; i++) {
      var ch = text[i];
      if (inStr) { if (esc) esc = false; else if (ch === "\\") esc = true; else if (ch === '"') inStr = false; }
      else if (ch === '"') inStr = true;
      else if (ch === "{") depth++;
      else if (ch === "}") { depth--; if (depth === 0) return i; }
    }
    return -1;
  }

  function _appointmentToCard(d) {
    var actions = [{ label: "📅 Add to calendar", kind: "ics" }];
    if (d.html_link) actions.push({ label: "Open in Google Calendar", kind: "link", value: d.html_link });
    return {
      type: "appointment", badge: "✓ Confirmed",
      title: d.summary || "Appointment", subtitle: _fmtRange(d.start, d.end),
      location: d.location,
      ics: { summary: d.summary, start: d.start, end: d.end, description: d.description, location: d.location },
      actions: actions,
    };
  }

  function _extractCards(text) {
    // Regex handles normal: [[APPOINTMENT]]{"..."} and code-fenced: [[APPOINTMENT]]\n```json\n{...}
    var cards = [], ranges = [],
      re = /\[\[(CARD|APPOINTMENT)\]\][ \t]*\n?[ \t]*(?:```(?:json|JSON)?[ \t]*\n?)?\{/g, m;
    while ((m = re.exec(text)) !== null) {
      var braceStart = text.indexOf("{", m.index);
      var end = _balancedEnd(text, braceStart);
      if (end === -1) continue;
      var data;
      try { data = JSON.parse(text.slice(braceStart, end + 1)); } catch (e) { continue; }
      cards.push(m[1] === "APPOINTMENT" ? _appointmentToCard(data) : data);
      // Extend range to also swallow a closing code fence (```) immediately after the JSON.
      var endExtended = end + 1;
      var closingFence = text.slice(end + 1).match(/^[ \t]*\n?[ \t]*```[ \t]*\n?/);
      if (closingFence) endExtended += closingFence[0].length;
      ranges.push([m.index, endExtended]);
      re.lastIndex = endExtended;
    }
    var cleaned = text;
    ranges.sort(function (a, b) { return b[0] - a[0]; }).forEach(function (r) { cleaned = cleaned.slice(0, r[0]) + cleaned.slice(r[1]); });
    return { cleaned: cleaned.trim(), cards: cards };
  }

  function _renderMessageHtml(text) {
    var ext = _extractCards(text);
    if (!ext.cards.length) return _renderMarkdown(text);
    var html = _renderMarkdown(ext.cleaned);
    ext.cards.forEach(function (c) { html += _cardHtml(c); });
    return html;
  }

  function _fmtRange(startIso, endIso) {
    try {
      var s = new Date(startIso);
      var out = s.toLocaleString(currentLang || undefined, { weekday: "short", year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
      if (endIso) out += " – " + new Date(endIso).toLocaleTimeString(currentLang || undefined, { hour: "2-digit", minute: "2-digit" });
      return out;
    } catch (e) { return startIso + (endIso ? " – " + endIso : ""); }
  }

  function _cardHtml(c) {
    function a(s) { return String(s == null ? "" : s).replace(/"/g, "&quot;"); }
    function e(s) { return String(s == null ? "" : s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;"); }
    var body = "";
    if (c.badge) body += '<div style="font-size:12px;color:#16a34a;font-weight:600">' + e(c.badge) + "</div>";
    if (c.title) body += '<div style="font-weight:600;margin-top:2px;color:#0f172a">' + e(c.title) + "</div>";
    if (c.subtitle) body += '<div style="font-size:13px;color:#475569;margin-top:2px">' + e(c.subtitle) + "</div>";
    (c.lines || []).forEach(function (l) { body += '<div style="font-size:13px;color:#475569">' + e(l) + "</div>"; });
    if (c.location) body += '<div style="font-size:13px;color:#475569">📍 ' + e(c.location) + "</div>";
    var img = c.image ? '<img src="' + a(c.image) + '" style="width:48px;height:48px;object-fit:cover;border-radius:8px;flex-shrink:0">' : "";
    var inner = img ? '<div style="display:flex;gap:10px">' + img + "<div>" + body + "</div></div>" : body;
    var actions = (c.actions || []).map(function (act) {
      if (act.kind === "link") {
        return '<a href="' + a(act.value) + '" target="_blank" rel="noopener" style="font-size:13px;color:var(--w-primary,#2563eb);text-decoration:none;padding:6px 0">' + e(act.label) + "</a>";
      }
      var attrs = 'data-kind="' + a(act.kind) + '" data-value="' + a(act.value || "") + '"';
      if (act.kind === "ics" && c.ics) {
        attrs += ' data-summary="' + a(c.ics.summary) + '" data-start="' + a(c.ics.start) + '" data-end="' + a(c.ics.end || "") + '" data-desc="' + a(c.ics.description || "") + '" data-loc="' + a(c.ics.location || "") + '"';
      }
      return '<button type="button" class="wz-card-act" ' + attrs + ' style="background:var(--w-primary,#2563eb);color:#fff;border:0;border-radius:8px;padding:6px 12px;font-size:13px;cursor:pointer">' + e(act.label) + "</button>";
    }).join("");
    return '<div style="border:1px solid #e2e8f0;border-radius:12px;padding:12px;margin-top:8px;background:#f8fafc">'
      + inner + (actions ? '<div style="display:flex;gap:10px;align-items:center;margin-top:8px;flex-wrap:wrap">' + actions + "</div>" : "") + "</div>";
  }

  function _buildIcs(c) {
    function fmt(dt) { try { return new Date(dt).toISOString().replace(/[-:]/g, "").replace(/\.\d{3}/, ""); } catch (e) { return ""; } }
    function esc(s) { return String(s || "").replace(/\\/g, "\\\\").replace(/;/g, "\\;").replace(/,/g, "\\,").replace(/\n/g, "\\n"); }
    var lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//MATE//Wizard//EN", "CALSCALE:GREGORIAN", "BEGIN:VEVENT",
      "UID:mate-" + Date.now() + "@wizard", "DTSTAMP:" + fmt(new Date().toISOString()),
      "DTSTART:" + fmt(c.start), "DTEND:" + fmt(c.end || c.start), "SUMMARY:" + esc(c.summary)];
    if (c.desc) lines.push("DESCRIPTION:" + esc(c.desc));
    if (c.loc) lines.push("LOCATION:" + esc(c.loc));
    lines.push("END:VEVENT", "END:VCALENDAR");
    return lines.join("\r\n");
  }

  function _downloadIcs(c) {
    var blob = new Blob([_buildIcs(c)], { type: "text/calendar;charset=utf-8" });
    var url = URL.createObjectURL(blob);
    var a = document.createElement("a");
    a.href = url;
    a.download = (c.summary || "appointment").replace(/[^\w\-]+/g, "_").slice(0, 40) + ".ics";
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(function () { URL.revokeObjectURL(url); }, 1500);
  }

  function _loadLazyArtifacts() {
    document.querySelectorAll("img.art-lazy-load").forEach(function(imgEl) {
      var url = imgEl.getAttribute("data-art-url");
      if (url && !imgEl.getAttribute("data-loading")) {
        imgEl.setAttribute("data-loading", "true");
        imgEl.style.opacity = "0.5";
        
        // Cache lookup
        if (artifactCache[url]) {
          imgEl.src = artifactCache[url];
          imgEl.style.opacity = "1";
          imgEl.classList.remove("art-lazy-load");
          _scrollToBottom();
          return;
        }
        
        fetch(url)
          .then(function(r) { return r.json(); })
          .then(function(data) {
             var inlineData = data.inlineData || data.inline_data;
             if (inlineData && inlineData.data) {
                 var mimeType = inlineData.mimeType || inlineData.mime_type || 'image/png';
                 var cleanBase64 = inlineData.data.replace(/\s+/g, '').replace(/-/g, '+').replace(/_/g, '/');
                 var base64Src = 'data:' + mimeType + ';base64,' + cleanBase64;
                 artifactCache[url] = base64Src; // Cache it!
                 imgEl.src = base64Src;
                 imgEl.style.opacity = "1";
                 imgEl.classList.remove("art-lazy-load");
                 _scrollToBottom();
             } else {
                 imgEl.style.display = "none";
             }
          }).catch(function() { 
             imgEl.style.display = "none"; 
          });
      }
    });
  }

  function _showTyping(show) {
    if (typingEl) {
      typingEl.classList.toggle("active", show);
      if (show) {
        messagesEl.appendChild(typingEl);
        _scrollToBottom();
      }
    }
  }

  function _scrollToBottom() {
    messagesEl.scrollTop = messagesEl.scrollHeight;
  }

  function _newChat() {
    sessionId = "";
    forceNewSession = true;
    localStorage.removeItem(`${STORAGE_PREFIX}_sid`);
    sessionStorage.removeItem(`${STORAGE_PREFIX}_msgs`);
    messagesEl.innerHTML = "";
    if (greetingEl) { greetingEl.style.display = ""; messagesEl.appendChild(greetingEl); }
    messagesEl.appendChild(typingEl);
  }

  // Deliberate "End conversation": clear the session + history, then ask the launcher to
  // minimize the panel. Next open starts a fresh conversation.
  function _endConversation() {
    _newChat();
    try { window.parent.postMessage({ type: "mate-close" }, "*"); } catch (_) {}
  }

  function _saveHistory() {
    var msgs = [];
    messagesEl.querySelectorAll(".widget-message").forEach(function (el) {
      var role = el.classList.contains("user") ? "user" : "agent";
      var author = el.getAttribute("data-author") || "";
      var textContent = "";
      if (role === "agent") {
        textContent = el._rawMarkdown || el.innerHTML;
      } else {
        textContent = el.textContent;
      }
      msgs.push({ role: role, text: textContent, author: author });
    });
    try { sessionStorage.setItem(`${STORAGE_PREFIX}_msgs`, JSON.stringify(msgs)); } catch (_) {}
  }

  function getAgentColor(agentName) {
    if (!agentName) return "#6b7280";
    var colors = [
      "#3b82f6", // Blue
      "#10b981", // Emerald
      "#8b5cf6", // Violet
      "#f59e0b", // Amber
      "#ec4899", // Pink
      "#14b8a6", // Teal
      "#f97316", // Orange
      "#6366f1", // Indigo
      "#a855f7", // Purple
    ];
    var hash = 0;
    for (var i = 0; i < agentName.length; i++) {
      hash = agentName.charCodeAt(i) + ((hash << 5) - hash);
    }
    var index = Math.abs(hash) % colors.length;
    return colors[index];
  }

  function getAgentInitials(agentName) {
    if (!agentName) return "A";
    var clean = agentName.replace(/^ant_/, "");
    var parts = clean.split(/[_-]/).filter(Boolean);
    // Use the last meaningful segment so agents that share a project prefix
    // (e.g. mystery_evening_doktorka vs …_udovica) get distinct initials
    // instead of collapsing to the shared prefix ("ME").
    var GENERIC = { root: 1, agent: 1, bot: 1, main: 1 };
    while (parts.length > 1 && GENERIC[parts[parts.length - 1].toLowerCase()]) parts.pop();
    var last = parts.length ? parts[parts.length - 1] : clean;
    return last.slice(0, 2).toUpperCase();
  }

  function _generateId() {
    return "u_" + Math.random().toString(36).slice(2, 10) + Date.now().toString(36);
  }

  function _renderMarkdown(text) {
    if (!text) return "";

    // Pre-process any MATE image artifacts to use lazy-loading img tags
    // 1. Markdown images pointing to artifacts
    text = text.replace(/!\[([^\]]*)\]\((.*?\/api\/widget\/artifacts\/[^\s)]+)\)/gi, function(_, alt, url) {
        return '<img class="widget-msg-image widget-generated-image art-lazy-load" data-art-url="' + url + '" alt="' + alt + '">';
    });

    // 2. Markdown links pointing to image artifacts
    text = text.replace(/\[([^\]]*)\]\((.*?\/api\/widget\/artifacts\/[^\s)]+)\)/gi, function(_, label, url) {
        var lowerUrl = url.toLowerCase();
        var isImage = lowerUrl.indexOf('.png') !== -1 || lowerUrl.indexOf('.jpg') !== -1 || lowerUrl.indexOf('.jpeg') !== -1 || lowerUrl.indexOf('.webp') !== -1;
        if (isImage) {
            return '<img class="widget-msg-image widget-generated-image art-lazy-load" data-art-url="' + url + '" alt="' + label + '">';
        }
        return '[' + label + '](' + url + ')';
    });

    // 3. Raw URLs in text pointing to image artifacts (e.g. printed as text by the agent)
    text = text.replace(/(^|\s)(\/api\/widget\/artifacts\/[^\s"')]+\.(?:png|jpg|jpeg|webp)(?:\/\d+)?)/gi, function(match, space, url) {
        return space + '<img class="widget-msg-image widget-generated-image art-lazy-load" data-art-url="' + url + '" alt="Screenshot">';
    });

    var html = text
      // Code blocks
      .replace(/```(\w*)\n([\s\S]*?)```/g, function (_, lang, code) {
        return '<pre><code>' + _escapeHtml(code.trim()) + '</code></pre>';
      })
      // Inline code
      .replace(/`([^`]+)`/g, '<code>$1</code>')
      // Bold
      .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
      // Italic
      .replace(/\*(.+?)\*/g, '<em>$1</em>')
      // Inline images
      .replace(/!\[([^\]]*)\]\(([^)]+)\)/g, '<img src="$2" alt="$1" class="widget-msg-image widget-generated-image">')
      // Links
      .replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>')
      // Headers (h3 max inside chat)
      .replace(/^### (.+)$/gm, '<strong>$1</strong>')
      .replace(/^## (.+)$/gm, '<strong>$1</strong>')
      .replace(/^# (.+)$/gm, '<strong>$1</strong>')
      // Unordered lists
      .replace(/^[*-] (.+)$/gm, '<li>$1</li>')
      // Ordered lists
      .replace(/^\d+\. (.+)$/gm, '<li>$1</li>')
      // Paragraphs
      .replace(/\n{2,}/g, '</p><p>')
      .replace(/\n/g, '<br>');

    // Wrap consecutive <li> in <ul>
    html = html.replace(/(<li>.*?<\/li>)+/gs, function (match) {
      return '<ul>' + match + '</ul>';
    });

    return '<p>' + html + '</p>';
  }

  function _escapeHtml(text) {
    var d = document.createElement("div");
    d.textContent = text;
    return d.innerHTML;
  }

  // --- SVG Icons & Actions ----------------------------------------------
  const COPY_SVG = '<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:middle"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg>';
  const CHECK_SVG = '<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:middle"><polyline points="20 6 9 17 4 12"></polyline></svg>';
  const DOWNLOAD_SVG = '<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:middle"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path><polyline points="7 10 12 15 17 10"></polyline><line x1="12" y1="15" x2="12" y2="3"></line></svg>';

  function _addMessageActions(messageEl) {
    if (!messageEl) return;
    if (messageEl.querySelector(".widget-message-actions")) return;
    
    var actionsContainer = document.createElement("div");
    actionsContainer.className = "widget-message-actions";
    
    var s = UI_STRINGS[currentLang] || UI_STRINGS["en"];
    var copyText = s.copy || "Copy";
    var copiedText = s.copied || "Copied!";
    var downloadText = s.download || "Download";
    
    var copyBtn = document.createElement("button");
    copyBtn.className = "widget-message-action-btn copy-btn";
    copyBtn.title = copyText;
    copyBtn.innerHTML = COPY_SVG;
    
    copyBtn.addEventListener("click", function (e) {
      e.stopPropagation();
      var cleanText = _getCleanMessageText(messageEl);
      if (navigator.clipboard && window.isSecureContext) {
        navigator.clipboard.writeText(cleanText).then(function () {
          copyBtn.innerHTML = CHECK_SVG;
          copyBtn.title = copiedText;
          setTimeout(function () {
            copyBtn.innerHTML = COPY_SVG;
            copyBtn.title = copyText;
          }, 2000);
        }).catch(function () {
          _fallbackCopy(cleanText, copyBtn, copiedText, copyText);
        });
      } else {
        _fallbackCopy(cleanText, copyBtn, copiedText, copyText);
      }
    });
    
    var downloadBtn = document.createElement("button");
    downloadBtn.className = "widget-message-action-btn download-btn";
    downloadBtn.title = downloadText;
    downloadBtn.innerHTML = DOWNLOAD_SVG;
    
    downloadBtn.addEventListener("click", function (e) {
      e.stopPropagation();
      var cleanText = _getCleanMessageText(messageEl);
      var fn = (AGENT_NAME || "agent").replace(/[^a-zA-Z0-9_-]/g, "") + "-response.md";
      var blob = new Blob([cleanText], { type: "text/markdown;charset=utf-8" });
      var url = URL.createObjectURL(blob);
      var a = document.createElement("a");
      a.href = url;
      a.download = fn;
      a.style.display = "none";
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
    });
    
    actionsContainer.appendChild(copyBtn);
    actionsContainer.appendChild(downloadBtn);
    messageEl.appendChild(actionsContainer);
  }

  function _fallbackCopy(text, btn, successLabel, normalLabel) {
    var ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    try {
      document.execCommand("copy");
      btn.innerHTML = CHECK_SVG;
      btn.title = successLabel;
      setTimeout(function () {
        btn.innerHTML = COPY_SVG;
        btn.title = normalLabel;
      }, 2000);
    } catch (err) {
      console.error("Fallback copy failed", err);
    }
    document.body.removeChild(ta);
  }

  function _getCleanMessageText(messageEl) {
    return messageEl._rawMarkdown || messageEl.innerText || "";
  }


  // --- Boot ------------------------------------------------------------
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
