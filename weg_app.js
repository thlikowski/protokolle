// ── Daten (werden per API geladen) ───────────────────────────────────────────
let RAW = { protokolle: [], beschluesse: [], edits_meta: {} };

// ── Unified Notizen-System (API-basiert) ─────────────────────────────────────
// Eine Notiz hat: { id, datum, hv, objekt, betreff, text, status,
//                   beschluss_id (optional), erstellt_am, geaendert_am }

// In-Memory-Cache der Notizen (wird beim Start geladen)
let _notizenCache = null;

async function loadNotizenFromAPI() {
  try {
    const r = await fetch('/api/notizen');
    _notizenCache = await r.json();
  } catch(e) {
    console.error('Notizen laden fehlgeschlagen:', e);
    _notizenCache = {};
  }
}

function getNotizen() {
  return _notizenCache || {};
}

async function saveNotizAPI(notizData) {
  const r = await fetch('/api/notizen', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(notizData),
  });
  const saved = await r.json();
  // Cache aktualisieren
  if (!_notizenCache) _notizenCache = {};
  _notizenCache[saved.id] = saved;
  return saved;
}

async function deleteNotizAPI(id) {
  await fetch(`/api/notizen/${encodeURIComponent(id)}`, { method: 'DELETE' });
  if (_notizenCache) delete _notizenCache[id];
}

// ── Kommentare-System (Status-only, unabhängig von Notizen) ──────────────────
// Status wird in der kommentare-Tabelle gespeichert.
// Format: { beschluss_id: { status, geaendert_am } }
let _kommentareCache = null;

async function loadKommentareFromAPI() {
  try {
    const r = await fetch('/api/kommentare');
    if (r.ok) {
      _kommentareCache = await r.json();
    } else {
      _kommentareCache = {};
    }
  } catch(e) {
    console.error('Kommentare laden fehlgeschlagen:', e);
    _kommentareCache = {};
  }
}

function getKommentare() {
  const komm = _kommentareCache || {};
  // Fallback: für Beschlüsse ohne kommentare-Eintrag alten Notiz-Status verwenden
  // (Rückwärtskompatibilität — kommentare-Einträge haben immer Vorrang)
  const result = Object.assign({}, komm);
  for (const n of Object.values(_notizenCache || {})) {
    if (n.beschluss_id && !(n.beschluss_id in result)) {
      result[n.beschluss_id] = { status: n.status || 'offen', geaendert_am: n.geaendert_am };
    }
  }
  return result;
}

async function saveKommentarAPI(beschluss_id, status) {
  // Lokalen Cache IMMER aktualisieren – auch wenn Server-Speicherung fehlschlägt
  if (!_kommentareCache) _kommentareCache = {};
  _kommentareCache[beschluss_id] = { status, geaendert_am: new Date().toISOString() };
  try {
    const r = await fetch('/api/kommentare', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ beschluss_id, status }),
    });
    if (r.ok) return await r.json();
  } catch(e) {
    console.error('Kommentar-API nicht erreichbar:', e);
  }
  return { beschluss_id, status, ok: true };
}

// ── State ──────────────────────────────────────────────────────────────────────
let state = {
  tab:       'beschluesse',
  search:    '',
  objekt:    null,
  hv:        null,
  year:      null,
  beirat:    false,
  status:    null,
  protokoll: null,
  selected:  null,
};

// ── Init ───────────────────────────────────────────────────────────────────────
async function init() {
  // Daten + Notizen + Kommentare + Belegprüfungen parallel laden
  try {
    const [dataResp] = await Promise.all([
      fetch('/api/data'),
      loadNotizenFromAPI(),
      loadKommentareFromAPI(),
      loadBelegpruefungenFromAPI(),
    ]);
    if (!dataResp.ok) throw new Error(`HTTP ${dataResp.status}`);
    const data = await dataResp.json();
    RAW.protokolle  = data.protokolle  || [];
    RAW.beschluesse = data.beschluesse || [];
    RAW.edits_meta  = data.edits_meta  || {};
  } catch(e) {
    console.error('Daten laden fehlgeschlagen:', e);
    document.body.innerHTML = `<div style="color:#f88;font-family:monospace;padding:40px">
      <h2>⚠ Server nicht erreichbar</h2>
      <p>Bitte starte den Server mit:<br><code>python3 weg_server.py</code></p>
      <p style="color:#888;font-size:13px">${e}</p>
    </div>`;
    return;
  }

  // Header-Stats
  const beiratCount = RAW.beschluesse.filter(b => b.beirat_relevant).length;
  document.getElementById('hdr-proto').textContent  = RAW.protokolle.length;
  document.getElementById('hdr-beschl').textContent = RAW.beschluesse.length;
  document.getElementById('hdr-beirat').textContent = beiratCount;
  document.getElementById('tab-beirat-badge').textContent = beiratCount;
  document.getElementById('beirat-count').textContent = beiratCount;

  buildFilters();
  updateStatusCounts();
  updateNotizBadge();
  render();

  document.getElementById('search').addEventListener('input', e => {
    state.search = e.target.value.trim();
    render();
  });
}

// ── Filter aufbauen ────────────────────────────────────────────────────────────
function buildFilters() {
  // Objekte
  const objekte = [...new Set(RAW.protokolle.map(p => p.weg_objekt).filter(Boolean))].sort();
  const objDiv = document.getElementById('objekt-filters');
  objDiv.innerHTML = '';
  // Alle-Button
  const allBtn = document.createElement('button');
  allBtn.className = 'filter-btn' + (state.objekt === null ? ' active' : '');
  allBtn.innerHTML = `Alle <span class="count">${RAW.protokolle.length}</span>`;
  allBtn.onclick = () => { state.objekt = null; buildFilters(); render(); };
  objDiv.appendChild(allBtn);

  for (const obj of objekte) {
    const cnt = RAW.protokolle.filter(p => p.weg_objekt === obj).length;
    const btn = document.createElement('button');
    btn.className = 'filter-btn' + (state.objekt === obj ? ' active' : '');
    btn.innerHTML = `${obj} <span class="count">${cnt}</span>`;
    btn.onclick = () => { state.objekt = obj; buildFilters(); render(); };
    objDiv.appendChild(btn);
  }

  // Hausverwaltungen
  const hvs = [...new Set(RAW.protokolle.map(p => p.hausverwaltung).filter(Boolean))].sort();
  const hvDiv = document.getElementById('hv-filters');
  hvDiv.innerHTML = '';
  const allHvBtn = document.createElement('button');
  allHvBtn.className = 'filter-btn' + (state.hv === null ? ' active' : '');
  allHvBtn.innerHTML = `Alle <span class="count">${RAW.protokolle.length}</span>`;
  allHvBtn.onclick = () => { state.hv = null; buildFilters(); render(); };
  hvDiv.appendChild(allHvBtn);
  for (const hv of hvs) {
    const cnt = RAW.protokolle.filter(p => p.hausverwaltung === hv).length;
    const short = hv.replace(' Hausverwaltung GmbH','').replace(' GmbH','').replace(' / La Casa','');
    const btn = document.createElement('button');
    btn.className = 'filter-btn' + (state.hv === hv ? ' active' : '');
    btn.title = hv;
    btn.innerHTML = `${short} <span class="count">${cnt}</span>`;
    btn.onclick = () => { state.hv = hv; buildFilters(); render(); };
    hvDiv.appendChild(btn);
  }

  // Jahre aus Dateinamen + aktuelles Jahr immer einschließen
  const currentYear = new Date().getFullYear().toString();
  const years = [...new Set([
    ...RAW.protokolle.map(p => {
      const parts = p.dateiname.split('_');
      return parts.length >= 2 ? parts[1].substring(0,4) : null;
    }),
    currentYear
  ].filter(Boolean))].sort();
  const yearDiv = document.getElementById('year-filters');
  yearDiv.innerHTML = '';
  for (const y of years) {
    const btn = document.createElement('button');
    btn.className = 'year-btn' + (state.year === y ? ' active' : '');
    btn.textContent = y;
    btn.onclick = () => {
      state.year = (state.year === y) ? null : y;
      buildFilters(); render();
    };
    yearDiv.appendChild(btn);
  }

  // Beirat-Filter Button
  const bb = document.getElementById('beirat-filter');
  bb.className = 'filter-btn beirat-btn' + (state.beirat ? ' active' : '');
  document.getElementById('beirat-count').textContent =
    RAW.beschluesse.filter(b => {
      if (!b.beirat_relevant) return false;
      if (state.objekt   && b.weg_objekt     !== state.objekt)   return false;
      if (state.hv       && b.hausverwaltung !== state.hv)       return false;
      if (state.year) {
        const fy = (b.dateiname||'').split('_')[1]?.substring(0,4);
        if (fy !== state.year) return false;
      }
      if (state.protokoll && b.protokoll_id !== state.protokoll) return false;
      return true;
    }).length;
}

function toggleBeirat() {
  state.beirat = !state.beirat;
  buildFilters(); render();
}

function toggleStatus(s) {
  state.status = (state.status === s) ? null : s;
  const btns = document.querySelectorAll('#status-filters .filter-btn');
  btns.forEach(b => b.classList.remove('active'));
  if (state.status) {
    const idx = ['offen','erledigt'].indexOf(s);
    if (idx >= 0) btns[idx].classList.add('active');
  }
  render();
}

function clearFilters() {
  state = { ...state, search: '', objekt: null, hv: null, year: null, beirat: false, status: null, protokoll: null };
  document.getElementById('search').value = '';
  buildFilters();
  render();
}

// ── Filtern ────────────────────────────────────────────────────────────────────
function filteredBeschluesse() {
  const komm = getKommentare();
  return RAW.beschluesse.filter(b => {
    // Objekt
    if (state.objekt && b.weg_objekt !== state.objekt) return false;
    // HV
    if (state.hv && b.hausverwaltung !== state.hv) return false;
    // Jahr – aus Dateiname (zuverlässiger als OCR-Datum)
    if (state.year) {
      const parts = (b.dateiname||'').split('_');
      const fileYear = parts.length >= 2 ? parts[1].substring(0,4) : '';
      if (fileYear !== state.year) return false;
    }
    // Protokoll
    if (state.protokoll && b.protokoll_id !== state.protokoll) return false;
    // Beirat
    if (state.beirat && !b.beirat_relevant) return false;
    // Status
    if (state.status) {
      const k = komm[b.id];
      const kStatus = k ? k.status : (b.beirat_relevant ? 'offen' : 'erledigt');
      if (kStatus !== state.status) return false;
    }
    // Tolerante Suche inkl. verknüpfter Notizen
    if (state.search) {
      const notizTexts = Object.values(getNotizen())
        .filter(n => n.beschluss_id === b.id)
        .flatMap(n => [n.betreff, n.text]);
      const fields = [b.beschluss_text, b.top_nr, b.top_titel,
                      b.weg_objekt, b.versammlungs_datum, ...notizTexts];
      if (!fuzzySearchMatch(state.search, fields)) return false;
    }
    return true;
  });
}

// ── Render ─────────────────────────────────────────────────────────────────────
function render() {
  const results = filteredBeschluesse();
  updateBreadcrumb();

  // Protokoll-Filter-Tag
  const tag = document.getElementById('proto-filter-tag');
  if (state.protokoll) {
    const p = RAW.protokolle.find(x => x.id === state.protokoll);
    tag.style.display = 'inline';
    tag.textContent   = p ? `· ${p.weg_objekt}  ${p.versammlungs_datum}` : '';
  } else {
    tag.style.display = 'none';
    tag.textContent   = '';
  }

  if (state.tab === 'beschluesse') {
    document.getElementById('result-count').textContent =
      `${results.length} Beschlüsse${state.search ? ` für „${state.search}"` : ''}`;
    renderBeschluesse(results);
    updateStatusCounts();
  } else if (state.tab === 'beirat') {
    document.getElementById('result-count').textContent = '';
    renderBeirat();
    updateStatusCounts();
  } else if (state.tab === 'protokolle') {
    renderProtokolle();
    updateStatusCounts();
  } else if (state.tab === 'notizen') {
    document.getElementById('result-count').textContent = '';
    renderNotizen();  // aktualisiert cnt-offen/cnt-erledigt selbst
  }
}

