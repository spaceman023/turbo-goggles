/* PRISM Web UI — frontend application */

// ── State ───────────────────────────────────────────────────────────────────

const state = {
  jobs: [],
  configs: [],
  hasApiKey: false,
  currentJobId: null,
  currentRunId: null,
  ws: null,
  liveChunks: {},    // chunk_index -> { status, layer, message }
  liveJobId: null,

  // Pipeline builder state
  pipeline: {
    name: "My Pipeline",
    max_workers: 4,
    layers: [],
  },
  selectedLayerIndex: -1,
  backendHealth: {},  // backend_name -> boolean
  addPickerOpen: false,
};

// ── Init ────────────────────────────────────────────────────────────────────

document.addEventListener("DOMContentLoaded", async () => {
  // Wire up event handlers
  document.getElementById("btn-new-job").addEventListener("click", openBuilder);
  document.getElementById("btn-settings").addEventListener("click", openSettingsModal);
  document.getElementById("btn-copy-output").addEventListener("click", copyOutput);
  document.getElementById("btn-download-output").addEventListener("click", downloadOutput);

  // Builder top bar handlers
  document.getElementById("builder-workers").addEventListener("input", (e) => {
    document.getElementById("builder-workers-val").textContent = e.target.value;
    state.pipeline.max_workers = parseInt(e.target.value);
  });
  document.getElementById("builder-name").addEventListener("input", (e) => {
    state.pipeline.name = e.target.value;
  });
  document.getElementById("builder-pdf").addEventListener("change", onBuilderFileChange);

  // Close modal on backdrop click
  document.getElementById("modal-backdrop").addEventListener("click", (e) => {
    if (e.target === e.currentTarget) closeModals();
  });

  // Keyboard shortcuts
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      if (state.addPickerOpen) {
        closeAddLayerPicker();
      } else {
        closeModals();
      }
    }
    // Delete key removes selected layer (only when builder is visible and not typing in an input)
    if (e.key === "Delete" || e.key === "Backspace") {
      const tag = document.activeElement?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
      if (document.getElementById("view-builder").style.display !== "none" && state.selectedLayerIndex >= 0) {
        e.preventDefault();
        removeLayer(state.selectedLayerIndex);
      }
    }
  });

  // Close add-layer picker when clicking outside
  document.addEventListener("click", (e) => {
    if (state.addPickerOpen) {
      const picker = document.getElementById("add-layer-picker");
      const btn = document.getElementById("btn-add-layer");
      if (!picker.contains(e.target) && e.target !== btn) {
        closeAddLayerPicker();
      }
    }
  });

  // Load initial data
  await Promise.all([loadConfigs(), loadSettings(), loadJobs()]);

  // Toast container
  const tc = document.createElement("div");
  tc.className = "toast-container";
  tc.id = "toast-container";
  document.body.appendChild(tc);
});

// ── Data loading ────────────────────────────────────────────────────────────

async function loadJobs() {
  try {
    state.jobs = await api("/api/jobs");
    renderJobs();
  } catch (e) {
    console.error("Failed to load jobs:", e);
  }
}

async function loadConfigs() {
  try {
    state.configs = await api("/api/configs");
  } catch (e) {
    console.error("Failed to load configs:", e);
  }
}

async function loadSettings() {
  try {
    const s = await api("/api/settings");
    state.hasApiKey = s.has_api_key;
  } catch (e) {
    console.error("Failed to load settings:", e);
  }
}

async function loadBackendHealth() {
  try {
    const health = await api("/api/backends/health");
    // health may be an object like { openrouter: true, codex: false, ... }
    if (typeof health === "object" && health !== null) {
      state.backendHealth = health;
    }
  } catch (e) {
    console.warn("Backend health check unavailable:", e);
    state.backendHealth = {};
  }
}

// ── Rendering: Jobs List ────────────────────────────────────────────────────

