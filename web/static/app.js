// Dashboard frontend — debug build
const els = {
  statusDot: document.getElementById('status-dot'),
  statusText: document.getElementById('status-text'),
  uptime: document.getElementById('uptime'),
  lastScraped: document.getElementById('last-scraped'),
  lastAction: document.getElementById('last-action'),
  version: document.getElementById('version'),
  statTotal: document.getElementById('stat-total'),
  statManufacturers: document.getElementById('stat-manufacturers'),
  statPending: document.getElementById('stat-pending'),
  statDone: document.getElementById('stat-done'),
  statFailed: document.getElementById('stat-failed'),
  btnDiscover: document.getElementById('btn-discover'),
  btnStart: document.getElementById('btn-start'),
  btnTest: document.getElementById('btn-test'),
  btnStop: document.getElementById('btn-stop'),
  btnPrescrape: document.getElementById('btn-prescrape'),
  btnWipe: document.getElementById('btn-wipe'),
  btnWipeMfrs: document.getElementById('btn-wipe-mfrs'),
  btnRetry: document.getElementById('btn-retry'),
  btnDownload: document.getElementById('btn-download'),
  logs: document.getElementById('logs'),
  results: document.getElementById('results'),
  queryTotal: document.getElementById('query-total'),
  btnQuery: document.getElementById('btn-query'),
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

// --- Status Polling ---
let _lastPrescrapeRunning = false;
let _lastScraperRunning = false;
let _lastDiscoverRunning = false;

async function updateStatus() {
  try {
    const res = await fetch('/api/status');
    const data = await res.json();
    dbg('status: running=' + data.running + ' scraper=' + data.scraper_running + ' prescraper=' + data.prescraper_running);

    els.statusDot.className = 'dot ' + (data.running ? 'running' : 'stopped');
    if (data.stop_requested && data.scraper_running) {
      els.statusText.textContent = 'Stopping…';
      setLastAction('Stop requested, waiting for current page to finish…', 0);
    } else {
      els.statusText.textContent = data.running ? 'Running' : 'Stopped';
    }

    els.statTotal.textContent = data.total_boats || 0;
    els.statManufacturers.textContent = data.total_manufacturers || 0;
    els.statPending.textContent = data.pending || 0;
    els.statDone.textContent = data.done || 0;
    els.statFailed.textContent = data.failed || 0;

    if (data.uptime_seconds) {
      const m = Math.floor(data.uptime_seconds / 60);
      const s = Math.floor(data.uptime_seconds % 60);
      els.uptime.textContent = `Uptime: ${m}m ${s}s`;
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

    const anythingRunning = data.scraper_running || data.discover_running || data.prescraper_running;

    els.btnDiscover.disabled = anythingRunning;
    els.btnStart.disabled = anythingRunning;
    els.btnTest.disabled = anythingRunning;
    els.btnStop.disabled = !data.scraper_running && !data.discover_running;
    els.btnPrescrape.disabled = anythingRunning;
    els.btnWipe.disabled = anythingRunning;
    els.btnWipeMfrs.disabled = anythingRunning;

    // Detect transitions for toast notifications
    if (_lastPrescrapeRunning && !data.prescraper_running) {
      showToast('Prescraper finished!', 'success');
      setLastAction('Prescraper complete');
    }
    if (_lastScraperRunning && !data.scraper_running) {
      showToast('Scraper finished!', 'success');
      setLastAction('Scraper complete');
    }
    if (_lastDiscoverRunning && !data.discover_running) {
      showToast('Index pull finished!', 'success');
      setLastAction('Index pull complete');
    }
    _lastPrescrapeRunning = data.prescraper_running;
    _lastScraperRunning = data.scraper_running;
    _lastDiscoverRunning = data.discover_running;

  } catch (e) {
    uiErr('Status poll failed: ' + e.message);
  }
}

// --- Controls ---
els.btnDiscover.addEventListener('click', async () => {
  dbg('Pull Index clicked');
  setLastAction('Pulling sitemap index…');
  const source = document.getElementById('scrape-source').value;
  const refresh = document.getElementById('cb-refresh').checked;
  try {
    const res = await fetch('/api/discover', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({source: source || null, refresh: refresh}) });
    const data = await res.json();
    dbg('discover response: ' + JSON.stringify(data));
    if (data.success) {
      showToast('Pulling index for ' + (source || 'All Sites') + (refresh ? ' (refresh)' : ''));
    } else {
      showToast('Discovery already running', 'error');
    }
  } catch (e) {
    uiErr('POST /api/discover failed: ' + e.message);
  }
});

els.btnStart.addEventListener('click', async () => {
  dbg('Start Scraping clicked');
  els.btnStart.disabled = true;
  setLastAction('Starting scraper…');
  try {
    const res = await fetch('/api/start', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({}) });
    const data = await res.json();
    dbg('start response: ' + JSON.stringify(data));
    if (data.success) {
      showToast('Scraper started');
    } else {
      showToast('Scraper already running', 'error');
    }
  } catch (e) {
    uiErr('POST /api/start failed: ' + e.message);
  }
});