function highlight(text, q) {
  if (!q || !text) return text || '';
  // Für jedes Suchwort: exakte + normalisierte Varianten hervorheben
  let result = text;
  for (const word of q.trim().split(/\s+/).filter(Boolean)) {
    const esc = word.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    try { result = result.replace(new RegExp(`(${esc})`, 'gi'), '<mark>$1</mark>'); }
    catch(e) {}
  }
  return result;
}

function renderBeschluesse(results) {
  const container = document.getElementById('beschluss-list');
  const q = state.search.toLowerCase();
  if (!results.length) {
    container.innerHTML = '<div class="empty">Keine Beschlüsse gefunden.</div>';
    return;
  }
  container.innerHTML = results.map(b => {
    const text = (b.beschluss_text || '').substring(0, 120);
    const highlighted = q ? highlight(text, state.search) : text;
    const eb = effectiveB(b);
    const ergebnisClass = eb.ergebnis === 'angenommen' ? 'ergebnis-angenommen'
                        : eb.ergebnis === 'abgelehnt'  ? 'ergebnis-abgelehnt' : '';
    const selected = state.selected === b.id ? ' selected' : '';
    const beiratRow = b.beirat_relevant ? ' beirat-row' : '';
    return `
      <div class="beschluss-row${selected}${beiratRow}" onclick="selectBeschluss(${b.id})">
        <div class="col-objekt">${b.weg_objekt || '–'}</div>
        <div class="col-datum">${b.versammlungs_datum || '–'}</div>
        <div class="col-top">${b.top_nr}</div>
        <div class="col-text">${highlighted}</div>
        <div class="col-ergebnis ${ergebnisClass}">${eb.ergebnis || '–'}</div>
        <div class="col-beirat">${b.beirat_relevant ? '🔴' : ''}</div>
        <div class="col-edit"><a href="#" onclick="event.preventDefault();event.stopPropagation();openImportEdit(${b.protokoll_id},${b.id})" title="Bearbeiten">✎</a></div>
      </div>`;
  }).join('');
}

function renderBeirat() {
  const komm = getKommentare();
  // Nutze filteredBeschluesse() damit Objekt/HV/Jahr-Filter auch hier wirken
  const list = filteredBeschluesse().filter(b => b.beirat_relevant);
  const container = document.getElementById('beirat-list');
  if (!list.length) {
    container.innerHTML = '<div class="empty">Keine Beirat-Beschlüsse.</div>';
    return;
  }
  container.innerHTML = list.map(b => {
    const k = komm[b.id];
    const status = k ? k.status : 'offen';
    const statusLabel = status === 'erledigt' ? '● Erledigt' : '○ Offen';
    const statusColor = status === 'erledigt' ? 'var(--green)' : 'var(--accent)';
    const selected = state.selected === b.id ? ' selected' : '';
    return `
      <div class="beschluss-row beirat-row${selected}" onclick="selectBeschluss(${b.id})">
        <div class="col-objekt">${b.weg_objekt || '–'}</div>
        <div class="col-datum">${b.versammlungs_datum || '–'}</div>
        <div class="col-top">${b.top_nr}</div>
        <div class="col-text">${(b.beschluss_text || '').substring(0,120)}</div>
        <div class="col-ergebnis" style="color:${statusColor}">${statusLabel}</div>
        <div></div>
      </div>`;
  }).join('');
}

function renderProtokolle() {
  // Alle Filter anwenden (Objekt, HV, Jahr, Beirat, Status)
  let protos = RAW.protokolle;
  if (state.objekt) protos = protos.filter(p => p.weg_objekt === state.objekt);
  if (state.hv)     protos = protos.filter(p => p.hausverwaltung === state.hv);
  if (state.year)   protos = protos.filter(p => (p.dateiname||'').split('_')[1]?.startsWith(state.year));
  // Beirat: nur Protokolle mit mindestens einem beirat-relevanten Beschluss
  if (state.beirat) protos = protos.filter(p =>
    RAW.beschluesse.some(b => b.protokoll_id === p.id && b.beirat_relevant));
  // Status: nur Protokolle mit mindestens einem Beschluss im gewünschten Status
  if (state.status) {
    const komm = getKommentare();
    protos = protos.filter(p =>
      RAW.beschluesse.some(b => {
        if (b.protokoll_id !== p.id) return false;
        const k = komm[b.id] || { status: b.beirat_relevant ? 'offen' : 'erledigt' };
        return k.status === state.status;
      }));
  }

  const container = document.getElementById('proto-grid');
  const total = RAW.protokolle.length;
  document.getElementById('result-count').textContent =
    protos.length < total ? `${protos.length} von ${total} Protokollen` : '';
  if (!protos.length) {
    container.innerHTML = '<div class="empty">Keine Protokolle gefunden.</div>';
    return;
  }
  container.innerHTML = protos.map(p => {
    // Beschlüsse aus RAW.beschluesse zählen (protokoll_id verknüpft)
    const beschl = RAW.beschluesse.filter(b => b.protokoll_id === p.id);
    const beiratCnt = beschl.filter(b => b.beirat_relevant).length;
    const year = (p.dateiname||'').split('_')[1]?.substring(0,4) || '';
    // Notizen zum gleichen Objekt + Datum
    const notizen = getNotizen();
    const notizCnt = Object.values(notizen).filter(n =>
      n.objekt === p.weg_objekt && n.datum === p.versammlungs_datum
    ).length;
    return `
      <div class="proto-card" onclick="openProtokoll(${p.id})">
        <div class="pc-objekt">${p.weg_objekt || '–'}</div>
        <div class="pc-datum">${p.versammlungs_datum || year}</div>
        <div class="pc-hv">${p.hausverwaltung || '–'}</div>
        <div class="pc-footer">
          <div class="pc-stats">
            <div class="pc-stat">Beschlüsse <span>${beschl.length}</span></div>
            ${beiratCnt > 0 ? `<div class="pc-stat"><span class="pc-beirat-dot"></span><span>${beiratCnt} Beirat</span></div>` : ''}
            ${notizCnt > 0 ? `<div class="pc-stat">💬 <span>${notizCnt} Notiz${notizCnt>1?'en':''}</span></div>` : ''}
          </div>
          <div class="pc-actions">
            <a class="pdf-link" href="output/${p.dateiname}" target="_blank" style="padding:3px 8px;font-size:10px" onclick="event.stopPropagation()">PDF</a>
            <a class="pdf-link" href="#" title="PDF austauschen" style="padding:3px 8px;font-size:10px" onclick="event.preventDefault();event.stopPropagation();replacePdf(${p.id})">⇄ PDF</a>
            <a class="import-btn" href="#" onclick="event.preventDefault();event.stopPropagation();openImportEdit(${p.id},null)">✎</a>
          </div>
        </div>
      </div>`;
  }).join('');
}

function openProtokoll(id) {
  state.protokoll = id;
  state.selected  = null;
  switchTabDirect('beschluesse');
  updateBreadcrumb();
}

// ── Detail ─────────────────────────────────────────────────────────────────────
function selectBeschluss(id) {
  state.selected = id;
  hideNotizPanel();

  const b  = RAW.beschluesse.find(x => x.id === id);
  if (!b) return;
  const eb = effectiveB(b);

  const komm = getKommentare();
  const k = komm[id] || { status: b.beirat_relevant ? 'offen' : 'erledigt' };

  // Header
  document.getElementById('d-top').textContent  = `TOP ${b.top_nr}`;
  document.getElementById('d-meta').textContent =
    `${b.weg_objekt}  ·  ${b.versammlungs_datum}  ·  ${b.hausverwaltung}`;
  const badge = document.getElementById('d-beirat-badge');
  badge.style.display = b.beirat_relevant ? 'block' : 'none';

  // Text (effektiv)
  document.getElementById('d-text').textContent = eb.beschluss_text || '(kein Text erkannt)';

  // Abstimmung (effektiv)
  const abstWrap = document.getElementById('d-abstimmung-wrap');
  if (eb.ja_stimmen || eb.nein_stimmen || eb.enthaltungen) {
    abstWrap.style.display = 'block';
    document.getElementById('d-abstimmung').innerHTML = `
      <div class="abstimmung-cell">
        <div class="a-label">JA</div>
        <div class="a-val a-ja">${eb.ja_stimmen || '–'}</div>
      </div>
      <div class="abstimmung-cell">
        <div class="a-label">NEIN</div>
        <div class="a-val a-nein">${eb.nein_stimmen || '–'}</div>
      </div>
      <div class="abstimmung-cell">
        <div class="a-label">Enthaltung</div>
        <div class="a-val a-enth">${eb.enthaltungen || '–'}</div>
      </div>`;
  } else {
    abstWrap.style.display = 'none';
  }

  // Status-Buttons: aus kommentare-Cache lesen
  document.querySelectorAll('.status-btn').forEach(btn => btn.classList.remove('active'));
  const statusMap = { 'offen': 's-offen', 'erledigt': 's-erledigt' };
  document.querySelector(`.${statusMap[k.status]}`)?.classList.add('active');

  // PDF-Link – Pfad: output/<dateiname>
  const pdfLink = document.getElementById('d-pdf-link');
  pdfLink.href = `output/${b.dateiname}`;
  document.getElementById('d-pdf-name').textContent = b.dateiname.replace('_durchsuchbar','');

  // Notizen zu diesem Beschluss
  renderBeschlussNotizen(id);

  // Panel zeigen
  document.getElementById('detail-panel').classList.remove('hidden');

  // Liste neu rendern (für selected-Markierung)
  if (state.tab === 'beschluesse') renderBeschluesse(filteredBeschluesse());
  if (state.tab === 'beirat') renderBeirat();
}

async function setStatus(s) {
  if (!state.selected) return;
  const b = RAW.beschluesse.find(x => x.id === state.selected);

  // Status in kommentare-Tabelle speichern (kein Notiz-Zwang)
  // Cache wird in saveKommentarAPI() immer zuerst aktualisiert – kein Early-Return nötig
  await saveKommentarAPI(state.selected, s);

  // Status-Button sofort aktualisieren
  document.querySelectorAll('.status-btn').forEach(btn => btn.classList.remove('active'));
  const statusMap = { 'offen': 's-offen', 'erledigt': 's-erledigt' };
  document.querySelector(`.${statusMap[s]}`)?.classList.add('active');
  updateStatusCounts();
  if (state.tab === 'beirat') renderBeirat();

  // Notiz-Formular optional öffnen (vorausgefüllt) – Abbrechen erstellt KEINE Notiz
  const savedSelected = state.selected;
  const todayDE = new Date().toLocaleDateString('de-DE', { day:'2-digit', month:'2-digit', year:'numeric' });
  const statusLabel = s === 'erledigt' ? 'Erledigt' : 'Offen gesetzt';
  neueNotiz({
    objekt:       b?.weg_objekt        || '',
    hv:           b?.hausverwaltung    || '',
    datum:        b?.versammlungs_datum || '',
    beschluss_id: state.selected,
    betreff:      b ? `TOP ${b.top_nr}: ${(b.top_titel || b.beschluss_text || '').substring(0,60)}` : '',
    text:         `${statusLabel} am ${todayDE}`,
  });
  state.selected = savedSelected;
}


function updateStatusCounts() {
  const komm = getKommentare();
  // Alle aktiven Filter anwenden (außer Status selbst) – damit der Zähler
  // exakt das zeigt, was beim Klick auf den Filter-Button erscheint.
  const filtered = RAW.beschluesse.filter(b => {
    if (state.objekt    && b.weg_objekt     !== state.objekt)    return false;
    if (state.hv        && b.hausverwaltung !== state.hv)        return false;
    if (state.year) {
      const fileYear = (b.dateiname || '').split('_')[1]?.substring(0, 4);
      if (fileYear !== state.year) return false;
    }
    if (state.protokoll && b.protokoll_id   !== state.protokoll) return false;
    if (state.beirat    && !b.beirat_relevant)                   return false;
    return true;
  });
  let offen = 0, erledigt = 0;
  for (const b of filtered) {
    const k = komm[b.id];
    const s = k ? k.status : (b.beirat_relevant ? 'offen' : 'erledigt');
    if (s === 'offen')    offen++;
    if (s === 'erledigt') erledigt++;
  }
  document.getElementById('cnt-offen').textContent    = offen;
  document.getElementById('cnt-erledigt').textContent = erledigt;
}