function renderJobs() {
  const list = document.getElementById("jobs-list");
  const empty = document.getElementById("jobs-empty");

  if (state.jobs.length === 0) {
    list.innerHTML = "";
    empty.style.display = "";
    return;
  }

  empty.style.display = "none";
  list.innerHTML = state.jobs
    .map((j) => {
      const name = j.pipeline_name || j.job_id;
      const input = j.input_file.replace(/^input\//, "");
      const size = j.output_size ? formatBytes(j.output_size) : "--";
      const time = formatTime(j.created_at);
      return `
      <div class="job-card" onclick="showJobDetail('${j.job_id}')">
        <div class="job-status-dot ${j.status}"></div>
        <div class="job-info">
          <div class="job-title">${esc(name)}</div>
          <div class="job-meta">
            <span>${esc(input)}</span>
            <span>${j.total_pages} pages</span>
            <span>${j.completed_chunks}/${j.total_chunks} chunks</span>
            <span>${time}</span>
          </div>
        </div>
        <span class="job-badge ${j.status}">${j.status}</span>
        <span class="job-size">${size}</span>
        <button class="btn-delete" onclick="event.stopPropagation(); deleteJob('${j.job_id}')" title="Delete job">&times;</button>
      </div>`;
    })
    .join("");
}

// ── Views ───────────────────────────────────────────────────────────────────

function showJobs() {
  document.getElementById("view-jobs").style.display = "";
  document.getElementById("view-detail").style.display = "none";
  document.getElementById("view-builder").style.display = "none";
  state.currentJobId = null;
  if (state.ws) {
    state.ws.close();
    state.ws = null;
  }
  loadJobs();
}

async function showJobDetail(jobId) {
  state.currentJobId = jobId;
  document.getElementById("view-jobs").style.display = "none";
  document.getElementById("view-detail").style.display = "";
  document.getElementById("view-builder").style.display = "none";

  try {
    const manifest = await api(`/api/jobs/${jobId}`);
    renderJobDetail(manifest);

    if (manifest.status === "complete") {
      await loadOutput(jobId);
    }
  } catch (e) {
    toast("Failed to load job: " + e.message, "error");
    showJobs();
  }
}

// ── Rendering: Job Detail ───────────────────────────────────────────────────

function renderJobDetail(manifest) {
  const input = manifest.input_file.replace(/^input\//, "");
  const pipeline = manifest.pipeline || {};
  const chunks = manifest.chunks || [];
  const completed = chunks.filter((c) => c.status === "complete").length;
  const pct = chunks.length ? Math.round((completed / chunks.length) * 100) : 0;

  // Header
  document.getElementById("detail-header").innerHTML = `
    <h2>${esc(pipeline.name || manifest.job_id)}</h2>
    <span class="job-badge ${manifest.status}">${manifest.status}</span>
    <div class="detail-stats">
      <span>${esc(input)}</span>
      <span>${manifest.total_pages} pages</span>
      <span>${chunks.length} chunks</span>
      ${manifest.status === "halted" ?
        `<button class="btn btn-sm" onclick="resumeJob('${manifest.job_id}')">Resume</button>` : ""}
      ${["complete", "halted", "failed"].includes(manifest.status) ?
        `<button class="btn btn-sm" onclick="retryJob('${manifest.job_id}')">Retry</button>` : ""}
      <button class="btn btn-sm btn-danger" onclick="deleteJob('${manifest.job_id}')">Delete</button>
    </div>
  `;

  // Layer pipeline
  const layers = pipeline.layers || [];
  document.getElementById("detail-layers").innerHTML = layers
    .map((l, i) => {
      const type = l.type;
      const label = l.engine || l.model || l.format || l.id;
      return (
        (i > 0 ? '<span class="layer-arrow">&rarr;</span>' : "") +
        `<span class="layer-chip ${type}" title="${esc(l.id)}">${type}: ${esc(label)}</span>`
      );
    })
    .join("");

  // Progress
  document.getElementById("detail-bar").querySelector(".progress-fill").style.width = pct + "%";
  document.getElementById("detail-bar-label").textContent =
    `${completed} / ${chunks.length} chunks (${pct}%)`;

  // Chunk grid
  renderChunks(chunks);
}

function renderChunks(chunks) {
  const grid = document.getElementById("detail-chunks");
  grid.innerHTML = chunks
    .map((c, i) => {
      const live = state.liveChunks[i];
      const status = live ? live.status : c.status;
      const layers = c.layers_completed ? c.layers_completed.length : 0;
      const err = c.error ? `Error: ${c.error}` : "";
      const tip = `${c.id}: ${status}${layers ? `, ${layers} layers done` : ""}${err ? `\n${err}` : ""}`;
      return `<div class="chunk-cell ${status}" title="${esc(tip)}">${i + 1}</div>`;
    })
    .join("");
}

async function loadOutput(jobId) {
  try {
    const res = await api(`/api/jobs/${jobId}/output`);
    const section = document.getElementById("detail-output-section");
    section.style.display = "";
    const container = document.getElementById("detail-output");

    if (res.format === "md" && typeof marked !== "undefined") {
      container.innerHTML = marked.parse(res.content);
    } else {
      container.innerHTML = `<pre>${esc(res.content)}</pre>`;
    }
  } catch (e) {
    // Output not ready yet
    document.getElementById("detail-output-section").style.display = "none";
  }
}

// ── Resume Job ──────────────────────────────────────────────────────────────

async function resumeJob(jobId) {
  try {
    const res = await fetch(`/api/runs/${jobId}/resume`, {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: `workers=4`,
    });
    if (!res.ok) throw new Error("Failed to resume");
    const data = await res.json();
    state.currentRunId = data.run_id;
    state.liveChunks = {};
    toast("Resuming pipeline...", "info");
    connectWebSocket(data.run_id);
  } catch (e) {
    toast(e.message, "error");
  }
}

// ── WebSocket ───────────────────────────────────────────────────────────────

function connectWebSocket(runId) {
  if (state.ws) state.ws.close();

  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  const ws = new WebSocket(`${proto}//${location.host}/ws/${runId}`);
  state.ws = ws;

  ws.onmessage = (e) => {
    try {
      const event = JSON.parse(e.data);
      handlePipelineEvent(event);
    } catch (err) {
      console.error("WS parse error:", err);
    }
  };

  ws.onclose = () => {
    if (state.ws === ws) state.ws = null;
  };

  ws.onerror = (e) => {
    console.error("WS error:", e);
  };
}

function handlePipelineEvent(event) {
  switch (event.type) {
    case "progress":
      state.liveChunks[event.chunk] = {
        status: "running",
        layer: event.layer,
        message: event.message,
      };
      if (!state.liveJobId && event.job_id) {
        state.liveJobId = event.job_id;
        // Load full manifest to get chunk/layer info
        loadLiveManifest(event.job_id);
      }
      updateLiveProgress();
      break;

    case "layer_complete":
      if (state.liveChunks[event.chunk]) {
        state.liveChunks[event.chunk].layer = event.layer;
      }
      updateLiveProgress();
      break;

    case "error":
      state.liveChunks[event.chunk] = {
        status: "halted",
        layer: event.layer,
        message: event.error,
      };
      toast(`Chunk ${event.chunk + 1} error: ${event.error}`, "error");
      updateLiveProgress();
      break;

    case "complete":
      toast("Pipeline complete!", "success");
      // Reload the full job detail
      if (event.job_id) {
        setTimeout(() => showJobDetail(event.job_id), 500);
      }
      break;

    case "done": {
      if (event.exit_code !== 0) {
        // Extract a useful error from stderr
        const stderr = event.stderr || "";
        const errLine = stderr.split("\n").filter(l =>
          l.includes("Error") || l.includes("error") || l.includes("Halted") || l.includes("OPENROUTER")
        ).pop() || `Pipeline exited with code ${event.exit_code}`;
        toast(errLine.trim().slice(0, 200), "error");
      }
      if (event.job_id) {
        setTimeout(() => showJobDetail(event.job_id), 500);
      }
      break;
    }
  }
}

async function loadLiveManifest(jobId) {
  try {
    const manifest = await api(`/api/jobs/${jobId}`);
    renderJobDetail(manifest);
  } catch (e) {
    // Job might not be ready yet, retry
    setTimeout(() => loadLiveManifest(jobId), 1000);
  }
}

function updateLiveProgress() {
  // Count statuses from live data
  const chunkCells = document.querySelectorAll(".chunk-cell");
  let completed = 0;
  let running = 0;
  let halted = 0;

  chunkCells.forEach((cell, i) => {
    const live = state.liveChunks[i];
    if (live) {
      cell.className = `chunk-cell ${live.status}`;
      if (live.message) cell.title = live.message;
    }
    if (cell.classList.contains("complete")) completed++;
    if (cell.classList.contains("running")) running++;
    if (cell.classList.contains("halted")) halted++;
  });

  // If no chunk cells yet but we have live data, show a simple counter
  const total = chunkCells.length || Object.keys(state.liveChunks).length || 1;
  const pct = Math.round((completed / total) * 100);

  const fill = document.querySelector("#detail-bar .progress-fill");
  if (fill) fill.style.width = pct + "%";

  const label = document.getElementById("detail-bar-label");
  if (label) {
    label.textContent = `${completed}/${total} chunks complete` +
      (running ? `, ${running} running` : "") +
      (halted ? `, ${halted} halted` : "");
  }
}

// ── Settings ────────────────────────────────────────────────────────────────

function openSettingsModal() {
  document.getElementById("modal-backdrop").style.display = "flex";
  document.getElementById("modal-settings").style.display = "";
  document.getElementById("modal-load-pipeline").style.display = "none";
  switchSettingsTab("backends");
  refreshBackendHealth();
  refreshEngineHealth();
  refreshGPUStatus();
}

function closeModals() {
  document.getElementById("modal-backdrop").style.display = "none";
  document.getElementById("modal-settings").style.display = "none";
  document.getElementById("modal-load-pipeline").style.display = "none";
}

function switchSettingsTab(tab) {
  document.querySelectorAll(".settings-tab").forEach((t) => {
    t.classList.toggle("active", t.dataset.tab === tab);
  });
  document.querySelectorAll(".settings-panel").forEach((p) => {
    p.style.display = "none";
  });
  const panel = document.getElementById(`settings-${tab}`);
  if (panel) panel.style.display = "";
}

async function refreshBackendHealth() {
  const el = document.getElementById("settings-backend-status");
  try {
    const data = await api("/api/backends/health");
    el.innerHTML = Object.entries(data)
      .map(
        ([name, info]) => `
        <div class="status-row">
          <span class="status-dot ${info.available ? "available" : "unavailable"}"></span>
          <span class="status-name">${esc(name)}</span>
          <span class="status-detail">${esc(info.detail)}</span>
        </div>`
      )
      .join("");
  } catch (e) {
    el.innerHTML = `<p class="field-hint">Failed to load</p>`;
  }
}

async function refreshEngineHealth() {
  const el = document.getElementById("settings-engine-status");
  try {
    const data = await api("/api/engines/health");
    el.innerHTML = Object.entries(data)
      .map(
        ([name, info]) => `
        <div class="status-row">
          <span class="status-dot ${info.available ? "available" : "unavailable"}"></span>
          <span class="status-name">${esc(name)}</span>
          <span class="status-detail">${esc(info.detail)}</span>
        </div>`
      )
      .join("");
  } catch (e) {
    el.innerHTML = `<p class="field-hint">Failed to load</p>`;
  }
}

async function refreshGPUStatus() {
  const el = document.getElementById("settings-gpu-status");
  try {
    const data = await api("/api/gpu");
    el.innerHTML = `
      <div class="status-row">
        <span class="status-dot ${data.device !== "cpu" ? "available" : "unavailable"}"></span>
        <span class="status-name">Device: ${esc(data.device)}</span>
        <span class="status-detail">${esc(data.detail) || "CPU only"}</span>
      </div>
      <div class="status-row">
        <span class="status-dot ${data.cuda ? "available" : "unavailable"}"></span>
        <span class="status-name">CUDA</span>
        <span class="status-detail">${data.cuda ? "Available" : "Not available"}</span>
      </div>
      <div class="status-row">
        <span class="status-dot ${data.mps ? "available" : "unavailable"}"></span>
        <span class="status-name">MPS (Apple Silicon)</span>
        <span class="status-detail">${data.mps ? "Available" : "Not available"}</span>
      </div>`;
  } catch (e) {
    el.innerHTML = `<p class="field-hint">Failed to detect GPU</p>`;
  }
}

async function saveSettings() {
  const key = document.getElementById("settings-api-key").value;
  try {
    if (key) {
      const form = new URLSearchParams();
      form.append("api_key", key);
      await fetch("/api/settings", { method: "POST", body: form });
      state.hasApiKey = true;
    }
    closeModals();
    toast("Settings saved", "success");
  } catch (e) {
    toast("Failed to save settings", "error");
  }
}

// ── Retry ───────────────────────────────────────────────────────────────────

async function retryJob(jobId) {
  if (!confirm(`Retry job ${jobId}?\nThis creates a new job with the same pipeline and input.`)) return;
  try {
    const res = await fetch(`/api/jobs/${jobId}/retry`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    });
    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || "Retry failed");
    }
    const data = await res.json();
    state.currentRunId = data.run_id;
    state.liveChunks = {};
    state.liveJobId = null;

    toast("Retrying pipeline...", "info");

    // Switch to live detail view
    document.getElementById("view-jobs").style.display = "none";
    document.getElementById("view-detail").style.display = "";
    document.getElementById("detail-output-section").style.display = "none";
    document.getElementById("detail-header").innerHTML = `
      <h2><span class="spinner"></span> Retrying pipeline...</h2>
      <span class="job-badge running">running</span>
    `;
    document.getElementById("detail-layers").innerHTML = "";
    document.getElementById("detail-chunks").innerHTML = "";
    document.getElementById("detail-bar").querySelector(".progress-fill").style.width = "0%";
    document.getElementById("detail-bar-label").textContent = "Waiting for pipeline to start...";

    connectWebSocket(data.run_id);
  } catch (e) {
    toast(e.message, "error");
  }
}

