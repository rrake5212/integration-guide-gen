const jobId = window.GUIDEGEN?.jobId;
const status = window.GUIDEGEN?.status;
const tabs = Array.from(document.querySelectorAll('.tab'));
const panels = Array.from(document.querySelectorAll('[data-panel]'));
const copyButton = document.getElementById('copy-button');
const downloadLink = document.getElementById('download-link');

const downloads = {
  human: `/g/${jobId}/guide.md`,
  json: `/g/${jobId}/agent.json`,
  md: `/g/${jobId}/agent.md`,
};
let activeTab = 'human';

function selectTab(name) {
  activeTab = name;
  tabs.forEach((tab) => tab.setAttribute('aria-selected', String(tab.dataset.tab === name)));
  panels.forEach((panel) => { panel.hidden = panel.dataset.panel !== name; });
  if (downloadLink) downloadLink.href = downloads[name];
  if (copyButton) copyButton.textContent = 'Copy';
}
tabs.forEach((tab) => tab.addEventListener('click', () => selectTab(tab.dataset.tab)));
if (downloadLink) downloadLink.href = downloads[activeTab];

if (copyButton) {
  copyButton.addEventListener('click', async () => {
    const panel = document.querySelector(`[data-panel="${activeTab}"]`);
    const text = activeTab === 'human' ? panel?.innerText || '' : panel?.textContent || '';
    try {
      await navigator.clipboard.writeText(text);
      copyButton.textContent = 'Copied';
      setTimeout(() => { copyButton.textContent = 'Copy'; }, 1600);
    } catch (_) {
      copyButton.textContent = 'Select manually';
      setTimeout(() => { copyButton.textContent = 'Copy'; }, 2000);
    }
  });
}

const progress = document.getElementById('progress');
if (progress && (status === 'queued' || status === 'running')) {
  const stageEl = document.getElementById('progress-stage');
  const noteEl = document.getElementById('progress-note');
  const fillEl = document.getElementById('progress-fill');
  const pctEl = document.getElementById('progress-pct');
  let source = null;
  const update = (job) => {
    if (stageEl) stageEl.textContent = job.message || job.stage || 'Working';
    if (noteEl) noteEl.textContent = `Stage: ${job.stage || 'starting'} · ${job.progress || 0}%`;
    if (fillEl) fillEl.style.width = `${Math.max(2, job.progress || 0)}%`;
    if (pctEl) pctEl.textContent = `${job.progress || 0}%`;
    if (job.status === 'done' || job.status === 'failed') {
      if (source) source.close();
      setTimeout(() => window.location.reload(), 450);
    }
  };
  try {
    source = new EventSource(`/api/guides/${jobId}/events`);
    source.onmessage = (event) => {
      try { update(JSON.parse(event.data)); } catch (_) {}
    };
    source.onerror = () => {
      if (source) source.close();
      setTimeout(() => window.location.reload(), 2500);
    };
  } catch (_) {
    setTimeout(() => window.location.reload(), 2500);
  }
}
