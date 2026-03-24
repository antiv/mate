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
  let pendingImages = []; // [{dataUrl, mimeType, base64}]

  // --- DOM refs --------------------------------------------------------
  const messagesEl = document.getElementById("widgetMessages");
  const inputEl = document.getElementById("widgetInput");
  const sendBtn = document.getElementById("widgetSendBtn");
  const typingEl = document.getElementById("widgetTyping");
  const newChatBtn = document.getElementById("widgetNewChat");
  const greetingEl = document.getElementById("widgetGreeting");
  const headerTitle = document.getElementById("widgetHeaderTitle");
  const attachBtn = document.getElementById("widgetAttachBtn");
  const fileInput = document.getElementById("widgetFileInput");
  const imagePreview = document.getElementById("widgetImagePreview");

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

    // Listen for theme changes from parent
    window.addEventListener("message", function (e) {
      if (e.data && e.data.type === "mate-theme") {
        document.documentElement.setAttribute("data-theme", e.data.theme === "dark" ? "dark" : "");
      }
    });
  }

  // --- Send message ----------------------------------------------------
  function _send() {
    const text = inputEl.value.trim();
    const images = pendingImages.slice();
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
    const parts = [];
    images.forEach(function (img) {
      parts.push({ inline_data: { mime_type: img.mimeType, data: img.base64 } });
    });
    if (text) parts.push({ text: text });

    const payload = { message: text, parts: parts, user_id: userId, session_id: sessionId, new_session: forceNewSession };
    forceNewSession = false;

    fetch(`${BASE}/widget/api/chat`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Widget-Key": API_KEY,
      },
      body: JSON.stringify(payload),
    })
      .then(function (res) {
        if (!res.ok) throw new Error("Chat request failed: " + res.status);
        return _readSSE(res);
      })
      .catch(function (err) {
        _showTyping(false);
        _appendMessage("agent", "Sorry, something went wrong. Please try again.");
        console.error("Widget chat error:", err);
      })
      .finally(function () {
        sending = false;
        sendBtn.disabled = false;
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
    var agentText = "";
    var agentEl = null;
    var currentAuthor = "";
    var seenToolUse = false;

    var THINKING_HTML = '<span class="widget-thinking-inline">' +
      '<span class="widget-thinking-dot"></span>' +
      '<span class="widget-thinking-dot"></span>' +
      '<span class="widget-thinking-dot"></span>' +
      ' Thinking\u2026</span>';

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

        if (evt.session_id) {
          sessionId = evt.session_id;
          localStorage.setItem(STORAGE_PREFIX + "_sid", sessionId);
        }

        var actions = evt.actions || {};
        if (actions.transfer_to_agent || actions.escalate) return;

        var author = evt.author || "";
        if (author && author !== currentAuthor) {
          currentAuthor = author;
          agentText = "";
          seenToolUse = false;
          // Keep bubble but reset to thinking state
          if (agentEl) agentEl.innerHTML = THINKING_HTML;
        }

        var parts = (evt.content && evt.content.parts) || [];
        if (!parts.length) return;

        var hasToolPart = false;
        for (var i = 0; i < parts.length; i++) {
          if (parts[i].functionCall || parts[i].functionResponse ||
              parts[i].function_call || parts[i].function_response) {
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
  const MAX_IMAGE_SIZE = 10 * 1024 * 1024; // 10 MB
  const MAX_DIMENSION = 2048;

  function _handleFileSelect(e) {
    const files = e.target.files;
    if (!files || !files.length) return;
    for (let i = 0; i < files.length; i++) {
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
        cb({ dataUrl: dataUrl, mimeType: mimeType, base64: base64 });
      };
      img.src = e.target.result;
    };
    reader.readAsDataURL(file);
  }

  function _renderPreviews() {
    imagePreview.innerHTML = "";
    pendingImages.forEach(function (img, idx) {
      const item = document.createElement("div");
      item.className = "widget-preview-item";
      const imgEl = document.createElement("img");
      imgEl.src = img.dataUrl;
      item.appendChild(imgEl);
      const removeBtn = document.createElement("button");
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
    forceNewSession = true;
    localStorage.removeItem(`${STORAGE_PREFIX}_sid`);
    sessionStorage.removeItem(`${STORAGE_PREFIX}_msgs`);
    messagesEl.innerHTML = "";
    if (greetingEl) { greetingEl.style.display = ""; messagesEl.appendChild(greetingEl); }
    messagesEl.appendChild(typingEl);
  }

  function _saveHistory() {
    var msgs = [];
    messagesEl.querySelectorAll(".widget-message").forEach(function (el) {
      var role = el.classList.contains("user") ? "user" : "agent";
      msgs.push({ role: role, text: role === "user" ? el.textContent : el.innerHTML });
    });
    try { sessionStorage.setItem(`${STORAGE_PREFIX}_msgs`, JSON.stringify(msgs)); } catch (_) {}
  }

  function _generateId() {
    return "u_" + Math.random().toString(36).slice(2, 10) + Date.now().toString(36);
  }

  // --- Lightweight markdown renderer -----------------------------------
  function _renderMarkdown(text) {
    if (!text) return "";
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

  // --- Boot ------------------------------------------------------------
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