// ── Delete ──────────────────────────────────────────────────────────────────

async function deleteJob(jobId) {
  if (!confirm(`Delete job ${jobId}?\nThis removes all files from disk.`)) return;
  try {
    const res = await fetch(`/api/jobs/${jobId}`, { method: "DELETE" });
    if (!res.ok) throw new Error("Delete failed");
    toast("Job deleted", "success");
    if (state.currentJobId === jobId) {
      showJobs();
    } else {
      loadJobs();
    }
  } catch (e) {
    toast(e.message, "error");
  }
}

// ── Output actions ──────────────────────────────────────────────────────────

async function copyOutput() {
  try {
    const res = await api(`/api/jobs/${state.currentJobId}/output`);
    await navigator.clipboard.writeText(res.content);
    toast("Copied to clipboard", "success");
  } catch (e) {
    toast("Failed to copy", "error");
  }
}

function downloadOutput() {
  if (state.currentJobId) {
    window.open(`/api/jobs/${state.currentJobId}/output/download`, "_blank");
  }
}

// ══════════════════════════════════════════════════════════════════════════════
// ── Pipeline Builder ──────────────────────────────────────────────────────
// ══════════════════════════════════════════════════════════════════════════════

function openBuilder() {
  // Reset pipeline state for a fresh build
  state.pipeline = {
    name: "My Pipeline",
    max_workers: 4,
    layers: [],
  };
  state.selectedLayerIndex = -1;
  state.addPickerOpen = false;

  // Sync UI
  document.getElementById("builder-name").value = state.pipeline.name;
  document.getElementById("builder-workers").value = state.pipeline.max_workers;
  document.getElementById("builder-workers-val").textContent = state.pipeline.max_workers;

  // Reset file input
  document.getElementById("builder-pdf").value = "";
  const fileLabel = document.getElementById("builder-file-label");
  fileLabel.querySelector("span").textContent = "Choose PDF...";
  fileLabel.classList.remove("has-file");

  // Switch view
  document.getElementById("view-jobs").style.display = "none";
  document.getElementById("view-detail").style.display = "none";
  document.getElementById("view-builder").style.display = "";

  // Load backend health
  loadBackendHealth();

  // Render
  renderBuilderLayers();
  renderInspector();
}

