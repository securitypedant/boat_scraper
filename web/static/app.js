// Dashboard frontend — debug build
const els = {
  statusDot: document.getElementById('status-dot'),
  statusText: document.getElementById('status-text'),
  uptime: document.getElementById('uptime'),
  lastScraped: document.getElementById('last-scraped'),
  lastAction: document.getElementById('last-action'),
  version: document.getElementById('version'),
  logLevel: document.getElementById('log-level'),
  statTotal: document.getElementById('stat-total'),
  statManufacturers: document.getElementById('stat-manufacturers'),
  statPending: document.getElementById('stat-pending'),
  statDone: document.getElementById('stat-done'),
  statFailed: document.getElementById('stat-failed'),
  scrapeSource: document.getElementById('scrape-source'),
  scrapeAction: document.getElementById('scrape-action'),
  scheduleInterval: document.getElementById('schedule-interval'),
  cbSchedule: document.getElementById('cb-schedule'),
  btnStart: document.getElementById('btn-start'),
  btnStopAll: document.getElementById('btn-stop-all'),
  btnPrescrape: document.getElementById('btn-prescrape'),
  btnWipe: document.getElementById('btn-wipe'),
  btnWipeMfrs: document.getElementById('btn-wipe-mfrs'),
  btnDownload: document.getElementById('btn-download'),
  btnDeleteAll: document.getElementById('btn-delete-all'),
  sourceCards: document.getElementById('source-cards'),
  logs: document.getElementById('logs'),
  results: document.getElementById('results'),
  queryTotal: document.getElementById('query-total'),
  btnQuery: document.getElementById('btn-query'),
  scheduleSource: document.getElementById('schedule-source'),
  scheduleAction: document.getElementById('schedule-action'),
  scheduleMinutes: document.getElementById('schedule-minutes'),
  btnAddSchedule: document.getElementById('btn-add-schedule'),
  schedulesList: document.getElementById('schedules-list'),
};

// Progress bar elements
const prescrapeProgress = document.getElementById('prescrape-progress');
const prescrapeBar = document.getElementById('prescrape-bar');
const prescrapePct = document.getElementById('prescrape-pct');
const prescrapeLabel = document.getElementById('prescrape-label');

let isRunning = false;

// Modal elements
const modal = document.getElementById('edit-modal');
const btnCloseModal = document.getElementById('btn-close-modal');
const btnSaveEdit = document.getElementById('btn-save-edit');
const editStatus = document.getElementById('edit-status');

// Toast
const toastContainer = document.getElementById('toast-container');

// --- Debug helper ---
function dbg(msg) {
  console.debug('[BOAT]', msg);
}

function uiErr(msg) {
  console.error('[BOAT]', msg);
  appendLog('[JS ERROR] ' + msg);
  showToast(msg, 'error');
}

let _lastActionTimer = null;
function setLastAction(msg, ttlSec = 5) {
  if (!els.lastAction) return;
  els.lastAction.textContent = msg;
  clearTimeout(_lastActionTimer);
  if (ttlSec > 0) {
    _lastActionTimer = setTimeout(() => { els.lastAction.textContent = ''; }, ttlSec * 1000);
  }
}

function showToast(msg, type = 'info') {
  if (!toastContainer) return;
  const div = document.createElement('div');
  const bg = type === 'error' ? '#ef4444' : type === 'success' ? '#22c55e' : '#3b82f6';
  div.style.cssText = `background:${bg};color:#fff;padding:10px 14px;border-radius:6px;font-size:.85rem;box-shadow:0 2px 8px rgba(0,0,0,.25);pointer-events:auto;max-width:320px;`;
  div.textContent = msg;
  toastContainer.appendChild(div);
  setTimeout(() => {
    div.style.opacity = '0';
    div.style.transform = 'translateX(20px)';
    div.style.transition = 'opacity .4s,transform .4s';
    setTimeout(() => div.remove(), 400);
  }, 4000);
}

// --- SSE Logs ---
let _activeEvtSource = null;

function connectLogs() {
  if (_activeEvtSource) {
    _activeEvtSource.close();
    _activeEvtSource = null;
  }
  dbg('Connecting SSE...');
  const evtSource = new EventSource('/api/logs');
  _activeEvtSource = evtSource;
  evtSource.onmessage = (e) => {
    const data = JSON.parse(e.data);
    if (data.type === 'log') appendLog(data.line);
  };
  evtSource.onerror = () => {
    dbg('SSE error, reconnecting in 3s...');
    evtSource.close();
    _activeEvtSource = null;
    setTimeout(connectLogs, 3000);
  };
}

