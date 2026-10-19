'use strict';
// Servergegevens gaan altijd via textContent (h() maakt tekstknopen); innerHTML alleen voor eigen iconen.
const $ = (s) => document.querySelector(s);
const main = $('#main');
const euro = new Intl.NumberFormat('nl-NL', {style: 'currency', currency: 'EUR'});
const getal = (v) => new Intl.NumberFormat('nl-NL').format(v);
const nlNaarGetal = (t) => Number(String(t ?? '').replace(/[^\d,-]/g, '').replace(',', '.')) || 0;
const paths = {
  folder: '<path d="M3 7V5a1 1 0 0 1 1-1h6l2 3h8a1 1 0 0 1 1 1v11H3Z"/><path d="M3 9h18"/>',
  overview: '<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/>',
  list: '<path d="m3 6 1 1 2-2m-3 8 1 1 2-2m-3 8 1 1 2-2M10 6h11M10 13h11M10 20h11"/>',
  export: '<path d="M12 3v12m-4-4 4 4 4-4M4 15v6h16v-6"/>',
  moon: '<path d="M20 14.4A8.5 8.5 0 0 1 9.6 4 8.5 8.5 0 1 0 20 14.4Z"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1.4 1.4m11.2 11.2L19 19M5 19l1.4-1.4M17.6 6.4 19 5"/>',
  sheet: '<rect x="4" y="3" width="16" height="18" rx="1"/><path d="M4 9h16M4 15h16M10 9v12"/>',
};
const icon = (n) => `<svg viewBox="0 0 24 24" aria-hidden="true">${paths[n]}</svg>`;
const svg = (n) => Object.assign(document.createElement('span'), {innerHTML: icon(n)}).firstChild;

// h('div', {class:'x', onclick: fn, 'aria-label': 'y'}, 'tekst', kindknoop, ...)
function h(tag, props = {}, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (v == null || v === false) continue;
    if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
    else if (k === 'class') el.className = v;
    else el.setAttribute(k, v === true ? '' : v);
  }
  for (const k of kids.flat()) if (k != null && k !== false) el.append(k);
  return el;
}

const nav = [['dossier', 'folder', 'Dossier'], ['overzicht', 'overview', 'Overzicht'], ['bevindingen', 'list', 'Bevindingen'], ['export', 'export', 'Export']];
const state = {info: {}, run: null, dossier: '', gekozen: [], bezig: '', fout: '', map: '', filter: {mw: '', groep: '', q: ''}, sorteer: ''};

async function api(url, opties) {
  let r;
  try { r = await fetch(url, opties); } catch { throw new Error('Geen verbinding met Weekstaat. Draait het programma nog?'); }
  const tekst = await r.text();
  let d = {};
  try { d = JSON.parse(tekst); } catch { /* geen JSON */ }
  if (!r.ok) throw new Error(d.fout || `Er ging iets mis (${r.status}).`);
  return d;
}

function heading(titel, uitleg, actie) {
  return h('div', {class: 'page-heading'}, h('div', {}, h('h1', {}, titel, h('span', {class: 'accent-dot'}, '.')), h('p', {}, uitleg)), actie);
}
const knop = (tekst, props = {}) => h('button', {type: 'button', class: 'button' + (props.primair ? ' primary' : ''), ...props, primair: null}, tekst);
const download = (naam, tekst, primair) => h('a', {class: 'button' + (primair ? ' primary' : ''), href: `/api/run/${state.run.run}/bestand/${encodeURIComponent(naam)}`, download: naam, 'aria-label': `${tekst}: ${naam} downloaden`}, svg('export'), tekst);
const leeg = (tekst) => h('div', {class: 'panel'}, h('div', {class: 'empty'}, h('p', {}, tekst), h('p', {}, h('a', {class: 'text-link', href: '#dossier'}, 'Naar het dossier'))));

