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

  // --- Memory Blocks ---------------------------------------------------
  var blocksList = document.getElementById("blocksList");
  var blockForm = document.getElementById("blockForm");
  var blockLabel = document.getElementById("blockLabel");
  var blockValue = document.getElementById("blockValue");
  var blockDesc = document.getElementById("blockDescription");
  var blockFormTitle = document.getElementById("blockFormTitle");
  var editingBlockId = null;

  function loadBlocks() {
    api("GET", "/memory-blocks").then(function (res) {
      if (!res.success) {
        blocksList.innerHTML = '<div class="admin-empty">No memory blocks found or tool not configured.</div>';
        return;
      }
      var blocks = res.blocks || [];
      if (blocks.length === 0) {
        blocksList.innerHTML = '<div class="admin-empty">No memory blocks yet.</div>';
        return;
      }
      blocksList.innerHTML = blocks.map(function (b) {
        return '<div class="admin-list-item">'
          + '<div><div class="admin-list-item-label">' + escapeHtml(b.label) + '</div>'
          + '<div class="admin-list-item-desc">' + escapeHtml((b.value || "").substring(0, 100)) + '</div></div>'
          + '<div class="admin-list-item-actions">'
          + '<button class="admin-btn admin-btn-sm" onclick="widgetAdmin.editBlock(\'' + b.block_id + '\')">Edit</button>'
          + '<button class="admin-btn admin-btn-sm admin-btn-danger" onclick="widgetAdmin.deleteBlock(\'' + b.block_id + '\')">Delete</button>'
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
      blockFormTitle.textContent = "Edit Memory Block";
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
        blockFormTitle.textContent = "New Memory Block";
        loadBlocks();
      }
    });
  });

  document.getElementById("blockFormCancel").addEventListener("click", function () {
    editingBlockId = null;
    blockLabel.value = "";
    blockValue.value = "";
    blockDesc.value = "";
    blockFormTitle.textContent = "New Memory Block";
  });

  // --- File Search -----------------------------------------------------
  var fileStoresList = document.getElementById("fileStoresList");
  var uploadForm = document.getElementById("uploadForm");
  var uploadStoreSelect = document.getElementById("uploadStore");
  var uploadFileInput = document.getElementById("uploadFile");
  var uploadArea = document.getElementById("uploadArea");

  function loadFiles() {
    api("GET", "/files").then(function (res) {
      if (!res.success || !res.stores || res.stores.length === 0) {
        fileStoresList.innerHTML = '<div class="admin-empty">No file search stores assigned to this agent.</div>';
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
          ? '<div class="admin-empty" style="padding:12px">No files in this store.</div>'
          : files.map(function (f) {
              return '<div class="admin-list-item">'
                + '<div><div class="admin-list-item-label">' + escapeHtml(f.display_name || f.document_name) + '</div>'
                + '<div class="admin-list-item-desc">' + (f.mime_type || "") + ' — ' + _fmtSize(f.file_size) + '</div></div>'
                + '<div class="admin-list-item-actions">'
                + '<button class="admin-btn admin-btn-sm admin-btn-danger" onclick="widgetAdmin.deleteFile(' + f.id + ')">Delete</button>'
                + '</div></div>';
            }).join("");

        return '<div class="admin-card">'
          + '<div class="admin-card-title">' + escapeHtml(s.display_name || s.store_name) + '</div>'
          + filesHtml
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

  // --- Init ------------------------------------------------------------
  loadAgent();
  loadBlocks();
  loadFiles();

  // Expose for inline onclick
  window.widgetAdmin = {
    editBlock: editBlock,
    deleteBlock: deleteBlock,
    deleteFile: deleteFile,
  };
})();