function appendLog(line) {
  const div = document.createElement('div');
  div.className = 'log-line';
  if (line.includes('Error') || line.includes('error') || line.includes('ERROR')) div.classList.add('error');
  else if (line.includes('Warning') || line.includes('WARN')) div.classList.add('warn');
  div.textContent = line;
  els.logs.appendChild(div);
  els.logs.scrollTop = els.logs.scrollHeight;
  while (els.logs.children.length > 100) {
    els.logs.removeChild(els.logs.firstChild);
  }
}

// --- Log level ---
async function loadLogLevel() {
  try {
    const res = await fetch('/api/log-level');
    const data = await res.json();
    if (els.logLevel) els.logLevel.value = data.level || 'STANDARD';
  } catch (e) {
    dbg('Could not load log level: ' + e.message);
  }
}

if (els.logLevel) {
  els.logLevel.addEventListener('change', async () => {
    const level = els.logLevel.value;
    try {
      await fetch('/api/log-level', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({level}) });
      showToast('Log level set to ' + level);
    } catch (e) {
      uiErr('Failed to set log level: ' + e.message);
    }
  });
}

// --- Schedules ---
async function loadSchedules() {
  try {
    const res = await fetch('/api/schedules');
    const data = await res.json();
    if (!data.success) return;
    const list = data.schedules;
    if (!list || list.length === 0) {
      if (els.schedulesList) els.schedulesList.innerHTML = '<div style="color:var(--muted);">No schedules configured.</div>';
      return;
    }
    let html = '<div style="display:flex;flex-direction:column;gap:6px;">';
    for (const s of list) {
      const next = s.next_run ? new Date(s.next_run).toLocaleString() : 'not scheduled';
      html += `<div style="display:flex;justify-content:space-between;align-items:center;padding:8px;background:var(--bg);border:1px solid var(--border);border-radius:6px;">
        <div><strong>${s.source}</strong> — ${s.action.replace(/_/g, ' ')} every ${s.minutes} min <span style="color:var(--muted)">(next: ${next})</span></div>
        <button class="btn btn-danger" style="font-size:.75rem;padding:4px 10px;" onclick="deleteSchedule('${s.source}', '${s.action}')">Delete</button>
      </div>`;
    }
    html += '</div>';
    if (els.schedulesList) els.schedulesList.innerHTML = html;
  } catch (e) {
    dbg('Could not load schedules: ' + e.message);
  }
}

window.deleteSchedule = async function(source, action) {
  try {
    const res = await fetch(`/api/schedules/${source}/${action}`, { method: 'DELETE' });
    const data = await res.json();
    if (data.success) {
      showToast(`Removed schedule ${source}/${action}`);
      loadSchedules();
    } else {
      showToast('Failed to remove schedule', 'error');
    }
  } catch (e) {
    uiErr('DELETE schedule failed: ' + e.message);
  }
};

if (els.btnAddSchedule) {
  els.btnAddSchedule.addEventListener('click', async () => {
    const source = els.scheduleSource.value;
    const action = els.scheduleAction.value;
    const minutes = parseInt(els.scheduleMinutes.value) || 60;
    try {
      const res = await fetch('/api/schedules', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ source, action, minutes })
      });
      const data = await res.json();
      if (data.success) {
        showToast(`Scheduled ${source} ${action.replace(/_/g, ' ')} every ${minutes} min`);
        loadSchedules();
      } else {
        showToast('Failed to add schedule', 'error');
      }
    } catch (e) {
      uiErr('POST schedule failed: ' + e.message);
    }
  });
}

// --- Source helpers ---
const SOURCE_NAMES = {
  'AllBoatSites': 'All Boat Sites',
  'BoatTrader': 'BoatTrader',
  'YachtWorld': 'YachtWorld',
  'BoatsDotCom': 'Boats.com',
  'CarMax': 'CarMax',
  'Carvana': 'Carvana',
};

let _schedules = [];
let _lastRunningKeys = [];
let _lastPrescrapeRunning = false;