// ── Tabs ───────────────────────────────────────────────────────────────────────
function switchTab(tab) {
  state.tab = tab;
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  event.target.classList.add('active');

  document.getElementById('tab-beschluesse').style.display = tab === 'beschluesse' ? 'block' : 'none';
  document.getElementById('tab-protokolle').style.display  = tab === 'protokolle'  ? 'block' : 'none';
  document.getElementById('tab-beirat').style.display      = tab === 'beirat'      ? 'block' : 'none';
  document.getElementById('tab-notizen').style.display     = tab === 'notizen'     ? 'block' : 'none';
  if (tab !== 'notizen') hideNotizPanel();

  render();
}


// ── Notizen-System ───────────────────────────────────────────────────────────

let notizEditId = null;
let notizPanelOpen = false;

// Datumskonvertierung
function toIsoDate(d) {
  if (!d) return '';
  if (/^\d{4}-\d{2}-\d{2}$/.test(d)) return d;
  const p = d.split('.');
  if (p.length === 3) return `${p[2]}-${p[1].padStart(2,'0')}-${p[0].padStart(2,'0')}`;
  return '';
}
function toDisplayDate(d) {
  if (!d) return '';
  if (/^\d{2}\.\d{2}\.\d{4}$/.test(d)) return d;
  const p = d.split('-');
  if (p.length === 3) return `${p[2]}.${p[1]}.${p[0]}`;
  return d;
}

function getNotizCount() { return Object.keys(getNotizen()).length; }

function updateNotizBadge() {
  document.getElementById('tab-notizen-badge').textContent = getNotizCount();
}

function fillNotizSelects(objekt, hv) {
  const hvSelect  = document.getElementById('nf-hv');
  const objSelect = document.getElementById('nf-objekt');
  if (!hvSelect || !objSelect) return;
  const hvs  = [...new Set(RAW.protokolle.map(p => p.hausverwaltung).filter(Boolean))].sort();
  const objs = [...new Set(RAW.protokolle.map(p => p.weg_objekt).filter(Boolean))].sort();
  const shortHv = h => h.replace(' Hausverwaltung GmbH','').replace(' GmbH','').replace(' / La Casa','');
  hvSelect.innerHTML  = '<option value="">– Hausverwaltung wählen –</option>'
    + hvs.map(h => `<option value="${h}">${shortHv(h)}</option>`).join('');
  objSelect.innerHTML = '<option value="">– Objekt wählen –</option>'
    + objs.map(o => `<option value="${o}">${o}</option>`).join('');
  if (hv)     hvSelect.value  = hv;
  if (objekt) objSelect.value = objekt;
}

function fillBeschlussSelect(objekt, beschlussId) {
  const sel = document.getElementById('nf-beschluss-id');
  const wrap = document.getElementById('nf-beschluss-wrap');
  if (!sel) return;
  // Nur Beschlüsse des gewählten Objekts anbieten
  const beschl = objekt
    ? RAW.beschluesse.filter(b => b.weg_objekt === objekt)
    : RAW.beschluesse;
  sel.innerHTML = '<option value="">– kein Beschluss-Bezug –</option>'
    + beschl.map(b => `<option value="${b.id}">${b.versammlungs_datum} · TOP ${b.top_nr} · ${(b.top_titel||b.beschluss_text||'').substring(0,50)}</option>`).join('');
  if (beschlussId) sel.value = beschlussId;
  if (wrap) wrap.style.display = 'block';
}

function neueNotiz(prefill) {
  notizEditId = null;
  const p = prefill || {};
  fillNotizSelects(p.objekt, p.hv);
  fillBeschlussSelect(p.objekt, p.beschluss_id);

  const today = new Date().toISOString().split('T')[0];
  document.getElementById('nf-datum').value   = p.datum ? toIsoDate(p.datum) : today;
  document.getElementById('nf-betreff').value = p.betreff || '';
  document.getElementById('nf-text').value    = p.text || '';
  setGmailLinks(p.gmail_links || []);
  _setNotizStatusToggle(p.status || 'offen');

  document.getElementById('notiz-panel-title').textContent = 'Neue Notiz';
  document.getElementById('notiz-delete-btn').style.display = 'none';
  document.getElementById('notiz-save-note').style.opacity = '0';
  showNotizPanel();
  renderNotizen(); // Liste neu rendern (keine Karte selected bei neuer Notiz)
}

function editNotiz(id) {
  notizEditId = id;
  const n = getNotizen()[id];
  if (!n) return;
  fillNotizSelects(n.objekt, n.hv);
  fillBeschlussSelect(n.objekt, n.beschluss_id);
  document.getElementById('nf-datum').value   = toIsoDate(n.datum || '');
  document.getElementById('nf-betreff').value = n.betreff || '';
  document.getElementById('nf-text').value    = n.text || '';
  setGmailLinks(n.gmail_links || []);
  _setNotizStatusToggle(n.status || 'offen');
  document.getElementById('notiz-panel-title').textContent = 'Notiz bearbeiten';
  document.getElementById('notiz-delete-btn').style.display = 'inline-block';
  document.getElementById('notiz-save-note').style.opacity = '0';
  showNotizPanel();
  renderNotizen(); // Liste sofort neu rendern damit selected-Markierung stimmt
}

// ── Gmail-Link-Liste ─────────────────────────────────────────────────────────

