const TRANSCRIPTION_POLL_MS = 500;
const REPORTS_POLL_MS = 10000;

let isInspecting = false;
let reportsViewOpen = false;
let reportsPollHandle = null;

// ---------------------------------------------------------------------
// Toast
// ---------------------------------------------------------------------

function showToast(message, kind) {
  const toast = document.getElementById("toast");
  toast.textContent = message;
  toast.className = "toast";
  if (kind === "error") toast.classList.add("toast-error");
  if (kind === "success") toast.classList.add("toast-success");
  clearTimeout(showToast._t);
  showToast._t = setTimeout(() => toast.classList.add("hidden"), 3200);
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}

// ---------------------------------------------------------------------
// Live transcription bar (Petra wake word -> Whisper transcription)
// ---------------------------------------------------------------------

async function pollTranscription() {
  if (!isInspecting) {
    // Not inspecting — keep the bar hidden and don't bother polling.
    const bar = document.getElementById("transcription-bar");
    bar.classList.add("hidden");
    return;
  }

  try {
    const res = await fetch("/api/transcription");
    const data = await res.json();

    const bar = document.getElementById("transcription-bar");
    const label = document.getElementById("transcription-label");
    const textEl = document.getElementById("transcription-text");

    bar.classList.remove("processing", "complete");

    if (data.status === "idle") {
      bar.classList.add("hidden");
      return;
    }

    bar.classList.remove("hidden");

    if (data.status === "listening") {
      label.textContent = "Listening...";
      textEl.textContent = "";
    } else if (data.status === "processing") {
      bar.classList.add("processing");
      label.textContent = "Processing...";
      textEl.textContent = "";
    } else if (data.status === "complete") {
      bar.classList.add("complete");
      label.textContent = "Petra heard:";
      textEl.textContent = data.text || "";
    }
  } catch (err) {
    // ignore, retry next tick
  }
}

// ---------------------------------------------------------------------
// Reports library
// ---------------------------------------------------------------------

function renderReportCard(report) {
  const card = document.createElement("div");
  card.className = "report-card";
  card.innerHTML = `
    <div class="report-card-date">${escapeHtml(report.generated_at)}</div>
    <div class="report-card-meta">${report.size_kb} KB &middot; ${escapeHtml(report.filename)}</div>
    <div class="report-card-actions">
      <button class="btn btn-outline btn-view-report" data-url="${report.url}">View</button>
      <a class="btn btn-secondary btn-download-report" href="${report.url}" download>Download</a>
    </div>
  `;

  card.querySelector(".btn-view-report").addEventListener("click", () => {
    openViewReportModal(report.url);
  });

  return card;
}

async function loadReportsList() {
  try {
    const res = await fetch("/api/reports");
    const data = await res.json();
    const reports = data.reports || [];

    const listEl = document.getElementById("reports-list");
    listEl.innerHTML = "";

    if (reports.length === 0) {
      listEl.innerHTML = '<p class="reports-empty">No reports generated yet. Complete an inspection and generate your first report.</p>';
      return;
    }

    reports.forEach((report) => {
      listEl.appendChild(renderReportCard(report));
    });
  } catch (err) {
    // ignore, retry next tick
  }
}

// Diagnostic helper: pings the server for the voice thread's actual
// alive/dead state, tagged with a label, and logs it in the browser
// console. The server also prints the same check to its own terminal
// (see /api/voice/status) so both sides of the app confirm the same
// moment in time.
async function logVoiceStatus(label) {
  try {
    const res = await fetch(`/api/voice/status?label=${encodeURIComponent(label)}`);
    const data = await res.json();
    console.log(`[voice-diagnostic] ${label}: alive=${data.alive}, should_run=${data.should_run}`);
  } catch (err) {
    console.log(`[voice-diagnostic] ${label}: could not reach server`);
  }
}

function showReportsView() {
  logVoiceStatus("Reports tab clicked");

  document.getElementById("dashboard-view").classList.add("hidden");
  document.getElementById("reports-view").classList.remove("hidden");
  reportsViewOpen = true;

  loadReportsList();
  if (reportsPollHandle) clearInterval(reportsPollHandle);
  reportsPollHandle = setInterval(loadReportsList, REPORTS_POLL_MS);
}

function hideReportsView() {
  document.getElementById("reports-view").classList.add("hidden");
  document.getElementById("dashboard-view").classList.remove("hidden");
  reportsViewOpen = false;

  if (reportsPollHandle) {
    clearInterval(reportsPollHandle);
    reportsPollHandle = null;
  }

  logVoiceStatus("Navigated back to main dashboard");
}

document.getElementById("btn-reports-tab").addEventListener("click", showReportsView);
document.getElementById("btn-reports-back").addEventListener("click", hideReportsView);

// ---------------------------------------------------------------------
// View Report modal (used by the Reports library's View button)
// ---------------------------------------------------------------------

const viewReportModal = document.getElementById("view-report-modal");

function openViewReportModal(url) {
  document.getElementById("view-report-iframe").src = url;
  document.getElementById("btn-download-view-report").href = url;
  viewReportModal.classList.remove("hidden");
}

function closeViewReportModal() {
  viewReportModal.classList.add("hidden");
  document.getElementById("view-report-iframe").src = "";
}