function closeBuilder() {
  showJobs();
}

function onBuilderFileChange(e) {
  const label = document.getElementById("builder-file-label");
  if (e.target.files.length) {
    label.querySelector("span").textContent = e.target.files[0].name;
    label.classList.add("has-file");
  } else {
    label.querySelector("span").textContent = "Choose PDF...";
    label.classList.remove("has-file");
  }
}

// ── Layer ID generation ─────────────────────────────────────────────────────

function generateLayerId(type) {
  // Count existing layers of this type to generate next index
  const existing = state.pipeline.layers.filter(l => l.type === type);
  let idx = existing.length + 1;
  // Make sure it's unique
  const usedIds = new Set(state.pipeline.layers.map(l => l.id));
  while (usedIds.has(`${type}-${idx}`)) idx++;
  return `${type}-${idx}`;
}

// ── Add / Remove / Select layers ────────────────────────────────────────────

function toggleAddLayerPicker() {
  const picker = document.getElementById("add-layer-picker");
  if (state.addPickerOpen) {
    closeAddLayerPicker();
  } else {
    picker.style.display = "";
    state.addPickerOpen = true;
  }
}

function closeAddLayerPicker() {
  document.getElementById("add-layer-picker").style.display = "none";
  state.addPickerOpen = false;
}

function addLayer(type, engineOrBackendOrFormat) {
  const layer = {
    id: generateLayerId(type),
    type: type,
    engine: "",
    backend: "",
    model: "",
    prompt_file: "",
    include_image: false,
    temperature: 0.3,
    max_tokens: 4096,
    source_layers: [],
    format: "",
    preserve_page_breaks: false,
    options: {},
    languages: "eng",
  };

  switch (type) {
    case "ocr":
      layer.engine = engineOrBackendOrFormat;
      // Tesseract uses "eng", most others use "en"
      layer.languages = (engineOrBackendOrFormat === "tesseract") ? "eng" : "en";
      break;
    case "llm":
      layer.backend = engineOrBackendOrFormat;
      layer.prompt_file = "prompts/ocr_correction.md";
      if (engineOrBackendOrFormat === "openrouter") {
        layer.model = "google/gemini-2.0-flash-001";
      }
      break;
    case "fusion":
      layer.backend = "openrouter";
      layer.prompt_file = "prompts/fusion.md";
      break;
    case "output":
      layer.format = engineOrBackendOrFormat;
      break;
  }

  state.pipeline.layers.push(layer);
  state.selectedLayerIndex = state.pipeline.layers.length - 1;
  closeAddLayerPicker();
  renderBuilderLayers();
  renderInspector();
}

function removeLayer(index) {
  if (index < 0 || index >= state.pipeline.layers.length) return;
  const removedId = state.pipeline.layers[index].id;
  state.pipeline.layers.splice(index, 1);

  // Clean up source_layers references
  for (const layer of state.pipeline.layers) {
    if (layer.source_layers) {
      layer.source_layers = layer.source_layers.filter(id => id !== removedId);
    }
  }

  // Adjust selection
  if (state.selectedLayerIndex >= state.pipeline.layers.length) {
    state.selectedLayerIndex = state.pipeline.layers.length - 1;
  }
  if (state.pipeline.layers.length === 0) {
    state.selectedLayerIndex = -1;
  }

  renderBuilderLayers();
  renderInspector();
}