// ---- Dossier ----
async function draai(body) {
  state.fout = ''; state.bezig = 'Aansluiting wordt gedraaid…'; render();
  try {
    if (body.sessie === true) {
      const {id} = await api('/api/sessie', {method: 'POST'});
      for (const [i, {pad, file}] of state.gekozen.entries()) {
        state.bezig = `Uploaden ${i + 1} van ${state.gekozen.length}…`; render();
        await api(`/api/sessie/${id}/bestand?pad=${encodeURIComponent(pad)}`, {method: 'PUT', body: file});
      }
      state.bezig = 'Aansluiting wordt gedraaid…'; render();
      body = {sessie: id};
    }
    state.run = await api('/api/aansluiten', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
    state.filter = {mw: '', groep: '', q: ''};
    state.bezig = '';
    $('#dossier-name').textContent = body.map ? body.map.split(/[\\/]/).filter(Boolean).pop() : 'Gekozen bestanden';
    location.hash = '#overzicht';
  } catch (e) {
    state.bezig = ''; state.fout = e.message; render();
    const a = $('[role=alert]'); if (a) a.focus();
  }
}

function kiesBestanden(lijst, map) {
  state.gekozen = [...lijst].map((file) => ({file, pad: map ? file.webkitRelativePath.split('/').slice(1).join('/') : file.name})).filter((b) => b.pad);
  state.fout = ''; render();
}

function dossier() {
  const bezig = !!state.bezig;
  const mapInvoer = h('input', {type: 'text', id: 'map', value: state.map, autocomplete: 'off', spellcheck: 'false', disabled: bezig, oninput: (e) => { state.map = e.target.value; }});
  const mapInput = h('input', {type: 'file', webkitdirectory: true, hidden: true, onchange: (e) => kiesBestanden(e.target.files, true)});
  const bestInput = h('input', {type: 'file', multiple: true, hidden: true, onchange: (e) => kiesBestanden(e.target.files, false)});
  const zone = h('div', {class: 'dropzone'},
    h('strong', {}, 'Of kies de bestanden in de browser'),
    h('div', {class: 'row'}, knop('Map kiezen', {disabled: bezig, onclick: () => mapInput.click()}), knop('Bestanden kiezen', {disabled: bezig, onclick: () => bestInput.click()})),
    mapInput, bestInput);
  zone.addEventListener('dragover', (e) => { e.preventDefault(); zone.classList.add('over'); });
  zone.addEventListener('dragleave', () => zone.classList.remove('over'));
  zone.addEventListener('drop', (e) => {
    e.preventDefault(); zone.classList.remove('over');
    if (e.dataTransfer.files.length) kiesBestanden(e.dataTransfer.files, false);
  });
  const n = state.gekozen.length;
  return [
    heading('Dossier', 'Kies de map met uren, orders en betalingen. Alles blijft op deze computer.'),
    h('section', {class: 'panel', 'aria-labelledby': 'bron-titel'},
      h('div', {class: 'panel-heading'}, h('div', {}, h('h2', {id: 'bron-titel'}, 'Bronnen kiezen'), h('p', {}, 'Een map op deze computer, of bestanden uit je browser.'))),
      h('div', {class: 'field'}, h('label', {for: 'map'}, 'Map op deze computer'), h('div', {class: 'field-row'}, mapInvoer, knop('Aansluiting draaien', {primair: true, disabled: bezig, onclick: () => draai({map: state.map.trim()})}))),
      h('div', {class: 'divider'}, 'of'),
      zone,
      n ? h('ul', {class: 'file-list', 'aria-label': 'Gekozen bestanden'}, state.gekozen.map((b) => h('li', {}, b.pad))) : null,
      n ? h('div', {class: 'run-bar'}, knop(`Aansluiting draaien met ${n} ${n === 1 ? 'bestand' : 'bestanden'}`, {primair: true, disabled: bezig, onclick: () => draai({sessie: true})}), knop('Keuze wissen', {disabled: bezig, onclick: () => { state.gekozen = []; render(); }})) : null,
      bezig ? h('div', {class: 'run-bar', role: 'status'}, h('strong', {}, state.bezig)) : null,
      state.fout ? h('div', {class: 'alert', role: 'alert', tabindex: '-1'}, state.fout) : null,
      h('div', {class: 'panel-note'}, h('strong', {}, 'Wat hoort in een dossier?'),
        h('ul', {}, ['uren.csv en instellingen.toml (verplicht)', 'orders/ met de orders als pdf', 'bank.csv en debiteurenkaart.csv (optioneel)', 'mail/ (optioneel)'].map((t) => h('li', {}, t))),
        h('p', {}, 'Sjablonen en uitleg staan in de README. Niets wordt verstuurd naar internet.'))),
  ];
}

// ---- Overzicht ----
function overzicht() {
  const r = state.run;
  if (!r) return [heading('Overzicht', 'De uitkomst van de aansluiting verschijnt hier.'), leeg('Er is nog geen aansluiting gedraaid.')];
  const regels = r.medewerkers.flatMap((m) => m.samenvatting);
  const verschil = regels.reduce((s, x) => s + x.verschil, 0);
  const dagen = regels.reduce((s, x) => s + x.dagen, 0);
  const kern = [['Medewerkers', getal(r.medewerkers.length)], ['Bevindingen', getal(r.bevindingen.length)], ['Totaalverschil', euro.format(verschil)], ['Dagen', getal(dagen)]];
  return [
    heading('Overzicht', 'Per medewerker: wat is gefactureerd, wat staat in de orders en wat is het verschil.'),
    h('div', {class: 'panel employee-summary', role: 'group', 'aria-label': 'Kerncijfers'}, kern.map(([l, v]) => h('div', {}, h('div', {class: 'metric-label'}, l), h('div', {class: 'metric-value num'}, v)))),
    r.medewerkers.map((m) => {
      const som = (k) => m.samenvatting.reduce((s, x) => s + x[k], 0);
      const num = (v, label, euroNotatie = true) => h('td', {class: 'num', 'data-label': label}, euroNotatie ? euro.format(v) : getal(v));
      return h('section', {class: 'panel', 'aria-label': `Samenvatting ${m.naam}`},
        h('div', {class: 'panel-heading'}, h('div', {}, h('h2', {}, m.naam), h('p', {}, m.bestand)), download(m.bestand, 'Excel downloaden')),
        h('table', {class: 'employee-table'},
          h('thead', {}, h('tr', {}, ['Status', 'Dagen', 'Factuurkant', 'Orderkant', 'Verschil'].map((t) => h('th', {scope: 'col'}, t)))),
          h('tbody', {}, m.samenvatting.map((x) => h('tr', {}, h('td', {class: 'left'}, x.status), num(x.dagen, 'Dagen', false), num(x.factuurkant, 'Factuurkant'), num(x.orderkant, 'Orderkant'), num(x.verschil, 'Verschil')))),
          h('tfoot', {}, h('tr', {}, h('td', {class: 'left'}, 'Totaal'), num(som('dagen'), 'Dagen', false), num(som('factuurkant'), 'Factuurkant'), num(som('orderkant'), 'Orderkant'), num(som('verschil'), 'Verschil')))));
    }),
  ];
}

// ---- Bevindingen ----
const uniek = (rijen, k) => [...new Set(rijen.map((r) => r[k]).filter(Boolean))].sort((a, b) => a.localeCompare(b, 'nl'));
function bevindingen() {
  const r = state.run;
  if (!r) return [heading('Bevindingen', 'Wat bij de aansluiting opviel.'), leeg('Er is nog geen aansluiting gedraaid.')];
  const f = state.filter;
  const kies = (label, key, opties) => h('label', {class: 'filter-label'}, label, h('select', {onchange: (e) => { f[key] = e.target.value; render(); }}, h('option', {value: ''}, 'Alle'), opties.map((o) => h('option', {value: o, selected: o === f[key]}, o))));
  const q = f.q.toLowerCase();
  let rijen = r.bevindingen.filter((b) => (!f.mw || b.medewerker === f.mw) && (!f.groep || b.groep === f.groep) && (!q || Object.values(b).join(' ').toLowerCase().includes(q)));
  if (state.sorteer) rijen = [...rijen].sort((a, b) => (nlNaarGetal(a.bedrag) - nlNaarGetal(b.bedrag)) * (state.sorteer === 'op' ? 1 : -1));
  const zoek = h('input', {type: 'search', value: f.q, 'aria-label': 'Zoeken in bevindingen', placeholder: 'Zoeken…', oninput: (e) => { f.q = e.target.value; render(true); }});
  const tabel = rijen.length
    ? h('table', {class: 'employee-table'},
      h('thead', {}, h('tr', {}, h('th', {scope: 'col'}, 'Medewerker'), h('th', {scope: 'col', style: 'text-align:left'}, 'Groep'), h('th', {scope: 'col', style: 'text-align:left'}, 'Onderwerp'),
        h('th', {scope: 'col', 'aria-sort': state.sorteer === 'op' ? 'ascending' : state.sorteer === 'af' ? 'descending' : 'none'},
          h('button', {type: 'button', class: 'th-btn', onclick: () => { state.sorteer = state.sorteer === 'af' ? 'op' : state.sorteer === 'op' ? '' : 'af'; render(); }}, 'Bedrag' + (state.sorteer === 'op' ? ' ↑' : state.sorteer === 'af' ? ' ↓' : ''))))),
      h('tbody', {}, rijen.map((b) => {
        const open = () => toonDetail(b, tr);
        const tr = h('tr', {class: 'click', tabindex: '0', role: 'button', 'aria-label': `Details: ${b.onderwerp || b.sleutel}`, onclick: open,
          onkeydown: (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open(); } }},
        h('td', {class: 'left', 'data-label': 'Medewerker'}, b.medewerker), h('td', {class: 'wrap', 'data-label': 'Groep'}, b.groep), h('td', {class: 'wrap', 'data-label': 'Onderwerp'}, b.onderwerp), h('td', {class: 'num', 'data-label': 'Bedrag'}, b.bedrag));
        return tr;
      })))
    : h('div', {class: 'empty'}, 'Geen bevindingen voor dit filter.');
  return [
    heading('Bevindingen', 'Klik op een rij voor wat er aan de hand is en wat te doen.'),
    h('section', {class: 'panel', 'aria-label': 'Bevindingen'},
      h('div', {class: 'filters'}, kies('Medewerker', 'mw', uniek(r.bevindingen, 'medewerker')), kies('Groep', 'groep', uniek(r.bevindingen, 'groep')), zoek, h('span', {class: 'muted small', role: 'status'}, `${rijen.length} van ${r.bevindingen.length}`)),
      tabel),
  ];
}