document.getElementById("btn-close-view-report-x").addEventListener("click", closeViewReportModal);
viewReportModal.addEventListener("click", (e) => {
  if (e.target === viewReportModal) closeViewReportModal();
});

// ---------------------------------------------------------------------
// Top navigation actions
// ---------------------------------------------------------------------

document.getElementById("btn-start").addEventListener("click", async () => {
  const startBtn = document.getElementById("btn-start");

  logVoiceStatus("Start Inspection clicked");

  startBtn.disabled = true;
  startBtn.textContent = "Starting...";

  try {
    const res = await fetch("/api/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    });

    let data;
    try {
      data = await res.json();
    } catch (parseErr) {
      showToast(`Server returned an unexpected response (HTTP ${res.status}).`, "error");
      return;
    }

    if (data.ok) {
      showToast("Inspection started", "success");
    } else {
      showToast(data.error || "Could not start inspection.", "error");
    }
  } catch (err) {
    showToast("Could not reach the server. Is app.py still running?", "error");
  } finally {
    startBtn.disabled = false;
    startBtn.textContent = "Start Inspection";
  }
});

// ---------------------------------------------------------------------
// Generate Report modal (bottom sheet)
// ---------------------------------------------------------------------

const reportModal = document.getElementById("report-modal");

function resetReportSheet() {
  document.getElementById("report-form").classList.remove("hidden");
  document.getElementById("report-viewer").classList.add("hidden");
}

document.getElementById("btn-report").addEventListener("click", () => {
  resetReportSheet();
  reportModal.classList.remove("hidden");
});

function closeReportSheet() {
  reportModal.classList.add("hidden");
}

document.getElementById("btn-close-report-x").addEventListener("click", closeReportSheet);
reportModal.addEventListener("click", (e) => {
  if (e.target === reportModal) closeReportSheet();
});

document.getElementById("btn-generate-report").addEventListener("click", async () => {
  const genBtn = document.getElementById("btn-generate-report");
  const payload = {
    project_name: document.getElementById("report-project").value,
    address: document.getElementById("report-address").value,
    inspector_name: document.getElementById("report-inspector").value,
    notes: document.getElementById("report-notes").value,
  };

  genBtn.disabled = true;
  genBtn.textContent = "Generating...";

  try {
    const res = await fetch("/api/report", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await res.json();

    if (data.ok) {
      document.getElementById("report-form").classList.add("hidden");
      const viewer = document.getElementById("report-viewer");
      viewer.classList.remove("hidden");
      document.getElementById("report-iframe").src = data.view_url;
      document.getElementById("btn-download-report").href = data.download_url;
      showToast("Report generated", "success");
    } else {
      showToast(data.error || "Could not generate report.", "error");
    }
  } catch (err) {
    showToast("Could not reach the server.", "error");
  } finally {
    genBtn.disabled = false;
    genBtn.textContent = "Generate PDF";
  }
});

document.getElementById("btn-new-report").addEventListener("click", resetReportSheet);

// ---------------------------------------------------------------------
// Clear Session modal
// ---------------------------------------------------------------------

const clearModal = document.getElementById("clear-modal");
const deleteConfirmRow = document.getElementById("delete-confirm-row");

document.getElementById("btn-clear").addEventListener("click", () => {
  deleteConfirmRow.classList.add("hidden");
  clearModal.classList.remove("hidden");
});

function closeClearModal() {
  clearModal.classList.add("hidden");
}

document.getElementById("btn-close-modal").addEventListener("click", closeClearModal);
document.getElementById("btn-close-modal-x").addEventListener("click", closeClearModal);
clearModal.addEventListener("click", (e) => {
  if (e.target === clearModal) closeClearModal();
});

document.getElementById("btn-new-inspection").addEventListener("click", async () => {
  try {
    const res = await fetch("/api/session/new", { method: "POST" });
    const data = await res.json();
    if (data.ok) {
      showToast("Started a brand new inspection", "success");
      closeClearModal();
    } else {
      showToast(data.error || "Could not start new inspection.", "error");
    }
  } catch (err) {
    showToast("Could not reach the server.", "error");
  }
});

document.getElementById("btn-continue-inspection").addEventListener("click", async () => {
  await fetch("/api/session/continue", { method: "POST" });
  showToast("Continuing existing inspection", "success");
  closeClearModal();
});

document.getElementById("btn-delete-inspections").addEventListener("click", () => {
  deleteConfirmRow.classList.remove("hidden");
});

document.getElementById("btn-confirm-delete").addEventListener("click", async () => {
  const value = document.getElementById("delete-confirm-input").value;
  try {
    const res = await fetch("/api/session/delete", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ confirm: value }),
    });
    const data = await res.json();
    if (data.ok) {
      showToast(`Deleted ${data.removed} file(s)`, "success");
      closeClearModal();
    } else {
      showToast(data.error || "Confirmation did not match", "error");
    }
  } catch (err) {
    showToast("Could not reach the server.", "error");
  }
  document.getElementById("delete-confirm-input").value = "";
});

// ---------------------------------------------------------------------
// Kick off polling
// ---------------------------------------------------------------------

pollTranscription();
setInterval(pollTranscription, TRANSCRIPTION_POLL_MS);
