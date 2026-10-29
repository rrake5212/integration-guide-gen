const form = document.getElementById('resolve-form');
const input = document.getElementById('query');
const button = document.getElementById('resolve-button');
const resolutionEl = document.getElementById('resolution');
const errorEl = document.getElementById('error');

function showError(message) {
  errorEl.textContent = message;
  errorEl.hidden = false;
}
function clearError() {
  errorEl.hidden = true;
  errorEl.textContent = '';
}
function setBusy(busy, label = 'Resolve ↗') {
  button.disabled = busy;
  button.textContent = busy ? 'Reading…' : label;
}
function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}
async function postJSON(url, body) {
  const response = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  let data = {};
  try { data = await response.json(); } catch (_) {}
  return { response, data };
}
async function createGuide(query, pick) {
  setBusy(true, 'Starting…');
  try {
    const { response, data } = await postJSON('/api/guides', pick ? { input: query, pick } : { input: query });
    if (!response.ok) {
      if (data.candidates) {
        renderCandidates(data.candidates, query);
        showError(data.error?.message || 'That name is ambiguous.');
      } else {
        showError(data.error?.message || 'Could not start the guide.');
      }
      setBusy(false);
      return;
    }
    window.location.href = '/g/' + data.job_id;
  } catch (err) {
    showError('The service did not answer. Try again.');
    setBusy(false);
  }
}
function renderCandidates(candidates, query) {
  resolutionEl.hidden = false;
  resolutionEl.replaceChildren();
  const card = el('div', 'resolution-card');
  card.append(el('p', 'kicker-small', 'Ambiguous name'));
  card.append(el('h2', '', 'Pick the right provider'));
  card.append(el('p', 'mono', 'We will not guess between these.'));
  const list = el('div', 'candidates');
  candidates.forEach((candidate) => {
    const row = el('button', 'candidate');
    row.type = 'button';
    const left = el('div');
    left.append(el('strong', '', candidate.name));
    left.append(el('div', 'mono', candidate.id + (candidate.domain ? ' · ' + candidate.domain : '')));
    row.append(left);
    const badge = el('span', 'badge badge--' + (candidate.availability || 'unknown'),
      (candidate.availability || 'unknown').replaceAll('_', ' '));
    row.append(badge);
    row.addEventListener('click', () => createGuide(query, candidate.id));
    list.append(row);
  });
  card.append(list);
  resolutionEl.append(card);
}
function renderResolved(data) {
  resolutionEl.hidden = false;
  resolutionEl.replaceChildren();
  const card = el('div', 'resolution-card');
  const top = el('div', 'resolution-top');
  const left = el('div');
  left.append(el('p', 'kicker-small',
    data.decision === 'catalogue' ? 'Matched from the toolkit index' : 'Resolved'));
  left.append(el('h2', '', data.provider.name));
  left.append(el('p', 'mono', data.provider.id + ' · ' + data.confidence + ' confidence · ' + data.matched_by));
  top.append(left);
  const badge = el('span', 'badge badge--' + (data.availability || 'unknown'),
    (data.availability || 'unknown').replaceAll('_', ' '));
  top.append(badge);
  card.append(top);
  const actions = el('div', 'resolution-actions');
  const go = el('button', 'btn', data.can_build === false ? 'Docs not available yet' : 'Generate setup guide');
  go.type = 'button';
  go.disabled = data.can_build === false;
  go.addEventListener('click', () => createGuide(data.input));
  actions.append(go);
  if (data.provider.console) {
    const consoleLink = el('a', 'link', 'Console ↗');
    consoleLink.href = data.provider.console;
    consoleLink.target = '_blank';
    consoleLink.rel = 'noopener noreferrer';
    actions.append(consoleLink);
  }
  if (data.provider.docs?.length) {
    const docsLink = el('a', 'link', 'Provider docs ↗');
    docsLink.href = data.provider.docs[0];
    docsLink.target = '_blank';
    docsLink.rel = 'noopener noreferrer';
    actions.append(docsLink);
  }
  card.append(actions);
  resolutionEl.append(card);
}
async function resolveQuery(query) {
  clearError();
  resolutionEl.hidden = true;
  setBusy(true);
  try {
    const { response, data } = await postJSON('/api/resolve', { input: query });
    if (!response.ok) {
      showError(data.error?.message || 'Could not resolve that.');
      setBusy(false);
      return;
    }
    if (data.decision === 'resolved' || data.decision === 'catalogue') {
      renderResolved(data);
    } else if (data.decision === 'ask') {
      renderCandidates(data.candidates || [], query);
    } else {
      showError('We could not match that to a provider. Try the app’s website address.');
    }
  } catch (err) {
    showError('The service did not answer. Try again.');
  }
  setBusy(false);
}
form.addEventListener('submit', (event) => {
  event.preventDefault();
  const query = input.value.trim();
  if (!query) return;
  resolveQuery(query);
});
document.querySelectorAll('[data-example]').forEach((chip) => {
  chip.addEventListener('click', () => {
    input.value = chip.dataset.example;
    resolveQuery(input.value);
  });
});
input.focus();