function toonDetail(b, terug) {
  const dlg = $('#detail-dialog');
  const blok = (t, v) => v ? [h('h3', {}, t), h('p', {}, v)] : null;
  dlg.replaceChildren(
    h('div', {class: 'dialog-head'}, h('div', {}, h('span', {class: 'eyebrow'}, [b.groep, b.soort, b.medewerker].filter(Boolean).join(' · ')), h('h2', {id: 'dialog-title'}, b.onderwerp || b.sleutel || 'Bevinding')),
      h('button', {type: 'button', class: 'icon-button', 'aria-label': 'Sluiten', onclick: () => dlg.close()}, '✕')),
    h('div', {class: 'dialog-body'}, b.bedrag ? h('div', {class: 'dialog-amount num'}, b.bedrag) : null, blok('Wat', b.wat), blok('Bewijs', b.bewijs), blok('Actie', b.actie), blok('Wie', b.wie), b.btw ? blok('Btw', b.btw) : null),
    h('div', {class: 'dialog-footer'}, knop('Sluiten', {onclick: () => dlg.close()})));
  dlg.addEventListener('close', () => terug.isConnected && terug.focus(), {once: true});
  dlg.showModal();
}

// ---- Export ----
function exportScherm() {
  const r = state.run;
  if (!r) return [heading('Export', 'Alle bestanden van de aansluiting.'), leeg('Er is nog geen aansluiting gedraaid.')];
  const eerst = (n) => (n.startsWith('Overzicht totaal') ? 0 : n === 'bevindingen.csv' ? 1 : 2);
  const namen = [...r.bestanden].sort((a, b) => eerst(a) - eerst(b) || a.localeCompare(b, 'nl'));
  return [
    heading('Export', 'Download de Excel-overzichten en de lijst met bevindingen.'),
    h('section', {class: 'panel', 'aria-label': 'Bestanden'}, namen.map((n) => h('div', {class: 'file-item' + (eerst(n) === 0 ? ' main' : '')}, svg('sheet'), h('span', {}, n), download(n, 'Downloaden', eerst(n) === 0)))),
    h('details', {class: 'panel'}, h('summary', {}, 'Uitvoer van de opdrachtregel'), h('pre', {tabindex: '0'}, r.log || '(geen uitvoer)')),
  ];
}