function _esc(s) {
  return (s || '').replace(/&/g,'&amp;').replace(/"/g,'&quot;').replace(/</g,'&lt;');
}

function addGmailLinkRow(label = '', url = '') {
  const list = document.getElementById('nf-gmail-links-list');
  const row = document.createElement('div');
  row.className = 'gmail-link-row';
  row.innerHTML = `
    <input class="gmail-label-inp" type="text" placeholder="Bezeichnung (optional)" value="${_esc(label)}">
    <input class="gmail-url-inp"   type="url"  placeholder="https://mail.google.com/mail/u/0/…" value="${_esc(url)}">
    <a class="gmail-open-btn" target="_blank"${url ? ` href="${_esc(url)}" style="display:inline-flex"` : ''}>✉</a>
    <button type="button" class="gmail-remove-btn" title="Entfernen">×</button>
  `;
  const urlInp    = row.querySelector('.gmail-url-inp');
  const openBtn   = row.querySelector('.gmail-open-btn');
  const removeBtn = row.querySelector('.gmail-remove-btn');
  urlInp.addEventListener('input', () => {
    const v = urlInp.value.trim();
    if (v.startsWith('https://')) {
      openBtn.href = v;
      openBtn.style.display = 'inline-flex';
    } else {
      openBtn.style.display = 'none';
    }
  });
  removeBtn.addEventListener('click', () => row.remove());
  list.appendChild(row);
}

function setGmailLinks(links) {
  const list = document.getElementById('nf-gmail-links-list');
  if (!list) return;
  list.innerHTML = '';
  (links || []).forEach(l => addGmailLinkRow(l.label || '', l.url || ''));
}

function getGmailLinks() {
  const rows = document.querySelectorAll('#nf-gmail-links-list .gmail-link-row');
  const result = [];
  rows.forEach(row => {
    const url   = row.querySelector('.gmail-url-inp')?.value.trim()   || '';
    const label = row.querySelector('.gmail-label-inp')?.value.trim() || '';
    if (url) result.push({ label, url });
  });
  return result;
}

// ─────────────────────────────────────────────────────────────────────────────

function showNotizPanel() {
  document.getElementById('detail-panel').classList.remove('hidden');
  document.querySelector('.detail-header').style.display = 'none';
  document.querySelector('.detail-body').style.display   = 'none';
  document.getElementById('notiz-detail').style.display = 'block';
  state.selected = null;
  notizPanelOpen = true;
}

function hideNotizPanel() {
  if (document.querySelector('.detail-header'))
    document.querySelector('.detail-header').style.display = '';
  if (document.querySelector('.detail-body'))
    document.querySelector('.detail-body').style.display   = '';
  document.getElementById('notiz-detail').style.display   = 'none';
  notizEditId = null;
  notizPanelOpen = false;
  // Liste neu rendern damit selected-Klasse sofort verschwindet
  if (state.tab === 'notizen') renderNotizen();
  // Beschluss-Kontext auffrischen
  if (state.selected) {
    renderBeschlussNotizen(state.selected);
    // Status-Buttons aus kommentare-Cache neu setzen
    const komm = getKommentare();
    const b = RAW.beschluesse.find(x => x.id === state.selected);
    const currentStatus = komm[state.selected]?.status || (b?.beirat_relevant ? 'offen' : 'erledigt');
    document.querySelectorAll('.status-btn').forEach(btn => btn.classList.remove('active'));
    const statusMap = { 'offen': 's-offen', 'erledigt': 's-erledigt' };
    document.querySelector(`.${statusMap[currentStatus]}`)?.classList.add('active');
  }
}

async function saveNotiz() {
  const hv     = document.getElementById('nf-hv').value.trim();
  const objekt = document.getElementById('nf-objekt').value.trim();
  const betreff= document.getElementById('nf-betreff').value.trim();
  if (!hv)      { alert('Bitte Hausverwaltung wählen.'); return; }
  if (!objekt)  { alert('Bitte Objekt wählen.'); return; }
  if (!betreff) { alert('Bitte Betreff eingeben.'); return; }

  const beschlussId = document.getElementById('nf-beschluss-id')?.value;
  const id = notizEditId || ('n_' + Date.now());

  const notizData = {
    id,
    datum:        toDisplayDate(document.getElementById('nf-datum').value.trim()),
    hv,
    objekt,
    betreff,
    text:         document.getElementById('nf-text').value,
    status:       document.getElementById('nf-status').value || 'offen',
    beschluss_id: beschlussId ? parseInt(beschlussId) : null,
    gmail_links:  getGmailLinks(),
  };

  try {
    const saved = await saveNotizAPI(notizData);
    notizEditId = saved.id;
  } catch(e) {
    alert('Speichern fehlgeschlagen: ' + e);
    return;
  }

  document.getElementById('notiz-delete-btn').style.display = 'inline-block';
  document.getElementById('notiz-panel-title').textContent  = 'Notiz bearbeiten';
  const note = document.getElementById('notiz-save-note');
  note.style.opacity = '1';
  setTimeout(() => note.style.opacity = '0', 2000);
  updateNotizBadge();
  renderNotizen();
  if (state.selected) renderBeschlussNotizen(state.selected);
}


async function deleteNotiz() {
  if (!notizEditId) return;
  if (!confirm('Notiz wirklich löschen?')) return;
  try {
    await deleteNotizAPI(notizEditId);
  } catch(e) {
    alert('Löschen fehlgeschlagen: ' + e);
    return;
  }
  notizEditId = null;
  document.getElementById('detail-panel').classList.add('hidden');
  hideNotizPanel();
  updateNotizBadge();
  renderNotizen();
  if (state.selected) renderBeschlussNotizen(state.selected);
}

// ── Notiz-Status-Hilfsfunktionen ─────────────────────────────────────────────

function _setNotizStatusToggle(st) {
  const input = document.getElementById('nf-status');
  const btn   = document.getElementById('nf-status-toggle');
  if (!input || !btn) return;
  input.value = st;
  if (st === 'erledigt') {
    btn.textContent = '● Erledigt';
    btn.style.borderColor = 'var(--green)';
    btn.style.color       = 'var(--green)';
    btn.style.background  = 'rgba(80,192,112,0.1)';
  } else {
    btn.textContent = '○ Offen';
    btn.style.borderColor = 'var(--accent)';
    btn.style.color       = 'var(--accent)';
    btn.style.background  = 'rgba(255,168,0,0.08)';
  }
}

function toggleNotizFormStatus() {
  const input = document.getElementById('nf-status');
  _setNotizStatusToggle(input.value === 'offen' ? 'erledigt' : 'offen');
}

async function toggleNotizStatus(id, event) {
  event.stopPropagation();
  const n = getNotizen()[id];
  if (!n) return;
  const next = (n.status || 'offen') === 'offen' ? 'erledigt' : 'offen';
  await saveNotizAPI({ ...n, status: next });
  if (notizEditId === id) _setNotizStatusToggle(next);
  renderNotizen();
  if (state.selected) renderBeschlussNotizen(state.selected);
}

function renderNotizen() {
  const container = document.getElementById('notizen-list');
  if (!container) return;
  const all = getNotizen();
  const q   = state.search.toLowerCase();

  let list = Object.values(all).sort((a,b) =>
    (b.geaendert_am||'').localeCompare(a.geaendert_am||''));

  // Filter
  if (state.objekt) list = list.filter(n => n.objekt === state.objekt);
  if (state.hv)     list = list.filter(n => n.hv === state.hv);
  if (state.year)   list = list.filter(n => {
    // Jahr aus Datum extrahieren (egal ob TT.MM.JJJJ oder JJJJ-MM-TT)
    const notizYear = (n.datum||'').match(/\d{4}/)?.[0] || '';
    if (notizYear === state.year) return true;
    // auch treffen wenn verknüpfter Beschluss aus dem Filterjahr stammt
    if (n.beschluss_id) {
      const b = (RAW.beschluesse||[]).find(b => b.id == n.beschluss_id);
      if (b) {
        const parts = (b.dateiname||'').split('_');
        const fileYear = parts.length >= 2 ? parts[1].substring(0,4) : '';
        if (fileYear === state.year) return true;
      }
    }
    return false;
  });

  // Status-Zähler aktualisieren (vor Status-Filter, damit beide Zahlen stimmen)
  document.getElementById('cnt-offen').textContent    = list.filter(n => (n.status||'offen') === 'offen').length;
  document.getElementById('cnt-erledigt').textContent = list.filter(n => (n.status||'offen') === 'erledigt').length;

  if (state.status) list = list.filter(n => (n.status||'offen') === state.status);
  if (q)            list = list.filter(n =>
    fuzzySearchMatch(state.search, [n.betreff, n.text, n.datum, n.hv, n.objekt]));

  if (!list.length) {
    container.innerHTML = '<div class="empty">Keine Notizen vorhanden.</div>';
    return;
  }

  const statusLabel = s => s === 'erledigt' ? '● Erledigt' : '○ Offen';
  const statusColor = s => s === 'erledigt' ? 'var(--green)' : 'var(--accent)';
  const shortHv = h => (h||'').replace(' Hausverwaltung GmbH','').replace(' GmbH','').replace(' / La Casa','');

  container.innerHTML = list.map(n => {
    const beschlussHint = n.beschluss_id
      ? (() => { const b = RAW.beschluesse.find(x => x.id === n.beschluss_id);
                 return b ? ` · TOP ${b.top_nr}` : ''; })()
      : '';
    const isSelected = (notizPanelOpen && notizEditId === n.id) ? ' selected' : '';
    const st = n.status || 'offen';
    return `
      <div class="notiz-card${isSelected}" onclick="editNotiz('${n.id}')">
        <div class="notiz-card-header">
          <span class="notiz-betreff">${n.betreff||'(kein Betreff)'}</span>
          <span class="notiz-datum">${n.datum||''}</span>
        </div>
        <div class="notiz-meta">${n.objekt} · ${shortHv(n.hv)}${beschlussHint}</div>
        <div style="display:flex;justify-content:space-between;align-items:center;margin-top:4px">
          ${n.text ? `<div class="notiz-preview">${(n.text).substring(0,100)}${n.text.length>100?'…':''}</div>` : '<div></div>'}
          <button onclick="toggleNotizStatus('${n.id}',event)" style="font-size:11px;color:${statusColor(st)};white-space:nowrap;margin-left:8px;background:none;border:1px solid ${statusColor(st)};border-radius:10px;padding:1px 8px;cursor:pointer;font-weight:600">${statusLabel(st)}</button>
        </div>
        ${(n.gmail_links||[]).map((l,i) => `<a class="notiz-gmail-btn" href="${l.url}" target="_blank" onclick="event.stopPropagation()">${l.label ? `✉ ${l.label}` : (i===0 && (n.gmail_links||[]).length===1 ? '✉ E-Mail öffnen' : `✉ Mail ${i+1}`)}</a>`).join('')}
      </div>`;
  }).join('');
}

// Notizen im Beschluss-Detail-Panel anzeigen
function renderBeschlussNotizen(beschlussId) {
  const container = document.getElementById('d-notizen-list');
  if (!container) return;
  const all = getNotizen();

  // Notizen die direkt diesem Beschluss zugeordnet sind
  const direct  = Object.values(all).filter(n => n.beschluss_id === beschlussId);
  // Notizen zum gleichen Protokoll (Objekt + Datum) ohne direkten Beschluss-Bezug
  const b = RAW.beschluesse.find(x => x.id === beschlussId);
  const proto = b ? Object.values(all).filter(n =>
    !n.beschluss_id && n.objekt === b.weg_objekt && n.datum === b.versammlungs_datum
  ) : [];

  const list = [...direct, ...proto];

  if (!list.length) {
    container.innerHTML = '<div class="linked-notiz-empty">Keine Notizen zu diesem Beschluss oder Protokoll.</div>';
    return;
  }

  const shortHv = h => (h||'').replace(' Hausverwaltung GmbH','').replace(' GmbH','').replace(' / La Casa','');
  container.innerHTML = list.map(n => `
    <div class="linked-notiz-card" onclick="editNotiz('${n.id}'); switchTabDirect('notizen')">
      <div class="linked-notiz-betreff">${n.betreff||'(kein Betreff)'}</div>
      <div class="linked-notiz-meta">${n.datum||''} · ${shortHv(n.hv)}${n.beschluss_id ? ' · direkt verknüpft' : ' · Protokoll-Notiz'}</div>
      ${n.text ? `<div class="linked-notiz-preview">${n.text.substring(0,120)}${n.text.length>120?'…':''}</div>` : ''}
      ${(n.gmail_links||[]).length ? `<div style="display:flex;flex-wrap:wrap;gap:4px;margin-top:6px">${(n.gmail_links||[]).map((l,i) => `<a class="notiz-gmail-btn" href="${l.url}" target="_blank" onclick="event.stopPropagation()">${l.label ? `✉ ${l.label}` : (i===0 && (n.gmail_links||[]).length===1 ? '✉ E-Mail öffnen' : `✉ Mail ${i+1}`)}</a>`).join('')}</div>` : ''}
    </div>
  `).join('');
}

// Neue Notiz mit Beschluss-Kontext vorausgefüllt
function neueNotizFuerBeschluss() {
  if (!state.selected) return;
  const b = RAW.beschluesse.find(x => x.id === state.selected);
  if (!b) return;
  neueNotiz({
    objekt: b.weg_objekt,
    hv: b.hausverwaltung,
    datum: b.versammlungs_datum,
    beschluss_id: b.id,
    betreff: `TOP ${b.top_nr}: ${(b.top_titel||'').substring(0,60)}`,
  });
}

function switchTabDirect(tab) {
  state.tab = tab;
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.tab').forEach(t => {
    if ((t.getAttribute('onclick')||'').includes(`'${tab}'`)) t.classList.add('active');
  });
  document.getElementById('tab-beschluesse').style.display = tab === 'beschluesse' ? 'block' : 'none';
  document.getElementById('tab-protokolle').style.display  = tab === 'protokolle'  ? 'block' : 'none';
  document.getElementById('tab-beirat').style.display      = tab === 'beirat'      ? 'block' : 'none';
  document.getElementById('tab-notizen').style.display     = tab === 'notizen'     ? 'block' : 'none';
  if (tab !== 'notizen') hideNotizPanel();
  render();
}

// ── Tolerante Suche (OCR-Fehler, Umlaute, Leerzeichen) ───────────────────────

function normalizeSearch(s) {
  if (!s) return '';
  s = s.toLowerCase();
  s = s.replace(/ß/g, 'ss');
  s = s.replace(/[äÄ]/g, 'ae').replace(/[öÖ]/g, 'oe').replace(/[üÜ]/g, 'ue');
  s = s.replace(/\s+/g, ''); // Leerzeichen entfernen (OCR-Artefakte)
  return s;
}

function levenshtein(a, b) {
  if (a.length < b.length) { const t = a; a = b; b = t; }
  if (!b.length) return a.length;
  let prev = Array.from({length: b.length+1}, (_,i) => i);
  for (const ca of a) {
    const curr = [prev[0]+1];
    for (let j = 0; j < b.length; j++) {
      curr.push(Math.min(prev[j+1]+1, curr[j]+1, prev[j]+(ca!==b[j]?1:0)));
    }
    prev = curr;
  }
  return prev[b.length];
}

function fuzzyContains(query, text, threshold=0.82) {
  if (!query || !text) return false;
  const q = normalizeSearch(query);
  const t = normalizeSearch(text);
  if (!q) return true;
  if (t.includes(q)) return true;          // exakter Treffer nach Normalisierung
  if (q.length < 4) return t.includes(q); // kurze Queries nur exakt
  // Sliding-Window Levenshtein mit variablen Fensterbreiten (±2)
  // Nötig weil Umlaut-Normalisierung Längen verändert (ä→ae: +1 Zeichen)
  let best = 0;
  for (const w of [q.length, q.length-1, q.length+1, q.length-2, q.length+2]) {
    if (w < 3 || w > t.length) continue;
    for (let i = 0; i <= t.length - w; i++) {
      const dist  = levenshtein(q, t.slice(i, i + w));
      const ratio = 1 - dist / Math.max(q.length, 1);
      if (ratio > best) best = ratio;
      if (best >= 1.0) break;
    }
  }
  return best >= threshold;
}

// Suche nach mehreren Wörtern: alle müssen matchen (AND-Logik)
function fuzzySearchMatch(query, fields) {
  const words = query.trim().split(/\s+/).filter(Boolean);
  const haystack = fields.join(' ');
  return words.every(w => fuzzyContains(w, haystack));
}

// ── Belegprüfung-System ──────────────────────────────────────────────────────

let _belegCache = [];
let belegEditId   = null;
let belegPanelOpen = false;

async function loadBelegpruefungenFromAPI() {
  try {
    const r = await fetch('/api/belegpruefungen');
    _belegCache = await r.json();
  } catch(e) {
    console.error('Belegprüfungen laden fehlgeschlagen:', e);
    _belegCache = [];
  }
}


function fillBelegSelects(objekt, hv) {
  const hvSel  = document.getElementById('bf-hv');
  const objSel = document.getElementById('bf-objekt');
  if (!hvSel || !objSel) return;
  const hvs  = [...new Set(RAW.protokolle.map(p => p.hausverwaltung).filter(Boolean))].sort();
  const objs = [...new Set(RAW.protokolle.map(p => p.weg_objekt).filter(Boolean))].sort();
  const shortHv = h => h.replace(' Hausverwaltung GmbH','').replace(' GmbH','').replace(' / La Casa','');
  hvSel.innerHTML  = '<option value="">– Hausverwaltung –</option>'
    + hvs.map(h => `<option value="${h}">${shortHv(h)}</option>`).join('');
  objSel.innerHTML = '<option value="">– Objekt –</option>'
    + objs.map(o => `<option value="${o}">${o}</option>`).join('');
  if (hv)     hvSel.value  = hv;
  if (objekt) objSel.value = objekt;
}

function renderBelegDokumente(dokumente) {
  const container = document.getElementById('bf-dok-list');
  if (!container) return;
  if (!dokumente || !dokumente.length) {
    container.innerHTML = '<div style="font-size:11px;color:var(--muted);padding:4px 0">Noch keine Dokumente hochgeladen.</div>';
    return;
  }
  container.innerHTML = dokumente.map(d => {
    const openBtn = d.link
      ? `<a class="dok-open-btn" href="${_esc(d.link)}" target="_blank" onclick="event.stopPropagation()">↗ Öffnen</a>`
      : `<span class="dok-open-btn disabled">↗ Öffnen</span>`;
    return `
      <div class="dok-row">
        <span class="dok-name">📄 ${_esc(d.name||'')}</span>
        ${openBtn}
        <button class="dok-del-btn" onclick="removeBelegDokument(${d.id})" title="Entfernen">×</button>
      </div>`;
  }).join('');
}