function formatUptime(seconds) {
  if (!seconds && seconds !== 0) return '';
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = Math.floor(seconds % 60);
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m ${s}s`;
}

// --- Source actions ---
window.discoverSource = async function(source, refresh = false) {
  dbg('Discover ' + source);
  setLastAction('Pulling index for ' + (SOURCE_NAMES[source] || source));
  try {
    const res = await fetch('/api/discover', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({source: source === 'AllBoatSites' ? '' : source, refresh: refresh}) });
    const data = await res.json();
    if (data.success) {
      showToast('Pulling index for ' + (SOURCE_NAMES[source] || source));
    } else {
      showToast('Discovery already running for ' + source, 'error');
    }
  } catch (e) {
    uiErr('POST /api/discover failed: ' + e.message);
  }
};

window.startSource = async function(source, limit = null) {
  dbg('Start ' + source);
  setLastAction('Starting scraper for ' + (SOURCE_NAMES[source] || source));
  try {
    const res = await fetch('/api/start', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({source: source === 'AllBoatSites' ? '' : source, limit: limit, mode: 'scrape'}) });
    const data = await res.json();
    if (data.success) {
      showToast('Scraper started for ' + (SOURCE_NAMES[source] || source));
    } else {
      showToast('Scraper already running for ' + source, 'error');
    }
  } catch (e) {
    uiErr('POST /api/start failed: ' + e.message);
  }
};

window.stopSource = async function(source) {
  dbg('Stop ' + source);
  setLastAction('Stopping ' + (SOURCE_NAMES[source] || source));
  try {
    const res = await fetch('/api/stop', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({source: source === 'AllBoatSites' ? '' : source}) });
    const data = await res.json();
    if (data.success) {
      showToast('Stop signal sent to ' + (SOURCE_NAMES[source] || source));
    } else {
      showToast(source + ' not running', 'error');
    }
  } catch (e) {
    uiErr('POST /api/stop failed: ' + e.message);
  }
};

// --- Status Polling ---
async function updateStatus() {
  try {
    const [statusRes, schedRes] = await Promise.all([
      fetch('/api/status'),
      fetch('/api/schedules'),
    ]);
    const data = await statusRes.json();
    const schedData = await schedRes.json();
    _schedules = schedData.success ? (schedData.schedules || []) : [];

    const anythingRunning = data.running;
    const runningKeys = data.running_sources || [];

    const anyStopRequested = data.stop_requested && Object.values(data.stop_requested).some(Boolean);
    els.statusDot.className = 'dot ' + (anythingRunning ? 'running' : 'stopped');
    els.statusText.textContent = anythingRunning ? (anyStopRequested ? 'Stopping…' : 'Running') : 'Stopped';

    els.statTotal.textContent = data.total_boats || 0;
    els.statManufacturers.textContent = data.total_manufacturers || 0;
    els.statPending.textContent = data.pending || 0;
    els.statDone.textContent = data.done || 0;
    els.statFailed.textContent = data.failed || 0;

    if (data.uptime_seconds) {
      els.uptime.textContent = `Uptime: ${formatUptime(data.uptime_seconds)}`;
    } else {
      els.uptime.textContent = '';
    }

    if (data.last_scraped) {
      els.lastScraped.textContent = 'Last scraped: ' + data.last_scraped;
    }

    // Prescrape progress bar
    if (data.prescraper_running && data.prescrape_total > 0) {
      prescrapeProgress.style.display = 'block';
      const pct = Math.round((data.prescrape_current / data.prescrape_total) * 100);
      prescrapeBar.style.width = pct + '%';
      prescrapePct.textContent = pct + '%';
      prescrapeLabel.textContent = `Fetching… page ${data.prescrape_current} / ${data.prescrape_total} (${data.prescrape_records} records)`;
    } else {
      prescrapeProgress.style.display = 'none';
    }

    renderSourceCards(data.sources || {}, runningKeys);
    loadSchedules();

    const prevRunningKeys = _lastRunningKeys;
    const finishedKeys = prevRunningKeys.filter(k => !runningKeys.includes(k));
    for (const k of finishedKeys) {
      showToast((SOURCE_NAMES[k] || k) + ' finished!', 'success');
    }
    _lastRunningKeys = runningKeys;

    if (_lastPrescrapeRunning && !data.prescraper_running) {
      showToast('Prescraper finished!', 'success');
      setLastAction('Prescraper complete');
    }
    _lastPrescrapeRunning = data.prescraper_running;

    els.btnPrescrape.disabled = anythingRunning;
    els.btnWipe.disabled = anythingRunning;
    els.btnWipeMfrs.disabled = anythingRunning;

  } catch (e) {
    uiErr('Status poll failed: ' + e.message);
  }
}

function renderSourceCards(sources, runningKeys) {
  if (!els.sourceCards) return;
  const keys = Object.keys(sources);
  if (keys.length === 0) {
    els.sourceCards.innerHTML = '<div style="color:var(--muted);">No sources configured.</div>';
    return;
  }
  const carSources = ['CarMax', 'Carvana'];
  let html = '';
  for (const key of keys) {
    const s = sources[key];
    const running = runningKeys.includes(key) || s.running || s.discover_running;
    const sched = _schedules.find(x => x.source === key);
    const schedBadge = sched && sched.enabled
      ? `<span style="font-size:.75rem;padding:2px 8px;background:var(--accent);color:#fff;border-radius:4px;">scheduled ${sched.minutes}m</span>`
      : '<span style="font-size:.75rem;padding:2px 8px;background:var(--bg);color:var(--muted);border-radius:4px;border:1px solid var(--border);">no schedule</span>';
    const isCar = carSources.includes(key);
    const statHtml = isCar
      ? `<span style="color:var(--muted);font-size:.8rem;">${s.records || 0} records${s.last_scraped ? ' · last ' + s.last_scraped : ''}</span>`
      : `<span style="color:var(--muted);font-size:.8rem;">pending ${s.pending || 0} · done ${s.done || 0} · failed ${s.failed || 0}</span>`;
    html += `<div style="padding:12px;background:var(--bg);border:1px solid var(--border);border-radius:8px;display:flex;flex-direction:column;gap:8px;">
      <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;">
        <strong>${SOURCE_NAMES[key] || key}</strong>
        ${running ? `<span style="font-size:.75rem;color:var(--accent);font-weight:600;">running ${formatUptime(s.uptime_seconds)}</span>` : schedBadge}
      </div>
      <div>${statHtml}</div>
      <div style="display:flex;gap:6px;flex-wrap:wrap;margin-top:auto;">
        <button class="btn btn-primary" style="font-size:.8rem;padding:6px 12px;" ${running ? 'disabled' : ''} onclick="startSource('${key}')">Scrape</button>
        <button class="btn" style="font-size:.8rem;padding:6px 12px;background:var(--surface);border:1px solid var(--border);color:var(--text);" ${s.discover_running ? 'disabled' : ''} onclick="discoverSource('${key}')">Pull Index</button>
        <button class="btn btn-danger" style="font-size:.8rem;padding:6px 12px;" ${!running ? 'disabled' : ''} onclick="stopSource('${key}')">Stop</button>
      </div>
    </div>`;
  }
  els.sourceCards.innerHTML = html;
}

// --- Add / Start scraper ---
els.btnStart.addEventListener('click', async () => {
  const sourceKey = els.scrapeSource.value || 'AllBoatSites';
  const mode = els.scrapeAction.value;
  const limit = mode === 'test' ? 5 : null;
  const scheduleOn = els.cbSchedule.checked;
  const interval = parseInt(els.scheduleInterval.value) || 10080;

  dbg(`Start clicked: ${sourceKey} mode=${mode} schedule=${scheduleOn} interval=${interval}`);
  setLastAction('Starting ' + mode + ' for ' + (SOURCE_NAMES[sourceKey] || sourceKey));

  try {
    const res = await fetch('/api/start', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({
        source: sourceKey === 'AllBoatSites' ? '' : sourceKey,
        mode: mode,
        limit: limit,
        schedule_enabled: scheduleOn,
        schedule_minutes: interval,
      })
    });
    const data = await res.json();
    dbg('start response: ' + JSON.stringify(data));
    if (data.success) {
      showToast('Started ' + mode.replace(/_/g, ' ') + ' for ' + (SOURCE_NAMES[sourceKey] || sourceKey) + (scheduleOn ? ' (scheduled)' : ''));
    } else {
      showToast('Already running or failed for ' + sourceKey, 'error');
    }
  } catch (e) {
    uiErr('POST /api/start failed: ' + e.message);
  }
});

els.btnStopAll.addEventListener('click', async () => {
  dbg('Stop all clicked');
  setLastAction('Stopping all sources…');
  try {
    const res = await fetch('/api/stop', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({all: true}) });
    const data = await res.json();
    if (data.success) {
      showToast('Stop signal sent to all running sources');
    } else {
      showToast('Nothing running', 'error');
    }
  } catch (e) {
    uiErr('POST /api/stop all failed: ' + e.message);
  }
});

els.btnPrescrape.addEventListener('click', async () => {
  dbg('Prescrape clicked');
  els.btnPrescrape.disabled = true;
  setLastAction('Starting manufacturer prescraper…');
  try {
    const res = await fetch('/api/prescrape', { method: 'POST' });
    const data = await res.json();
    dbg('prescrape response: ' + JSON.stringify(data));
    if (data.success) {
      showToast('Prescraper started');
    } else {
      showToast('Prescraper already running or data already present.', 'error');
    }
  } catch (e) {
    uiErr('POST /api/prescrape failed: ' + e.message);
  }
});

els.btnWipe.addEventListener('click', async () => {
  if (!confirm(
    'Wipe Boats?\n\nThis will DELETE all boats and reset the progress queue.\n\n' +
    'Manufacturers will be kept.\n\nProceed?'
  )) return;
  els.btnWipe.disabled = true;
  setLastAction('Wiping boats…');
  try {
    const res = await fetch('/api/wipe', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({keep_manufacturers:true}) });
    const data = await res.json();
    dbg('wipe response: ' + JSON.stringify(data));
    if (data.success) {
      showToast(`Boats wiped. Remaining: ${data.remaining_boats} boats.`);
      setLastAction('Boats wiped');
      updateStatus();
    }
  } catch (e) {
    uiErr('POST /api/wipe failed: ' + e.message);
  }
});

els.btnWipeMfrs.addEventListener('click', async () => {
  if (!confirm(
    'Wipe Manufacturers?\n\nThis will DELETE all ' +
    (els.statManufacturers ? els.statManufacturers.textContent : '') +
    ' manufacturer records.\n\nThis cannot be undone.\n\nProceed?'
  )) return;
  els.btnWipeMfrs.disabled = true;
  setLastAction('Wiping manufacturers…');
  try {
    const res = await fetch('/api/wipe-manufacturers', { method: 'POST' });
    const data = await res.json();
    dbg('wipe-mfrs response: ' + JSON.stringify(data));
    if (data.success) {
      showToast(`Manufacturers wiped. Deleted ${data.deleted} records.`);
      setLastAction('Manufacturers wiped');
      updateStatus();
    }
  } catch (e) {
    uiErr('POST /api/wipe-manufacturers failed: ' + e.message);
  }
});

els.btnDownload.addEventListener('click', () => {
  setLastAction('Downloading database…');
  const a = document.createElement('a');
  a.href = '/api/download';
  a.download = '';
  document.body.appendChild(a);
  a.click();
  a.remove();
  showToast('Download started');
  setLastAction('Download started');
});

// --- Edit / Delete ---

async function openEditModal(boatId) {
  try {
    const res = await fetch(`/api/boat/${boatId}`);
    const data = await res.json();
    if (!data.success) { alert('Failed to load boat: ' + data.error); return; }
    const boat = data.boat;

    document.getElementById('edit-id').value = boat.id;
    document.getElementById('edit-year').value = boat.year ?? '';
    document.getElementById('edit-make').value = boat.make ?? '';
    document.getElementById('edit-name').value = boat.name ?? '';
    document.getElementById('edit-length').value = boat.length ?? '';
    document.getElementById('edit-class').value = boat.class ?? '';
    document.getElementById('edit-engine').value = boat.engine ?? '';
    document.getElementById('edit-total_power').value = boat.total_power ?? '';
    document.getElementById('edit-engine_hours').value = boat.engine_hours ?? '';
    document.getElementById('edit-model').value = boat.model ?? '';
    document.getElementById('edit-capacity').value = boat.capacity ?? '';
    document.getElementById('edit-hin').value = boat.hin ?? '';
    document.getElementById('edit-source').value = boat.source || 'BoatTrader';

    editStatus.textContent = '';
    modal.classList.add('active');
  } catch (e) { uiErr('GET /api/boat/' + boatId + ': ' + e.message); }
}

btnCloseModal.addEventListener('click', () => {
  modal.classList.remove('active');
});

modal.addEventListener('click', (e) => {
  if (e.target === modal) modal.classList.remove('active');
});

btnSaveEdit.addEventListener('click', async () => {
  const boatId = document.getElementById('edit-id').value;
  const body = {};
  const fields = ['year','make','name','length','class','engine','total_power','engine_hours','model','capacity','hin','source'];
  for (const f of fields) {
    const el = document.getElementById('edit-' + f);
    if (el) body[f] = el.value === '' ? null : el.value;
  }
  if (body.year) body.year = parseInt(body.year, 10) || null;

  btnSaveEdit.disabled = true;
  editStatus.textContent = 'Saving…';
  try {
    const res = await fetch(`/api/boat/${boatId}`, { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(body) });
    const data = await res.json();
    dbg('edit response: ' + JSON.stringify(data));
    if (data.success) {
      editStatus.textContent = 'Saved.';
      modal.classList.remove('active');
      runQuery();
    } else {
      editStatus.textContent = 'Error: ' + (data.error || 'Unknown');
    }
  } catch (e) {
    editStatus.textContent = 'Network error.';
  } finally {
    btnSaveEdit.disabled = false;
  }
});

async function deleteBoat(boatId, boatName) {
  if (!confirm(`Delete boat #${boatId}?\n\n${boatName}`)) return;
  try {
    const res = await fetch(`/api/boat/${boatId}`, { method: 'DELETE' });
    const data = await res.json();
    dbg('delete response: ' + JSON.stringify(data));
    if (data.success) {
      runQuery();
    } else {
      alert('Delete failed');
    }
  } catch (e) { uiErr('DELETE /api/boat/' + boatId + ': ' + e.message); }
}