function selectLayer(index) {
  state.selectedLayerIndex = index;
  renderBuilderLayers();
  renderInspector();
}

// ── Render Builder Layers ───────────────────────────────────────────────────

function renderBuilderLayers() {
  const stack = document.getElementById("builder-layer-stack");

  if (state.pipeline.layers.length === 0) {
    stack.innerHTML = `
      <div class="layer-stack-empty">
        <svg width="36" height="36" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
          <rect x="3" y="3" width="18" height="18" rx="2" ry="2"/>
          <line x1="12" y1="8" x2="12" y2="16"/>
          <line x1="8" y1="12" x2="16" y2="12"/>
        </svg>
        <span>No layers yet. Click "Add Layer" below to start building your pipeline.</span>
      </div>`;
    return;
  }

  stack.innerHTML = state.pipeline.layers.map((layer, i) => {
    const isSelected = i === state.selectedLayerIndex;
    const label = getLayerDisplayLabel(layer);
    return `
      <div class="layer-card-wrap">
        ${i > 0 ? '<div class="layer-arrow-connector">&darr;</div>' : ""}
        <div class="layer-card ${isSelected ? "selected" : ""}" onclick="selectLayer(${i})" data-index="${i}">
          <span class="layer-card-type ${layer.type}">${layer.type}</span>
          <span class="layer-card-label">${esc(label)}</span>
          <span class="layer-card-id">${esc(layer.id)}</span>
          <button class="layer-card-delete" onclick="event.stopPropagation(); removeLayer(${i})" title="Remove layer">&times;</button>
        </div>
      </div>`;
  }).join("");
}

const ENGINE_DISPLAY_NAMES = {
  tesseract: "Tesseract",
  paddleocr: "PaddleOCR",
  easyocr: "EasyOCR",
  surya: "Surya",
  doctr: "docTR",
  macos_vision: "macOS Vision",
};

function getLayerDisplayLabel(layer) {
  switch (layer.type) {
    case "ocr":
      return ENGINE_DISPLAY_NAMES[layer.engine] || layer.engine || "OCR";
    case "llm":
      return layer.model || layer.backend || "LLM";
    case "fusion":
      return layer.backend ? `Fusion (${layer.backend})` : "Fusion";
    case "output":
      return layer.format ? `Output (.${layer.format})` : "Output";
    case "preprocess":
      return "Preprocess";
    default:
      return layer.type;
  }
}

// ── Render Inspector ────────────────────────────────────────────────────────

function renderInspector() {
  const inspector = document.getElementById("builder-inspector");

  if (state.selectedLayerIndex < 0 || state.selectedLayerIndex >= state.pipeline.layers.length) {
    inspector.innerHTML = '<div class="inspector-empty"><p>Select a layer to configure it</p></div>';
    return;
  }

  const layer = state.pipeline.layers[state.selectedLayerIndex];

  let html = '<div class="inspector-form">';

  // Title
  html += `
    <div class="inspector-title">
      <span class="layer-card-type ${layer.type}">${layer.type}</span>
      ${esc(layer.id)}
    </div>
    <div class="inspector-subtitle">Configure this ${layer.type} layer</div>
  `;

  switch (layer.type) {
    case "ocr":
      html += renderOCRInspector(layer);
      break;
    case "llm":
      html += renderLLMInspector(layer);
      break;
    case "fusion":
      html += renderFusionInspector(layer);
      break;
    case "output":
      html += renderOutputInspector(layer);
      break;
    default:
      html += `<p class="field-hint">No configuration available for this layer type.</p>`;
  }

  html += '</div>';
  inspector.innerHTML = html;

  // Attach change handlers after rendering
  attachInspectorHandlers(layer);
}

function renderOCRInspector(layer) {
  const engines = ["tesseract","paddleocr","easyocr","surya","doctr","macos_vision"];
  const labels = {tesseract:"Tesseract",paddleocr:"PaddleOCR",easyocr:"EasyOCR",surya:"Surya",doctr:"docTR",macos_vision:"macOS Vision"};
  return `
    <div class="form-field">
      <label>Engine</label>
      <select id="insp-engine">
        ${engines.map(e => `<option value="${e}" ${layer.engine === e ? "selected" : ""}>${labels[e]||e}</option>`).join("")}
      </select>
    </div>
    <div class="form-field">
      <label>Languages (comma-separated)</label>
      <input type="text" id="insp-languages" value="${esc(layer.languages || "eng")}" placeholder="eng,fra,deu">
      <div class="field-hint">e.g. eng, fra, deu, chi_sim</div>
    </div>
  `;
}

function renderLLMInspector(layer) {
  return `
    <div class="form-field">
      <label>Backend</label>
      <select id="insp-backend">
        ${renderBackendOptions(layer.backend)}
      </select>
    </div>
    <div class="form-field" id="insp-model-field" ${layer.backend !== "openrouter" ? 'style="display:none"' : ""}>
      <label>Model</label>
      <input type="text" id="insp-model" list="openrouter-models" value="${esc(layer.model || "")}" placeholder="Search models..." autocomplete="off">
      <datalist id="openrouter-models"></datalist>
      <div class="field-hint">Type to search OpenRouter models</div>
    </div>
    <div class="form-field">
      <label>Prompt File</label>
      <input type="text" id="insp-prompt-file" value="${esc(layer.prompt_file || "")}" placeholder="prompts/ocr_correction.md">
    </div>
    <hr class="inspector-divider">
    <div class="form-field">
      <label class="inspector-checkbox">
        <input type="checkbox" id="insp-include-image" ${layer.include_image ? "checked" : ""}>
        Include page image in prompt
      </label>
    </div>
    <div class="form-field">
      <label>Temperature: <strong id="insp-temp-val">${(layer.temperature ?? 0.3).toFixed(1)}</strong></label>
      <input type="range" id="insp-temperature" min="0" max="2" step="0.1" value="${layer.temperature ?? 0.3}">
    </div>
    <div class="form-field">
      <label>Max Tokens</label>
      <input type="number" id="insp-max-tokens" value="${layer.max_tokens ?? 4096}" min="1" max="128000">
    </div>
  `;
}