function neueBelegpruefung() {
  belegEditId = null;
  fillBelegSelects('', '');
  const today = new Date().toISOString().split('T')[0];
  document.getElementById('bf-termin').value = today;
  document.getElementById('bf-ort').value    = '';
  document.getElementById('bf-notiz').value  = '';
  document.getElementById('bf-dok-file').value = '';
  renderBelegDokumente([]);
  document.getElementById('beleg-panel-title').textContent = 'Neue Belegprüfung';
  document.getElementById('beleg-delete-btn').style.display = 'none';
  document.getElementById('beleg-save-note').style.opacity  = '0';
  showBelegPanel();
  renderBelegpruefungen();
}

function editBelegpruefung(id) {
  const bp = _belegCache.find(b => b.id === id);
  if (!bp) return;
  belegEditId = id;
  fillBelegSelects(bp.objekt, bp.hausverwaltung);
  document.getElementById('bf-termin').value = toIsoDate(bp.termin || '');
  document.getElementById('bf-ort').value    = bp.ort   || '';
  document.getElementById('bf-notiz').value  = bp.notiz || '';
  document.getElementById('bf-dok-file').value = '';
  renderBelegDokumente(bp.dokumente || []);
  document.getElementById('beleg-panel-title').textContent = 'Belegprüfung bearbeiten';
  document.getElementById('beleg-delete-btn').style.display = 'inline-block';
  document.getElementById('beleg-save-note').style.opacity  = '0';
  showBelegPanel();
  renderBelegpruefungen();
}

function showBelegPanel() {
  document.getElementById('beleg-global-detail').classList.remove('hidden');
  state.selected  = null;
  belegPanelOpen  = true;
  notizPanelOpen  = false;
}

function hideBelegPanel() {
  document.getElementById('beleg-global-detail').classList.add('hidden');
  belegEditId    = null;
  belegPanelOpen = false;
  renderBelegpruefungen();
}