// ---- Router ----
const schermen = {dossier, overzicht, bevindingen, export: exportScherm};
function render(behoudFocus) {
  const view = schermen[location.hash.slice(1)] ? location.hash.slice(1) : 'dossier';
  const q = behoudFocus && document.activeElement?.type === 'search' ? document.activeElement.selectionStart : null;
  $('#navigation').replaceChildren(...nav.map(([id, ic, tekst]) => {
    const a = h('a', {href: '#' + id, 'aria-current': id === view ? 'page' : null, class: id === view ? 'active' : ''}, svg(ic), tekst);
    return a;
  }));
  document.title = `${nav.find((n) => n[0] === view)[2]} · Weekstaat`;
  main.replaceChildren(...[schermen[view]()].flat(2));
  if (q !== null) { const z = main.querySelector('input[type=search]'); z.focus(); z.setSelectionRange(q, q); }
}
let vorige = '';
window.addEventListener('hashchange', () => { render(); if (location.hash !== vorige) { vorige = location.hash; main.focus({preventScroll: true}); window.scrollTo(0, 0); } });

// ---- Thema ----
function thema(t) {
  document.documentElement.dataset.theme = t;
  const b = $('#theme-toggle');
  b.innerHTML = icon(t === 'light' ? 'moon' : 'sun');
  b.setAttribute('aria-label', t === 'light' ? 'Donkere weergave inschakelen' : 'Lichte weergave inschakelen');
  try { localStorage.setItem('weekstaat-thema', t); } catch { /* opslag geblokkeerd */ }
}
let opgeslagen = null;
try { opgeslagen = localStorage.getItem('weekstaat-thema'); } catch { /* opslag geblokkeerd */ }
thema(opgeslagen || (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'));
$('#theme-toggle').addEventListener('click', () => thema(document.documentElement.dataset.theme === 'light' ? 'dark' : 'light'));

// ---- Start ----
api('/api/info').then((i) => {
  state.info = i; state.map = i.voorbeeld || '';
  $('#versie').textContent = `Weekstaat ${i.versie}`;
}).catch((e) => { state.fout = e.message; }).finally(() => { if (!location.hash) location.hash = '#dossier'; render(); });