function renderFusionInspector(layer) {
  // Source layers: show checkboxes for all layers that come before this one
  const idx = state.selectedLayerIndex;
  const priorLayers = state.pipeline.layers.slice(0, idx);
  let sourceLayersHtml = "";

  if (priorLayers.length === 0) {
    sourceLayersHtml = '<div class="source-layers-empty">No prior layers to fuse. Add OCR or LLM layers above this fusion layer.</div>';
  } else {
    sourceLayersHtml = priorLayers.map(pl => {
      const checked = (layer.source_layers || []).includes(pl.id) ? "checked" : "";
      return `
        <label class="inspector-checkbox">
          <input type="checkbox" class="insp-source-cb" value="${esc(pl.id)}" ${checked}>
          <span class="layer-card-type ${pl.type}" style="font-size:9px;padding:1px 6px;">${pl.type}</span>
          ${esc(pl.id)}
        </label>`;
    }).join("");
  }

  return `
    <div class="form-field">
      <label>Source Layers</label>
      <div class="source-layers-list" id="insp-source-layers">
        ${sourceLayersHtml}
      </div>
      <div class="field-hint">Select which layers to fuse</div>
    </div>
    <hr class="inspector-divider">
    <div class="form-field">
      <label>Backend</label>
      <select id="insp-backend">
        ${renderBackendOptions(layer.backend)}
      </select>
    </div>
    <div class="form-field" id="insp-model-field" ${layer.backend !== "openrouter" ? 'style="display:none"' : ""}>
      <label>Model</label>
      <input type="text" id="insp-model" value="${esc(layer.model || "")}" placeholder="google/gemini-2.0-flash-001">
    </div>
    <div class="form-field">
      <label>Prompt File</label>
      <input type="text" id="insp-prompt-file" value="${esc(layer.prompt_file || "")}" placeholder="prompts/fusion.md">
    </div>
    <div class="form-field">
      <label>Temperature: <strong id="insp-temp-val">${(layer.temperature ?? 0.3).toFixed(1)}</strong></label>
      <input type="range" id="insp-temperature" min="0" max="2" step="0.1" value="${layer.temperature ?? 0.3}">
    </div>
    <div class="form-field">
      <label>Max Tokens</label>
      <input type="number" id="insp-max-tokens" value="${layer.max_tokens ?? 4096}" min="1" max="128000">
    </div>
  `;
}

function renderOutputInspector(layer) {
  return `
    <div class="form-field">
      <label>Format</label>
      <select id="insp-format">
        <option value="txt" ${layer.format === "txt" ? "selected" : ""}>Plain Text (.txt)</option>
        <option value="md" ${layer.format === "md" ? "selected" : ""}>Markdown (.md)</option>
      </select>
    </div>
    <div class="form-field">
      <label class="inspector-checkbox">
        <input type="checkbox" id="insp-preserve-page-breaks" ${layer.preserve_page_breaks ? "checked" : ""}>
        Preserve page breaks
      </label>
    </div>
  `;
}

function renderBackendOptions(selectedBackend) {
  const backends = ["openrouter", "codex", "gemini", "claude"];
  return backends.map(b => {
    const health = state.backendHealth[b];
    let dot = '<span class="backend-health-dot unknown"></span>';
    if (health === true) dot = '<span class="backend-health-dot healthy"></span>';
    else if (health === false) dot = '<span class="backend-health-dot unhealthy"></span>';
    const sel = b === selectedBackend ? "selected" : "";
    // Select options can't contain HTML, so we use data attributes and a workaround is not practical.
    // Instead just render plain options.
    return `<option value="${b}" ${sel}>${b}</option>`;
  }).join("");
}

// ── Inspector change handlers ───────────────────────────────────────────────

function attachInspectorHandlers(layer) {
  const idx = state.selectedLayerIndex;

  // OCR
  const engineSel = document.getElementById("insp-engine");
  if (engineSel) {
    engineSel.addEventListener("change", () => {
      layer.engine = engineSel.value;
      layer.languages = (engineSel.value === "tesseract") ? "eng" : "en";
      const langInput = document.getElementById("insp-languages");
      if (langInput) langInput.value = layer.languages;
      renderBuilderLayers();
    });
  }

  const langInput = document.getElementById("insp-languages");
  if (langInput) {
    langInput.addEventListener("input", () => {
      layer.languages = langInput.value;
    });
  }

  // LLM / Fusion backend
  const backendSel = document.getElementById("insp-backend");
  if (backendSel) {
    // Show health dots inline above the select
    renderBackendHealthIndicator(backendSel);
    backendSel.addEventListener("change", () => {
      layer.backend = backendSel.value;
      // Show/hide model field
      const modelField = document.getElementById("insp-model-field");
      if (modelField) {
        modelField.style.display = backendSel.value === "openrouter" ? "" : "none";
      }
      renderBuilderLayers();
    });
  }

  const modelInput = document.getElementById("insp-model");
  if (modelInput) {
    let _modelSearchTimeout = null;
    modelInput.addEventListener("input", () => {
      layer.model = modelInput.value;
      renderBuilderLayers();
      // Debounced model search
      clearTimeout(_modelSearchTimeout);
      _modelSearchTimeout = setTimeout(() => searchModels(modelInput.value), 300);
    });
    // Load initial models on focus
    modelInput.addEventListener("focus", () => {
      if (!document.getElementById("openrouter-models").children.length) {
        searchModels("");
      }
    });
  }

  const promptInput = document.getElementById("insp-prompt-file");
  if (promptInput) {
    promptInput.addEventListener("input", () => {
      layer.prompt_file = promptInput.value;
    });
  }

  const includeImg = document.getElementById("insp-include-image");
  if (includeImg) {
    includeImg.addEventListener("change", () => {
      layer.include_image = includeImg.checked;
    });
  }

  const tempSlider = document.getElementById("insp-temperature");
  if (tempSlider) {
    tempSlider.addEventListener("input", () => {
      layer.temperature = parseFloat(tempSlider.value);
      const valSpan = document.getElementById("insp-temp-val");
      if (valSpan) valSpan.textContent = layer.temperature.toFixed(1);
    });
  }

  const maxTokens = document.getElementById("insp-max-tokens");
  if (maxTokens) {
    maxTokens.addEventListener("input", () => {
      layer.max_tokens = parseInt(maxTokens.value) || 4096;
    });
  }

  // Fusion source layers
  const sourceCbs = document.querySelectorAll(".insp-source-cb");
  sourceCbs.forEach(cb => {
    cb.addEventListener("change", () => {
      layer.source_layers = Array.from(document.querySelectorAll(".insp-source-cb:checked")).map(c => c.value);
    });
  });

  // Output
  const formatSel = document.getElementById("insp-format");
  if (formatSel) {
    formatSel.addEventListener("change", () => {
      layer.format = formatSel.value;
      renderBuilderLayers();
    });
  }

  const preservePB = document.getElementById("insp-preserve-page-breaks");
  if (preservePB) {
    preservePB.addEventListener("change", () => {
      layer.preserve_page_breaks = preservePB.checked;
    });
  }
}