// --- Query ---
function buildQueryParams() {
  const p = new URLSearchParams();
  const year = document.getElementById('q-year').value;
  if (year) p.set('year', year);
  const make = document.getElementById('q-make').value;
  if (make) p.set('make', make);
  const cls = document.getElementById('q-class').value;
  if (cls) p.set('class', cls);
  const engine = document.getElementById('q-engine').value;
  if (engine) p.set('engine', engine);
  const hin = document.getElementById('q-hin').value;
  if (hin) p.set('hin', hin);
  const source = document.getElementById('q-source').value;
  if (source) p.set('source', source);
  const minLen = document.getElementById('q-min-length').value;
  if (minLen) p.set('min_length', minLen);
  const maxLen = document.getElementById('q-max-length').value;
  if (maxLen) p.set('max_length', maxLen);
  p.set('order_by', document.getElementById('q-order').value);
  p.set('limit', '50');
  return p;
}

function buildQueryFiltersJson() {
  const filters = {};
  const year = document.getElementById('q-year').value;
  if (year) filters.year = parseInt(year, 10);
  const make = document.getElementById('q-make').value;
  if (make) filters.make = make;
  const cls = document.getElementById('q-class').value;
  if (cls) filters.boat_class = cls;
  const engine = document.getElementById('q-engine').value;
  if (engine) filters.engine = engine;
  const hin = document.getElementById('q-hin').value;
  if (hin) filters.hin = hin;
  const source = document.getElementById('q-source').value;
  if (source) filters.source = source;
  const minLen = document.getElementById('q-min-length').value;
  if (minLen) filters.min_length = parseInt(minLen, 10);
  const maxLen = document.getElementById('q-max-length').value;
  if (maxLen) filters.max_length = parseInt(maxLen, 10);
  return filters;
}

