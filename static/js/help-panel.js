/**
 * Help button and panel on every dashboard page: a chat with the built-in
 * `mate_help` agent, which answers from MATE's documentation.
 *
 * Each question is sent with the page the person is on, so "what does this do?"
 * has a subject. The conversation is one ADK session per user, kept across page
 * loads (session id and messages in localStorage) until "New chat".
 *
 * Expects <div id="mateHelp" data-user="..."> from base.html. The button only
 * appears when the agent server lists the mate_help app.
 */
(function () {
  "use strict";

  var APP = "mate_help";
  var MAX_STORED = 40;
  var root = document.getElementById("mateHelp");
  if (!root || !root.dataset.user) return;
  var USER = root.dataset.user;
  var KEY = "mate_help_" + USER;

  var state = load();
  var sending = false;
  var button, panel, list, form, input, sendBtn;

  function load() {
    try {
      var saved = JSON.parse(localStorage.getItem(KEY) || "{}");
      return { sid: saved.sid || "", messages: Array.isArray(saved.messages) ? saved.messages : [] };
    } catch (_) {
      return { sid: "", messages: [] };
    }
  }

  function save() {
    state.messages = state.messages.slice(-MAX_STORED);
    try { localStorage.setItem(KEY, JSON.stringify(state)); } catch (_) {}
  }

  // --- Rendering -------------------------------------------------------
  // Agent text is untrusted (it quotes documentation and could quote anything),
  // so it is escaped first and only a little markdown becomes HTML.
  function esc(s) {
    return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  function safeHref(url) {
    var u = String(url).trim();
    // A leading "/" must not be followed by "/" or "\": browsers read "/\host" as "//host".
    return /^(\/(?![\/\\])|https?:\/\/|#)/i.test(u) ? u : "";
  }

  function inline(s) {
    return s
      .replace(/`([^`]+)`/g, "<code>$1</code>")
      .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
      .replace(/(^|[^*\w])\*([^*\n]+?)\*(?!\w)/g, "$1<em>$2</em>")
      .replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, function (match, label, href) {
        var safe = safeHref(href.replace(/&amp;/g, "&"));
        if (!safe) return match;
        var external = /^https?:/i.test(safe);
        return '<a href="' + esc(safe) + '"' + (external ? ' target="_blank" rel="noopener"' : "") + ">" + label + "</a>";
      });
  }

  function renderMarkdown(text) {
    var lines = esc(text || "").split("\n");
    var html = "", listTag = "", para = [];
    function flushPara() { if (para.length) { html += "<p>" + inline(para.join("<br>")) + "</p>"; para = []; } }
    function closeList() { if (listTag) { html += "</" + listTag + ">"; listTag = ""; } }
    lines.forEach(function (line) {
      var bullet = line.match(/^\s*[-*] (.+)$/), number = line.match(/^\s*\d+[.)] (.+)$/);
      var heading = line.match(/^#{1,4} (.+)$/);
      if (bullet || number) {
        flushPara();
        var tag = bullet ? "ul" : "ol";
        if (listTag !== tag) { closeList(); html += "<" + tag + ">"; listTag = tag; }
        html += "<li>" + inline((bullet || number)[1]) + "</li>";
      } else if (!line.trim()) {
        flushPara(); closeList();
      } else if (heading) {
        flushPara(); closeList();
        html += "<p><strong>" + inline(heading[1]) + "</strong></p>";
      } else {
        closeList(); para.push(line);
      }
    });
    flushPara(); closeList();
    return html;
  }

  function addMessage(role, text) {
    var el = document.createElement("div");
    el.className = "mate-help-msg " + role;
    setText(el, role, text);
    list.appendChild(el);
    list.scrollTop = list.scrollHeight;
    return el;
  }

  function setText(el, role, text) {
    if (role === "agent") el.innerHTML = renderMarkdown(text);
    else el.textContent = text;
  }

  function renderAll() {
    list.innerHTML = "";
    var intro = document.createElement("div");
    intro.className = "mate-help-intro";
    intro.textContent = "Ask how to do something in MATE. Answers come from the documentation.";
    list.appendChild(intro);
    state.messages.forEach(function (m) { addMessage(m.role, m.text); });
  }

  // --- Talking to the agent --------------------------------------------
  function createSession() {
    return fetch("/apps/" + APP + "/users/" + encodeURIComponent(USER) + "/sessions", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    }).then(function (res) {
      if (!res.ok) throw new Error("Could not start a conversation (" + res.status + ")");
      return res.json();
    }).then(function (data) {
      state.sid = data.id || "";
      save();
      return state.sid;
    });
  }

  function run(sid, text) {
    return fetch("/run_sse", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
      body: JSON.stringify({
        app_name: APP,
        user_id: USER,
        session_id: sid,
        new_message: { role: "user", parts: [{ text: text }] },
        streaming: true,
      }),
    });
  }

  // The page goes with the question, so the agent knows what "this" is.
  function withPage(text) {
    var title = (document.title || "").replace(/\s*[-|·]\s*MATE.*$/i, "").trim();
    return "[Dashboard page: " + (title ? title + " " : "") + "(" + location.pathname + ")]\n" + text;
  }

  // Collects the agent's final text from ADK's event stream: partial events
  // carry pieces, a final event carries the whole turn; tool calls are skipped.
  function readStream(res, onText) {
    var reader = res.body.getReader(), decoder = new TextDecoder();
    var buffer = "", streamed = "", finalText = "";
    function handle(line) {
      if (line.indexOf("data: ") !== 0) return;
      var evt;
      try { evt = JSON.parse(line.slice(6)); } catch (_) { return; }
      if (evt.error || evt.errorMessage) throw new Error(evt.errorMessage || evt.error);
      var parts = (evt.content && evt.content.parts) || [];
      var text = parts.filter(function (p) { return p.text && !p.thought; })
        .map(function (p) { return p.text; }).join("");
      if (!text) return;
      if (evt.partial) { streamed += text; onText(streamed); }
      else { finalText = text; streamed = ""; onText(finalText); }
    }
    function pump() {
      return reader.read().then(function (r) {
        if (r.done) return finalText || streamed;
        buffer += decoder.decode(r.value, { stream: true });
        var lines = buffer.split("\n");
        buffer = lines.pop();
        lines.forEach(handle);
        return pump();
      });
    }
    return pump();
  }

  function send(text) {
    if (sending || !text.trim()) return;
    sending = true;
    sendBtn.disabled = true;
    state.messages.push({ role: "user", text: text });
    save();
    addMessage("user", text);
    var bubble = addMessage("agent", "…");

    var message = withPage(text);
    (state.sid ? Promise.resolve(state.sid) : createSession())
      .then(function (sid) { return run(sid, message); })
      .then(function (res) {
        // A session the server no longer has (restart, cleared database): start a new one.
        if (res.status === 404) return createSession().then(function (sid) { return run(sid, message); });
        return res;
      })
      .then(function (res) {
        if (!res.ok) {
          return res.json().catch(function () { return {}; }).then(function (d) {
            throw new Error(d.detail || d.error || "The help agent is not available (" + res.status + ")");
          });
        }
        return readStream(res, function (t) { setText(bubble, "agent", t); list.scrollTop = list.scrollHeight; });
      })
      .then(function (answer) {
        answer = answer || "(no answer)";
        setText(bubble, "agent", answer);
        state.messages.push({ role: "agent", text: answer });
        save();
      })
      .catch(function (err) {
        bubble.className = "mate-help-msg error";
        bubble.textContent = err.message || "Something went wrong.";
      })
      .then(function () {
        sending = false;
        sendBtn.disabled = false;
        input.focus();
      });
  }

  function newChat() {
    state = { sid: "", messages: [] };
    save();
    renderAll();
    input.focus();
  }

  // --- UI --------------------------------------------------------------
  function build() {
    button = document.createElement("button");
    button.type = "button";
    button.className = "mate-help-button";
    button.textContent = "?";
    button.title = "Help";
    button.setAttribute("aria-label", "Open help");
    button.setAttribute("aria-expanded", "false");

    panel = document.createElement("section");
    panel.className = "mate-help-panel";
    panel.hidden = true;
    panel.setAttribute("aria-label", "MATE Help");
    panel.innerHTML =
      '<div class="mate-help-header"><span class="mate-help-title">MATE Help</span>' +
      '<button type="button" data-act="new">New chat</button>' +
      '<button type="button" data-act="close" aria-label="Close help">✕</button></div>' +
      '<div class="mate-help-messages" aria-live="polite"></div>' +
      '<form class="mate-help-form"><textarea rows="1" placeholder="Ask a question…" aria-label="Your question"></textarea>' +
      '<button type="submit">Send</button></form>';

    list = panel.querySelector(".mate-help-messages");
    form = panel.querySelector("form");
    input = form.querySelector("textarea");
    sendBtn = form.querySelector("button");

    button.addEventListener("click", function () { toggle(panel.hidden); });
    panel.querySelector('[data-act="close"]').addEventListener("click", function () { toggle(false); });
    panel.querySelector('[data-act="new"]').addEventListener("click", newChat);
    form.addEventListener("submit", function (e) {
      e.preventDefault();
      var text = input.value;
      input.value = "";
      send(text);
    });
    input.addEventListener("keydown", function (e) {
      if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); form.requestSubmit(); }
      if (e.key === "Escape") toggle(false);
    });

    root.appendChild(panel);
    root.appendChild(button);
    renderAll();
  }

  function toggle(open) {
    panel.hidden = !open;
    button.setAttribute("aria-expanded", String(open));
    if (open) { list.scrollTop = list.scrollHeight; input.focus(); } else { button.focus(); }
  }

  // Show the button only when the agent server has the help agent.
  fetch("/list-apps", { credentials: "same-origin" })
    .then(function (res) { return res.ok ? res.json() : []; })
    .then(function (apps) { if (Array.isArray(apps) && apps.indexOf(APP) !== -1) build(); })
    .catch(function () {});
})();
