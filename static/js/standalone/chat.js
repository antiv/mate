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
        msgs.forEach(function (m) { _appendMessage(m.role, m.text, true, null, m.author); });
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
    var files = pendingFiles.slice();
    var canvasCtx = window.mateGetCanvasCode && window.mateGetCanvasCode();
    if ((!text && !files.length && !canvasCtx) || sending) return;

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
    sendBtn.disabled = true;
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
        var msg = err.message || "Sorry, something went wrong. Please try again.";
        msg = msg.replace(/^Error:\s*/i, "");
        _appendMessage("agent", msg, false, null, "agent");
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

    var THINKING_HTML =
      '<span class="widget-thinking-inline">' +
      '<span class="widget-thinking-dot"></span>' +
      '<span class="widget-thinking-dot"></span>' +
      '<span class="widget-thinking-dot"></span>' +
      " Thinking\u2026</span>";

    function _ensureBubble() {
      if (!agentEl) {
        var wrapper = document.createElement("div");
        wrapper.className = "widget-message-wrapper agent-message";
        
        var avatarColor = getAgentColor(currentAuthor);
        var initials = getAgentInitials(currentAuthor);
        
        var avatarEl = document.createElement("div");
        avatarEl.className = "widget-agent-avatar";
        avatarEl.style.backgroundColor = avatarColor;
        avatarEl.textContent = initials;
        avatarEl.title = currentAuthor;
        
        agentEl = document.createElement("div");
        agentEl.className = "widget-message agent";
        agentEl.setAttribute("data-author", currentAuthor);
        agentEl.innerHTML = THINKING_HTML;
        
        wrapper.appendChild(avatarEl);
        wrapper.appendChild(agentEl);
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
              // Build artifact URL
              var artUrl = BASE + "/apps/" + AGENT_NAME + "/users/" + userId +
                "/sessions/" + sessionId + "/artifacts/" + artFilename +
                "/versions/" + artVersion;
              // Clear thinking dots if present
              if (agentEl.querySelector(".widget-thinking-inline")) {
                agentEl.innerHTML = "";
              }
              var imgEl = document.createElement("img");
              imgEl.className = "widget-msg-image widget-generated-image";
              imgEl.alt = artFilename;
              imgEl.title = artFilename;
              imgEl.style.opacity = "0.5";
              agentEl.appendChild(imgEl);
              _scrollToBottom();
              
              (function(imgElem, url) {
                fetch(url)
                  .then(function(r) { return r.json(); })
                  .then(function(data) {
                    var inlineData = data.inlineData || data.inline_data;
                    if (inlineData && inlineData.data) {
                      var mimeType = inlineData.mimeType || inlineData.mime_type || 'image/png';
                      var cleanBase64 = inlineData.data.replace(/\s+/g, '').replace(/-/g, '+').replace(/_/g, '/');
                      imgElem.src = 'data:' + mimeType + ';base64,' + cleanBase64;
                      imgElem.style.opacity = "1";
                    } else {
                      imgElem.style.display = "none";
                    }
                    _scrollToBottom();
                  }).catch(function() {
                    imgElem.style.display = "none";
                  });
              })(imgEl, artUrl);
            }
          }
          // If this event has no content (action-only artifact event), skip rest
          if (!evt.content || !evt.content.parts || !evt.content.parts.length) return;
        }

        var author = evt.author || "";
        if (author && author !== currentAuthor) {
          currentAuthor = author;
          agentText = "";
          // When author changes, start a new bubble instead of overwriting/resetting the existing one.
          agentEl = null;
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
          return;
        }

        for (var j = 0; j < parts.length; j++) {
          // Handle inline image data (generated artifacts)
          var inlineData = parts[j].inline_data || parts[j].inlineData;
          if (inlineData && inlineData.mime_type && inlineData.mime_type.indexOf('image/') === 0) {
            _showTyping(false);
            _ensureBubble();
            var cleanBase64 = inlineData.data.replace(/\s+/g, '').replace(/-/g, '+').replace(/_/g, '/');
            var imgSrc = 'data:' + inlineData.mime_type + ';base64,' + cleanBase64;
            var imgEl2 = document.createElement('img');
            imgEl2.src = imgSrc;
            imgEl2.className = 'widget-msg-image widget-generated-image';
            imgEl2.alt = 'Generated image';
            // If bubble had thinking dots, clear them first
            if (agentEl.querySelector('.widget-thinking-inline')) {
              agentEl.innerHTML = '';
            }
            agentEl.appendChild(imgEl2);
            _scrollToBottom();
            continue;
          }

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
          _appendMessage("agent", "(no response)", false, null, "agent");
        }
        _saveHistory();
        if (agentEl && window.mateOnAgentDone) window.mateOnAgentDone(agentEl);
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
      el.innerHTML = _renderMarkdown(text);
      
      wrapper.appendChild(avatarEl);
      wrapper.appendChild(el);
      messagesEl.appendChild(wrapper);
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
    el.innerHTML = _renderMarkdown(text);
    _scrollToBottom();
    _loadLazyArtifacts();
  }

  function _loadLazyArtifacts() {
    document.querySelectorAll("img.art-lazy-load").forEach(function(imgEl) {
      var url = imgEl.getAttribute("data-art-url");
      if (url && !imgEl.getAttribute("data-loading")) {
        imgEl.setAttribute("data-loading", "true");
        imgEl.style.opacity = "0.5";
        fetch(url)
          .then(function(r) { return r.json(); })
          .then(function(data) {
             var inlineData = data.inlineData || data.inline_data;
             if (inlineData && inlineData.data) {
                 var mimeType = inlineData.mimeType || inlineData.mime_type || 'image/png';
                 var cleanBase64 = inlineData.data.replace(/\s+/g, '').replace(/-/g, '+').replace(/_/g, '/');
                 imgEl.src = 'data:' + mimeType + ';base64,' + cleanBase64;
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
      msgs.push({ role: role, text: role === "user" ? el.textContent : el.innerHTML, author: author });
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
    var parts = clean.split(/[_-]/);
    if (parts.length > 1) {
      return (parts[0].charAt(0) + parts[1].charAt(0)).toUpperCase();
    }
    return clean.charAt(0).toUpperCase();
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

  // --- Boot ------------------------------------------------------------
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