function renderResults(data) {
  if (!data.success) {
    els.results.innerHTML = `<div class="empty">Error: ${data.error}</div>`;
    els.btnDeleteAll.style.display = 'none';
    return;
  }
  els.queryTotal.textContent = `Total: ${data.total} | Showing ${data.rows.length}`;
  if (data.rows.length === 0) {
    els.results.innerHTML = '<div class="empty">No results.</div>';
    els.btnDeleteAll.style.display = 'none';
    return;
  }

  els.btnDeleteAll.style.display = 'inline-flex';

  const cols = ['year','make','name','length','class','engine','total_power','engine_hours','model','capacity','hin','source'];
  let html = '<table><thead><tr>';
  html += '<th>Year</th><th>Make</th><th>Name</th><th>Length</th><th>Class</th><th>Engine</th><th>Power</th><th>Hours</th><th>Model</th><th>Capacity</th><th>HIN</th><th>Source</th><th style="text-align:center;width:80px;">Actions</th>';
  html += '</tr></thead><tbody>';

  for (const row of data.rows) {
    html += '<tr>';
    for (const c of cols) {
      let val = row[c] ?? '';
      html += `<td>${val}</td>`;
    }
    html += `<td style="text-align:center;white-space:nowrap;">`;
    html += `<button class="btn btn-primary" style="padding:4px 8px;font-size:.7rem;margin-right:4px;" onclick="boatApp.edit(${row.id})">Edit</button>`;
    html += `<button class="btn btn-danger" style="padding:4px 8px;font-size:.7rem;" onclick="boatApp.del(${row.id},'${(row.name||'').replace(/'/g,"\\'")}')">Delete</button>`;
    html += '</td></tr>';
  }
  html += '</tbody></table>';
  els.results.innerHTML = html;
}

