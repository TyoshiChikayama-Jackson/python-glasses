const JOBS_POLL_MS = 3000;

const TERMINAL_STATES = new Set(["complete", "failed"]);

let jobsPollHandle = null;
let selectedFile = null;
let currentFindingsJobId = null;

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

function formatMMSS(totalSeconds) {
  const total = Math.max(0, Math.round(totalSeconds || 0));
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

function badgeClassForStatus(status) {
  if (status === "FAIL") return "badge-high";
  if (status === "CAUTION") return "badge-review";
  return "badge-pass";
}

// ---------------------------------------------------------------------
// View switching
// ---------------------------------------------------------------------

function showView(viewName) {
  document.querySelectorAll(".view").forEach((el) => el.classList.add("hidden"));
  document.getElementById(`view-${viewName}`).classList.remove("hidden");

  document.querySelectorAll(".view-tab").forEach((tab) => {
    tab.classList.toggle("active", tab.dataset.view === viewName);
  });

  if (viewName === "jobs") {
    loadJobs();
    ensureJobsPolling();
  } else if (viewName === "reports") {
    loadReports();
  }
}

document.getElementById("nav-upload").addEventListener("click", () => showView("upload"));
document.getElementById("nav-jobs").addEventListener("click", () => showView("jobs"));
document.getElementById("nav-reports").addEventListener("click", () => showView("reports"));

// ---------------------------------------------------------------------
// Upload view — drop zone + form validation
// ---------------------------------------------------------------------

const dropZone = document.getElementById("drop-zone");
const fileInput = document.getElementById("file-input");
const selectedFileEl = document.getElementById("selected-file");
const startAnalysisBtn = document.getElementById("btn-start-analysis");

const uploadProjectInput = document.getElementById("upload-project");
const uploadAddressInput = document.getElementById("upload-address");
const uploadInspectorInput = document.getElementById("upload-inspector");

const ACCEPTED_EXTENSIONS = [".mp4", ".mov", ".avi"];

function isAcceptedVideoFile(file) {
  const name = file.name.toLowerCase();
  return ACCEPTED_EXTENSIONS.some((ext) => name.endsWith(ext));
}

function updateStartButtonState() {
  const hasFile = !!selectedFile;
  const hasFields =
    uploadProjectInput.value.trim() &&
    uploadAddressInput.value.trim() &&
    uploadInspectorInput.value.trim();
  startAnalysisBtn.disabled = !(hasFile && hasFields);
}

function setSelectedFile(file) {
  if (!file) return;

  if (!isAcceptedVideoFile(file)) {
    showToast("Unsupported file type. Please choose a .mp4, .mov, or .avi file.", "error");
    return;
  }

  selectedFile = file;
  selectedFileEl.textContent = `${file.name} (${(file.size / (1024 * 1024)).toFixed(1)} MB)`;
  selectedFileEl.classList.remove("hidden");
  dropZone.classList.add("has-file");
  updateStartButtonState();
}

dropZone.addEventListener("click", () => fileInput.click());

fileInput.addEventListener("change", () => {
  if (fileInput.files.length > 0) {
    setSelectedFile(fileInput.files[0]);
  }
});

["dragenter", "dragover"].forEach((eventName) => {
  dropZone.addEventListener(eventName, (e) => {
    e.preventDefault();
    e.stopPropagation();
    dropZone.classList.add("drag-active");
  });
});

["dragleave", "drop"].forEach((eventName) => {
  dropZone.addEventListener(eventName, (e) => {
    e.preventDefault();
    e.stopPropagation();
    dropZone.classList.remove("drag-active");
  });
});

dropZone.addEventListener("drop", (e) => {
  const files = e.dataTransfer.files;
  if (files.length > 0) {
    setSelectedFile(files[0]);
  }
});

[uploadProjectInput, uploadAddressInput, uploadInspectorInput].forEach((input) => {
  input.addEventListener("input", updateStartButtonState);
});

function resetUploadForm() {
  selectedFile = null;
  fileInput.value = "";
  selectedFileEl.classList.add("hidden");
  selectedFileEl.textContent = "";
  dropZone.classList.remove("has-file");
  uploadProjectInput.value = "";
  uploadAddressInput.value = "";
  uploadInspectorInput.value = "";
  document.getElementById("upload-progress-row").classList.add("hidden");
  document.getElementById("upload-progress-fill").style.width = "0%";
  updateStartButtonState();
}

startAnalysisBtn.addEventListener("click", () => {
  if (!selectedFile) return;

  const formData = new FormData();
  formData.append("file", selectedFile);
  formData.append("project_name", uploadProjectInput.value.trim());
  formData.append("address", uploadAddressInput.value.trim());
  formData.append("inspector_name", uploadInspectorInput.value.trim());

  const progressRow = document.getElementById("upload-progress-row");
  const progressFill = document.getElementById("upload-progress-fill");
  const progressLabel = document.getElementById("upload-progress-label");

  progressRow.classList.remove("hidden");
  startAnalysisBtn.disabled = true;

  const xhr = new XMLHttpRequest();
  xhr.open("POST", "/api/upload");

  xhr.upload.addEventListener("progress", (e) => {
    if (e.lengthComputable) {
      const pct = Math.round((e.loaded / e.total) * 100);
      progressFill.style.width = `${pct}%`;
      progressLabel.textContent = `Uploading... ${pct}%`;
    }
  });

  xhr.onload = () => {
    let data;
    try {
      data = JSON.parse(xhr.responseText);
    } catch (err) {
      showToast(`Server returned an unexpected response (HTTP ${xhr.status}).`, "error");
      startAnalysisBtn.disabled = false;
      return;
    }

    if (xhr.status >= 200 && xhr.status < 300 && data.ok) {
      showToast("Upload complete — analysis started", "success");
      resetUploadForm();
      showView("jobs");
    } else {
      showToast(data.error || "Could not start analysis.", "error");
      startAnalysisBtn.disabled = false;
    }
  };

  xhr.onerror = () => {
    showToast("Could not reach the server. Is app.py still running?", "error");
    startAnalysisBtn.disabled = false;
  };

  progressLabel.textContent = "Uploading... 0%";
  xhr.send(formData);
});

// ---------------------------------------------------------------------
// Jobs view
// ---------------------------------------------------------------------

function renderJobCard(job) {
  const card = document.createElement("div");
  card.className = "job-card";

  const sourceFilename = job.source_filename || "recording";
  const projectName = job.project_name || "Untitled project";
  const submittedAt = job.submitted_at
    ? new Date(job.submitted_at).toLocaleString()
    : "";
  const progress = job.progress != null ? job.progress : 0;
  const stageLabel = job.stage_label || job.state;

  let stageHtml = `<div class="job-stage">${escapeHtml(stageLabel)}</div>`;
  if (job.state === "failed" && job.error) {
    stageHtml = `<div class="job-stage job-stage-failed">${escapeHtml(stageLabel)}: ${escapeHtml(job.error)}</div>`;
  }

  let resultsHtml = "";
  if (job.state === "complete" && job.findings_summary) {
    const s = job.findings_summary;
    resultsHtml = `
      <div class="job-findings-summary">
        <span class="badge badge-high">${s.fail} FAIL</span>
        <span class="badge badge-review">${s.caution} CAUTION</span>
        <span class="badge badge-pass">${s.pass} PASS</span>
      </div>
      <div class="job-card-actions">
        <button class="btn btn-outline btn-view-findings" data-job-id="${job.job_id}">View Findings</button>
        <button class="btn btn-secondary btn-view-report" data-job-id="${job.job_id}">View Report</button>
      </div>
    `;
  }

  card.innerHTML = `
    <div class="job-card-top">
      <div>
        <div class="job-filename">${escapeHtml(sourceFilename)}</div>
        <div class="job-project">${escapeHtml(projectName)}</div>
      </div>
      <div class="job-submitted">${escapeHtml(submittedAt)}</div>
    </div>
    <div class="progress-bar">
      <div class="progress-fill ${job.state === 'failed' ? 'progress-fill-failed' : ''}" style="width: ${progress}%"></div>
    </div>
    ${stageHtml}
    ${resultsHtml}
  `;

  const viewFindingsBtn = card.querySelector(".btn-view-findings");
  if (viewFindingsBtn) {
    viewFindingsBtn.addEventListener("click", () => openFindingsView(job.job_id));
  }

  const viewReportBtn = card.querySelector(".btn-view-report");
  if (viewReportBtn) {
    viewReportBtn.addEventListener("click", () => {
      const url = job.report_url || `/api/jobs/${job.job_id}`;
      if (job.report_url) {
        openViewReportModal(job.report_url);
      } else {
        fetch(`/api/jobs/${job.job_id}`)
          .then((res) => res.json())
          .then((data) => {
            if (data.report_url) openViewReportModal(data.report_url);
            else showToast("Report not available yet.", "error");
          });
      }
    });
  }

  return card;
}

async function loadJobs() {
  try {
    const res = await fetch("/api/jobs");
    const data = await res.json();
    const jobList = data.jobs || [];

    const listEl = document.getElementById("jobs-list");
    listEl.innerHTML = "";

    if (jobList.length === 0) {
      listEl.innerHTML = '<p class="jobs-empty">No jobs yet. Upload a walkthrough recording to begin.</p>';
    } else {
      jobList.forEach((job) => listEl.appendChild(renderJobCard(job)));
    }

    const stillRunning = jobList.some((job) => !TERMINAL_STATES.has(job.state));
    if (!stillRunning && jobsPollHandle) {
      clearInterval(jobsPollHandle);
      jobsPollHandle = null;
    } else if (stillRunning) {
      ensureJobsPolling();
    }
  } catch (err) {
    // ignore, retry next tick
  }
}

function ensureJobsPolling() {
  if (jobsPollHandle) return;
  jobsPollHandle = setInterval(loadJobs, JOBS_POLL_MS);
}

// ---------------------------------------------------------------------
// Findings view
// ---------------------------------------------------------------------

const FINDING_TYPE_LABELS = {
  confirmed: "Confirmed",
  unconfirmed: "Stated, not visually confirmed",
  unmentioned: "Detected, not mentioned",
};

function renderFindingCard(finding) {
  const card = document.createElement("div");
  card.className = "finding-card";

  const status = finding.status || "PASS";
  const typeLabel = FINDING_TYPE_LABELS[finding.finding_type] || finding.finding_type || "";

  let excerptHtml = "";
  if (finding.transcript_excerpt) {
    excerptHtml = `<p class="finding-excerpt">"${escapeHtml(finding.transcript_excerpt)}"</p>`;
  }

  let imageHtml = "";
  if (finding.annotated_frame_url) {
    imageHtml = `<img class="finding-image" src="${finding.annotated_frame_url}" alt="Annotated frame">`;
  }

  let detectionsHtml = "";
  const detections = finding.detections || [];
  if (detections.length > 0) {
    const items = detections
      .map((d) => `<span class="detection-chip">${escapeHtml(d.class)}: ${d.confidence}%</span>`)
      .join("");
    detectionsHtml = `<div class="finding-detections">${items}</div>`;
  }

  let tradeHtml = "";
  if (finding.trade_responsible) {
    tradeHtml = `<div class="finding-trade">Trade: ${escapeHtml(finding.trade_responsible)}</div>`;
  }

  card.innerHTML = `
    <div class="finding-top-row">
      <span class="badge ${badgeClassForStatus(status)}">${escapeHtml(status)}</span>
      <span class="finding-type-label">${escapeHtml(typeLabel)}</span>
      <span class="finding-timestamp">${formatMMSS(finding.timestamp)}</span>
    </div>
    <h3 class="finding-name">${escapeHtml(finding.violation_name || "Unspecified issue")}</h3>
    ${excerptHtml}
    ${imageHtml}
    ${detectionsHtml}
    ${tradeHtml}
  `;

  return card;
}

async function openFindingsView(jobId) {
  currentFindingsJobId = jobId;
  document.getElementById("transcript-panel").classList.add("hidden");
  document.getElementById("btn-toggle-transcript").textContent = "Show Full Transcript";

  try {
    const [jobRes, findingsRes] = await Promise.all([
      fetch(`/api/jobs/${jobId}`),
      fetch(`/api/jobs/${jobId}/findings`),
    ]);
    const jobData = await jobRes.json();
    const findingsData = await findingsRes.json();

    document.getElementById("findings-project-name").textContent =
      jobData.project_name || "Project";

    const findings = findingsData.findings || [];
    const summary = jobData.findings_summary || {
      fail: findings.filter((f) => f.status === "FAIL").length,
      caution: findings.filter((f) => f.status === "CAUTION").length,
      pass: findings.filter((f) => f.status === "PASS").length,
    };

    const overallStatus = summary.fail > 0 ? "FAIL" : summary.caution > 0 ? "CAUTION" : "PASS";
    const overallBadge = document.getElementById("findings-overall-badge");
    overallBadge.textContent = overallStatus;
    overallBadge.className = `badge ${badgeClassForStatus(overallStatus)}`;

    document.getElementById("findings-counts").textContent =
      `${summary.fail} FAIL · ${summary.caution} CAUTION · ${summary.pass} PASS`;

    const listEl = document.getElementById("findings-list");
    listEl.innerHTML = "";
    findings.forEach((finding) => listEl.appendChild(renderFindingCard(finding)));

    showView("findings");
  } catch (err) {
    showToast("Could not load findings for this job.", "error");
  }
}

document.getElementById("btn-findings-back").addEventListener("click", () => {
  showView("jobs");
});

document.getElementById("btn-toggle-transcript").addEventListener("click", async () => {
  const panel = document.getElementById("transcript-panel");
  const btn = document.getElementById("btn-toggle-transcript");

  if (!panel.classList.contains("hidden")) {
    panel.classList.add("hidden");
    btn.textContent = "Show Full Transcript";
    return;
  }

  if (!currentFindingsJobId) return;

  try {
    const res = await fetch(`/api/jobs/${currentFindingsJobId}/transcript`);
    const data = await res.json();
    const segments = data.segments || [];

    if (segments.length === 0) {
      panel.innerHTML = '<p class="transcript-empty">No transcript available for this walkthrough.</p>';
    } else {
      panel.innerHTML = segments
        .map((seg) => `
          <div class="transcript-line">
            <span class="transcript-timestamp">${formatMMSS(seg.start)}</span>
            <span class="transcript-text">${escapeHtml(seg.text)}</span>
          </div>
        `)
        .join("");
    }

    panel.classList.remove("hidden");
    btn.textContent = "Hide Full Transcript";
  } catch (err) {
    showToast("Could not load transcript.", "error");
  }
});

// ---------------------------------------------------------------------
// Reports view
// ---------------------------------------------------------------------

function renderReportCard(report) {
  const card = document.createElement("div");
  card.className = "report-card";
  card.innerHTML = `
    <div class="report-card-date">${escapeHtml(report.project_name || "Untitled project")}</div>
    <div class="report-card-meta">${escapeHtml(report.generated_at)} &middot; ${report.size_kb} KB</div>
    <div class="report-card-actions">
      <button class="btn btn-outline btn-view-report">View</button>
      <a class="btn btn-secondary btn-download-report" href="${report.url}" download>Download</a>
    </div>
  `;

  card.querySelector(".btn-view-report").addEventListener("click", () => {
    openViewReportModal(report.url);
  });

  return card;
}

async function loadReports() {
  try {
    const res = await fetch("/api/reports");
    const data = await res.json();
    const reports = data.reports || [];

    const listEl = document.getElementById("reports-list");
    listEl.innerHTML = "";

    if (reports.length === 0) {
      listEl.innerHTML = '<p class="reports-empty">No reports generated yet. Complete a job to generate your first report.</p>';
      return;
    }

    reports.forEach((report) => {
      listEl.appendChild(renderReportCard(report));
    });
  } catch (err) {
    // ignore, retry next tick
  }
}

// ---------------------------------------------------------------------
// View Report modal
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
// Initial state
// ---------------------------------------------------------------------

updateStartButtonState();