function renderBackendHealthIndicator(selectEl) {
  // Add a small health indicator line above the select
  const existing = selectEl.parentElement.querySelector(".backend-health-indicators");
  if (existing) existing.remove();

  const div = document.createElement("div");
  div.className = "backend-health-indicators";
  div.style.cssText = "display:flex;gap:10px;margin-bottom:6px;flex-wrap:wrap;";

  const backends = ["openrouter", "codex", "gemini", "claude"];
  backends.forEach(b => {
    const health = state.backendHealth[b];
    let dotClass = "unknown";
    if (health === true) dotClass = "healthy";
    else if (health === false) dotClass = "unhealthy";

    const span = document.createElement("span");
    span.style.cssText = "font-size:11px;color:var(--text3);display:flex;align-items:center;gap:3px;";
    span.innerHTML = `<span class="backend-health-dot ${dotClass}"></span>${b}`;
    div.appendChild(span);
  });

  selectEl.parentElement.insertBefore(div, selectEl);
}

// ── Build pipeline config JSON ──────────────────────────────────────────────

function buildPipelineConfig() {
  const config = {
    name: state.pipeline.name || "Untitled Pipeline",
    max_workers: state.pipeline.max_workers || 4,
    chunking: {
      strategy: "page",
      chunk_size: 1,
      overlap: 0,
    },
    layers: state.pipeline.layers.map(layer => {
      const out = { id: layer.id, type: layer.type };

      switch (layer.type) {
        case "ocr":
          out.engine = layer.engine;
          out.options = {
            languages: (layer.languages || "eng").split(",").map(s => s.trim()).filter(Boolean),
          };
          break;

        case "llm":
          out.backend = layer.backend;
          if (layer.backend === "openrouter" && layer.model) {
            out.model = layer.model;
          }
          if (layer.prompt_file) out.prompt_file = layer.prompt_file;
          out.options = {};
          if (layer.include_image) out.options.include_image = true;
          if (layer.temperature != null) out.options.temperature = layer.temperature;
          if (layer.max_tokens) out.options.max_tokens = layer.max_tokens;
          break;

        case "fusion":
          out.source_layers = layer.source_layers || [];
          out.backend = layer.backend;
          if (layer.backend === "openrouter" && layer.model) {
            out.model = layer.model;
          }
          if (layer.prompt_file) out.prompt_file = layer.prompt_file;
          out.options = {};
          if (layer.temperature != null) out.options.temperature = layer.temperature;
          if (layer.max_tokens) out.options.max_tokens = layer.max_tokens;
          break;

        case "output":
          out.format = layer.format || "txt";
          out.options = {};
          if (layer.preserve_page_breaks) out.options.preserve_page_breaks = true;
          break;

        default:
          out.options = {};
      }

      return out;
    }),
  };

  return config;
}

// ── Run Pipeline ────────────────────────────────────────────────────────────

async function runPipeline() {
  const pdfInput = document.getElementById("builder-pdf");
  if (!pdfInput.files.length) {
    toast("Please select a PDF file", "error");
    return;
  }

  if (state.pipeline.layers.length === 0) {
    toast("Add at least one layer to the pipeline", "error");
    return;
  }

  const btn = document.getElementById("btn-run-pipeline");
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span> Starting...';

  try {
    const config = buildPipelineConfig();
    const form = new FormData();
    form.append("pdf", pdfInput.files[0]);
    form.append("pipeline_json", JSON.stringify(config));
    form.append("workers", String(state.pipeline.max_workers));

    const res = await fetch("/api/runs", { method: "POST", body: form });
    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || "Failed to start job");
    }
    const data = await res.json();
    state.currentRunId = data.run_id;

    toast("Pipeline started!", "success");

    // Switch to live detail view
    document.getElementById("view-builder").style.display = "none";
    document.getElementById("view-detail").style.display = "";
    document.getElementById("detail-output-section").style.display = "none";
    state.liveChunks = {};
    state.liveJobId = null;

    // Show placeholder
    document.getElementById("detail-header").innerHTML = `
      <h2><span class="spinner"></span> Initializing pipeline...</h2>
      <span class="job-badge running">running</span>
    `;
    document.getElementById("detail-layers").innerHTML = "";
    document.getElementById("detail-chunks").innerHTML = "";
    document.getElementById("detail-bar").querySelector(".progress-fill").style.width = "0%";
    document.getElementById("detail-bar-label").textContent = "Waiting for pipeline to start...";

    connectWebSocket(data.run_id);
  } catch (e) {
    toast(e.message, "error");
  } finally {
    btn.disabled = false;
    btn.textContent = "Run Pipeline";
  }
}