async function saveBelegpruefung() {
  const termin = document.getElementById('bf-termin').value.trim();
  const objekt = document.getElementById('bf-objekt').value.trim();
  const hv     = document.getElementById('bf-hv').value.trim();
  const ort    = document.getElementById('bf-ort').value.trim();
  const notiz  = document.getElementById('bf-notiz').value;

  const data = { termin, objekt, hausverwaltung: hv, ort, notiz };
  let saved;
  try {
    if (belegEditId) {
      const r = await fetch(`/api/belegpruefungen/${belegEditId}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data),
      });
      saved = await r.json();
    } else {
      const r = await fetch('/api/belegpruefungen', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data),
      });
      saved = await r.json();
      belegEditId = saved.id;
      document.getElementById('beleg-delete-btn').style.display = 'inline-block';
      document.getElementById('beleg-panel-title').textContent  = 'Belegprüfung bearbeiten';
    }
    // Cache aktualisieren
    const idx = _belegCache.findIndex(b => b.id === saved.id);
    if (idx >= 0) {
      saved.dokumente = _belegCache[idx].dokumente; // Dokumente aus lokalem Cache behalten
      _belegCache[idx] = saved;
    } else {
      saved.dokumente = saved.dokumente || [];
      _belegCache.unshift(saved);
    }
  } catch(e) {
    alert('Speichern fehlgeschlagen: ' + e);
    return;
  }
  const note = document.getElementById('beleg-save-note');
  note.style.opacity = '1';
  setTimeout(() => note.style.opacity = '0', 2000);
  renderBelegpruefungen();
}

async function deleteBelegpruefung() {
  if (!belegEditId) return;
  if (!confirm('Belegprüfung und alle Dokumente wirklich löschen?')) return;
  try {
    await fetch(`/api/belegpruefungen/${belegEditId}`, { method: 'DELETE' });
    _belegCache = _belegCache.filter(b => b.id !== belegEditId);
  } catch(e) {
    alert('Löschen fehlgeschlagen: ' + e);
    return;
  }
  document.getElementById('detail-panel').classList.add('hidden');
  hideBelegPanel();
}

async function uploadBelegDateien() {
  const input = document.getElementById('bf-dok-file');
  if (!input.files.length) { alert('Bitte zuerst eine Datei auswählen.'); return; }

  // Noch nicht gespeichert → erst Belegprüfung anlegen
  if (!belegEditId) {
    await saveBelegpruefung();
    if (!belegEditId) return;
  }

  const btn = document.querySelector('.dok-add-btn');
  btn.disabled  = true;
  btn.textContent = '⏳ Wird hochgeladen…';

  for (const file of Array.from(input.files)) {
    try {
      const b64 = await new Promise((res, rej) => {
        const reader = new FileReader();
        reader.onload  = e => res(e.target.result.split(',')[1]);
        reader.onerror = rej;
        reader.readAsDataURL(file);
      });
      const r = await fetch(`/api/belegpruefungen/${belegEditId}/upload`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: file.name, data: b64 }),
      });
      const dok = await r.json();
      const bp  = _belegCache.find(b => b.id === belegEditId);
      if (bp) {
        if (!bp.dokumente) bp.dokumente = [];
        bp.dokumente.push(dok);
        renderBelegDokumente(bp.dokumente);
      }
    } catch(e) {
      alert(`Fehler bei „${file.name}": ${e}`);
    }
  }

  input.value = '';
  btn.disabled    = false;
  btn.textContent = '📁 Hochladen';
  renderBelegpruefungen();
}

async function removeBelegDokument(dokId) {
  if (!confirm('Dokument wirklich entfernen?')) return;
  try {
    await fetch(`/api/belegpruefungen/${belegEditId}/dokumente/${dokId}`, { method: 'DELETE' });
    const bp = _belegCache.find(b => b.id === belegEditId);
    if (bp) {
      bp.dokumente = (bp.dokumente || []).filter(d => d.id !== dokId);
      renderBelegDokumente(bp.dokumente);
    }
    renderBelegpruefungen();
  } catch(e) {
    alert('Löschen fehlgeschlagen: ' + e);
  }
}

async function openBelegOrdner() {
  try {
    const r = await fetch('/api/belegpruefung/open-folder');
    if (!r.ok) {
      const err = await r.json().catch(() => ({}));
      alert('Fehler: ' + (err.error || r.status));
    }
  } catch(e) {
    alert('Server nicht erreichbar – bitte Server neu starten.');
  }
}

function renderBelegpruefungen() {
  const container = document.getElementById('belegpruefung-list');
  if (!container) return;

  const list = [..._belegCache].sort((a,b) => (b.termin||'').localeCompare(a.termin||''));

  if (!list.length) {
    container.innerHTML = '<div class="empty">Noch keine Belegprüfungen erfasst.<br><span style="font-size:11px">Klicke auf „＋ Neue Belegprüfung" um den ersten Termin anzulegen.</span></div>';
    return;
  }

  const shortHv = h => (h||'').replace(' Hausverwaltung GmbH','').replace(' GmbH','').replace(' / La Casa','');
  container.innerHTML = list.map(bp => {
    const isSelected = (belegPanelOpen && belegEditId === bp.id) ? ' selected' : '';
    const dokCount   = (bp.dokumente || []).length;
    return `
      <div class="beleg-card${isSelected}" onclick="editBelegpruefung(${bp.id})">
        <div class="beleg-card-header">
          <span class="beleg-termin">${toDisplayDate(bp.termin||'')}</span>
          <span class="beleg-objekt">${bp.objekt||'–'}</span>
          ${dokCount ? `<span class="beleg-dok-count">📄 ${dokCount} Dokument${dokCount!==1?'e':''}</span>` : ''}
        </div>
        <div class="beleg-meta">${shortHv(bp.hausverwaltung||'')}${bp.ort ? ` · ${bp.ort}` : ''}</div>
        ${bp.notiz ? `<div class="beleg-notiz-preview">${bp.notiz.substring(0,140)}${bp.notiz.length>140?'…':''}</div>` : ''}
      </div>`;
  }).join('');
}

// ── Manueller Editor (API-basiert) ───────────────────────────────────────────
// Edits werden direkt in die DB geschrieben (beschluesse + beschluss_edits)
// RAW.edits_meta = { [beschluss_id]: [felder] } für Badge-Anzeige

// Effektive Felder (kein Merge mehr nötig, Edits stehen direkt in RAW)
function effectiveB(b) { return b; }

// ── Globale Navigation ─────────────────────────────────────────────────────────
function setGlobalView(view) {
  document.querySelectorAll('.global-view').forEach(v => v.classList.remove('active'));
  document.getElementById('view-' + view).classList.add('active');
  document.querySelectorAll('.gnav-btn').forEach(b => b.classList.remove('active'));
  document.getElementById('gnav-' + view).classList.add('active');
  if (view === 'import')        impLoadProtokolle();
  if (view === 'belegpruefung') renderBelegpruefungen();
}

// Programmatisch zum Import-View wechseln und einen Beschluss öffnen
function openImportEdit(protoId, beschlussId) {
  setGlobalView('import');
  impSetTopMode('manuell');
  setTimeout(async () => {
    await impLoadProtokolle();
    const sel = document.getElementById('imp-proto-select');
    sel.value = protoId;
    impCurrentProtoId = String(protoId);
    document.getElementById('imp-btn-delete-proto').style.display = 'inline-block';
    await _impLoadBeschluesseFull();
    // PDF automatisch laden
    const proto = RAW.protokolle.find(p => p.id == protoId);
    if (proto?.dateiname) impTryAutoLoadPDF(proto.dateiname);
    if (beschlussId) impLoadBeschluss(beschlussId);
    else impSetMode('list');
  }, 80);
}

function openImportNewProto() {
  setGlobalView('import');
  impSetTopMode('manuell');
  setTimeout(() => impOpenNewProtoModal(), 80);
}

// ── Breadcrumb ─────────────────────────────────────────────────────────────────
function updateBreadcrumb() { /* entfernt – Breadcrumb nicht mehr vorhanden */ }
function clearBreadcrumbFilter() { clearFilters(); }

// ── Import – gemeinsame Hilfsobjekte ──────────────────────────────────────────
const IMP_HV_MAP = {
  'Am Frauentor':    'La Casa Hausverwaltung GmbH',
  'Dr.-Külz-Straße': 'MM-Consult',
  'Mariental':       'Bernhardt / La Casa Hausverwaltung GmbH',
  'Rosengarten':     'MM-Consult',
};

let impCurrentProtoId  = null;
let impCurrentBeschluss = null;
let impBeschluesse      = [];

// ── Import – Modus-Wahl (Analyse / Import / Manuell) ──────────────────────────
function impSetTopMode(mode) {
  const isManuell = (mode === 'manuell');
  const isAnalyse = (mode === 'analyse');
  const isImport  = (mode === 'import');
  document.getElementById('imp-analyse-view').style.display  = isAnalyse ? 'flex' : 'none';
  document.getElementById('imp-import-view').style.display   = isImport  ? 'flex' : 'none';
  document.getElementById('imp-manuell-view').style.display  = isManuell ? 'flex' : 'none';
  document.getElementById('imp-proto-bar').style.display     = isManuell ? 'flex' : 'none';
  document.getElementById('imp-proto-info').style.display    = isManuell ? 'inline' : 'none';
  document.getElementById('imp-mode-analyse-btn').classList.toggle('active', isAnalyse);
  document.getElementById('imp-mode-import-btn').classList.toggle('active',  isImport);
  document.getElementById('imp-mode-manuell-btn').classList.toggle('active', isManuell);
}

// ── Import Manuell: Protokolle laden ──────────────────────────────────────────
async function impLoadProtokolle() {
  const r   = await fetch('/api/data');
  const d   = await r.json();
  const sel = document.getElementById('imp-proto-select');
  const cur = sel.value;
  sel.innerHTML = '<option value="">– Protokoll wählen –</option>';
  d.protokolle
    .sort((a,b) => (a.weg_objekt + a.versammlungs_datum).localeCompare(b.weg_objekt + b.versammlungs_datum))
    .forEach(p => {
      const o = document.createElement('option');
      o.value = p.id;
      o.textContent = `${p.weg_objekt} · ${p.versammlungs_datum}`;
      sel.appendChild(o);
    });
  if (cur) sel.value = cur;
  if (impCurrentProtoId) _impLoadBeschluesseFull();
}

async function impOnProtoSelect() {
  impCurrentProtoId = document.getElementById('imp-proto-select').value || null;
  impBeschluesse = [];
  impSetMode('list');
  impUpdateNewBeschlussButtons();
  document.getElementById('imp-btn-delete-proto').style.display = impCurrentProtoId ? 'inline-block' : 'none';
  if (!impCurrentProtoId) {
    document.getElementById('imp-proto-info').textContent = '–';
    return;
  }
  await _impLoadBeschluesseFull();
  const r = await fetch('/api/data');
  const d = await r.json();
  const p = d.protokolle.find(x => x.id == impCurrentProtoId);
  if (p) {
    document.getElementById('imp-proto-info').textContent =
      `${p.weg_objekt} · ${p.versammlungs_datum}`;
    impTryAutoLoadPDF(p.dateiname);
  }
}

async function _impLoadBeschluesseFull() {
  if (!impCurrentProtoId) return;
  const r = await fetch('/api/data');
  const d = await r.json();
  impBeschluesse = d.beschluesse.filter(b => b.protokoll_id == impCurrentProtoId);
  impRenderList();
  document.getElementById('imp-list-count').textContent = impBeschluesse.length;
}

function impRenderList() {
  const c = document.getElementById('imp-beschluss-list');
  if (!impBeschluesse.length) {
    c.innerHTML = '<div class="imp-empty">Noch keine Beschlüsse<br>→ „Neuer Beschluss" klicken</div>';
    return;
  }
  const sortTop = s => {
    const parts = String(s).split('.');
    let score = 0, factor = 1;
    for (const p of parts) {
      const n = parseInt(p);
      score += (isNaN(n) ? (p.toLowerCase().charCodeAt(0) - 96) : n) * factor;
      factor /= 1000;
    }
    return score;
  };
  c.innerHTML = [...impBeschluesse]
    .sort((a,b) => sortTop(a.top_nr) - sortTop(b.top_nr))
    .map(b => {
      const erg = b.ergebnis || '';
      const cls = erg.includes('angenommen') ? 'ang' : erg.includes('abgelehnt') ? 'abg' : '';
      const sel = impCurrentBeschluss == b.id ? ' selected' : '';
      return `<div class="imp-beschluss-card${sel}" onclick="impLoadBeschluss(${b.id})">
        <div class="imp-bc-top">TOP ${b.top_nr}${b.seite ? ' · S.'+b.seite : ''}</div>
        <div class="imp-bc-titel">${b.top_titel || (b.beschluss_text||'').substring(0,60) || '(kein Titel)'}</div>
        <div class="imp-bc-meta">
          <span>JA: ${b.ja_stimmen || '–'}</span>
          ${b.enthaltungen ? `<span>Enth: ${b.enthaltungen}</span>` : ''}
          ${erg ? `<span class="imp-bc-ergebnis ${cls}">${erg}</span>` : ''}
        </div>
      </div>`;
    }).join('');
}

function impLoadBeschluss(id) {
  const b = impBeschluesse.find(x => x.id === id);
  if (!b) return;
  impCurrentBeschluss = id;
  document.getElementById('imp-f-top').value      = b.top_nr    || '';
  document.getElementById('imp-f-titel').value    = b.top_titel || '';
  document.getElementById('imp-f-text').value     = b.beschluss_text || '';
  document.getElementById('imp-f-ja').value       = b.ja_stimmen    || '';
  document.getElementById('imp-f-nein').value     = b.nein_stimmen  || '';
  document.getElementById('imp-f-enth').value     = b.enthaltungen  || '';
  document.getElementById('imp-f-ergebnis').value = b.ergebnis      || 'angenommen';
  document.getElementById('imp-f-beirat').value   = b.beirat_relevant ? '1' : '0';
  document.getElementById('imp-f-seite').value    = (b.seite != null) ? b.seite : '';
  const fp = document.getElementById('imp-form-panel');
  fp.classList.remove('imp-mode-list','imp-mode-new','imp-mode-edit');
  fp.classList.add('imp-mode-edit');
  document.getElementById('imp-form-title').textContent = `BEARBEITEN · TOP ${b.top_nr}`;
  document.getElementById('imp-save-feedback').textContent = '';
  impRenderList();
}

async function impSaveBeschluss() {
  if (!impCurrentProtoId) { alert('Bitte zuerst ein Protokoll wählen.'); return; }
  const top = document.getElementById('imp-f-top').value.trim();
  if (!top) { alert('TOP-Nummer ist Pflicht.'); return; }
  const btn = document.getElementById('imp-btn-save');
  if (btn.disabled) return;
  btn.disabled = true;
  const payload = {
    protokoll_id:    parseInt(impCurrentProtoId),
    top_nr:          top,
    top_titel:       document.getElementById('imp-f-titel').value.trim(),
    beschluss_text:  document.getElementById('imp-f-text').value.trim(),
    ja_stimmen:      document.getElementById('imp-f-ja').value.trim()   || null,
    nein_stimmen:    document.getElementById('imp-f-nein').value.trim() || null,
    enthaltungen:    document.getElementById('imp-f-enth').value.trim() || null,
    ergebnis:        document.getElementById('imp-f-ergebnis').value  || null,
    beirat_relevant: parseInt(document.getElementById('imp-f-beirat').value),
    seite:           (() => { const v = document.getElementById('imp-f-seite').value; return v !== '' ? parseInt(v) : null; })(),
  };
  const wasNew = !impCurrentBeschluss;
  const url    = wasNew ? '/api/import/beschluss' : `/api/import/beschluss/${impCurrentBeschluss}`;
  const method = wasNew ? 'POST' : 'PUT';
  try {
    const r = await fetch(url, { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
    if (!r.ok) throw new Error(await r.text());
    const saved = await r.json();
    // RAW aktualisieren
    const dataResp = await fetch('/api/data');
    const data = await dataResp.json();
    RAW.beschluesse = data.beschluesse || [];
    RAW.protokolle  = data.protokolle  || [];
    await _impLoadBeschluesseFull();
    if (wasNew) {
      impSetMode('new');
    } else {
      impCurrentBeschluss = saved.id;
      impLoadBeschluss(saved.id);
      document.getElementById('imp-save-feedback').textContent = `✓ TOP ${saved.top_nr} gespeichert`;
    }
  } catch(e) { alert('Fehler beim Speichern: ' + e); }
  finally { btn.disabled = false; }
}

async function impDeleteBeschluss() {
  if (!impCurrentBeschluss) return;
  const b = impBeschluesse.find(x => x.id === impCurrentBeschluss);
  if (!confirm(`TOP ${b?.top_nr || ''} wirklich löschen?`)) return;
  try {
    const r = await fetch(`/api/import/beschluss/${impCurrentBeschluss}`, { method: 'DELETE' });
    if (!r.ok) throw new Error(await r.text());
    const dataResp = await fetch('/api/data');
    const data = await dataResp.json();
    RAW.beschluesse = data.beschluesse || [];
    await _impLoadBeschluesseFull();
    impSetMode('list');
  } catch(e) { alert('Fehler beim Löschen: ' + e); }
}

async function impDeleteProtokoll() {
  if (!impCurrentProtoId) return;
  const sel  = document.getElementById('imp-proto-select');
  const name = sel.options[sel.selectedIndex]?.textContent || impCurrentProtoId;
  if (!confirm(`Protokoll „${name}" samt ALLEN Beschlüssen wirklich löschen?`)) return;
  try {
    const r = await fetch(`/api/import/protokoll/${impCurrentProtoId}`, { method: 'DELETE' });
    if (!r.ok) throw new Error(await r.text());
    const res = await r.json();
    impCurrentProtoId = null; impBeschluesse = [];
    impSetMode('list'); impUpdateNewBeschlussButtons();
    document.getElementById('imp-proto-info').textContent = '–';
    document.getElementById('imp-btn-delete-proto').style.display = 'none';
    const dataResp = await fetch('/api/data');
    const data = await dataResp.json();
    RAW.beschluesse = data.beschluesse || [];
    RAW.protokolle  = data.protokolle  || [];
    await impLoadProtokolle();
    alert(`Protokoll gelöscht (${res.beschluesse_deleted} Beschlüsse entfernt).`);
  } catch(e) { alert('Fehler beim Löschen: ' + e); }
}

function impSetMode(mode) {
  if (mode === 'new' && !impCurrentProtoId) return;
  const fp = document.getElementById('imp-form-panel');
  fp.classList.remove('imp-mode-list','imp-mode-new','imp-mode-edit');
  fp.classList.add('imp-mode-' + mode);
  document.getElementById('imp-save-feedback').textContent = '';
  if (mode === 'list') {
    impCurrentBeschluss = null;
    impClearFields();
    document.getElementById('imp-form-title').textContent = 'BESCHLÜSSE';
    impRenderList();
  } else if (mode === 'new') {
    impCurrentBeschluss = null;
    impClearFields();
    document.getElementById('imp-form-title').textContent = 'NEUER BESCHLUSS';
    impRenderList();
    document.getElementById('imp-f-top').focus();
  }
}

function impClearFields() {
  ['imp-f-top','imp-f-titel','imp-f-text','imp-f-ja','imp-f-nein','imp-f-enth','imp-f-seite']
    .forEach(id => document.getElementById(id).value = '');
  document.getElementById('imp-f-ergebnis').value = 'angenommen';
  document.getElementById('imp-f-beirat').value   = '0';
}

function impUpdateNewBeschlussButtons() {
  const off = !impCurrentProtoId;
  document.querySelectorAll('#imp-btn-new-wrap .imp-btn, #imp-btn-new-header').forEach(b => {
    b.disabled = off; b.style.opacity = off ? '0.35' : ''; b.style.cursor = off ? 'not-allowed' : '';
  });
}

function impLoadPDF(input) {
  const file = input.files[0];
  if (!file) return;
  document.getElementById('imp-pdf-drop').style.display  = 'none';
  document.getElementById('imp-pdf-frame').style.display = 'block';
  document.getElementById('imp-pdf-frame').src = URL.createObjectURL(file);
}

async function impTryAutoLoadPDF(dateiname) {
  if (!dateiname) return;
  try {
    const r = await fetch(`/output/${dateiname}`, { method: 'HEAD' });
    if (r.ok) {
      document.getElementById('imp-pdf-drop').style.display  = 'none';
      document.getElementById('imp-pdf-frame').style.display = 'block';
      document.getElementById('imp-pdf-frame').src = `/output/${dateiname}`;
    }
  } catch(e) { /* Server nicht erreichbar → Drop-Zone bleibt */ }
}

function impOpenNewProtoModal() {
  document.getElementById('imp-proto-modal').style.display = 'flex';
}
function impCloseModal() {
  document.getElementById('imp-proto-modal').style.display = 'none';
}
function impOnObjektChange() {
  document.getElementById('imp-m-hv').value = IMP_HV_MAP[document.getElementById('imp-m-objekt').value] || '';
}

function _isoToGerman(iso) {
  if (!iso) return '';
  const [y, m, d] = iso.split('-');
  return `${d}.${m}.${y}`;
}

async function impSaveProtokoll() {
  const objekt   = document.getElementById('imp-m-objekt').value;
  const datumIso = document.getElementById('imp-m-datum').value.trim();
  if (!objekt || !datumIso) { alert('Objekt und Datum sind Pflicht.'); return; }
  const payload = {
    dateiname:          document.getElementById('imp-m-dateiname').value.trim()
                        || `${objekt.replace(/[^a-zA-Z]/g,'')}${datumIso}.pdf`,
    pdf_pfad:           `output/${document.getElementById('imp-m-dateiname').value.trim() || datumIso+'.pdf'}`,
    versammlungs_datum: _isoToGerman(datumIso),
    hausverwaltung:     document.getElementById('imp-m-hv').value.trim(),
    weg_objekt:         objekt,
    ort:                document.getElementById('imp-m-ort').value.trim(),
  };
  try {
    const r = await fetch('/api/import/protokoll', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
    });
    if (!r.ok) throw new Error(await r.text());
    const saved = await r.json();
    impCloseModal();
    const dataResp = await fetch('/api/data');
    const data = await dataResp.json();
    RAW.protokolle  = data.protokolle  || [];
    RAW.beschluesse = data.beschluesse || [];
    buildFilters();
    await impLoadProtokolle();
    document.getElementById('imp-proto-select').value = saved.id;
    impCurrentProtoId = String(saved.id);
    await impOnProtoSelect();
  } catch(e) { alert('Fehler: ' + e); }
}

// ── Import – PDF Analyse ───────────────────────────────────────────────────────
let _impPdfData     = null;
let _impAnalyseRes  = null;

function impOnFileSelect(input) {
  const file = input.files[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = e => {
    _impPdfData = e.target.result.split(',')[1];
    document.getElementById('imp-filename-display').textContent = file.name;
    document.getElementById('imp-filename-display').style.display = 'block';
    document.getElementById('imp-upload-zone').classList.add('has-file');
    document.getElementById('imp-analyse-btn').disabled = false;
    document.getElementById('imp-analyse-btn').dataset.filename = file.name;
    _impAnalyseRes = null;
  };
  reader.readAsDataURL(file);
}

function impOnDrop(event) {
  event.preventDefault();
  document.getElementById('imp-upload-zone').classList.remove('dragover');
  const file = event.dataTransfer.files[0];
  if (file && file.name.endsWith('.pdf')) {
    try {
      const dt = new DataTransfer();
      dt.items.add(file);
      document.getElementById('imp-file-input').files = dt.files;
    } catch(e) {}
    impOnFileSelect({ files: [file] });
  }
}

async function impStartAnalyse() {
  if (!_impPdfData) return;
  const filename = document.getElementById('imp-analyse-btn').dataset.filename || 'upload.pdf';
  const useLlm   = document.getElementById('imp-opt-llm').checked;

  document.getElementById('imp-step-upload').style.display   = 'block';
  document.getElementById('imp-step-upload').style.display   = 'none';
  document.getElementById('imp-step-loading').style.display  = 'flex';
  document.getElementById('imp-step-result').style.display   = 'none';

  try {
    const r = await fetch('/api/import/analyse', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ pdf_data: _impPdfData, filename, use_llm: useLlm }),
    });
    if (!r.ok) throw new Error(await r.text());
    _impAnalyseRes = await r.json();
    _impShowAnalyseResult(_impAnalyseRes);
  } catch(e) {
    document.getElementById('imp-step-upload').style.display  = 'block';
    document.getElementById('imp-step-loading').style.display = 'none';
    alert('Fehler bei der Analyse: ' + e);
  }
}