els.btnTest.addEventListener('click', async () => {
  dbg('Test Run (5) clicked');
  els.btnTest.disabled = true;
  setLastAction('Starting test run (5 URLs)…');
  try {
    const res = await fetch('/api/start', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({limit: 5}) });
    const data = await res.json();
    dbg('test response: ' + JSON.stringify(data));
    if (data.success) {
      showToast('Test run started (5 URLs)');
    } else {
      showToast('Already running', 'error');
    }
  } catch (e) {
    uiErr('POST /api/start (test) failed: ' + e.message);
  }
});

els.btnStop.addEventListener('click', async () => {
  dbg('Stop clicked');
  setLastAction('Stopping scraper…');
  try {
    const res = await fetch('/api/stop', { method: 'POST' });
    const data = await res.json();
    dbg('stop response: ' + JSON.stringify(data));
    if (!data.success) {
      showToast('Scraper not running', 'error');
    } else {
      showToast('Stop signal sent');
    }
  } catch (e) {
    uiErr('POST /api/stop failed: ' + e.message);
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

els.btnRetry.addEventListener('click', async () => {
  if (!confirm('Retry Failed URLs?\n\nThis will reset ALL failed URLs back to pending so they can be scraped again.\n\nProceed?')) return;
  els.btnRetry.disabled = true;
  setLastAction('Resetting failed URLs…');
  try {
    const res = await fetch('/api/retry-failed', { method: 'POST' });
    const data = await res.json();
    dbg('retry response: ' + JSON.stringify(data));
    if (data.success) {
      showToast(`Reset ${data.reset_count} failed URLs to pending.`);
      setLastAction('Failed URLs reset');
      updateStatus();
    }
  } catch (e) {
    uiErr('POST /api/retry-failed failed: ' + e.message);
  } finally {
    els.btnRetry.disabled = false;
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

function renderResults(data) {
  if (!data.success) {
    els.results.innerHTML = `<div class="empty">Error: ${data.error}</div>`;
    return;
  }
  els.queryTotal.textContent = `Total: ${data.total} | Showing ${data.rows.length}`;
  if (data.rows.length === 0) {
    els.results.innerHTML = '<div class="empty">No results.</div>';
    return;
  }

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
      elsSitemap.urls.innerHTML = data.urls.map(u =>
        `<div style="display:flex;gap:8px;align-items:center;padding:4px 0;border-bottom:1px solid var(--border);font-family:monospace;font-size:.8rem;">
          <span style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${u}</span>
          <button class="btn-copy-url" data-url="${u}" style="padding:2px 8px;font-size:.75rem;background:var(--bg);border:1px solid var(--border);border-radius:4px;cursor:pointer;">Copy</button>
        </div>`
      ).join('');
      elsSitemap.list.style.display = 'block';
      elsSitemap.instructions.style.display = 'inline';
      showToast(data.count + ' sitemap URLs loaded');

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

// Poll status every 2 seconds
setInterval(updateStatus, 2000);
updateStatus();

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