// ── Save Pipeline ───────────────────────────────────────────────────────────

async function savePipeline() {
  const name = state.pipeline.name || "Untitled Pipeline";
  const saveName = prompt("Save pipeline as:", name);
  if (!saveName) return;

  state.pipeline.name = saveName;
  document.getElementById("builder-name").value = saveName;

  try {
    const config = buildPipelineConfig();
    const encodedName = encodeURIComponent(saveName);
    const res = await fetch(`/api/pipelines/${encodedName}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(config),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || "Failed to save pipeline");
    }
    toast(`Pipeline "${saveName}" saved`, "success");
  } catch (e) {
    toast(e.message, "error");
  }
}

// ── Load Pipeline ───────────────────────────────────────────────────────────

async function loadPipeline() {
  document.getElementById("modal-backdrop").style.display = "flex";
  document.getElementById("modal-settings").style.display = "none";
  document.getElementById("modal-load-pipeline").style.display = "";

  const listEl = document.getElementById("load-pipeline-list");
  listEl.innerHTML = '<p class="field-hint">Loading...</p>';

  try {
    const pipelines = await api("/api/pipelines");

    if (!Array.isArray(pipelines) || pipelines.length === 0) {
      listEl.innerHTML = '<p class="field-hint">No saved pipelines found.</p>';
      return;
    }

    listEl.innerHTML = pipelines.map(p => {
      const name = typeof p === "string" ? p : (p.name || p);
      const layerCount = p.layers ? p.layers.length : "";
      const meta = layerCount ? `${layerCount} layers` : "";
      return `
        <div class="load-pipeline-item" onclick="doLoadPipeline('${esc(typeof p === "string" ? p : p.name)}')">
          <span class="load-pipeline-item-name">${esc(typeof p === "string" ? p : p.name)}</span>
          <span class="load-pipeline-item-meta">${meta}</span>
        </div>`;
    }).join("");
  } catch (e) {
    listEl.innerHTML = `<p class="field-hint">Failed to load pipelines: ${esc(e.message)}</p>`;
  }
}

async function doLoadPipeline(name) {
  closeModals();

  try {
    const encodedName = encodeURIComponent(name);
    const config = await api(`/api/pipelines/${encodedName}`);
    populateBuilderFromConfig(config);
    toast(`Pipeline "${name}" loaded`, "success");
  } catch (e) {
    toast("Failed to load pipeline: " + e.message, "error");
  }
}

function populateBuilderFromConfig(config) {
  state.pipeline.name = config.name || "Untitled Pipeline";
  state.pipeline.max_workers = config.max_workers || 4;
  state.pipeline.layers = (config.layers || []).map(l => {
    return {
      id: l.id || generateLayerId(l.type),
      type: l.type,
      engine: l.engine || "",
      backend: l.backend || "",
      model: l.model || "",
      prompt_file: l.prompt_file || "",
      include_image: (l.options && l.options.include_image) || false,
      temperature: (l.options && l.options.temperature != null) ? l.options.temperature : 0.3,
      max_tokens: (l.options && l.options.max_tokens) || 4096,
      source_layers: l.source_layers || [],
      format: l.format || "",
      preserve_page_breaks: (l.options && l.options.preserve_page_breaks) || false,
      options: l.options || {},
      languages: (l.options && l.options.languages) ? (Array.isArray(l.options.languages) ? l.options.languages.join(",") : l.options.languages) : "eng",
    };
  });

  state.selectedLayerIndex = state.pipeline.layers.length > 0 ? 0 : -1;

  // Sync UI
  document.getElementById("builder-name").value = state.pipeline.name;
  document.getElementById("builder-workers").value = state.pipeline.max_workers;
  document.getElementById("builder-workers-val").textContent = state.pipeline.max_workers;

  renderBuilderLayers();
  renderInspector();
}

// ── Helpers ─────────────────────────────────────────────────────────────────

async function searchModels(query) {
  try {
    const models = await api(`/api/openrouter/models?q=${encodeURIComponent(query)}`);
    const datalist = document.getElementById("openrouter-models");
    if (datalist) {
      datalist.innerHTML = models.map(m =>
        `<option value="${esc(m.id)}">${esc(m.name)}${m.context_length ? ` (${(m.context_length/1000).toFixed(0)}k ctx)` : ""}</option>`
      ).join("");
    }
  } catch (e) {
    // Silently fail — model search is optional
  }
}

async function api(url) {
  const res = await fetch(url);
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.detail || `HTTP ${res.status}`);
  }
  return res.json();
}

function esc(s) {
  if (!s) return "";
  const d = document.createElement("div");
  d.textContent = String(s);
  return d.innerHTML;
}

function formatBytes(bytes) {
  if (bytes < 1024) return bytes + " B";
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + " KB";
  return (bytes / (1024 * 1024)).toFixed(1) + " MB";
}

function formatTime(iso) {
  if (!iso) return "";
  try {
    const d = new Date(iso);
    const now = new Date();
    const diff = now - d;
    if (diff < 60_000) return "just now";
    if (diff < 3600_000) return Math.floor(diff / 60_000) + "m ago";
    if (diff < 86400_000) return Math.floor(diff / 3600_000) + "h ago";
    return d.toLocaleDateString();
  } catch {
    return iso;
  }
}

function toast(message, type = "info") {
  const container = document.getElementById("toast-container");
  if (!container) return;
  const el = document.createElement("div");
  el.className = `toast ${type}`;
  el.textContent = message;
  container.appendChild(el);
  setTimeout(() => {
    el.style.opacity = "0";
    el.style.transition = "opacity 0.3s";
    setTimeout(() => el.remove(), 300);
  }, 4000);
}