// --- Sitemap URLs ---
const elsSitemap = {
  btnShow: document.getElementById('btn-show-sitemaps'),
  list: document.getElementById('sitemap-list'),
  urls: document.getElementById('sitemap-urls'),
  instructions: document.getElementById('sitemap-instructions'),
  btnDownloadAll: document.getElementById('btn-download-all-sitemaps'),
  btnCopyAll: document.getElementById('btn-copy-all-sitemaps'),
};

if (elsSitemap.btnShow) {
  elsSitemap.btnShow.addEventListener('click', async () => {
    const source = document.getElementById('scrape-source').value || 'YachtWorld';
    dbg('Fetching sitemap URLs for ' + source);
    setLastAction('Loading sitemap URLs…');
    elsSitemap.btnShow.disabled = true;
    try {
      const res = await fetch('/api/sitemap-urls?source=' + encodeURIComponent(source));
      const data = await res.json();
      if (data.error) {
        showToast('Failed: ' + data.error, 'error');
        return;
      }
      elsSitemap.urls.innerHTML = data.urls.map(u => {
        const filename = u.split('/').pop();
        return `<div style="display:flex;gap:8px;align-items:center;padding:4px 0;border-bottom:1px solid var(--border);font-family:monospace;font-size:.8rem;">
          <a href="${u}" target="_blank" style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:var(--accent);text-decoration:none;" title="${u}">${filename}</a>
          <button class="btn-download-url" data-url="${u}" data-filename="${filename}" style="padding:2px 8px;font-size:.75rem;background:var(--success);color:#fff;border:none;border-radius:4px;cursor:pointer;">Download</button>
          <button class="btn-copy-url" data-url="${u}" style="padding:2px 8px;font-size:.75rem;background:var(--bg);border:1px solid var(--border);border-radius:4px;cursor:pointer;">Copy</button>
        </div>`;
      }).join('');
      elsSitemap.list.style.display = 'block';
      elsSitemap.instructions.style.display = 'inline';
      showToast(data.count + ' sitemap URLs loaded');

      // Wire up download buttons
      elsSitemap.urls.querySelectorAll('.btn-download-url').forEach(btn => {
        btn.addEventListener('click', async () => {
          const url = btn.dataset.url;
          const filename = btn.dataset.filename;
          btn.textContent = 'Fetching…';
          btn.disabled = true;
          try {
            // Fetch through browser (has Cloudflare clearance)
            const resp = await fetch(url, {credentials: 'include', redirect: 'follow'});
            if (!resp.ok) throw new Error('HTTP ' + resp.status);
            const blob = await resp.blob();
            const a = document.createElement('a');
            a.href = URL.createObjectURL(blob);
            a.download = filename;
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            URL.revokeObjectURL(a.href);
            btn.textContent = 'Downloaded';
          } catch (e) {
            // If CORS blocked it, fall back to opening in new tab
            window.open(url, '_blank');
            btn.textContent = 'Opened';
            showToast('CORS blocked direct download — saved to tab, use Ctrl+S', 'warning');
          }
          setTimeout(() => { btn.textContent = 'Download'; btn.disabled = false; }, 2000);
        });
      });

      // Wire up copy buttons
      elsSitemap.urls.querySelectorAll('.btn-copy-url').forEach(btn => {
        btn.addEventListener('click', () => {
          navigator.clipboard.writeText(btn.dataset.url);
          btn.textContent = 'Copied!';
          setTimeout(() => btn.textContent = 'Copy', 1500);
        });
      });
    } catch (e) {
      uiErr('Failed to load sitemap URLs: ' + e.message);
    } finally {
      elsSitemap.btnShow.disabled = false;
    }
  });
}

