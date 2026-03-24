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

  // --- State -----------------------------------------------------------
  var sessionId = localStorage.getItem(STORAGE_PREFIX + "_sid") || "";
  var userId = localStorage.getItem(STORAGE_PREFIX + "_uid") || _generateId();
  localStorage.setItem(STORAGE_PREFIX + "_uid", userId);

  var sending = false;
  var pendingImages = []; // [{dataUrl, mimeType, base64}]

  // --- DOM refs --------------------------------------------------------
  var messagesEl = document.getElementById("widgetMessages");
  var inputEl = document.getElementById("widgetInput");
  var sendBtn = document.getElementById("widgetSendBtn");
  var typingEl = document.getElementById("widgetTyping");
  var newChatBtn = document.getElementById("widgetNewChat");
  var greetingEl = document.getElementById("widgetGreeting");
  var attachBtn = document.getElementById("widgetAttachBtn");
  var fileInput = document.getElementById("widgetFileInput");
  var imagePreview = document.getElementById("widgetImagePreview");
  var headerTitle = document.getElementById("widgetHeaderTitle");

  // --- Init ------------------------------------------------------------
  function init() {
    // Theme — follow OS preference
    if (window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches) {
      document.documentElement.setAttribute("data-theme", "dark");
    }

    // Restore chat history from sessionStorage
    var saved = sessionStorage.getItem(STORAGE_PREFIX + "_msgs");
    if (saved) {
      try {
        var msgs = JSON.parse(saved);
        msgs.forEach(function (m) { _appendMessage(m.role, m.text, true); });
        if (greetingEl) greetingEl.style.display = "none";
      } catch (_) {}
    }

    sendBtn.addEventListener("click", _send);
    inputEl.addEventListener("keydown", function (e) {
      if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); _send(); }
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
    var text = inputEl.value.trim();
    var images = pendingImages.slice();
    if ((!text && !images.length) || sending) return;

    if (greetingEl) greetingEl.style.display = "none";
    _appendMessage("user", text, false, images);
    inputEl.value = "";
    inputEl.style.height = "auto";
    _clearPendingImages();
    sending = true;
    sendBtn.disabled = true;
    _showTyping(true);

    // Build parts array
    var parts = [];
    images.forEach(function (img) {
      parts.push({ inline_data: { mime_type: img.mimeType, data: img.base64 } });
    });
    if (text) parts.push({ text: text });

    var doSend = sessionId
      ? Promise.resolve(sessionId)
      : _createSession();

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
            });
          });
        }
        if (!res.ok) throw new Error("Chat request failed: " + res.status);
        return res;
      })
      .then(function (res) {
        return _readSSE(res);
      })
      .catch(function (err) {
        _showTyping(false);
        _appendMessage("agent", "Sorry, something went wrong. Please try again.");
        console.error("Standalone chat error:", err);
      })
      .finally(function () {
        sending = false;
        sendBtn.disabled = false;
      });
  }

  // --- Read SSE stream -------------------------------------------------
  function _readSSE(response) {
    var reader = response.body.getReader();
    var decoder = new TextDecoder();
    var buffer = "";
    var agentText = "";
    var agentEl = null;
    var currentAuthor = "";
    var seenToolUse = false;

    var THINKING_HTML =
      '<span class="widget-thinking-inline">' +
      '<span class="widget-thinking-dot"></span>' +
      '<span class="widget-thinking-dot"></span>' +
      '<span class="widget-thinking-dot"></span>' +
      " Thinking\u2026</span>";

    function _ensureBubble() {
      if (!agentEl) {
        agentEl = document.createElement("div");
        agentEl.className = "widget-message agent";
        agentEl.innerHTML = THINKING_HTML;
        messagesEl.appendChild(agentEl);
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

        var author = evt.author || "";
        if (author && author !== currentAuthor) {
          currentAuthor = author;
          agentText = "";
          seenToolUse = false;
          if (agentEl) agentEl.innerHTML = THINKING_HTML;
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
          seenToolUse = true;
          agentText = "";
          _showTyping(false);
          _ensureBubble();
          agentEl.innerHTML = THINKING_HTML;
          _scrollToBottom();
          return;
        }

        for (var j = 0; j < parts.length; j++) {
          var t = parts[j].text;
          if (!t) continue;

          _showTyping(false);
          _ensureBubble();

          // De-duplicate partial vs complete events
          if (agentText && t.length >= agentText.length && t.indexOf(agentText) === 0) {
            agentText = t;
          } else if (agentText && agentText.indexOf(t) === 0 && t.length <= agentText.length) {
            continue;
          } else {
            agentText += t;
          }

          _updateMessage(agentEl, agentText);
        }
      } catch (_) {}
    }

    return reader.read().then(function pump(result) {
      if (result.done) {
        _showTyping(false);
        if (!agentText && agentEl) {
          agentEl.innerHTML = _renderMarkdown("(no response)");
        } else if (!agentText && !agentEl) {
          _appendMessage("agent", "(no response)");
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

  // --- Image upload helpers --------------------------------------------
  var MAX_IMAGE_SIZE = 10 * 1024 * 1024; // 10 MB
  var MAX_DIMENSION = 2048;

  function _handleFileSelect(e) {
    var files = e.target.files;
    if (!files || !files.length) return;
    for (var i = 0; i < files.length; i++) {
      (function (file) {
        if (!file.type.startsWith("image/")) return;
        if (file.size > MAX_IMAGE_SIZE) {
          alert("Image too large (max 10 MB): " + file.name);
          return;
        }
        _readAndResizeImage(file, function (result) {
          pendingImages.push(result);
          _renderPreviews();
        });
      })(files[i]);
    }
    fileInput.value = "";
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
        cb({ dataUrl: dataUrl, mimeType: mimeType, base64: base64 });
      };
      img.src = e.target.result;
    };
    reader.readAsDataURL(file);
  }

  function _renderPreviews() {
    imagePreview.innerHTML = "";
    pendingImages.forEach(function (img, idx) {
      var item = document.createElement("div");
      item.className = "widget-preview-item";
      var imgEl = document.createElement("img");
      imgEl.src = img.dataUrl;
      item.appendChild(imgEl);
      var removeBtn = document.createElement("button");
      removeBtn.className = "widget-preview-remove";
      removeBtn.textContent = "\u00D7";
      removeBtn.onclick = function () {
        pendingImages.splice(idx, 1);
        _renderPreviews();
      };
      item.appendChild(removeBtn);
      imagePreview.appendChild(item);
    });
    imagePreview.classList.toggle("active", pendingImages.length > 0);
  }

  function _clearPendingImages() {
    pendingImages = [];
    imagePreview.innerHTML = "";
    imagePreview.classList.remove("active");
  }

  // --- DOM helpers -----------------------------------------------------
  function _appendMessage(role, text, skipSave, images) {
    var el = document.createElement("div");
    el.className = "widget-message " + role;
    if (role === "agent") {
      el.innerHTML = _renderMarkdown(text);
    } else {
      // Show attached images as thumbnails in user bubble
      if (images && images.length) {
        images.forEach(function (img) {
          var imgEl = document.createElement("img");
          imgEl.src = img.dataUrl;
          imgEl.className = "widget-msg-image";
          el.appendChild(imgEl);
        });
      }
      if (text) {
        var textNode = document.createElement("span");
        textNode.textContent = text;
        el.appendChild(textNode);
      }
    }
    messagesEl.appendChild(el);
    _scrollToBottom();
    if (!skipSave) _saveHistory();
    return el;
  }

  function _updateMessage(el, text) {
    el.innerHTML = _renderMarkdown(text);
    _scrollToBottom();
  }

  function _showTyping(show) {
    if (typingEl) typingEl.classList.toggle("active", show);
    if (show) _scrollToBottom();
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
      msgs.push({ role: role, text: role === "user" ? el.textContent : el.innerHTML });
    });
    try {
      sessionStorage.setItem(STORAGE_PREFIX + "_msgs", JSON.stringify(msgs));
    } catch (_) {}
  }

  function _generateId() {
    return "u_" + Math.random().toString(36).slice(2, 10) + Date.now().toString(36);
  }

  // --- Lightweight markdown renderer -----------------------------------
  function _renderMarkdown(text) {
    if (!text) return "";
    var html = text
      .replace(/```(\w*)\n([\s\S]*?)```/g, function (_, lang, code) {
        return "<pre><code>" + _escapeHtml(code.trim()) + "</code></pre>";
      })
      .replace(/`([^`]+)`/g, "<code>$1</code>")
      .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
      .replace(/\*(.+?)\*/g, "<em>$1</em>")
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

  // --- Boot ------------------------------------------------------------
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
