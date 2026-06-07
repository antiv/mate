/**
 * MATE Widget Admin Panel — client-side logic.
 *
 * Globals injected by template:
 *   WIDGET_API_KEY, WIDGET_AGENT_NAME, WIDGET_PROJECT_ID
 */
(function () {
  "use strict";

  var API_KEY = window.WIDGET_API_KEY || "";
  var BASE = window.location.origin;

  // --- Helpers ---------------------------------------------------------
  function api(method, path, body) {
    var opts = {
      method: method,
      headers: { "X-Widget-Key": API_KEY, "Content-Type": "application/json" },
    };
    if (body) opts.body = JSON.stringify(body);
    return fetch(BASE + "/widget/api" + path, opts).then(function (r) { return r.json(); });
  }

  function toast(msg, type) {
    var el = document.createElement("div");
    el.className = "admin-toast " + (type || "success");
    el.textContent = msg;
    document.body.appendChild(el);
    setTimeout(function () { el.remove(); }, 3000);
  }

  function escapeHtml(t) {
    var d = document.createElement("div");
    d.textContent = t;
    return d.innerHTML;
  }

  // --- Tabs ------------------------------------------------------------
  var tabs = document.querySelectorAll(".admin-tab");
  var panels = document.querySelectorAll(".admin-panel");

  tabs.forEach(function (tab) {
    tab.addEventListener("click", function () {
      tabs.forEach(function (t) { t.classList.remove("active"); });
      panels.forEach(function (p) { p.classList.remove("active"); });
      tab.classList.add("active");
      document.getElementById("panel-" + tab.dataset.tab).classList.add("active");
    });
  });

  // --- Appearance refs -------------------------------------------------
  var appearanceForm = document.getElementById("appearanceForm");
  var cfgTitle = document.getElementById("cfgTitle");
  var cfgGreeting = document.getElementById("cfgGreeting");
  var cfgTheme = document.getElementById("cfgTheme");
  var cfgButtonColor = document.getElementById("cfgButtonColor");
  var cfgIconUrl = document.getElementById("cfgIconUrl");
  var cfgShowAttachments = document.getElementById("cfgShowAttachments");
  var cfgContextInjection = document.getElementById("cfgContextInjection");

  // --- Agent Settings --------------------------------------------------
  var agentForm = document.getElementById("agentForm");
  var agentInstruction = document.getElementById("agentInstruction");
  var agentModel = document.getElementById("agentModel");
  var agentDescription = document.getElementById("agentDescription");

  function loadAgent() {
    api("GET", "/agent").then(function (res) {
      if (res.success && res.agent) {
        agentInstruction.value = res.agent.instruction || "";
        agentModel.value = res.agent.model_name || "";
        agentDescription.value = res.agent.description || "";
      }
    });
  }

  agentForm.addEventListener("submit", function (e) {
    e.preventDefault();
    api("PUT", "/agent", {
      instruction: agentInstruction.value,
      model_name: agentModel.value,
      description: agentDescription.value,
    }).then(function (res) {
      toast(res.success ? "Agent updated" : (res.detail || "Failed"), res.success ? "success" : "error");
    });
  });

  // --- Memory Blocks & Modal -------------------------------------------
  var blocksList = document.getElementById("blocksList");
  var blockForm = document.getElementById("blockForm");
  var blockLabel = document.getElementById("blockLabel");
  var blockValue = document.getElementById("blockValue");
  var blockDesc = document.getElementById("blockDescription");
  var blockFormTitle = document.getElementById("blockFormTitle");
  var editingBlockId = null;

  function showBlockModal(isEdit) {
    var modal = document.getElementById("blockModal");
    if (!modal) return;
    modal.classList.remove("hidden");
    if (!isEdit) {
      editingBlockId = null;
      blockLabel.value = "";
      blockValue.value = "";
      blockDesc.value = "";
      blockFormTitle.innerHTML = '<i class="fas fa-brain text-blue-500"></i> New Memory Block';
    }
  }

  function hideBlockModal() {
    var modal = document.getElementById("blockModal");
    if (modal) modal.classList.add("hidden");
  }

  function loadBlocks() {
    api("GET", "/memory-blocks").then(function (res) {
      if (!res.success) {
        blocksList.innerHTML = '<div class="text-center py-8 text-sm text-gray-400 dark:text-gray-500 flex flex-col items-center justify-center gap-2"><i class="fas fa-exclamation-triangle text-2xl text-yellow-500/80"></i><span>No memory blocks found or tool not configured.</span></div>';
        return;
      }
      var blocks = res.blocks || [];
      if (blocks.length === 0) {
        blocksList.innerHTML = '<div class="text-center py-8 text-sm text-gray-400 dark:text-gray-500 flex flex-col items-center justify-center gap-2"><i class="fas fa-brain text-2xl text-gray-300 dark:text-gray-650"></i><span>No memory blocks yet. Click "Add Block" to create one.</span></div>';
        return;
      }
      blocksList.innerHTML = blocks.map(function (b) {
        var valueText = (b.value || "");
        var truncated = valueText.length > 100 ? valueText.substring(0, 100) + "..." : valueText;
        return '<div class="flex items-center justify-between p-4 border border-gray-200 dark:border-gray-700 rounded-xl bg-gray-50/50 dark:bg-gray-800/40 hover:bg-gray-100/30 dark:hover:bg-gray-800/80 transition-all">'
          + '<div class="min-w-0 pr-4">'
          + '<div class="text-sm font-semibold text-gray-900 dark:text-white flex items-center gap-1.5">'
          + '<i class="fas fa-brain text-xs text-blue-500/80"></i> ' + escapeHtml(b.label)
          + '</div>'
          + '<div class="text-xs text-gray-500 dark:text-gray-400 mt-1 break-all font-mono font-normal">' + escapeHtml(truncated) + '</div>'
          + '</div>'
          + '<div class="flex items-center gap-2 flex-shrink-0">'
          + '<button class="px-2.5 py-1.5 text-xs font-medium border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-750 rounded-lg transition-colors" onclick="widgetAdmin.editBlock(\'' + b.block_id + '\')"><i class="fas fa-edit"></i> Edit</button>'
          + '<button class="px-2.5 py-1.5 text-xs font-medium border border-red-200 dark:border-red-800/40 text-red-600 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-950/20 rounded-lg transition-colors" onclick="widgetAdmin.deleteBlock(\'' + b.block_id + '\')"><i class="fas fa-trash"></i> Delete</button>'
          + '</div></div>';
      }).join("");
    });
  }

  function editBlock(blockId) {
    api("GET", "/memory-blocks").then(function (res) {
      var block = (res.blocks || []).find(function (b) { return b.block_id === blockId; });
      if (!block) return;
      editingBlockId = blockId;
      blockLabel.value = block.label || "";
      blockValue.value = block.value || "";
      blockDesc.value = block.description || "";
      blockFormTitle.innerHTML = '<i class="fas fa-brain text-blue-500"></i> Edit Memory Block';
      showBlockModal(true);
    });
  }

  function deleteBlock(blockId) {
    if (!confirm("Delete this memory block?")) return;
    api("DELETE", "/memory-blocks/" + blockId).then(function (res) {
      toast(res.success ? "Deleted" : "Failed", res.success ? "success" : "error");
      loadBlocks();
    });
  }

  blockForm.addEventListener("submit", function (e) {
    e.preventDefault();
    var data = { label: blockLabel.value, value: blockValue.value, description: blockDesc.value };
    var method = editingBlockId ? "PUT" : "POST";
    var path = editingBlockId ? "/memory-blocks/" + editingBlockId : "/memory-blocks";
    api(method, path, data).then(function (res) {
      toast(res.success ? "Saved" : (res.error || "Failed"), res.success ? "success" : "error");
      if (res.success) {
        blockLabel.value = "";
        blockValue.value = "";
        blockDesc.value = "";
        editingBlockId = null;
        loadBlocks();
        hideBlockModal();
      }
    });
  });

  document.getElementById("blockFormCancel").addEventListener("click", function () {
    editingBlockId = null;
    blockLabel.value = "";
    blockValue.value = "";
    blockDesc.value = "";
    hideBlockModal();
  });

  // --- File Search -----------------------------------------------------
  var fileStoresList = document.getElementById("fileStoresList");
  var uploadStoreSelect = document.getElementById("uploadStore");
  var uploadFileInput = document.getElementById("uploadFile");
  var uploadArea = document.getElementById("uploadArea");

  function loadFiles() {
    api("GET", "/files").then(function (res) {
      if (!res.success || !res.stores || res.stores.length === 0) {
        fileStoresList.innerHTML = '<div class="text-center py-8 text-sm text-gray-400 dark:text-gray-500 flex flex-col items-center justify-center gap-2 bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-xl p-6 shadow-sm"><i class="fas fa-folder-open text-2xl text-gray-300 dark:text-gray-600"></i><span>No file search stores assigned to this agent.</span></div>';
        uploadStoreSelect.innerHTML = '<option value="">No stores available</option>';
        return;
      }
      uploadStoreSelect.innerHTML = res.stores.map(function (s) {
        var name = s.display_name || s.store_name;
        return '<option value="' + escapeHtml(s.store_name) + '">' + escapeHtml(name) + '</option>';
      }).join("");

      fileStoresList.innerHTML = res.stores.map(function (s) {
        var files = s.files || [];
        var filesHtml = files.length === 0
          ? '<div class="text-center py-6 text-sm text-gray-400 dark:text-gray-500 flex flex-col items-center justify-center gap-1.5"><i class="fas fa-folder-open text-xl text-gray-300 dark:text-gray-600"></i><span>No files in this store.</span></div>'
          : files.map(function (f) {
              return '<div class="flex items-center justify-between p-3 border border-gray-200 dark:border-gray-700 rounded-lg bg-gray-50/30 dark:bg-gray-800/30">'
                + '<div class="min-w-0 pr-4">'
                + '<div class="text-sm font-medium text-gray-900 dark:text-white flex items-center gap-1.5">'
                + '<i class="fas fa-file-alt text-gray-400 dark:text-gray-500"></i> ' + escapeHtml(f.display_name || f.document_name)
                + '</div>'
                + '<div class="text-xs text-gray-500 dark:text-gray-400 mt-0.5">' + (f.mime_type || "") + ' — ' + _fmtSize(f.file_size) + '</div>'
                + '</div>'
                + '<button class="px-2.5 py-1.5 text-xs font-medium border border-red-200 dark:border-red-800/40 text-red-600 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-950/20 rounded-lg transition-colors flex-shrink-0" onclick="widgetAdmin.deleteFile(' + f.id + ')"><i class="fas fa-trash"></i> Delete</button>'
                + '</div>';
            }).join("");

        return '<div class="bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-xl p-6 shadow-sm">'
          + '<h3 class="text-base font-bold text-gray-900 dark:text-white mb-4 flex items-center gap-2">'
          + '<i class="fas fa-folder text-blue-500"></i> ' + escapeHtml(s.display_name || s.store_name)
          + '</h3>'
          + '<div class="space-y-2">' + filesHtml + '</div>'
          + '</div>';
      }).join("");
    });
  }

  function deleteFile(fileId) {
    if (!confirm("Delete this file?")) return;
    api("DELETE", "/files/" + fileId).then(function (res) {
      toast(res.success ? "Deleted" : "Failed", res.success ? "success" : "error");
      loadFiles();
    });
  }

  // Upload drag/drop + click
  if (uploadArea) {
    uploadArea.addEventListener("click", function () { uploadFileInput.click(); });
    uploadArea.addEventListener("dragover", function (e) { e.preventDefault(); uploadArea.style.borderColor = "#2563eb"; });
    uploadArea.addEventListener("dragleave", function () { uploadArea.style.borderColor = ""; });
    uploadArea.addEventListener("drop", function (e) {
      e.preventDefault();
      uploadArea.style.borderColor = "";
      if (e.dataTransfer.files.length) {
        uploadFileInput.files = e.dataTransfer.files;
        _doUpload(e.dataTransfer.files[0]);
      }
    });
    uploadFileInput.addEventListener("change", function () {
      if (uploadFileInput.files.length) _doUpload(uploadFileInput.files[0]);
    });
  }

  function _doUpload(file) {
    var store = uploadStoreSelect.value;
    if (!store) { toast("Select a store first", "error"); return; }
    var fd = new FormData();
    fd.append("file", file);
    fd.append("store_name", store);
    fd.append("display_name", file.name);

    fetch(BASE + "/widget/api/files/upload", {
      method: "POST",
      headers: { "X-Widget-Key": API_KEY },
      body: fd,
    })
    .then(function (r) { return r.json(); })
    .then(function (res) {
      toast(res.success ? "Uploaded" : (res.error || "Failed"), res.success ? "success" : "error");
      if (res.success) loadFiles();
    });
  }

  function _fmtSize(bytes) {
    if (!bytes) return "";
    if (bytes < 1024) return bytes + " B";
    if (bytes < 1048576) return (bytes / 1024).toFixed(1) + " KB";
    return (bytes / 1048576).toFixed(1) + " MB";
  }

  // --- Appearance ------------------------------------------------------
  function loadAppearance() {
    api("GET", "/widget-config").then(function (res) {
      if (!res.success) return;
      var cfg = res.widget_config || {};
      cfgTitle.value = cfg.title || "";
      cfgGreeting.value = cfg.greeting || "";
      cfgTheme.value = cfg.theme || "auto";
      cfgButtonColor.value = cfg.button_color || "#2563eb";
      cfgIconUrl.value = cfg.icon_url || "";
      cfgShowAttachments.checked = cfg.show_attachments !== false; // default true
      cfgContextInjection.checked = !!cfg.context_injection;
    });
  }

  // Real-time color update: floating button + chat iframe
  cfgButtonColor.addEventListener("input", function () {
    var color = cfgButtonColor.value;
    var btn = document.getElementById("mate-widget-btn");
    if (btn) btn.style.background = color;
    var iframe = document.getElementById("mate-widget-iframe");
    if (iframe && iframe.contentWindow) {
      try { iframe.contentWindow.postMessage({ type: "mate-color", button_color: color }, "*"); } catch (_) {}
    }
  });

  appearanceForm.addEventListener("submit", function (e) {
    e.preventDefault();
    api("PUT", "/widget-config", {
      title: cfgTitle.value,
      greeting: cfgGreeting.value,
      theme: cfgTheme.value,
      button_color: cfgButtonColor.value,
      icon_url: cfgIconUrl.value,
      show_attachments: cfgShowAttachments.checked,
      context_injection: cfgContextInjection.checked,
    }).then(function (res) {
      toast(res.success ? "Appearance saved" : (res.detail || "Failed"), res.success ? "success" : "error");
      if (res.success) {
        // Reload the widget iframe if it has been opened, so new config takes effect
        var iframe = document.getElementById("mate-widget-iframe");
        if (iframe && iframe.src && iframe.src !== "about:blank") {
          var base = iframe.src.split("&_t=")[0];
          iframe.src = base + "&_t=" + Date.now();
        }
      }
    });
  });

  // --- Init ------------------------------------------------------------
  loadAgent();
  loadBlocks();
  loadFiles();
  loadAppearance();

  // Expose for inline onclick
  window.widgetAdmin = {
    editBlock: editBlock,
    deleteBlock: deleteBlock,
    deleteFile: deleteFile,
    showBlockModal: showBlockModal,
    hideBlockModal: hideBlockModal,
  };
})();