if (elsSitemap.btnCopyAll) {
  elsSitemap.btnCopyAll.addEventListener('click', () => {
    const urls = Array.from(elsSitemap.urls.querySelectorAll('.btn-copy-url')).map(b => b.dataset.url).join('\n');
    navigator.clipboard.writeText(urls);
    elsSitemap.btnCopyAll.textContent = 'Copied!';
    setTimeout(() => elsSitemap.btnCopyAll.textContent = 'Copy All', 1500);
  });
}

if (elsSitemap.btnDownloadAll) {
  elsSitemap.btnDownloadAll.addEventListener('click', async () => {
    const btns = Array.from(elsSitemap.urls.querySelectorAll('.btn-download-url'));
    if (btns.length === 0) return;
    elsSitemap.btnDownloadAll.textContent = 'Downloading…';
    elsSitemap.btnDownloadAll.disabled = true;
    for (const btn of btns) {
      btn.click();
      await new Promise(r => setTimeout(r, 800)); // stagger to avoid browser blocking
    }
    elsSitemap.btnDownloadAll.textContent = 'Downloaded All';
    setTimeout(() => { elsSitemap.btnDownloadAll.textContent = 'Download All'; elsSitemap.btnDownloadAll.disabled = false; }, 2000);
  });
}

// --- Sitemap upload ---
const sitemapUploadInput = document.getElementById('sitemap-upload');
const sitemapUploadBtn = document.getElementById('btn-upload-sitemaps');
const uploadStatus = document.getElementById('upload-status');