function _impToIsoDate(d) {
  if (!d) return '';
  const p = d.split('.');
  if (p.length === 3) return `${p[2]}-${p[1].padStart(2,'0')}-${p[0].padStart(2,'0')}`;
  return d;
}

// ── Wort-Diff-Algorithmus (LCS-basiert) ────────────────────────────────────
function _escHtml(s) {
  return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

function _wordDiff(textPdf, textDb) {
  // Tokenisiert auf Wort-/Leerzeichen-Ebene, normalisiert Satzzeichen für Vergleich
  const tok  = t => (t.match(/\S+|\s+/g) || []);
  const norm = s => s.toLowerCase().replace(/[.,;:!?»«""''„"–\-]/g, '').trim();
  const a = tok(textPdf), b = tok(textDb);
  const m = a.length, n = b.length;

  // LCS-Tabelle
  const dp = Array.from({length: m+1}, () => new Int32Array(n+1));
  for (let i = m-1; i >= 0; i--)
    for (let j = n-1; j >= 0; j--)
      dp[i][j] = norm(a[i]) === norm(b[j])
        ? dp[i+1][j+1] + 1
        : Math.max(dp[i+1][j], dp[i][j+1]);

  // Diff-Sequenz aufbauen: {type: 'eq'|'pdf'|'db', a?, b?}
  const ops = [];
  let i = 0, j = 0;
  while (i < m || j < n) {
    if (i < m && j < n && norm(a[i]) === norm(b[j])) {
      ops.push({type:'eq', a:a[i], b:b[j]}); i++; j++;
    } else if (j < n && (i >= m || dp[i][j+1] >= (i < m ? dp[i+1][j] : 0))) {
      ops.push({type:'db', b:b[j]}); j++;
    } else {
      ops.push({type:'pdf', a:a[i]}); i++;
    }
  }

  // HTML für PDF-Seite: grün = nur im PDF, rot = nur in DB
  const pdfHtml = ops.map(op => {
    if (op.type === 'eq')  return _escHtml(op.a);
    if (op.type === 'pdf') return op.a.trim() ? `<mark class="diff-pdf">${_escHtml(op.a)}</mark>` : _escHtml(op.a);
    return op.b.trim() ? `<mark class="diff-db">${_escHtml(op.b)}</mark>` : '';
  }).join('');

  // HTML für DB-Seite: grün = nur in DB, rot = nur im PDF
  const dbHtml = ops.map(op => {
    if (op.type === 'eq') return _escHtml(op.b);
    if (op.type === 'db') return op.b.trim() ? `<mark class="diff-pdf">${_escHtml(op.b)}</mark>` : _escHtml(op.b);
    return op.a.trim() ? `<mark class="diff-db">${_escHtml(op.a)}</mark>` : '';
  }).join('');

  return {pdfHtml, dbHtml};
}

function _impShowAnalyseResult(result) {
  document.getElementById('imp-step-loading').style.display = 'none';
  const resultDiv = document.getElementById('imp-step-result');
  Object.assign(resultDiv.style, {display:'flex', flexDirection:'column', flex:'1', overflow:'hidden'});

  const proto       = result.protokoll;
  const pdfBeschl   = result.beschluesse || [];
  const content     = document.getElementById('imp-result-content');

  // Protokoll nicht in DB gefunden
  if (!result.already_exists) {
    content.innerHTML = `
      <div class="diff-not-found">
        <div class="nf-icon">🔍</div>
        <strong>Kein passendes Protokoll in der Datenbank</strong>
        <div>${_escHtml(proto.weg_objekt || '?')} · ${_escHtml(proto.versammlungs_datum || '?')}</div>
        <div style="margin-top:4px">Das Protokoll ist noch nicht importiert — bitte zuerst manuell anlegen.</div>
      </div>
      <div class="diff-footer">
        <button onclick="_impResetAnalyse()" style="padding:7px 18px;background:none;border:1px solid var(--border2);border-radius:5px;color:var(--muted);font-family:var(--mono);font-size:12px;cursor:pointer">↩ Andere PDF wählen</button>
      </div>`;
    return;
  }

  // Beschlüsse aus DB für dieses Protokoll
  const dbBeschl = RAW.beschluesse.filter(b => b.protokoll_id === result.existing_id);

  // Alle TOPs aus PDF + DB sammeln und sortieren
  const allTops = [...new Set([
    ...pdfBeschl.map(b => b.top_nr),
    ...dbBeschl.map(b => b.top_nr),
  ])].sort((a, b) => {
    const n = (s) => parseFloat((s||'').replace(',','.')) || 0;
    return n(a) - n(b);
  });

  let diffCount = 0;
  const rows = allTops.map(top => {
    const pB = pdfBeschl.find(b => b.top_nr === top);
    const dB = dbBeschl.find(b => b.top_nr === top);

    if (!pB) {
      diffCount++;
      return `<div class="diff-row diff-row-warn">
        <div class="diff-top">TOP ${_escHtml(top)} <span class="diff-badge warn">Nur in DB</span></div>
        <div class="diff-text-label"><span>PDF</span><span>Datenbank</span></div>
        <div class="diff-texts">
          <div class="diff-text pdf" style="color:var(--muted);font-style:italic">— fehlt im PDF —</div>
          <div class="diff-text db">${_escHtml(dB.beschluss_text||'')}</div>
        </div></div>`;
    }
    if (!dB) {
      diffCount++;
      return `<div class="diff-row diff-row-warn">
        <div class="diff-top">TOP ${_escHtml(top)} <span class="diff-badge warn">Nur im PDF</span></div>
        <div class="diff-text-label"><span>PDF</span><span>Datenbank</span></div>
        <div class="diff-texts">
          <div class="diff-text pdf">${_escHtml(pB.beschluss_text||'')}</div>
          <div class="diff-text db" style="color:var(--muted);font-style:italic">— nicht in DB —</div>
        </div></div>`;
    }

    const tPdf = (pB.beschluss_text||'').trim();
    const tDb  = (dB.beschluss_text||'').trim();

    // Wort-Diff berechnen (normalisiert Satzzeichen + Silbentrennung)
    const {pdfHtml, dbHtml} = _wordDiff(tPdf, tDb);
    const hasDiff = pdfHtml.includes('class="diff-') || dbHtml.includes('class="diff-');

    if (!hasDiff) {
      // Nur Formatierungsunterschiede (Satzzeichen, Zeilenumbrüche) → identisch
      return `<div class="diff-row diff-row-ok">
        <div class="diff-top">TOP ${_escHtml(top)} <span class="diff-badge ok">✓ identisch</span></div>
      </div>`;
    }

    diffCount++;
    return `<div class="diff-row diff-row-warn">
      <div class="diff-top">TOP ${_escHtml(top)} <span class="diff-badge warn">⚠ Unterschied</span></div>
      <div class="diff-text-label"><span>PDF</span><span>Datenbank</span></div>
      <div class="diff-texts">
        <div class="diff-text pdf">${pdfHtml}</div>
        <div class="diff-text db">${dbHtml}</div>
      </div></div>`;
  }).join('');

  const statusBadge = diffCount === 0
    ? `<span class="diff-badge ok">✓ Alles identisch</span>`
    : `<span class="diff-badge warn">⚠ ${diffCount} Unterschied${diffCount>1?'e':''}</span>`;

  content.innerHTML = `
    <div class="diff-header">
      <div>
        <strong>${_escHtml(proto.weg_objekt||'')}</strong>
        · ${_escHtml(proto.versammlungs_datum||'')}
        &nbsp;${statusBadge}
      </div>
      <div style="font-size:11px;color:var(--muted)">${pdfBeschl.length} Beschlüsse im PDF · ${dbBeschl.length} in DB</div>
    </div>
    <div class="diff-list">${rows}</div>
    <div class="diff-footer">
      <button onclick="_impResetAnalyse()" style="padding:7px 18px;background:none;border:1px solid var(--border2);border-radius:5px;color:var(--muted);font-family:var(--mono);font-size:12px;cursor:pointer">↩ Andere PDF wählen</button>
    </div>`;
}

function _impResetAnalyse() {
  _impPdfData = null; _impAnalyseRes = null;
  document.getElementById('imp-step-upload').style.display   = 'block';
  document.getElementById('imp-step-loading').style.display  = 'none';
  document.getElementById('imp-step-result').style.display   = 'none';
  document.getElementById('imp-upload-zone').classList.remove('has-file');
  document.getElementById('imp-filename-display').style.display = 'none';
  document.getElementById('imp-analyse-btn').disabled = true;
  document.getElementById('imp-file-input').value = '';
}


// ── Import-Modus (Einzeldatei → DB) ────────────────────────────────────────────
let _impiPdfData  = null;
let _impiAnalyseRes = null;

function impiOnFileSelect(input) {
  const file = (input.files || input)[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = e => {
    _impiPdfData = e.target.result.split(',')[1];
    document.getElementById('impi-upload-zone').classList.add('has-file');
    const disp = document.getElementById('impi-filename-display');
    disp.textContent = file.name;
    disp.style.display = 'block';
    const btn = document.getElementById('impi-start-btn');
    btn.disabled = false;
    btn.dataset.filename = file.name;
  };
  reader.readAsDataURL(file);
}

function impiOnDrop(e) {
  e.preventDefault();
  document.getElementById('impi-upload-zone').classList.remove('dragover');
  const file = e.dataTransfer.files[0];
  if (file && file.type === 'application/pdf') impiOnFileSelect({files: [file]});
}

async function impiStart() {
  if (!_impiPdfData) return;
  const filename = document.getElementById('impi-start-btn').dataset.filename || 'upload.pdf';
  const useLlm   = document.getElementById('impi-opt-llm').checked;
  document.getElementById('impi-step-upload').style.display  = 'none';
  document.getElementById('impi-step-loading').style.display = 'flex';
  document.getElementById('impi-step-result').style.display  = 'none';
  try {
    const r = await fetch('/api/import/analyse', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({pdf_data: _impiPdfData, filename, use_llm: useLlm}),
    });
    if (!r.ok) throw new Error(await r.text());
    _impiAnalyseRes = await r.json();
    _impiShowResult(_impiAnalyseRes, filename);
  } catch(e) {
    document.getElementById('impi-step-upload').style.display  = 'block';
    document.getElementById('impi-step-loading').style.display = 'none';
    alert('Fehler bei der Extraktion: ' + e);
  }
}

function _impiShowResult(result, filename) {
  document.getElementById('impi-step-loading').style.display = 'none';
  const resDiv = document.getElementById('impi-step-result');
  Object.assign(resDiv.style, {display:'flex', flexDirection:'column', flex:'1', overflow:'hidden'});

  const proto = result.protokoll;
  const beschl = result.beschluesse;
  const objekte = ['Am Frauentor','Dr.-Külz-Straße','Mariental','Rosengarten'];

  const warnHtml = result.already_exists
    ? `<div class="impi-already-warn">⚠ Protokoll mit diesem Datum und Objekt existiert bereits in der DB (ID ${result.existing_id}).</div>`
    : '';

  const rows = beschl.map((b, i) => `
    <tr>
      <td><input class="impi-input" style="width:55px" value="${_escHtml(b.top_nr||'')}" data-f="top_nr" data-i="${i}"></td>
      <td><input class="impi-input" value="${_escHtml(b.top_titel||'')}" data-f="top_titel" data-i="${i}"></td>
      <td><textarea class="impi-textarea" rows="2" data-f="beschluss_text" data-i="${i}">${_escHtml(b.beschluss_text||'')}</textarea></td>
      <td><input class="impi-input" style="width:50px" value="${b.ja_stimmen||''}" data-f="ja_stimmen" data-i="${i}"></td>
      <td><input class="impi-input" style="width:50px" value="${b.nein_stimmen||''}" data-f="nein_stimmen" data-i="${i}"></td>
      <td>
        <select class="impi-select" data-f="ergebnis" data-i="${i}">
          <option value="angenommen" ${b.ergebnis==='angenommen'?'selected':''}>angenommen</option>
          <option value="abgelehnt"  ${b.ergebnis==='abgelehnt' ?'selected':''}>abgelehnt</option>
          <option value="kein Beschluss" ${b.ergebnis==='kein Beschluss'?'selected':''}>kein Beschluss</option>
          <option value="" ${!b.ergebnis?'selected':''}>–</option>
        </select>
      </td>
      <td style="text-align:center"><input type="checkbox" ${b.beirat_relevant?'checked':''} data-f="beirat_relevant" data-i="${i}"></td>
      <td><button class="impi-del-btn" onclick="_impiDelRow(${i})" title="Zeile entfernen">✕</button></td>
    </tr>`).join('');

  document.getElementById('impi-result-content').innerHTML = `
    ${warnHtml}
    <div style="font-family:var(--mono);font-size:10px;letter-spacing:.1em;text-transform:uppercase;color:var(--muted);margin-bottom:8px">Protokoll-Metadaten</div>
    <div class="impi-meta-grid">
      <div class="impi-meta-field"><label>WEG-Objekt</label>
        <select id="impi-res-objekt" onchange="document.getElementById('impi-res-hv').value=IMP_HV_MAP[this.value]||''">
          <option value="">– wählen –</option>
          ${objekte.map(o=>`<option value="${o}" ${proto.weg_objekt===o?'selected':''}>${o}</option>`).join('')}
        </select>
      </div>
      <div class="impi-meta-field"><label>Versammlungsdatum</label>
        <input type="date" id="impi-res-datum" value="${_impToIsoDate(proto.versammlungs_datum)}">
      </div>
      <div class="impi-meta-field"><label>Hausverwaltung</label>
        <input type="text" id="impi-res-hv" value="${_escHtml(proto.hausverwaltung||'')}">
      </div>
      <div class="impi-meta-field"><label>Ort</label>
        <input type="text" id="impi-res-ort" value="${_escHtml(proto.ort||'')}">
      </div>
      <div class="impi-meta-field"><label>Dateiname</label>
        <input type="text" id="impi-res-dateiname" value="${_escHtml(proto.dateiname||filename)}">
      </div>
      <div class="impi-meta-field"><label>Format</label>
        <input type="text" value="Format ${proto.format||'?'}${proto.machine_readable?' · maschinenlesbar':' · OCR/Tesseract'}" readonly style="opacity:0.5;font-size:11px">
      </div>
    </div>
    <div style="font-family:var(--mono);font-size:10px;letter-spacing:.1em;text-transform:uppercase;color:var(--muted);margin-bottom:6px">${beschl.length} Beschlüsse erkannt</div>
    <div style="flex:1;overflow:auto">
      <table class="impi-table">
        <thead><tr>
          <th>TOP</th><th>Titel</th><th>Beschlusstext</th>
          <th>JA</th><th>NEIN</th><th>Ergebnis</th><th>Beirat</th><th></th>
        </tr></thead>
        <tbody id="impi-tbody">${rows}</tbody>
      </table>
    </div>
    <div style="display:flex;gap:12px;align-items:center;flex-wrap:wrap;padding:12px 0 4px">
      <button class="impi-import-btn" id="impi-do-btn" onclick="_impiDoImport()">In Datenbank importieren</button>
      <button onclick="_impiReset()" style="padding:8px 16px;background:none;border:1px solid var(--border2);border-radius:6px;color:var(--muted);font-family:var(--mono);font-size:12px;cursor:pointer">↩ Andere PDF wählen</button>
      <span id="impi-feedback"></span>
    </div>`;

  // Live-Sync Inputs → _impiAnalyseRes
  const tbody = document.getElementById('impi-tbody');
  tbody.addEventListener('input', e => {
    const el = e.target, i = parseInt(el.dataset.i), f = el.dataset.f;
    if (isNaN(i) || !f) return;
    _impiAnalyseRes.beschluesse[i][f] = el.type === 'checkbox' ? (el.checked?1:0) : el.value;
  });
  tbody.addEventListener('change', e => {
    const el = e.target, i = parseInt(el.dataset.i), f = el.dataset.f;
    if (isNaN(i) || !f) return;
    _impiAnalyseRes.beschluesse[i][f] = el.value;
  });
}

function _impiDelRow(idx) {
  if (!_impiAnalyseRes) return;
  _impiAnalyseRes.beschluesse.splice(idx, 1);
  _impiShowResult(_impiAnalyseRes, document.getElementById('impi-res-dateiname')?.value || '');
}

function _impiReset() {
  _impiPdfData = null; _impiAnalyseRes = null;
  document.getElementById('impi-step-upload').style.display  = 'block';
  document.getElementById('impi-step-loading').style.display = 'none';
  document.getElementById('impi-step-result').style.display  = 'none';
  document.getElementById('impi-upload-zone').classList.remove('has-file');
  document.getElementById('impi-filename-display').style.display = 'none';
  document.getElementById('impi-start-btn').disabled = true;
  document.getElementById('impi-file-input').value = '';
}

async function _impiDoImport() {
  if (!_impiAnalyseRes) return;
  const datumIso = document.getElementById('impi-res-datum').value;
  const protokoll = {
    dateiname:          document.getElementById('impi-res-dateiname').value.trim(),
    versammlungs_datum: _isoToGerman(datumIso),
    hausverwaltung:     document.getElementById('impi-res-hv').value.trim(),
    weg_objekt:         document.getElementById('impi-res-objekt').value,
    ort:                document.getElementById('impi-res-ort').value.trim(),
  };
  protokoll.pdf_pfad = `output/${protokoll.dateiname}`;
  const beschluesse = _impiAnalyseRes.beschluesse.filter(b => b.top_nr);
  const btn = document.getElementById('impi-do-btn');
  btn.disabled = true; btn.textContent = '⏳ Importiere…';
  try {
    const r = await fetch('/api/import/protokoll-komplett', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({protokoll, beschluesse, pdf_data: _impiPdfData}),
    });
    if (!r.ok) throw new Error(await r.text());
    const res = await r.json();
    // Globale Daten aktualisieren
    const dr = await fetch('/api/data');
    const d  = await dr.json();
    RAW.protokolle  = d.protokolle  || [];
    RAW.beschluesse = d.beschluesse || [];
    RAW.edits_meta  = d.edits_meta  || {};
    buildFilters(); updateStatusCounts(); updateNotizBadge();
    document.getElementById('hdr-proto').textContent  = RAW.protokolle.length;
    document.getElementById('hdr-beschl').textContent = RAW.beschluesse.length;
    document.getElementById('hdr-beirat').textContent = RAW.beschluesse.filter(b=>b.beirat_relevant).length;
    document.getElementById('tab-beirat-badge').textContent = RAW.beschluesse.filter(b=>b.beirat_relevant).length;
    document.getElementById('impi-feedback').innerHTML =
      `<span class="impi-success">✓ ${res.beschluesse_count} Beschlüsse importiert (Protokoll-ID ${res.protokoll_id})</span>`;
    btn.textContent = '✓ Importiert'; btn.style.background = 'var(--green)';
  } catch(e) {
    btn.disabled = false; btn.textContent = 'In Datenbank importieren';
    alert('Fehler beim Import: ' + e);
  }
}

// ── PDF austauschen ────────────────────────────────────────────────────────────
let _replacePdfProtoId = null;

function replacePdf(protoId) {
  _replacePdfProtoId = protoId;
  document.getElementById('replace-pdf-input').value = '';
  document.getElementById('replace-pdf-input').click();
}

async function replacePdfUpload(input) {
  const file = input.files[0];
  if (!file || !_replacePdfProtoId) return;
  const proto = RAW.protokolle.find(p => p.id === _replacePdfProtoId);
  const name  = proto ? `${proto.weg_objekt} · ${proto.versammlungs_datum}` : `ID ${_replacePdfProtoId}`;
  if (!confirm(`PDF für „${name}" ersetzen?\n\n→ ${file.name}\n\nDie bestehende _durchsuchbar.pdf wird überschrieben.`)) return;

  // Base64 lesen
  const b64 = await new Promise((res, rej) => {
    const r = new FileReader();
    r.onload  = e => res(e.target.result.split(',')[1]);
    r.onerror = rej;
    r.readAsDataURL(file);
  });

  // Spinner-Hinweis
  const toast = document.createElement('div');
  toast.style.cssText = 'position:fixed;bottom:24px;right:24px;background:var(--surface);border:1px solid var(--border2);padding:12px 20px;border-radius:8px;font-family:var(--mono);font-size:12px;z-index:9999;color:var(--text)';
  toast.textContent = '⏳ PDF wird verarbeitet (OCR) …';
  document.body.appendChild(toast);

  try {
    const r = await fetch(`/api/protokoll/${_replacePdfProtoId}/replace-pdf`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ pdf_data: b64 }),
    });
    if (!r.ok) throw new Error(await r.text());
    toast.style.borderColor = 'var(--green)';
    toast.style.color       = 'var(--green)';
    toast.textContent = '✓ PDF erfolgreich ausgetauscht';
  } catch(e) {
    toast.style.borderColor = 'var(--beirat)';
    toast.style.color       = 'var(--beirat)';
    toast.textContent       = '✗ Fehler: ' + e;
  }
  setTimeout(() => toast.remove(), 4000);
  _replacePdfProtoId = null;
}

// ── Start ──────────────────────────────────────────────────────────────────────
init();
