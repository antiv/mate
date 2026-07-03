/**
 * MATE Standalone Chat — client-side logic.
 *
 * Talks directly to the local ADK endpoints (/run_sse, /apps/...).
 * No widget API key required.
 *
 * Expects one global injected by the template:
 *   STANDALONE_AGENT_NAME
 */
(function () {
  "use strict";

  // --- Config ----------------------------------------------------------
  var AGENT_NAME = window.STANDALONE_AGENT_NAME || "agent";
  var BASE = window.location.origin;
  var STORAGE_PREFIX = "mate_standalone_" + AGENT_NAME;
  var IMAGE_EXTENSIONS = [".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp"];

  // --- State -----------------------------------------------------------
  var sessionId = localStorage.getItem(STORAGE_PREFIX + "_sid") || "";
  var userId = localStorage.getItem(STORAGE_PREFIX + "_uid") || _generateId();
  localStorage.setItem(STORAGE_PREFIX + "_uid", userId);

  var sending = false;
  var pendingFiles = []; // [{dataUrl, mimeType, base64, name}]
  var abortController = null;
  var activeAgentEl = null;
  var activeAgentText = "";
  var activeAgentAuthor = "";
  var activeAgentImages = [];
  var artifactCache = {};

  var currentLang = "en";
  if (document.documentElement.lang) {
    currentLang = document.documentElement.lang.split("-")[0].toLowerCase();
  }

  // UI string translations
  var UI_STRINGS = {
    en: { placeholder: "Type a message…", send: "Send", newChat: "New Chat", stop: "Stop", interrupted: "Response interrupted", copy: "Copy", copied: "Copied!", download: "Download" },
    sr: { placeholder: "Unesite poruku…", send: "Pošalji", newChat: "Nov razgovor", stop: "Prekini", interrupted: "Odgovor je prekinut", copy: "Kopiraj", copied: "Kopirano!", download: "Preuzmi" },
    hr: { placeholder: "Unesite poruku…", send: "Pošalji", newChat: "Novi razgovor", stop: "Prekini", interrupted: "Odgovor je prekinut", copy: "Kopiraj", copied: "Kopirano!", download: "Preuzmi" },
    bs: { placeholder: "Unesite poruku…", send: "Pošalji", newChat: "Novi razgovor", stop: "Prekini", interrupted: "Odgovor je prekinut", copy: "Kopiraj", copied: "Kopirano!", download: "Preuzmi" },
    de: { placeholder: "Nachricht eingeben…", send: "Senden", newChat: "Neuer Chat", stop: "Stoppen", interrupted: "Antwort unterbrochen", copy: "Kopieren", copied: "Kopiert!", download: "Herunterladen" },
    fr: { placeholder: "Écrivez un message…", send: "Envoyer", newChat: "Nouveau chat", stop: "Arrêter", interrupted: "Réponse interrompue", copy: "Copier", copied: "Copié !", download: "Télécharger" },
    es: { placeholder: "Escribe un mensaje…", send: "Enviar", newChat: "Nueva conversación", stop: "Detener", interrupted: "Respuesta interrumpida" },
    it: { placeholder: "Scrivi un messaggio…", send: "Invia", newChat: "Nuova chat", stop: "Interrompi", interrupted: "Risposta interrotta" },
    pt: { placeholder: "Escreva uma mensagem…", send: "Enviar", newChat: "Nova conversa", stop: "Parar", interrupted: "Resposta interrompida" },
    nl: { placeholder: "Typ een bericht…", send: "Versturen", newChat: "Nieuw gesprek", stop: "Stoppen", interrupted: "Reactie onderbroken" },
    pl: { placeholder: "Wpisz wiadomość…", send: "Wyślij", newChat: "Nowy czat", stop: "Zatrzymaj", interrupted: "Odpowiedź przerwana" },
    ru: { placeholder: "Введите сообщение…", send: "Отправить", newChat: "Новый чат", stop: "Остановить", interrupted: "Ответ прерван" },
    zh: { placeholder: "输入消息…", send: "发送", newChat: "新对话", stop: "停止", interrupted: "回答被中断" },
    ja: { placeholder: "メッセージを入力…", send: "送信", newChat: "新しいチャット", stop: "停止", interrupted: "回答が中断されました" },
    ar: { placeholder: "اكتب رسالة…", send: "إرسال", newChat: "محادثة جديدة", stop: "إيقاف", interrupted: "تم مقاطعة الإجابة" },
    he: { placeholder: "כתוב הודעה…", send: "שלח", newChat: "שיחה חדשה", stop: "עצור", interrupted: "התשובה הופסקה" },
    tr: { placeholder: "Mesaj yazın…", send: "Gönder", newChat: "Yeni Sohbet", stop: "Durdur", interrupted: "Yanıt yarıda kesildi" },
  };

  // --- DOM refs --------------------------------------------------------
  var messagesEl = document.getElementById("widgetMessages");
  var inputEl = document.getElementById("widgetInput");
  var sendBtn = document.getElementById("widgetSendBtn");
  var typingEl = document.getElementById("widgetTyping");
  var newChatBtn = document.getElementById("widgetNewChat");
  var greetingEl = document.getElementById("widgetGreeting");

  // Delegated handler for card actions (book a slot, add to cart, download .ics, ...).
  if (messagesEl) {
    messagesEl.addEventListener("click", function (e) {
      var btn = e.target.closest ? e.target.closest(".wz-card-act") : null;
      if (!btn) return;
      var kind = btn.getAttribute("data-kind");
      if (kind === "ics") {
        _downloadIcs({ summary: btn.getAttribute("data-summary"), start: btn.getAttribute("data-start"),
          end: btn.getAttribute("data-end"), desc: btn.getAttribute("data-desc"), loc: btn.getAttribute("data-loc") });
      } else if (kind === "message") {
        var v = btn.getAttribute("data-value");
        if (v) _sendText(v);
      }
    });
  }
  var attachBtn = document.getElementById("widgetAttachBtn");
  var fileInput = document.getElementById("widgetFileInput");
  var imagePreview = document.getElementById("widgetImagePreview");
  var headerTitle = document.getElementById("widgetHeaderTitle");

  // --- Init ------------------------------------------------------------
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

  function init() {
    // Theme — follow OS preference
    if (window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches) {
      document.documentElement.setAttribute("data-theme", "dark");
    }

    _updateSendBtnState();

    // Restore chat history from sessionStorage
    var saved = sessionStorage.getItem(STORAGE_PREFIX + "_msgs");
    if (saved) {
      try {
        var msgs = JSON.parse(saved);
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
    newChatBtn.addEventListener("click", _newChat);
    attachBtn.addEventListener("click", function () { fileInput.click(); });
    fileInput.addEventListener("change", _handleFileSelect);

    // Auto-resize textarea
    inputEl.addEventListener("input", function () {
      inputEl.style.height = "auto";
      inputEl.style.height = Math.min(inputEl.scrollHeight, 120) + "px";
    });
  }

  // --- Create ADK session ----------------------------------------------
  function _createSession() {
    return fetch(BASE + "/apps/" + AGENT_NAME + "/users/" + userId + "/sessions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    })
      .then(function (res) {
        if (!res.ok) throw new Error("Failed to create session: " + res.status);
        return res.json();
      })
      .then(function (data) {
        sessionId = data.id || "";
        localStorage.setItem(STORAGE_PREFIX + "_sid", sessionId);
        return sessionId;
      });
  }

  // --- Send message ----------------------------------------------------
  function _send() {
    if (sending) {
      _stopGeneration();
      return;
    }
    var text = inputEl.value.trim();
    var files = pendingFiles.slice();
    var canvasCtx = window.mateGetCanvasCode && window.mateGetCanvasCode();
    if ((!text && !files.length && !canvasCtx)) return;

    if (greetingEl) greetingEl.style.display = "none";
    var userEl = _appendMessage("user", text, false, files, "user");
    // Show a small badge on the user bubble when canvas code is attached
    if (canvasCtx && userEl) {
      var badge = document.createElement("span");
      badge.className = "mate-canvas-attach-badge";
      badge.textContent = "⌨ " + canvasCtx.lang + " · canvas";
      userEl.appendChild(badge);
    }
    inputEl.value = "";
    inputEl.style.height = "auto";
    _clearPendingFiles();
    sending = true;
    _updateSendBtnState();
    _showTyping(true);

    // Build parts array — append canvas code to the text part
    var parts = [];
    files.forEach(function (f) {
      parts.push({ 
        inline_data: { mime_type: f.mimeType, data: f.base64 },
        filename: f.name
      });
    });
    var sendText = text;
    if (canvasCtx) {
      sendText += (text ? "\n\n" : "") +
        "[Canvas code – " + canvasCtx.lang + "]\n```" + canvasCtx.lang + "\n" +
        canvasCtx.code + "\n```";
    }
    if (sendText) parts.push({ text: sendText });

    var doSend = sessionId
      ? Promise.resolve(sessionId)
      : _createSession();

    abortController = new AbortController();

    doSend
      .then(function (sid) {
        var payload = {
          app_name: AGENT_NAME,
          user_id: userId,
          session_id: sid,
          new_message: {
            role: "user",
            parts: parts,
          },
          streaming: true,
        };

        return fetch(BASE + "/run_sse", {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            Accept: "text/event-stream",
          },
          body: JSON.stringify(payload),
          signal: abortController.signal
        });
      })
      .then(function (res) {
        // If session not found, create a new one and retry
        if (res.status === 404) {
          return _createSession().then(function (newSid) {
            var payload = {
              app_name: AGENT_NAME,
              user_id: userId,
              session_id: newSid,
              new_message: {
                role: "user",
                parts: parts,
              },
              streaming: true,
            };
            return fetch(BASE + "/run_sse", {
              method: "POST",
              headers: {
                "Content-Type": "application/json",
                Accept: "text/event-stream",
              },
              body: JSON.stringify(payload),
              signal: abortController.signal
            });
          });
        }
        if (!res.ok) {
          return res.json()
            .catch(function () { return {}; })
            .then(function (errData) {
              throw new Error(errData.detail || errData.error || "Chat request failed: " + res.status);
            });
        }
        return res;
      })
      .then(function (res) {
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
        var msg = err.message || "Sorry, something went wrong. Please try again.";
        msg = msg.replace(/^Error:\s*/i, "");
        _appendMessage("agent", msg, false, null, "agent");
        console.error("Standalone chat error:", err);
      })
      .finally(function () {
        sending = false;
        abortController = null;
        _updateSendBtnState();
      });
  }

  // --- Read SSE stream -------------------------------------------------
  function _readSSE(response) {
    var reader = response.body.getReader();
    var decoder = new TextDecoder();
    var buffer = "";
    var segmentText = "";
    activeAgentText = "";
    activeAgentEl = null;
    activeAgentAuthor = "";
    activeAgentImages = [];

    var THINKING_HTML =
      '<span class="widget-thinking-inline">' +
      '<span class="widget-thinking-dot"></span>' +
      '<span class="widget-thinking-dot"></span>' +
      '<span class="widget-thinking-dot"></span>' +
      " Thinking\u2026</span>";

    function _ensureBubble() {
      if (!activeAgentEl) {
        var wrapper = document.createElement("div");
        wrapper.className = "widget-message-wrapper agent-message";
        
        var avatarColor = getAgentColor(activeAgentAuthor);
        var initials = getAgentInitials(activeAgentAuthor);
        
        var avatarEl = document.createElement("div");
        avatarEl.className = "widget-agent-avatar";
        avatarEl.style.backgroundColor = avatarColor;
        avatarEl.textContent = initials;
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

        // Skip transfer/routing actions
        var actions = evt.actions || {};
        if (actions.transfer_to_agent || actions.escalate) return;

        // --- Handle artifact delta (image artifacts) ---
        var artifactDelta = actions.artifactDelta || actions.artifact_delta;
        if (artifactDelta && typeof artifactDelta === "object") {
          var filenames = Object.keys(artifactDelta);
          for (var ai = 0; ai < filenames.length; ai++) {
            var artFilename = filenames[ai];
            var artVersion = artifactDelta[artFilename];
            // Check if this artifact is an image by extension
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
          // If this event has no content (action-only artifact event), skip rest
          if (!evt.content || !evt.content.parts || !evt.content.parts.length) return;
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

        var hasToolPart = false;
        for (var i = 0; i < parts.length; i++) {
          if (
            parts[i].functionCall ||
            parts[i].functionResponse ||
            parts[i].function_call ||
            parts[i].function_response
          ) {
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
        if (!activeAgentText && activeAgentEl) {
          activeAgentEl.innerHTML = _renderMarkdown("(no response)");
          _addMessageActions(activeAgentEl);
        } else if (!activeAgentText && !activeAgentEl) {
          _appendMessage("agent", "(no response)", false, null, "agent");
        } else if (activeAgentEl) {
          _addMessageActions(activeAgentEl);
        }
        _saveHistory();
        if (activeAgentEl && window.mateOnAgentDone) window.mateOnAgentDone(activeAgentEl);
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
  var MAX_FILE_SIZE = 10 * 1024 * 1024; // 10 MB (images and PDFs)
  var MAX_TEXT_FILE_SIZE = 5 * 1024 * 1024; // 5 MB (text files)
  var MAX_DIMENSION = 2048;
  var SUPPORTED_TEXT_EXTS = [
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
    var files = e.target.files;
    if (!files || !files.length) return;
    for (var i = 0; i < files.length; i++) {
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
    var reader = new FileReader();
    reader.onload = function (e) {
      var img = new Image();
      img.onload = function () {
        var w = img.width, h = img.height;
        if (w > MAX_DIMENSION || h > MAX_DIMENSION) {
          var scale = MAX_DIMENSION / Math.max(w, h);
          w = Math.round(w * scale);
          h = Math.round(h * scale);
        }
        var canvas = document.createElement("canvas");
        canvas.width = w;
        canvas.height = h;
        var ctx = canvas.getContext("2d");
        ctx.drawImage(img, 0, 0, w, h);
        var mimeType = file.type === "image/png" ? "image/png" : "image/jpeg";
        var quality = mimeType === "image/jpeg" ? 0.85 : undefined;
        var dataUrl = canvas.toDataURL(mimeType, quality);
        var base64 = dataUrl.split(",")[1];
        cb({ dataUrl: dataUrl, mimeType: mimeType, base64: base64, name: file.name });
      };
      img.src = e.target.result;
    };
    reader.readAsDataURL(file);
  }

  function _renderPreviews() {
    imagePreview.innerHTML = "";
    pendingFiles.forEach(function (f, idx) {
      var item = document.createElement("div");
      item.className = "widget-preview-item";
      
      var isImage = f.mimeType && f.mimeType.indexOf("image/") === 0;
      if (isImage) {
        var imgEl = document.createElement("img");
        imgEl.src = f.dataUrl;
        item.appendChild(imgEl);
      } else {
        var badgeEl = document.createElement("div");
        badgeEl.className = "widget-preview-file-badge";
        
        var ext = f.name.split(".").pop().toUpperCase();
        var icon = "📄";
        if (ext === "PDF") icon = "📕";
        else if (["JSON", "PY", "JS", "TS", "HTML", "CSS", "YAML", "YML"].indexOf(ext) !== -1) icon = "💻";
        
        badgeEl.innerHTML = '<span class="file-icon">' + icon + '</span><span class="file-name">' + f.name + '</span>';
        item.appendChild(badgeEl);
      }

      var removeBtn = document.createElement("button");
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
      avatarEl.style.backgroundColor = avatarColor;
      avatarEl.textContent = initials;
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

  // --- Generic rich cards (markers an agent prints: [[CARD]]{...} / [[APPOINTMENT]]{...}) ----
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
    return { type: "appointment", badge: "✓ Confirmed", title: d.summary || "Appointment",
      subtitle: _fmtRange(d.start, d.end), location: d.location,
      ics: { summary: d.summary, start: d.start, end: d.end, description: d.description, location: d.location }, actions: actions };
  }
  function _extractCards(text) {
    var cards = [], ranges = [], re = /\[\[(CARD|APPOINTMENT)\]\]\s*\{/g, m;
    while ((m = re.exec(text)) !== null) {
      var bs = text.indexOf("{", m.index), end = _balancedEnd(text, bs);
      if (end === -1) continue;
      var data; try { data = JSON.parse(text.slice(bs, end + 1)); } catch (e) { continue; }
      cards.push(m[1] === "APPOINTMENT" ? _appointmentToCard(data) : data);
      ranges.push([m.index, end + 1]); re.lastIndex = end + 1;
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
      if (act.kind === "link") return '<a href="' + a(act.value) + '" target="_blank" rel="noopener" style="font-size:13px;color:var(--w-primary,#2563eb);text-decoration:none;padding:6px 0">' + e(act.label) + "</a>";
      var attrs = 'data-kind="' + a(act.kind) + '" data-value="' + a(act.value || "") + '"';
      if (act.kind === "ics" && c.ics) attrs += ' data-summary="' + a(c.ics.summary) + '" data-start="' + a(c.ics.start) + '" data-end="' + a(c.ics.end || "") + '" data-desc="' + a(c.ics.description || "") + '" data-loc="' + a(c.ics.location || "") + '"';
      return '<button type="button" class="wz-card-act" ' + attrs + ' style="background:var(--w-primary,#2563eb);color:#fff;border:0;border-radius:8px;padding:6px 12px;font-size:13px;cursor:pointer">' + e(act.label) + "</button>";
    }).join("");
    return '<div style="border:1px solid #e2e8f0;border-radius:12px;padding:12px;margin-top:8px;background:#f8fafc">' + inner + (actions ? '<div style="display:flex;gap:10px;align-items:center;margin-top:8px;flex-wrap:wrap">' + actions + "</div>" : "") + "</div>";
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
    var link = document.createElement("a");
    link.href = url; link.download = (c.summary || "appointment").replace(/[^\w\-]+/g, "_").slice(0, 40) + ".ics";
    document.body.appendChild(link); link.click(); link.remove();
    setTimeout(function () { URL.revokeObjectURL(url); }, 1500);
  }
  function _sendText(text) {
    if (!inputEl || sending) return;
    inputEl.value = text;
    _send();
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
    localStorage.removeItem(STORAGE_PREFIX + "_sid");
    sessionStorage.removeItem(STORAGE_PREFIX + "_msgs");
    messagesEl.innerHTML = "";
    if (greetingEl) {
      greetingEl.style.display = "";
      messagesEl.appendChild(greetingEl);
    }
    messagesEl.appendChild(typingEl);
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
    try {
      sessionStorage.setItem(STORAGE_PREFIX + "_msgs", JSON.stringify(msgs));
    } catch (_) {}
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

  // --- Lightweight markdown renderer -----------------------------------
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
      .replace(/```(\w*)\n([\s\S]*?)```/g, function (_, lang, code) {
        var raw = code.trim();
        var escaped = _escapeHtml(raw);
        var l = lang || "code";
        // Store raw code URI-encoded so newlines survive the final \n→<br> pass
        var dataRaw = encodeURIComponent(raw);
        return '<div class="mate-code-block" data-lang="' + l + '" data-rawcode="' + dataRaw + '">'
          + '<div class="mate-code-header">'
          + '<span class="mate-code-lang">' + l + '</span>'
          + '<span class="mate-canvas-indicator">open in canvas</span>'
          + '<button class="mate-canvas-btn" onclick="if(window.mateOpenCanvas)window.mateOpenCanvas(this.closest(\'.mate-code-block\'))">Open in Canvas</button>'
          + '</div>'
          + '<pre><code>' + escaped + '</code></pre>'
          + '</div>';
      })
      .replace(/`([^`]+)`/g, "<code>$1</code>")
      .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
      .replace(/\*(.+?)\*/g, "<em>$1</em>")
      .replace(/!\[([^\]]*)\]\(([^)]+)\)/g, '<img src="$2" alt="$1" class="widget-msg-image widget-generated-image">')
      .replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>')
      .replace(/^### (.+)$/gm, "<strong>$1</strong>")
      .replace(/^## (.+)$/gm, "<strong>$1</strong>")
      .replace(/^# (.+)$/gm, "<strong>$1</strong>")
      .replace(/^[*-] (.+)$/gm, "<li>$1</li>")
      .replace(/^\d+\. (.+)$/gm, "<li>$1</li>")
      .replace(/\n{2,}/g, "</p><p>")
      .replace(/\n/g, "<br>");

    html = html.replace(/(<li>.*?<\/li>)+/gs, function (match) {
      return "<ul>" + match + "</ul>";
    });

    return "<p>" + html + "</p>";
  }

  function _escapeHtml(text) {
    var d = document.createElement("div");
    d.textContent = text;
    return d.innerHTML;
  }

  // --- SVG Icons & Actions ----------------------------------------------
  var COPY_SVG = '<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:middle"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg>';
  var CHECK_SVG = '<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:middle"><polyline points="20 6 9 17 4 12"></polyline></svg>';
  var DOWNLOAD_SVG = '<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:middle"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path><polyline points="7 10 12 15 17 10"></polyline><line x1="12" y1="15" x2="12" y2="3"></line></svg>';

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