if (sitemapUploadInput && sitemapUploadBtn) {
  sitemapUploadInput.addEventListener('change', async () => {
    const files = sitemapUploadInput.files;
    if (!files || files.length === 0) return;
    sitemapUploadBtn.textContent = 'Uploading…';
    sitemapUploadBtn.disabled = true;
    const form = new FormData();
    for (const f of files) form.append('files', f);
    try {
      const res = await fetch('/api/upload-sitemaps', { method: 'POST', body: form });
      const data = await res.json();
      if (data.success) {
        uploadStatus.textContent = `Uploaded ${data.count} file(s)`;
        uploadStatus.style.display = 'inline';
        showToast(`Uploaded ${data.count} sitemap file(s)`);
        setTimeout(() => { uploadStatus.style.display = 'none'; }, 5000);
      } else {
        showToast('Upload failed: ' + (data.error || 'Unknown'), 'error');
      }
    } catch (e) {
      uiErr('Upload failed: ' + e.message);
    } finally {
      sitemapUploadBtn.textContent = 'Upload Sitemaps';
      sitemapUploadBtn.disabled = false;
      sitemapUploadInput.value = '';
    }
  });
}

async function runQuery() {
  els.btnQuery.disabled = true;
  els.queryTotal.textContent = 'Loading…';
  try {
    const params = buildQueryParams();
    dbg('Running query: ' + params.toString());
    const res = await fetch('/api/query?' + params.toString());
    const data = await res.json();
    dbg('query response: ' + data.rows.length + ' rows');
    renderResults(data);
  } catch (e) {
    uiErr('GET /api/query failed: ' + e.message);
    els.results.innerHTML = '<div class="empty">Network error</div>';
  } finally {
    els.btnQuery.disabled = false;
  }
}

// Expose to inline handlers
const boatApp = { edit: openEditModal, del: deleteBoat };
if (typeof window !== 'undefined') window.boatApp = boatApp;

if (els.btnQuery) els.btnQuery.addEventListener('click', runQuery);

if (els.btnDeleteAll) {
  els.btnDeleteAll.addEventListener('click', async () => {
    const filters = buildQueryFiltersJson();
    const count = document.getElementById('query-total').textContent.match(/Total:\s*(\d+)/)?.[1] || 'all';
    if (!confirm(`Delete ALL ${count} records matching current filters?\n\nThis cannot be undone.`)) return;
    els.btnDeleteAll.disabled = true;
    els.btnDeleteAll.textContent = 'Deleting…';
    setLastAction('Deleting all matching records…');
    try {
      const res = await fetch('/api/delete-all', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(filters),
      });
      const data = await res.json();
      dbg('delete-all response: ' + JSON.stringify(data));
      if (data.success) {
        showToast(data.message, 'success');
        setLastAction('Delete complete');
        els.results.innerHTML = '<div class="empty">Records deleted. Run query again.</div>';
        els.btnDeleteAll.style.display = 'none';
        updateStatus();
      } else {
        showToast('Delete failed: ' + (data.error || 'Unknown'), 'error');
        els.btnDeleteAll.disabled = false;
        els.btnDeleteAll.textContent = 'Delete All Matching';
      }
    } catch (e) {
      uiErr('POST /api/delete-all failed: ' + e.message);
      els.btnDeleteAll.disabled = false;
      els.btnDeleteAll.textContent = 'Delete All Matching';
    }
  });
}

// Poll status every 2 seconds
setInterval(updateStatus, 2000);
updateStatus();
loadLogLevel();

// Fetch version once on load
(async function fetchVersion() {
  try {
    const res = await fetch('/api/version');
    const data = await res.json();
    if (els.version) els.version.textContent = 'v' + data.version;
  } catch (e) { /* ignore */ }
})();

connectLogs();
dbg('Dashboard loaded, polling started');
