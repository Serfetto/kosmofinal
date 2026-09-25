// @ts-nocheck — модуль постепенно типизируется без риска для проверенной геологики карты.
import * as maplibregl from 'maplibre-gl';
import maplibreWorkerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url';
import Chart from 'chart.js/auto';

// Vite не может автоматически определить worker URL из ESM-сборки MapLibre 6.
// Без явного URL растры работают, а GeoJSON/H3 остаётся необработанным.
maplibregl.setWorkerUrl(maplibreWorkerUrl);

Chart.defaults.color = '#94a3b8';
Chart.defaults.font.family = 'Montserrat, "Segoe UI", Arial, sans-serif';
Chart.defaults.font.size = 11;
Chart.defaults.font.weight = 500;
Chart.defaults.animation = { duration: 650, easing: 'easeOutQuart' };

const $ = (s) => document.querySelector(s);
const $$ = (s) => [...document.querySelectorAll(s)];
const nf = (v, d = 1) => (v == null || Number.isNaN(v)) ? '—' : Number(v).toLocaleString('ru-RU', { maximumFractionDigits: d });
const ruDate = (d) => d.split('-').reverse().join('.');
const shortDate = (d) => { const [y, m, dd] = d.split('-'); return `${dd}.${m}.${y.slice(2)}`; };
const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

// Покрытие (вспомогательный показатель детектора), м²/км²
const CLS_COL = ['rgba(0,0,0,0)', '#ffe38a', '#ffb24a', '#ff6a3d', '#d9214f'];
const CLS_NAME = ['нет', 'низкое', 'умеренное', 'высокое', 'очень высокое'];
const COL_A = '#fbbf24', COL_B = '#a78bfa', ACCENT = '#00f5ff';
// Статусы — те же ключи, что в pipeline/status.py
const DET_COL = { detected: '#ff5c5c', not_detected: '#2dd4bf', insufficient_data: '#6b7688' };
const DET_NAME = { detected: 'обнаружено', not_detected: 'не обнаружено', insufficient_data: 'недостаточно данных' };
const CONC_NAME = { model_estimate: 'модельная оценка', research_estimate: 'исследовательская оценка', unavailable: 'концентрация недоступна' };
// Концентрация, шт./км²: логарифмическая шкала 10…3000
const CONC_STOPS = [[0, '#1e3a8a'], [0.3, '#2f7fb8'], [0.55, '#f2c14e'], [0.8, '#f37b3a'], [1, '#d7263d']];
const CONC_MIN = 10, CONC_MAX = 3000;
const concT = (v) => (Math.log10(Math.max(v, CONC_MIN)) - Math.log10(CONC_MIN)) / (Math.log10(CONC_MAX) - Math.log10(CONC_MIN));

const S = {
  aois: [], aoi: null, hexes: null, series: null, conc: null, zones: null, field: null, objects: null,
  profiles: [], profile: 'B', di: 0, mode: 'conc', queryId: null,
  filters: { det: new Set(['detected', 'not_detected', 'insufficient_data']), conc: new Set(['model_estimate', 'research_estimate', 'unavailable']) },
  drift: null, accum: null, sel: null, cmp: { A: null, B: null }, drawing: null, draft: [],
  route: null, markers: [], stopMarkers: [], charts: {}, tool: null, anim: null,
};

// ---------- утилиты ----------
async function api(url, opts) {
  const r = await fetch(url, opts);
  if (!r.ok) {
    let msg = await r.text();
    try { msg = JSON.parse(msg).detail || msg; } catch { /* текст как есть */ }
    throw new Error(`${r.status}: ${typeof msg === 'string' ? msg : JSON.stringify(msg)}`);
  }
  return r.json();
}
let toastTimer;
function toast(msg, ms = 2600) {
  const t = $('#toast');
  t.textContent = msg;
  t.hidden = false;
  clearTimeout(toastTimer);
  if (ms) toastTimer = setTimeout(() => (t.hidden = true), ms);
}

async function withButtonLoading(button, loadingLabel, action) {
  button = button?.currentTarget || button;
  if (!button || button.dataset.loading === 'true') return action();
  const originalMarkup = button.innerHTML;
  const originalMinWidth = button.style.minWidth;
  const originalWidth = button.style.width;
  const measuredWidth = Math.ceil(button.getBoundingClientRect().width);

  button.dataset.loading = 'true';
  button.disabled = true;
  button.setAttribute('aria-busy', 'true');
  if (measuredWidth) {
    button.style.width = `${measuredWidth}px`;
    button.style.minWidth = `${measuredWidth}px`;
  }
  button.innerHTML = `<span class="button-spinner" aria-hidden="true"></span>${loadingLabel ? `<span class="button-loading-label">${loadingLabel}</span>` : ''}`;

  try {
    return await action();
  } finally {
    button.innerHTML = originalMarkup;
    button.style.width = originalWidth;
    button.style.minWidth = originalMinWidth;
    button.disabled = false;
    button.removeAttribute('aria-busy');
    delete button.dataset.loading;
  }
}
function lerpColor(stops, t) {
  t = Math.max(0, Math.min(1, t));
  for (let i = 1; i < stops.length; i++) {
    if (t <= stops[i][0]) {
      const [t0, c0] = stops[i - 1], [t1, c1] = stops[i];
      const k = (t - t0) / (t1 - t0 || 1);
      const a = hex2rgb(c0), b = hex2rgb(c1);
      return `rgb(${a.map((v, j) => Math.round(v + (b[j] - v) * k)).join(',')})`;
    }
  }
  return stops[stops.length - 1][1];
}
function hex2rgb(h) { const n = parseInt(h.slice(1), 16); return [n >> 16, (n >> 8) & 255, n & 255]; }
function pip(pt, poly) {
  let inside = false;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const [xi, yi] = poly[i], [xj, yj] = poly[j];
    if ((yi > pt[1]) !== (yj > pt[1]) && pt[0] < ((xj - xi) * (pt[1] - yi)) / (yj - yi) + xi) inside = !inside;
  }
  return inside;
}
function slope(xs, ys) {
  const n = xs.length; if (n < 3) return 0;
  const mx = xs.reduce((a, b) => a + b) / n, my = ys.reduce((a, b) => a + b) / n;
  let num = 0, den = 0;
  xs.forEach((x, i) => { num += (x - mx) * (ys[i] - my); den += (x - mx) ** 2; });
  return den ? num / den : 0;
}
const median = (a) => { const v = a.filter((x) => x != null).sort((x, y) => x - y); return v.length ? v[Math.floor(v.length / 2)] : null; };
const monthsFrom0 = (d) => (new Date(d) - new Date(S.series.dates[0])) / (30.4 * 864e5);
function cls(cover, ndet) {
  if (!(ndet > 0) || !(cover > 0)) return 0;
  const e = S.series.cover_class_edges; let k = 0;
  e.forEach((edge, i) => { if (cover > edge) k = i + 1; });
  return k;
}
function download(name, text, type) {
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([text], { type }));
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}
const localTime = (iso, tz) => {
  const d = new Date(new Date(iso).getTime() + tz * 3600e3);
  return `${String(d.getUTCDate()).padStart(2, '0')}.${String(d.getUTCMonth() + 1).padStart(2, '0')} ${String(d.getUTCHours()).padStart(2, '0')}:${String(d.getUTCMinutes()).padStart(2, '0')}`;
};
const profileInfo = () => S.profiles.find((p) => p.id === S.profile) || {};
const detStatus = (di, i) => S.series.status_codes[String(S.series.status[di][i])];
function concAt(di, i) {
  const c = S.conc;
  if (!c || !c.available) return { value: null, lo: null, hi: null, status: 'unavailable' };
  return { value: c.value[di][i], lo: c.lo80[di][i], hi: c.hi80[di][i], status: c.status_codes[String(c.status[di][i])] };
}
const badge = (key, text) => `<span class="badge ${key}">${esc(text)}</span>`;

// ---------- карта ----------
const map = new maplibregl.Map({
  container: 'map',
  style: {
    version: 8,
    sources: {
      dark: { type: 'raster', tiles: ['https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}'], tileSize: 256, maxzoom: 16, attribution: 'Esri, HERE, Garmin, © OpenStreetMap' },
      sat: { type: 'raster', tiles: ['https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}'], tileSize: 256, attribution: 'Esri World Imagery' },
    },
    layers: [
      { id: 'bg', type: 'background', paint: { 'background-color': '#0a111d' } },
      { id: 'dark', type: 'raster', source: 'dark' },
      { id: 'sat', type: 'raster', source: 'sat', layout: { visibility: 'none' } },
    ],
  },
  center: [39.8, 43.5], zoom: 10, attributionControl: { compact: true },
});
map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'bottom-right');
map.addControl(new maplibregl.ScaleControl({ unit: 'metric' }), 'bottom-left');

// CSS-grid меняет ширину карты во время сворачивания sidebar. MapLibre сам
// этого не отслеживает, поэтому синхронизируем WebGL canvas с контейнером.
const mapResizeObserver = new ResizeObserver(() => {
  requestAnimationFrame(() => map.resize());
});
mapResizeObserver.observe(document.querySelector('#map'));

const EMPTY = { type: 'FeatureCollection', features: [] };
function addGeo(id) { map.addSource(id, { type: 'geojson', data: EMPTY }); }

function initLayers(firstUrl, corners) {
  map.addSource('rgb', { type: 'image', url: firstUrl.rgb, coordinates: corners });
  map.addLayer({ id: 'rgb', type: 'raster', source: 'rgb', paint: { 'raster-opacity': 0.92, 'raster-fade-duration': 0 } });
  map.addSource('quality', { type: 'image', url: firstUrl.quality, coordinates: corners });
  map.addLayer({ id: 'quality', type: 'raster', source: 'quality', layout: { visibility: 'none' }, paint: { 'raster-resampling': 'nearest', 'raster-fade-duration': 0 } });
  ['hexes', 'sel', 'accumPts', 'draw', 'cmpA', 'cmpB', 'tracks', 'particles', 'cone', 'coneCenter', 'route', 'routeDrift', 'routeObs', 'zones', 'field', 'objects'].forEach(addGeo);
  map.addSource('debris', { type: 'image', url: firstUrl.debris, coordinates: corners });
  map.addLayer({ id: 'debris', type: 'raster', source: 'debris', paint: { 'raster-resampling': 'nearest', 'raster-fade-duration': 0 } });
  // Сетка должна лежать выше обоих растров: иначе тонкие линии теряются на ярком RGB-снимке.
  map.addLayer({ id: 'hex-fill', type: 'fill', source: 'hexes', paint: {
    'fill-color': ['get', 'col'],
    'fill-opacity': ['get', 'op'],
    'fill-outline-color': 'rgba(103,232,249,0.18)',
  } });
  map.addLayer({ id: 'hex-line', type: 'line', source: 'hexes', paint: {
    'line-color': ['case', ['>', ['get', 'op'], 0], 'rgba(186,245,255,0.62)', 'rgba(103,232,249,0.38)'],
    'line-width': ['interpolate', ['linear'], ['zoom'], 8, 0.7, 12, 1.15, 15, 1.65],
    'line-opacity': 0.78,
  } });
  // Исследовательская оценка — жёлтый пунктир по границе гекса: модельные оценки и измерения должны отличаться
  // с первого взгляда (заливка-штриховка fill-pattern в MapLibre 6 ломает отрисовку всего источника)
  map.addLayer({ id: 'hex-research', type: 'line', source: 'hexes', filter: ['==', ['get', 'hatch'], 1], paint: {
    'line-color': '#fbbf24', 'line-width': ['interpolate', ['linear'], ['zoom'], 8, 0.8, 13, 1.6], 'line-dasharray': [1.2, 1.4], 'line-opacity': 0.9 } });
  map.addLayer({ id: 'sel', type: 'line', source: 'sel', paint: { 'line-color': '#ffffff', 'line-width': 2.2 } });
  // Зоны детекции — результат детектора (площадь зоны), не измерение концентрации
  map.addLayer({ id: 'zones-fill', type: 'fill', source: 'zones', filter: ['==', ['get', 'show'], 1], paint: { 'fill-color': '#ff3b3b', 'fill-opacity': 0.35 } });
  map.addLayer({ id: 'zones-line', type: 'line', source: 'zones', filter: ['==', ['get', 'show'], 1], paint: {
    'line-color': '#ffd1d1', 'line-width': ['interpolate', ['linear'], ['zoom'], 10, 1, 15, 2.5] } });
  map.addLayer({ id: 'zones-dot', type: 'circle', source: 'zones', filter: ['==', ['get', 'show'], 1], maxzoom: 12.5, paint: {
    'circle-radius': 4, 'circle-color': '#ff3b3b', 'circle-stroke-color': '#fff', 'circle-stroke-width': 1 } });
  for (const [id, col] of [['cmpA', COL_A], ['cmpB', COL_B]]) {
    map.addLayer({ id: `${id}-fill`, type: 'fill', source: id, paint: { 'fill-color': col, 'fill-opacity': 0.08 } });
    map.addLayer({ id: `${id}-line`, type: 'line', source: id, paint: { 'line-color': col, 'line-width': 2, 'line-dasharray': [2, 1] } });
  }
  // Полевые измерения: сплошная линия/кружок с белой обводкой — не путать с модельными гексами
  const fieldColor = ['interpolate', ['linear'], ['log10', ['max', ['get', 'conc_items_km2'], CONC_MIN]],
    1, CONC_STOPS[0][1], 1.75, CONC_STOPS[1][1], 2.35, CONC_STOPS[2][1], 2.9, CONC_STOPS[3][1], 3.48, CONC_STOPS[4][1]];
  map.addLayer({ id: 'field-casing', type: 'line', source: 'field', filter: ['!=', '$type', 'Point'], paint: { 'line-color': '#fff', 'line-width': 7 } });
  map.addLayer({ id: 'field-line', type: 'line', source: 'field', filter: ['!=', '$type', 'Point'], paint: { 'line-color': fieldColor, 'line-width': 4 } });
  map.addLayer({ id: 'field-pt', type: 'circle', source: 'field', filter: ['==', '$type', 'Point'], paint: {
    'circle-radius': 8, 'circle-color': fieldColor, 'circle-stroke-color': '#fff', 'circle-stroke-width': 2.5 } });
  map.addLayer({ id: 'objects', type: 'circle', source: 'objects', layout: { visibility: 'none' }, paint: {
    'circle-radius': 3, 'circle-color': '#e2e8f0', 'circle-stroke-color': '#0f172a', 'circle-stroke-width': 1 } });
  map.addLayer({ id: 'draw-line', type: 'line', source: 'draw', paint: { 'line-color': '#fff', 'line-width': 1.5, 'line-dasharray': [1, 1] } });
  map.addLayer({ id: 'draw-pts', type: 'circle', source: 'draw', filter: ['==', '$type', 'Point'], paint: { 'circle-radius': 4, 'circle-color': '#fff' } });
  map.addLayer({ id: 'tracks', type: 'line', source: 'tracks', paint: { 'line-color': ACCENT, 'line-opacity': 0.16, 'line-width': 1 } });
  map.addLayer({ id: 'particles', type: 'circle', source: 'particles', paint: {
    'circle-radius': 2.6, 'circle-color': ['case', ['==', ['get', 'b'], 1], '#ff6a3d', '#9ff0ff'],
    'circle-stroke-color': '#04121a', 'circle-stroke-width': 0.5 } });
  map.addLayer({ id: 'cone', type: 'line', source: 'cone', paint: { 'line-color': '#9ff0ff', 'line-opacity': 0.35, 'line-width': 1 } });
  map.addLayer({ id: 'coneCenter', type: 'line', source: 'coneCenter', paint: { 'line-color': '#fff', 'line-width': 2.5 } });
  map.addLayer({ id: 'coneEnd', type: 'circle', source: 'coneCenter', filter: ['==', '$type', 'Point'], paint: { 'circle-radius': 5, 'circle-color': '#fff' } });
  map.addLayer({ id: 'routeDrift', type: 'line', source: 'routeDrift', paint: { 'line-color': '#ffb24a', 'line-width': 1.8 } });
  map.addLayer({ id: 'routeObs', type: 'circle', source: 'routeObs', paint: { 'circle-radius': 3.5, 'circle-color': '#ffb24a' } });
  map.addLayer({ id: 'route', type: 'line', source: 'route', paint: { 'line-color': ACCENT, 'line-width': 3, 'line-dasharray': [2, 1.2] } });

  map.on('mousemove', 'hex-fill', onHexHover);
  map.on('mouseleave', 'hex-fill', () => { $('#tip').hidden = true; map.getCanvas().style.cursor = ''; });
  for (const id of ['zones-fill', 'zones-dot', 'field-line', 'field-pt']) {
    map.on('mouseenter', id, () => { map.getCanvas().style.cursor = 'pointer'; });
    map.on('mouseleave', id, () => { map.getCanvas().style.cursor = ''; });
  }
  map.on('click', (e) => {
    if (S.drawing) return onDrawClick(e);
    const hit = map.queryRenderedFeatures(e.point, { layers: ['field-pt', 'field-line', 'zones-dot', 'zones-fill', 'hex-fill'].filter((l) => map.getLayer(l)) });
    if (!hit.length) return;
    const f = hit[0];
    if (f.layer.id.startsWith('field')) openField(f.properties.event_id);
    else if (f.layer.id.startsWith('zones')) openZone(f.properties.zone_id);
    else selectHex(f.properties.i);
  });
  map.on('dblclick', onDrawFinish);
}

// ---------- загрузка акватории ----------
async function loadAoi(id, preferDate) {
  S.aoi = S.aois.find((a) => a.id === id) || S.aois[0];
  $('#aoi').value = S.aoi.id;
  $('#workspace-aoi').textContent = S.aoi.name;
  $('#aoi-note').textContent = S.aoi.note || '';
  $('#aoi-note').hidden = !S.aoi.note;
  toast('Загрузка акватории…', 0);
  const [hexes, series] = await Promise.all([api(`/api/aois/${S.aoi.id}/hexes`), api(`/api/aois/${S.aoi.id}/series`)]);
  S.hexes = hexes; S.series = series; S.sel = null; S.accum = null;
  await loadConc();
  clearDrift(); clearRoute(); clearCompare();
  const di = series.dates.length - 1;
  const urls = sceneUrls(series.dates[di]);
  if (!map.getSource('rgb')) initLayers(urls, series.scenes[di].corners);
  const [x0, y0, x1, y1] = S.aoi.bbox;
  map.fitBounds([[x0, y0], [x1, y1]], { padding: 30, duration: 0 });
  placeMarkers();
  buildTimeline();
  renderField();
  // По умолчанию — самая «грязная» дата из спокойных
  let best = di, bestA = -1;
  series.scenes.forEach((s, i) => { if (!s.storm && s.area_m2 > bestA) { bestA = s.area_m2; best = i; } });
  const want = preferDate ? series.dates.indexOf(preferDate) : -1;
  await setDate(want >= 0 ? want : best);
  $('#toast').hidden = true;
}
async function loadConc() {
  try { S.conc = await api(`/api/aois/${S.aoi.id}/concentration?profile=${S.profile}`); }
  catch { S.conc = null; }
  renderProfileNote();
}
const sceneUrls = (d) => ({ rgb: `/data/${S.aoi.id}/${d}/rgb.jpg`, debris: `/data/${S.aoi.id}/${d}/debris.png`, quality: `/data/${S.aoi.id}/${d}/quality.png` });

function placeMarkers() {
  S.markers.forEach((m) => m.remove());
  S.markers = [];
  const el = document.createElement('div');
  el.className = 'port-marker'; el.textContent = '⚓'; el.title = 'Порт базирования судна';
  el.setAttribute('role', 'button');
  el.setAttribute('tabindex', '0');
  el.setAttribute('aria-label', 'Открыть план обследования из порта');
  const openPort = (event) => { event.stopPropagation(); openTool('route'); };
  el.addEventListener('click', openPort);
  el.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' || event.key === ' ') openPort(event);
  });
  S.markers.push(new maplibregl.Marker({ element: el }).setLngLat(S.aoi.port).addTo(map));
  for (const [name, ll] of Object.entries(S.aoi.rivers || {})) {
    const r = document.createElement('div');
    r.className = 'river-marker'; r.textContent = `устье ${name}`;
    S.markers.push(new maplibregl.Marker({ element: r, anchor: 'left', offset: [16, 10] }).setLngLat(ll).addTo(map));
  }
}

// ---------- профиль ----------
function renderProfiles() {
  $('#profile').innerHTML = S.profiles.filter((p) => p.show_in_ui).map((p) => `<option value="${p.id}">${p.id} · ${esc(p.label)}</option>`).join('');
  $('#profile').value = S.profile;
}
function renderProfileNote() {
  const p = profileInfo();
  const avail = S.aoi?.concentration?.[S.profile];
  const na = !S.conc || !S.conc.available;
  $('#profile-note').innerHTML = `${esc(p.material || '')}; ${esc(p.size_class || '')}; ${esc(p.method || '')}.` +
    `<br>Модель: ${esc(p.model_type || '—')} · обучение ${p.n_train ?? '—'} событий, отложено ${p.n_holdout ?? '—'}.` +
    (na ? `<br><span style="color:#fbbf24">Концентрация недоступна для этой акватории: ${esc(avail?.reason || S.conc?.reason || 'профиль не применим')}.</span>` : '');
}

// ---------- дата ----------
function buildTimeline() {
  S.charts.timeline?.destroy();
  const s = S.series;
  S.charts.timeline = new Chart($('#timeline'), {
    type: 'bar',
    data: { labels: s.dates.map(shortDate), datasets: [{ data: s.scenes.map((x) => x.n_zones ?? 0), borderRadius: 2, backgroundColor: [] }] },
    options: {
      responsive: true, maintainAspectRatio: false, animation: { duration: 650, easing: 'easeOutQuart' },
      plugins: { legend: { display: false }, tooltip: { callbacks: {
        title: (c) => ruDate(s.dates[c[0].dataIndex]),
        label: (c) => { const sc = s.scenes[c.dataIndex]; return [`зон детекции: ${nf(c.raw, 0)}`, `покрытие ≈ ${nf(sc.area_m2, 0)} м²`, `море: ${sc.sea}${sc.wind != null ? `, ветер ${nf(sc.wind)} м/с` : ''}`]; } } } },
      scales: {
        x: { ticks: { maxRotation: 0, autoSkip: true, maxTicksLimit: 5 }, grid: { display: false } },
        y: { ticks: { maxTicksLimit: 3, callback: (v) => nf(v, 0) }, grid: { color: 'rgba(148,163,184,.12)' } },
      },
      onClick: (e, els) => { if (els.length) setDate(els[0].index); },
    },
  });
}
function colorTimeline() {
  const c = S.charts.timeline; if (!c) return;
  c.data.datasets[0].backgroundColor = S.series.scenes.map((sc, i) => i === S.di ? ACCENT : sc.storm ? '#4a5568' : '#2c5d7a');
  c.update('none');
}

async function setDate(i) {
  const s = S.series;
  S.di = Math.max(0, Math.min(s.dates.length - 1, i));
  const d = s.dates[S.di], sc = s.scenes[S.di];
  $('#date-label').textContent = ruDate(d);
  $('#workspace-date').textContent = `${ruDate(d)} · Sentinel-2`;
  const glint = sc.glint > 0.03 ? 'сильный' : sc.glint > 0.01 ? 'умеренный' : 'слабый';
  const q = sc.quality || {};
  $('#scene-info').innerHTML = `Пролёт ${localTime(sc.datetime || d, S.aoi.tz)} (UTC+${S.aoi.tz}) · ${esc(sc.platform || '')} · облачность тайла ${nf(sc.tile_cloud_pct, 0)}%` +
    `<br>Видно воды: <b>${nf(sc.valid_frac * 100, 0)}%</b> · облака ${nf((q.cloud || 0) * 100, 1)}% · блик: ${glint} · море: <b>${sc.sea}</b>` +
    (sc.wind != null ? `, ветер ${nf(sc.wind)} м/с` : '') + (sc.storm ? `<br><span style="color:#ffb24a">Ненадёжная сцена (${sc.reason}): оставлены только крупные скопления, отсутствие мусора не подтверждается</span>` : '') +
    `<br><span class="small">Сцена: ${esc(sc.scene_id || '')}</span>`;
  const u = sceneUrls(d);
  map.getSource('rgb').updateImage({ url: u.rgb, coordinates: sc.corners });
  map.getSource('debris').updateImage({ url: u.debris, coordinates: sc.corners });
  map.getSource('quality').updateImage({ url: u.quality, coordinates: sc.corners });
  clearDrift(); clearRoute(); S.accum = null;
  if (S.mode === 'forecast' || S.mode === 'accum') setMode('conc');
  try { S.zones = await api(`/api/aois/${S.aoi.id}/${d}/zones`); } catch { S.zones = EMPTY; }
  paintZones();
  paintHexes();
  renderKpis();
  colorTimeline();
  syncUrl();
  if (S.sel != null && S.tool === 'hex') renderHexPanel();
  if (S.cmp.A || S.cmp.B) renderCompare();
}

// ---------- гексы ----------
const PERSIST = [[0, '#34416b'], [0.2, '#6b5cff'], [0.45, '#c04fe0'], [1, '#ff5c8a']];
const ACC = [[0, '#12475a'], [0.5, '#1f9fc4'], [1, '#b7f4ff']];
const QUAL = [[0, '#6b7688'], [0.5, '#475569'], [1, '#0f766e']];

function passesFilter(di, i) {
  const ds = detStatus(di, i), cs = concAt(di, i).status;
  return S.filters.det.has(ds) && S.filters.conc.has(cs);
}

function paintHexes() {
  if (!S.hexes) return;
  const s = S.series, di = S.di, m = S.mode;
  const trends = S.hexes.features.map((f) => Math.abs(f.properties.trend_cover)).filter((v) => v > 0).sort((a, b) => a - b);
  const tmax = trends.length ? trends[Math.floor(trends.length * 0.95)] || trends[trends.length - 1] : 1;
  const f72 = S.drift?.hexes?.[String(S.drift.hours)] || {};
  for (const f of S.hexes.features) {
    const p = f.properties, i = p.i;
    let col = 'rgba(0,0,0,0)', op = 0, hatch = 0;
    if (m === 'conc') {
      const c = concAt(di, i);
      if (c.value != null) { col = lerpColor(CONC_STOPS, concT(c.value)); op = 0.38; hatch = c.status === 'research_estimate' ? 1 : 0; }
    } else if (m === 'status') {
      const st = detStatus(di, i); col = DET_COL[st]; op = st === 'detected' ? 0.8 : st === 'insufficient_data' ? 0.45 : 0.12;
    } else if (m === 'quality') {
      col = lerpColor(QUAL, s.valid[di][i]); op = 0.5;
    } else if (m === 'cover') {
      if (s.valid[di][i] < 0.5) { col = '#6b7688'; op = 0.35; }
      else { const k = cls(s.cover[di][i], s.ndet[di][i]); col = CLS_COL[k]; op = k ? 0.8 : 0; }
    } else if (m === 'mean') {
      const k = cls(p.mean_cover, p.mean_cover > 0 ? 1 : 0); col = CLS_COL[k]; op = k ? 0.8 : 0;
    } else if (m === 'persist') {
      if (p.persistence > 0) { col = lerpColor(PERSIST, p.persistence / 0.5); op = 0.35 + 0.5 * Math.min(1, p.persistence / 0.3); }
    } else if (m === 'trend') {
      const t = p.trend_cover / tmax;
      if (Math.abs(t) > 0.05) { col = t > 0 ? lerpColor([[0, '#5a2a2a'], [1, '#ff4d3d']], t) : lerpColor([[0, '#1d3f55'], [1, '#3cc6e8']], -t); op = 0.3 + 0.55 * Math.min(1, Math.abs(t)); }
    } else if (m === 'accum') {
      const v = S.accum?.factor?.[p.h3] ?? 0;
      if (v > 1.15) { col = lerpColor(ACC, (v - 1.15) / 3); op = 0.3 + 0.5 * Math.min(1, (v - 1.15) / 2); }
    } else if (m === 'forecast') {
      const area = f72[p.h3] || 0;
      const cover = area / Math.max(p.water_km2, 0.05);
      const k = cls(cover, area > 0 ? 1 : 0); col = CLS_COL[k]; op = k ? 0.8 : 0;
    }
    if (['conc', 'status', 'quality', 'cover'].includes(m) && !passesFilter(di, i)) { op = 0; hatch = 0; }
    p.col = col; p.op = op; p.hatch = hatch;
  }
  map.getSource('hexes').setData(S.hexes);
  renderLegend();
}

function paintZones() {
  const fc = S.zones || EMPTY;
  for (const f of fc.features) {
    const cs = f.properties[`conc_${S.profile}_status`] || 'unavailable';
    f.properties.show = S.filters.det.has('detected') && S.filters.conc.has(cs) ? 1 : 0;
  }
  map.getSource('zones')?.setData(fc);
}

function renderField() {
  if (!S.field || !map.getSource('field')) return;
  map.getSource('field').setData(S.field);
}

function renderLegend() {
  const e = S.series?.cover_class_edges || [0, 15, 40, 100];
  const classes = () => [1, 2, 3, 4].map((k) => `<div class="leg-row"><span class="sw" style="background:${CLS_COL[k]}"></span>${CLS_NAME[k]} <span class="muted">${k < 4 ? `${nf(e[k - 1], 0)}–${nf(e[k], 0)}` : `> ${nf(e[3], 0)}`} м²/км²</span></div>`).join('');
  const concGrad = `<div class="grad" style="background:linear-gradient(90deg,${CONC_STOPS.map((s) => s[1]).join(',')})"></div><div class="grad-lbl small"><span>≤${CONC_MIN}</span><span>100</span><span>1000</span><span>≥${CONC_MAX} шт./км²</span></div>`;
  const fieldRow = `<div class="leg-row"><span class="sw field-sw"></span>полевое измерение (C = N/A), цвет — та же шкала</div>`;
  const statusRows = Object.entries(DET_NAME).map(([k, v]) => `<div class="leg-row"><span class="sw" style="background:${DET_COL[k]}"></span>${v}</div>`).join('');
  const na = !S.conc || !S.conc.available;
  const L = {
    conc: na
      ? `<p class="small" style="color:#fbbf24">Концентрация недоступна для профиля ${S.profile} в этой акватории: вне области применения модели.</p>${fieldRow}`
      : `${concGrad}<div class="leg-row"><span class="sw hatch-sw"></span>жёлтый пунктир — исследовательская оценка (перенос не подтверждён)</div>${fieldRow}
      <div class="leg-row"><span class="sw" style="background:#ff3b3b"></span>зона детекции (площадь, не концентрация)</div>
      <p class="muted small">Модельная оценка по полевым данным профиля в центре гекса на момент снимка. Интервал и причины статуса — в карточке гекса.</p>`,
    status: `${statusRows}<p class="muted small">«Не обнаружено» ставится только при ≥50% видимой воды и спокойном море; иначе — «недостаточно данных».</p>`,
    quality: `<div class="grad" style="background:linear-gradient(90deg,${QUAL.map((s) => s[1]).join(',')})"></div><div class="grad-lbl small"><span>0% воды видно</span><span>100%</span></div>
      <p class="muted small">Доля пригодных пикселей воды (без облаков, теней, льда). Попиксельная маска — слой «Маска качества».</p>`,
    cover: `${classes()}<div class="leg-row"><span class="sw" style="background:#6b7688"></span>нет данных (облака)</div>
      <p class="muted small">Покрытие — эквивалентная площадь плавающего мусора на км² по доле пикселя. Вспомогательный показатель детектора; в шт./км² не переводится.</p>`,
    mean: `${classes()}<p class="muted small">Среднее покрытие по безоблачным и нештормовым снимкам.</p>`,
    persist: `<div class="grad" style="background:linear-gradient(90deg,${PERSIST.map((s) => s[1]).join(',')})"></div><div class="grad-lbl small"><span>редко</span><span>≥50% снимков</span></div>
      <p class="muted small">Доля снимков, на которых в гексе был мусор. Устойчивые зоны — места хронического скопления.</p>`,
    trend: `<div class="grad" style="background:linear-gradient(90deg,#3cc6e8,#1d3f55,#5a2a2a,#ff4d3d)"></div><div class="grad-lbl small"><span>снижение</span><span>рост</span></div>
      <p class="muted small">Линейный тренд покрытия за период наблюдений.</p>`,
    accum: `<div class="grad" style="background:linear-gradient(90deg,${ACC.map((s) => s[1]).join(',')})"></div><div class="grad-lbl small"><span>×1,2</span><span>×4 и выше</span></div>
      <p class="muted small"><b>Прогноз, не наблюдение.</b> Акваторию равномерно засеяли частицами и за 72 ч посчитали, во сколько раз выросла их плотность.</p>`,
    forecast: `${classes()}<p class="muted small"><b>Прогноз, не наблюдение.</b> Покрытие через ${S.drift?.hours ?? 72} ч после пролёта (дрейф детекций течением и ветром).</p>`,
  };
  $('#legend').innerHTML = L[S.mode];
  const p = profileInfo(), sc = S.series?.scenes?.[S.di];
  const fieldDates = (S.field?.features || []).filter((f) => f.properties.profile === S.profile).map((f) => f.properties.date_utc).sort();
  $('#legend-meta').innerHTML = `Единица: <b>шт./км²</b> (концентрация), м²/км² (покрытие)` +
    `<br>Профиль ${S.profile}: <b>${esc(p.size_class || '')}</b>` +
    `<br>Снимок: <b>${sc ? ruDate(S.series.dates[S.di]) : '—'}</b> · измерения профиля: <b>${fieldDates.length ? `${ruDate(fieldDates[0])}–${ruDate(fieldDates[fieldDates.length - 1])}` : '—'}</b>` +
    `<br>Источник: Sentinel-2 L2A (Planetary Computer); полевой реестр кейса` +
    `<br>Модель: <b>${esc(S.conc?.model_version || p.model_version || '—')}</b> · детектор P ≥ ${nf(S.series?.detector?.p_det, 2)}`;
}

function onHexHover(e) {
  if (S.drawing) { map.getCanvas().style.cursor = 'crosshair'; return; }
  map.getCanvas().style.cursor = 'pointer';
  const p = e.features[0].properties, i = p.i, s = S.series, di = S.di;
  let txt;
  if (S.mode === 'conc') {
    const c = concAt(di, i);
    txt = c.value == null ? `Концентрация недоступна (профиль ${S.profile})`
      : `<b>${nf(c.value, 0)} шт./км²</b> <span class="muted">80%: ${nf(c.lo, 0)}–${nf(c.hi, 0)}</span><br>${CONC_NAME[c.status]} · профиль ${S.profile}<br>детекция: ${DET_NAME[detStatus(di, i)]}`;
  } else if (S.mode === 'status') txt = `<b>${DET_NAME[detStatus(di, i)]}</b><br>видно воды ${nf(s.valid[di][i] * 100, 0)}% · пикселей мусора ${s.ndet[di][i]}`;
  else if (S.mode === 'quality') txt = `Видно воды: <b>${nf(s.valid[di][i] * 100, 0)}%</b>`;
  else if (S.mode === 'cover') txt = s.valid[di][i] < 0.5 ? 'Нет данных: облака' : `Покрытие <b>${nf(s.cover[di][i])} м²/км²</b><br>пикселей с мусором: ${s.ndet[di][i]}`;
  else if (S.mode === 'mean') txt = `Среднее покрытие: <b>${nf(p.mean_cover)} м²/км²</b>`;
  else if (S.mode === 'persist') txt = `Мусор на <b>${nf(p.persistence * 100, 0)}%</b> снимков (${p.n_obs} наблюдений)`;
  else if (S.mode === 'trend') txt = `Тренд покрытия: <b>${p.trend_cover > 0 ? '+' : ''}${nf(p.trend_cover, 2)}</b> м²/км² в месяц`;
  else if (S.mode === 'accum') txt = `Прогноз · фактор скопления: <b>×${nf(S.accum?.factor?.[p.h3] ?? 0, 2)}</b>`;
  else {
    const a = S.drift?.hexes?.[String(S.drift.hours)]?.[p.h3] || 0;
    txt = `Прогноз через ${S.drift?.hours ?? 72} ч: <b>${nf(a / Math.max(p.water_km2, 0.05))} м²/км²</b>`;
  }
  const tip = $('#tip');
  tip.innerHTML = txt;
  const rect = map.getCanvas().getBoundingClientRect();
  tip.style.left = `${Math.max(8, Math.min(window.innerWidth - 280, rect.left + e.point.x + 14))}px`;
  tip.style.top = `${Math.max(8, Math.min(window.innerHeight - 100, rect.top + e.point.y + 14))}px`;
  tip.hidden = false;
}

async function setMode(m) {
  if (m === 'accum' && !S.accum) {
    toast('Считаем зоны схождения течений (засев ~2000 частиц, 72 ч)…', 0);
    try { S.accum = await api(`/api/aois/${S.aoi.id}/${S.series.dates[S.di]}/accumulation`); }
    catch (err) { toast(`Не удалось: ${err.message}`); return; }
    $('#toast').hidden = true;
  }
  if (m === 'forecast' && !S.drift) {
    await runDrift();
    if (!S.drift) return;
  }
  S.mode = m;
  $$('#mode button').forEach((b) => b.classList.toggle('on', b.dataset.mode === m));
  paintHexes();
  syncUrl();
}

// ---------- сводка ----------
function renderKpis() {
  const s = S.series, di = S.di, sc = s.scenes[di];
  const nz = S.zones?.features?.length || 0;
  let det = 0, insuff = 0;
  const vals = [];
  S.hexes.features.forEach((f) => {
    const i = f.properties.i, st = detStatus(di, i);
    if (st === 'detected') det++;
    if (st === 'insufficient_data') insuff++;
    const c = concAt(di, i); if (c.value != null) vals.push(c.value);
  });
  const med = median(vals);
  $('#kpis').innerHTML = [
    [nf(nz, 0), 'зон детекции на дату'],
    [med == null ? '—' : nf(med, 0), med == null ? 'шт./км²: концентрация недоступна' : `шт./км², медиана по гексам (профиль ${S.profile}, ${CONC_NAME[concAt(di, 0).status] || ''})`],
    [`${det} / ${insuff}`, `гексов «обнаружено» / «недостаточно данных» из ${S.hexes.features.length}`],
    [nf(sc.area_m2, 0), 'м² покрытия (вспомогательно)'],
  ].map(([b, t]) => `<div class="kpi"><b>${b}</b><span>${t}</span></div>`).join('');
  const zs = [...(S.zones?.features || [])].sort((a, b) => b.properties.cover_m2 - a.properties.cover_m2);
  $('#hotlist').innerHTML = zs.slice(0, 5).map((f) => {
    const z = f.properties, c = z[`conc_${S.profile}_items_km2`];
    return `<li data-z="${esc(z.zone_id)}"><span style="color:#ff5c5c">■</span> ${nf(z.n_pixels, 0)} пикс. · ${nf(z.zone_area_km2 * 1e6, 0)} м² зоны <span class="v">${c == null ? 'C недоступна' : `C≈${nf(c, 0)} шт./км²`}</span></li>`;
  }).join('') || '<li class="muted">на эту дату зон нет</li>';
  $$('#hotlist li[data-z]').forEach((li) => li.onclick = () => openZone(li.dataset.z, true));
}

// ---------- панель инструментов ----------
const TOOL_TITLE = { card: 'Карточка', hex: 'Участок акватории', compare: 'Сравнение участков', drift: 'Прогноз распространения', route: 'План обследования', about: 'О методе' };
function openTool(t, title) {
  if (S.tool === t && !$('#panel').hidden && !title) { closePanel(); return; }
  S.tool = t;
  $('#panel').hidden = false;
  $('#panel-title').textContent = title || TOOL_TITLE[t];
  $$('.tool').forEach((el) => el.classList.toggle('on', el.dataset.tool === t));
  $$('#tools button').forEach((b) => b.classList.toggle('on', b.dataset.tool === t));
  if (t === 'about') renderAbout();
}
function closePanel() {
  $('#panel').hidden = true; S.tool = null;
  $$('#tools button').forEach((b) => b.classList.remove('on'));
  stopDrawing();
}

// ---------- карточки: зона и полевое измерение ----------
function kv(rows) {
  return `<table class="kv">${rows.map(([k, v]) => `<tr><td>${k}</td><td>${v}</td></tr>`).join('')}</table>`;
}
function openZone(zoneId, fly = false) {
  const f = (S.zones?.features || []).find((x) => x.properties.zone_id === zoneId);
  if (!f) return;
  const z = f.properties, P = S.profile, p = profileInfo();
  if (fly) map.flyTo({ center: [z.lon, z.lat], zoom: 14 });
  const c = z[`conc_${P}_items_km2`], st = z[`conc_${P}_status`] || 'unavailable';
  const reasons = (z[`conc_${P}_reasons`] || '').split('; ').filter(Boolean);
  $('#card-body').innerHTML = `
    <p>${badge('detected', DET_NAME.detected)} ${badge(st, CONC_NAME[st])}</p>
    <div class="big-value">${c == null ? '—' : nf(c, 0)}<small>шт./км²</small></div>
    <p>Профиль ${P}: ${esc(p.label || '')}. ${c == null ? '' : `80%: ${nf(z[`conc_${P}_lo80`], 0)}–${nf(z[`conc_${P}_hi80`], 0)}; 95%: ${nf(z[`conc_${P}_lo95`], 0)}–${nf(z[`conc_${P}_hi95`], 0)} шт./км².`}</p>
    ${reasons.length ? `<ul class="reasons">${reasons.map((r) => `<li>${esc(r)}</li>`).join('')}</ul>` : ''}
    <h3>Зона детекции (результат детектора)</h3>
    ${kv([
      ['Площадь зоны', `${nf(z.zone_area_km2 * 1e6, 0)} м² (${nf(z.zone_area_km2, 4)} км²)`],
      ['Эквивалентное покрытие мусором', `${nf(z.cover_m2, 1)} м²`],
      ['Пикселей 10 м', nf(z.n_pixels, 0)],
      ['Вероятность P (средняя / макс.)', `${nf(z.p_mean, 2)} / ${nf(z.p_max, 2)}`],
      ['Координаты центра', `${nf(z.lat, 5)}, ${nf(z.lon, 5)}`],
    ])}
    <h3>Снимок и качество</h3>
    ${kv([
      ['Дата и время снимка', `${ruDate(z.date)} ${localTime(z.scene_datetime_utc, S.aoi.tz)} (UTC+${S.aoi.tz})`],
      ['Сцена', `<span class="small">${esc(z.scene_id)}</span>`],
      ['Видно воды на сцене', `${nf(z.valid_frac_scene * 100, 0)}%`],
      ['Ветер ERA5 / море', `${nf(z.wind_ms)} м/с · ${esc(z.sea)}`],
    ])}
    <h3>Ближайшее полевое измерение</h3>
    ${z.field_event_id ? kv([
      ['Событие', `<a href="#" data-ev="${esc(z.field_event_id)}">${esc(z.field_event_id)}</a> (профиль ${esc(z.field_profile)})`],
      ['Расстояние / разница дат', `${nf(z.field_distance_km, 0)} км · ${nf(z.field_date_gap_days, 0)} сут.`],
      ['Измерено', `${nf(z.field_conc_items_km2, 1)} шт./км² · ${ruDate(z.field_date)}`],
    ]) : '<p>нет</p>'}
    <p class="small">Модель: ${esc(z[`conc_${P}_model`] || '—')}. Концентрация взята из модели по полевым данным профиля и не выводится из площади маски.</p>
    <div class="export-grid"><button id="card-exp-geojson">Зоны даты · GeoJSON</button><button id="card-exp-csv">Зоны даты · CSV</button></div>`;
  $$('#card-body a[data-ev]').forEach((a) => a.onclick = (ev) => { ev.preventDefault(); openField(a.dataset.ev, true); });
  $('#card-exp-geojson').onclick = () => exportLayer('zones', 'geojson');
  $('#card-exp-csv').onclick = () => exportLayer('zones', 'csv');
  map.getSource('sel').setData({ type: 'FeatureCollection', features: [f] });
  openTool('card', `Зона ${z.zone_id.split('-').pop()}`);
}

async function openField(eventId, fly = false) {
  try {
    const r = await api(`/api/field/${encodeURIComponent(eventId)}`);
    const m = r.measurements[0];
    if (fly && m) map.flyTo({ center: [m.lon, m.lat], zoom: 10 });
    const rows = r.rows.map((x) => `<tr><td>${esc(x.sample_id)}</td><td>${esc(x.target_scope)}</td><td>${x.decision === 'included' ? `профиль ${esc(x.profile)} (${esc(x.role)})` : esc(x.reason_code)}</td></tr>`).join('');
    const pairs = r.pairs.map((p) => `<tr><td class="small">${esc((p.scene_datetime_utc || '—').replace('T', ' ').slice(0, 16))}</td><td>${esc(p.sync_tier || '')}</td><td>${badge(p.decision === 'accepted' ? 'model_estimate' : 'unavailable', p.reason_code)}</td></tr>`).join('');
    $('#card-body').innerHTML = m ? `
      <p>${badge('measurement', 'измерение')} ${badge('', `профиль ${m.profile} · ${m.role}`)}</p>
      <div class="big-value">${nf(m.conc_items_km2, 1)}<small>шт./км²</small></div>
      <p>${esc(m.formula)}${m.conc_lo95 != null ? `<br>95% ДИ (Гарвуд, счётный шум): ${nf(m.conc_lo95, 1)}–${nf(m.conc_hi95, 1)} шт./км²` : ''}</p>
      ${kv([
        ['Материал / размер', `${esc(m.material)} · ${esc(m.size_class)}`],
        ['Метод', esc(m.sampling_method)],
        ['Совокупность', esc(m.target_scope)],
        ['Дата, время UTC', `${ruDate(m.date_utc)} ${m.time_known ? `${m.t_start_utc.slice(11, 16)}–${m.t_end_utc.slice(11, 16)}` : '(время неизвестно)'}`],
        ['Геометрия', `${esc(m.geometry_type)}${m.length_km ? ` · ${nf(m.length_km, 1)} км × ${nf(m.width_m, 0)} м` : ''}`],
        ['Источник', `${esc(m.source_id)} · <span class="small">${esc(m.source_doi)} (${esc(m.source_license)})</span>`],
        ['Флаги качества', `<span class="small">${esc(m.quality_flags || '—')}</span>`],
      ])}
      <p class="small">Концентрация относится ко всей обследованной полосе, а не к точке на карте.</p>
      <h3>Строки реестра события</h3><table><tr><th>sample_id</th><th>scope</th><th>решение</th></tr>${rows}</table>
      <h3>Сопоставление со снимками</h3>${pairs ? `<table><tr><th>снимок</th><th>ярус</th><th>решение</th></tr>${pairs}</table>` : '<p>нет кандидатов</p>'}`
      : `<p>Событие не вошло ни в один профиль.</p><table>${rows}</table>`;
    openTool('card', `Измерение ${eventId}`);
  } catch (err) { toast(`Не удалось открыть событие: ${err.message}`); }
}

// ---------- участок ----------
function selectHex(i) {
  S.sel = i;
  const f = S.hexes.features[i];
  map.getSource('sel').setData({ type: 'FeatureCollection', features: [f] });
  openTool('hex', TOOL_TITLE.hex);
  renderHexPanel();
}
async function renderHexPanel() {
  const i = S.sel, p = S.hexes.features[i].properties, s = S.series, di = S.di;
  $('#hex-empty').hidden = true; $('#hex-body').hidden = false;
  const st = detStatus(di, i), c = concAt(di, i);
  $('#hex-kpis').innerHTML = [
    [c.value == null ? '—' : nf(c.value, 0), `шт./км² (профиль ${S.profile}) на ${ruDate(s.dates[di])}`],
    [DET_NAME[st], 'статус детекции'],
    [nf(s.cover[di][i]), 'покрытие, м²/км² (вспомогательно)'],
    [`${nf(p.persistence * 100, 0)}%`, `снимков с мусором (${p.n_obs})`],
    [`${p.trend_cover > 0 ? '+' : ''}${nf(p.trend_cover, 2)}`, 'тренд покрытия, м²/км² в мес'],
    [`${nf(p.water_km2, 2)} · ${nf(p.dist_coast_km, 1)}`, 'км² воды · км до берега'],
  ].map(([b, t]) => `<div class="kpi"><b>${b}</b><span>${t}</span></div>`).join('');
  $('#hex-conc').innerHTML = '<p class="muted small">Загрузка оценки…</p>';
  const data = s.dates.map((d, j) => (s.valid[j][i] >= 0.5 ? s.cover[j][i] : null));
  S.charts.hex?.destroy();
  S.charts.hex = new Chart($('#hex-chart'), {
    type: 'bar',
    data: { labels: s.dates.map(shortDate), datasets: [{ data, backgroundColor: s.dates.map((d, j) => j === S.di ? ACCENT : s.scenes[j].storm ? '#4a5568' : '#ff8a4c'), borderRadius: 2 }] },
    options: { responsive: true, maintainAspectRatio: false, animation: { duration: 650, easing: 'easeOutQuart' },
      plugins: { legend: { display: false }, title: { display: true, text: 'Покрытие по датам, м²/км² (вспомогательный показатель)', color: '#94a3b8', font: { weight: 'normal' } } },
      scales: { x: { ticks: { maxRotation: 0, autoSkip: true, maxTicksLimit: 5 }, grid: { display: false } }, y: { beginAtZero: true, grid: { color: 'rgba(148,163,184,.12)' }, ticks: { maxTicksLimit: 4 } } },
      onClick: (e, els) => { if (els.length) setDate(els[0].index); } },
  });
  $('#hex-drift-info').textContent = '';
  try {
    const r = await api(`/api/aois/${S.aoi.id}/${s.dates[di]}/hex/${i}?profile=${S.profile}`);
    if (S.sel !== i) return;
    $('#hex-conc').innerHTML = `<p>${badge(r.detection_status, r.detection_status_ru)} ${badge(r.status, r.status_ru)}</p>` +
      (r.conc_items_km2 == null ? '<p class="small">Концентрация недоступна: профиль не применим к точке.</p>'
        : `<p class="small">C ≈ <b>${nf(r.conc_items_km2, 0)}</b> шт./км²; 80%: ${nf(r.lo80, 0)}–${nf(r.hi80, 0)}; 95%: ${nf(r.lo95, 0)}–${nf(r.hi95, 0)}. Модель ${esc(r.model_type)} (${esc(r.model_version)}).</p>`) +
      (r.reasons.length ? `<ul class="reasons">${r.reasons.map((x) => `<li>${esc(x)}</li>`).join('')}</ul>` : '') +
      `<p class="small muted">Признаки: ${Object.entries(r.features).map(([k, v]) => `${k} = ${nf(v, 2)}`).join('; ')}. Ближайшее полевое измерение: ${nf(r.nearest_field_km, 0)} км.</p>`;
  } catch (err) { $('#hex-conc').innerHTML = `<p class="small">Оценка недоступна: ${esc(err.message)}</p>`; }
}

async function hexDrift(trigger = $('#hex-drift')) {
  const p = S.hexes.features[S.sel].properties;
  return withButtonLoading(trigger, 'Рассчитываем прогноз…', async () => {
    try {
      const r = await api(`/api/aois/${S.aoi.id}/${S.series.dates[S.di]}/drift_point?lon=${p.lon}&lat=${p.lat}&hours=72&n=40`);
      map.getSource('cone').setData({ type: 'FeatureCollection', features: r.tracks.map((t) => ({ type: 'Feature', geometry: { type: 'LineString', coordinates: t } })) });
      const end = r.center[r.center.length - 1];
      map.getSource('coneCenter').setData({ type: 'FeatureCollection', features: [
        { type: 'Feature', geometry: { type: 'LineString', coordinates: r.center } },
        { type: 'Feature', geometry: { type: 'Point', coordinates: end } }] });
      const km = haversine(p.lon, p.lat, end[0], end[1]);
      $('#hex-drift-info').innerHTML = `Прогноз: через 72 ч центр ансамбля сместится на <b>${nf(km)} км</b>, разброс ±${nf(r.spread_km[72])} км` +
        ` (24 ч: ±${nf(r.spread_km[24])} км). На берег выброшено ${nf(r.beached_frac * 100, 0)}% частиц.`;
    } catch (err) { toast(`Ошибка прогноза: ${err.message}`); }
  });
}
function haversine(lon1, lat1, lon2, lat2) {
  const R = 6371, r = Math.PI / 180;
  const a = Math.sin((lat2 - lat1) * r / 2) ** 2 + Math.cos(lat1 * r) * Math.cos(lat2 * r) * Math.sin((lon2 - lon1) * r / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(a));
}

// ---------- сравнение ----------
function startDrawing(which) {
  S.drawing = which; S.draft = [];
  map.doubleClickZoom.disable();
  map.getCanvas().style.cursor = 'crosshair';
  $$('#draw-a, #draw-b').forEach((b) => b.classList.toggle('on', b.id === `draw-${which.toLowerCase()}`));
  toast(`Участок ${which}: кликайте вершины, двойной клик — завершить`, 3500);
  updateDraft();
}
function stopDrawing() {
  S.drawing = null; S.draft = [];
  map.getCanvas().style.cursor = '';
  setTimeout(() => map.doubleClickZoom.enable(), 300);
  $$('#draw-a, #draw-b').forEach((b) => b.classList.remove('on'));
  if (map.getSource('draw')) updateDraft();
}
function updateDraft() {
  const pts = S.draft;
  const feats = pts.map((c) => ({ type: 'Feature', geometry: { type: 'Point', coordinates: c } }));
  if (pts.length > 1) feats.push({ type: 'Feature', geometry: { type: 'LineString', coordinates: pts } });
  map.getSource('draw').setData({ type: 'FeatureCollection', features: feats });
}
function onDrawClick(e) {
  if (!S.drawing) return;
  S.draft.push([e.lngLat.lng, e.lngLat.lat]);
  updateDraft();
}
function onDrawFinish(e) {
  if (!S.drawing) return;
  e.preventDefault();
  // dblclick добавил две одинаковые вершины — убираем дубли
  const pts = S.draft.filter((c, i, a) => i === 0 || Math.hypot(c[0] - a[i - 1][0], c[1] - a[i - 1][1]) > 1e-6);
  if (pts.length < 3) { toast('Нужно минимум 3 вершины'); return; }
  const which = S.drawing;
  S.cmp[which] = pts;
  map.getSource(`cmp${which}`).setData({ type: 'Feature', geometry: { type: 'Polygon', coordinates: [[...pts, pts[0]]] } });
  stopDrawing();
  renderCompare();
}
function clearCompare() {
  S.cmp = { A: null, B: null };
  if (map.getSource('cmpA')) { map.getSource('cmpA').setData(EMPTY); map.getSource('cmpB').setData(EMPTY); }
  $('#cmp-result').innerHTML = '';
  S.charts.cmp?.destroy(); S.charts.cmp = null;
}
function areaStats(poly) {
  const s = S.series;
  const inside = S.hexes.features.map((f) => f.properties).filter((p) => pip([p.lon, p.lat], poly));
  const km2 = inside.reduce((a, p) => a + p.water_km2, 0);
  const ser = s.dates.map((d, j) => {
    let area = 0, vk = 0;
    for (const p of inside) { const w = p.water_km2 * s.valid[j][p.i]; vk += w; area += s.cover[j][p.i] * w; }
    return vk > 0.5 * km2 && km2 > 0 ? area / vk : null;
  });
  const okIdx = ser.map((v, j) => (v != null && !s.scenes[j].storm ? j : -1)).filter((j) => j >= 0);
  const vals = okIdx.map((j) => ser[j]);
  const mean = vals.length ? vals.reduce((a, b) => a + b, 0) / vals.length : null;
  const conc = inside.map((p) => concAt(S.di, p.i).value).filter((v) => v != null);
  return {
    n: inside.length, km2, ser, mean, cur: ser[S.di], max: vals.length ? Math.max(...vals) : null,
    share: vals.length ? vals.filter((v) => v > 0).length / vals.length : null,
    trend: slope(okIdx.map((j) => monthsFrom0(s.dates[j])), vals),
    hot: inside.filter((p) => p.persistence >= 0.2).length,
    zones: (S.zones?.features || []).filter((f) => pip([f.properties.lon, f.properties.lat], poly)).length,
    conc: conc.length ? conc.reduce((a, b) => a + b, 0) / conc.length : null,
  };
}
function renderCompare() {
  const A = S.cmp.A ? areaStats(S.cmp.A) : null, B = S.cmp.B ? areaStats(S.cmp.B) : null;
  const col = (st, f) => (st ? f(st) : '—');
  const rows = [
    ['Акватория, км²', (s) => nf(s.km2)],
    ['Гексов', (s) => s.n],
    [`Зон детекции на ${ruDate(S.series.dates[S.di])}`, (s) => s.zones],
    [`Концентрация (модель, профиль ${S.profile}), шт./км²`, (s) => nf(s.conc, 0)],
    [`Покрытие на дату, м²/км²`, (s) => nf(s.cur, 2)],
    ['Среднее покрытие, м²/км²', (s) => nf(s.mean, 2)],
    ['Доля снимков с мусором', (s) => (s.share == null ? '—' : `${nf(s.share * 100, 0)}%`)],
    ['Устойчивых гексов (≥20%)', (s) => s.hot],
    ['Тренд покрытия, м²/км² в мес', (s) => `${s.trend > 0 ? '+' : ''}${nf(s.trend, 2)}`],
  ];
  let verdict = '';
  if (A && B && A.mean != null && B.mean != null) {
    const [hi, lo, nh, nl] = A.mean >= B.mean ? [A, B, 'A', 'B'] : [B, A, 'B', 'A'];
    const ratio = lo.mean > 0 ? hi.mean / lo.mean : null;
    verdict = `<div class="verdict">На участке <b>${nh}</b> детектор чаще видит скопления: среднее покрытие ${ratio ? `в <b>${nf(ratio, 1)} раза</b> выше` : 'выше (на участке ' + nl + ' мусор не найден)'}.` +
      ` Приоритет обследования — <b>${nh}</b>${hi.trend > 0 ? ', покрытие растёт' : ''}. Модельная концентрация по полевым данным от детекций не зависит и здесь служит фоном.</div>`;
  }
  $('#cmp-result').innerHTML = `<table><tr><th></th><th style="color:${COL_A}">A</th><th style="color:${COL_B}">B</th></tr>` +
    rows.map(([n, f]) => `<tr><td>${n}</td><td>${col(A, f)}</td><td>${col(B, f)}</td></tr>`).join('') + '</table>' + verdict;
  S.charts.cmp?.destroy();
  const ds = [];
  if (A) ds.push({ label: 'A', data: A.ser, borderColor: COL_A, backgroundColor: COL_A, spanGaps: true, tension: 0.25, pointRadius: 2 });
  if (B) ds.push({ label: 'B', data: B.ser, borderColor: COL_B, backgroundColor: COL_B, spanGaps: true, tension: 0.25, pointRadius: 2 });
  S.charts.cmp = new Chart($('#cmp-chart'), {
    type: 'line', data: { labels: S.series.dates.map(shortDate), datasets: ds },
    options: { responsive: true, maintainAspectRatio: false, animation: { duration: 650, easing: 'easeOutQuart' },
      plugins: { title: { display: true, text: 'Покрытие по датам, м²/км²', color: '#94a3b8', font: { weight: 'normal' } }, legend: { labels: { boxWidth: 10 } } },
      scales: { x: { ticks: { maxRotation: 0, autoSkip: true, maxTicksLimit: 5 }, grid: { display: false } }, y: { beginAtZero: true, grid: { color: 'rgba(148,163,184,.12)' } } } },
  });
}

// ---------- дрейф ----------
function clearDrift() {
  cancelAnimationFrame(S.anim); S.anim = null; S.drift = null;
  ['tracks', 'particles', 'cone', 'coneCenter'].forEach((id) => map.getSource(id)?.setData(EMPTY));
  $('#drift-ctrl').hidden = true;
  $('#drift-play').textContent = '▶';
}
async function runDrift(trigger = $('#drift-run')) {
  return withButtonLoading(trigger, 'Рассчитываем прогноз…', async () => {
    toast('Прогноз дрейфа: течения SMOC + ветер ERA5, ансамбль частиц…', 0);
    try {
      const r = await api(`/api/aois/${S.aoi.id}/${S.series.dates[S.di]}/drift?hours=72`);
      if (!r.n) { toast('На эту дату детекций нет — нечего переносить'); return; }
    S.drift = r;
    const n = r.frames[0].length;
    const tracks = [];
    for (let k = 0; k < n; k++) tracks.push({ type: 'Feature', geometry: { type: 'LineString', coordinates: r.frames.map((f) => f[k]) } });
    map.getSource('tracks').setData({ type: 'FeatureCollection', features: tracks });
    $('#drift-hour').max = r.hours;
    $('#drift-ctrl').hidden = false;
    showDriftHour(0);
    const beached = r.beached.reduce((a, b) => a + b, 0) / r.beached.length;
    const disp = [];
    for (let k = 0; k < n; k++) { const a = r.frames[0][k], b = r.frames[r.hours][k]; disp.push(haversine(a[0], a[1], b[0], b[1])); }
    disp.sort((a, b) => a - b);
    $('#drift-kpis').innerHTML = [
      [n, 'частиц в ансамбле'],
      [`${nf(disp[Math.floor(n / 2)])} км`, 'медианное смещение за 72 ч'],
      [`${nf(disp[Math.floor(n * 0.9)])} км`, '90-й перцентиль смещения'],
      [`${nf(beached * 100, 0)}%`, 'выброшено на берег'],
    ].map(([b, t]) => `<div class="kpi"><b>${b}</b><span>${t}</span></div>`).join('');
    $('#toast').hidden = true;
      playDrift();
    } catch (err) { toast(`Ошибка прогноза: ${err.message}`); }
  });
}
function showDriftHour(h) {
  const r = S.drift; if (!r) return;
  h = Math.round(h);
  $('#drift-hour').value = h;
  const t = new Date(new Date(r.t0).getTime() + h * 3600e3).toISOString();
  $('#drift-hlabel').textContent = `+${h} ч · ${localTime(t, S.aoi.tz)}`;
  // Выброшенная частица дальше не движется: считаем её на берегу с момента, когда позиция совпала с финальной
  const last = r.frames[r.hours];
  map.getSource('particles').setData({ type: 'FeatureCollection', features: r.frames[h].map((c, k) => ({
    type: 'Feature',
    properties: { b: r.beached[k] && c[0] === last[k][0] && c[1] === last[k][1] ? 1 : 0 },
    geometry: { type: 'Point', coordinates: c } })) });
}
function playDrift() {
  if (S.anim) { cancelAnimationFrame(S.anim); S.anim = null; $('#drift-play').textContent = '▶'; return; }
  $('#drift-play').textContent = '❚❚';
  let h = +$('#drift-hour').value >= S.drift.hours ? 0 : +$('#drift-hour').value;
  let last = performance.now();
  const step = (now) => {
    if (now - last > 70) { last = now; showDriftHour(h); h += 1; }
    if (h > S.drift.hours) { S.anim = null; $('#drift-play').textContent = '▶'; return; }
    S.anim = requestAnimationFrame(step);
  };
  S.anim = requestAnimationFrame(step);
}

// ---------- маршрут ----------
function clearRoute() {
  S.route = null;
  S.stopMarkers.forEach((m) => m.remove()); S.stopMarkers = [];
  ['route', 'routeDrift', 'routeObs'].forEach((id) => map.getSource(id)?.setData(EMPTY));
  $('#route-result').innerHTML = '';
}
async function runRoute(trigger = $('#route-run')) {
  const n = $('#r-n').value, sp = $('#r-speed').value, dl = $('#r-delay').value;
  return withButtonLoading(trigger, 'Строим маршрут…', async () => {
    try {
      const r = await api(`/api/aois/${S.aoi.id}/${S.series.dates[S.di]}/route?n=${n}&speed=${sp}&delay=${dl}`);
    clearRoute();
    S.route = r;
      if (!r.stops.length) { $('#route-result').innerHTML = `<p class="muted">${r.note || 'Нет целей в пределах смены.'}</p>`; return; }
    map.getSource('route').setData({ type: 'Feature', geometry: { type: 'LineString', coordinates: r.line } });
    map.getSource('routeDrift').setData({ type: 'FeatureCollection', features: r.stops.map((s) => ({ type: 'Feature', geometry: { type: 'LineString', coordinates: [s.observed, s.predicted] } })) });
    map.getSource('routeObs').setData({ type: 'FeatureCollection', features: r.stops.map((s) => ({ type: 'Feature', geometry: { type: 'Point', coordinates: s.observed } })) });
    for (const s of r.stops) {
      const el = document.createElement('div');
      el.className = 'stop-marker'; el.textContent = s.order;
      el.title = `Точка ${s.order}: ETA ${localTime(s.eta, S.aoi.tz)}`;
      S.stopMarkers.push(new maplibregl.Marker({ element: el }).setLngLat(s.predicted).addTo(map));
    }
    const tz = S.aoi.tz;
    $('#route-result').innerHTML = `
      <div class="kpis mt">
        <div class="kpi"><b>${nf(r.total_km)} км</b><span>длина маршрута</span></div>
        <div class="kpi"><b>${nf(r.duration_h)} ч</b><span>время в море</span></div>
        <div class="kpi"><b>${nf(r.covered_m2, 0)} м²</b><span>покрытия на маршруте</span></div>
        <div class="kpi"><b>${nf(r.total_m2 ? (100 * r.covered_m2) / r.total_m2 : 0, 0)}%</b><span>от всех скоплений</span></div>
      </div>
      <ul class="stops">${r.stops.map((s) => `<li>
        <span class="n">${s.order}</span>
        <span>${nf(s.area_m2, 0)} м² · ${s.n_pixels} пикс.<br><span class="meta">дрейф к прибытию ${nf(s.drift_km, 2)} км · переход ${nf(s.leg_km)} км</span></span>
        <span class="eta">${localTime(s.eta, tz)}<br><span class="meta">+${nf(s.eta_h)} ч</span></span></li>`).join('')}</ul>
      <p class="muted small">Время местное (UTC+${tz}). Оранжевые отрезки — прогноз смещения пятна от снимка до прибытия судна.</p>
      <div class="row gap8"><button class="ghost" id="exp-geojson">GeoJSON</button><button class="ghost" id="exp-gpx">GPX для навигатора</button></div>`;
    $('#exp-geojson').onclick = exportRouteGeoJSON;
    $('#exp-gpx').onclick = exportGPX;
    const b = r.line.reduce((bb, c) => [[Math.min(bb[0][0], c[0]), Math.min(bb[0][1], c[1])], [Math.max(bb[1][0], c[0]), Math.max(bb[1][1], c[1])]], [[180, 90], [-180, -90]]);
      map.fitBounds(b, { padding: { top: 80, bottom: 40, left: 40, right: 420 }, maxZoom: 13 });
    } catch (err) { toast(`Ошибка маршрута: ${err.message}`); }
  });
}
function exportRouteGeoJSON() {
  const r = S.route;
  const fc = { type: 'FeatureCollection', features: [
    { type: 'Feature', properties: { kind: 'route', total_km: r.total_km, pass_time: r.pass_time }, geometry: { type: 'LineString', coordinates: r.line } },
    ...r.stops.map((s) => ({ type: 'Feature', properties: { kind: 'stop', ...s }, geometry: { type: 'Point', coordinates: s.predicted } })),
  ] };
  download(`route_${S.aoi.id}_${S.series.dates[S.di]}.geojson`, JSON.stringify(fc, null, 1), 'application/geo+json');
}
function exportGPX() {
  const r = S.route;
  const wpt = r.stops.map((s) => `  <wpt lat="${s.predicted[1]}" lon="${s.predicted[0]}"><name>T${s.order}</name><desc>${nf(s.area_m2, 0)} m2, ETA ${s.eta}</desc></wpt>`).join('\n');
  const rte = r.line.map((c, i) => `    <rtept lat="${c[1]}" lon="${c[0]}"><name>${i === 0 || i === r.line.length - 1 ? 'PORT' : `T${i}`}</name></rtept>`).join('\n');
  const gpx = `<?xml version="1.0" encoding="UTF-8"?>\n<gpx version="1.1" creator="Flux" xmlns="http://www.topografix.com/GPX/1/1">\n${wpt}\n  <rte><name>Survey ${S.series.dates[S.di]}</name>\n${rte}\n  </rte>\n</gpx>\n`;
  download(`route_${S.aoi.id}_${S.series.dates[S.di]}.gpx`, gpx, 'application/gpx+xml');
}

// ---------- выгрузка и сохранённый запрос ----------
function exportParams(layer, format) {
  return new URLSearchParams({ aoi: S.aoi.id, date: S.series.dates[S.di], profile: S.profile, layer, format });
}
function exportLayer(layer, format) {
  const a = document.createElement('a');
  a.href = `/api/export?${exportParams(layer, format)}`;
  a.download = '';
  a.click();
}
async function saveQuery(trigger = $('#query-save')) {
  return withButtonLoading(trigger, '', async () => {
    try {
      const body = { aoi: S.aoi.id, date: S.series.dates[S.di], profile: S.profile, layer: 'zones', format: 'geojson' };
      const r = await api('/api/queries', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
      S.queryId = r.id;
      syncUrl();
      $('#query-info').innerHTML = `Запрос <b>${r.id}</b> сохранён: ${esc(r.params.aoi)}, ${ruDate(r.params.date)}, профиль ${r.params.profile}.<br>sha256 результата: ${r.result_sha256.slice(0, 16)}…`;
    } catch (err) { toast(`Не удалось сохранить запрос: ${err.message}`); }
  });
}
async function rerunQuery(trigger = $('#query-rerun')) {
  if (!S.queryId) { toast('Сначала сохраните запрос'); return; }
  return withButtonLoading(trigger, '', async () => {
    try {
      const r = await api(`/api/queries/${S.queryId}/rerun`, { method: 'POST' });
      $('#query-info').innerHTML = `Повтор запроса <b>${r.id}</b>: ${r.match ? '<span style="color:#5eead4">результат совпадает</span>' : '<span style="color:#fca5a5">результат отличается</span>'}` +
        `<br>сохранён ${r.saved_sha256.slice(0, 16)}… · сейчас ${r.rerun_sha256.slice(0, 16)}…`;
      if (r.params.aoi !== S.aoi.id || r.params.date !== S.series.dates[S.di] || r.params.profile !== S.profile) {
        S.profile = r.params.profile; $('#profile').value = S.profile;
        await loadAoi(r.params.aoi, r.params.date);
      }
    } catch (err) { toast(`Не удалось повторить запрос: ${err.message}`); }
  });
}
function syncUrl() {
  if (!S.aoi || !S.series) return;
  const q = new URLSearchParams({ aoi: S.aoi.id, date: S.series.dates[S.di], profile: S.profile, mode: S.mode });
  if (S.queryId) q.set('q', S.queryId);
  history.replaceState(null, '', `?${q}`);
}

function fitAoi() {
  if (!S.aoi) return;
  const [x0, y0, x1, y1] = S.aoi.bbox;
  map.fitBounds([[x0, y0], [x1, y1]], { padding: 42, duration: 650 });
}

async function toggleFullscreen(trigger = $('#map-fullscreen')) {
  return withButtonLoading(trigger, '', async () => {
    try {
      if (!document.fullscreenElement) await document.documentElement.requestFullscreen();
      else await document.exitFullscreen();
      requestAnimationFrame(() => map.resize());
    } catch (err) { toast(`Полноэкранный режим недоступен: ${err.message}`); }
  });
}

// ---------- о методе ----------
let metrics = null;
const pct = (v) => nf(v * 100, 1);
async function renderAbout() {
  const trigger = $('#tools button[data-tool="about"]');
  const render = async () => {
  if (!metrics) { try { metrics = await api('/api/metrics'); } catch { metrics = null; } }
  const det = metrics?.detector, conc = metrics?.concentration || {}, pairs = metrics?.pairs;
  const detRows = det ? Object.entries(det.methods).map(([k, m]) =>
    `<tr><td>${esc(m.name)}</td><td>${nf(m.precision, 3)}</td><td>${nf(m.recall, 3)}</td><td>${nf(m.f1, 3)}<br><span class="small muted">${nf(m.ci95.f1[0], 2)}–${nf(m.ci95.f1[1], 2)}</span></td><td>${nf(m.iou, 3)}</td></tr>`).join('') : '';
  const main = det?.methods?.xgb_filters;
  const fpRows = main ? Object.entries(main.fp_by_class).filter(([, v]) => v.class_px > 0).map(([k, v]) =>
    `<tr><td>${esc(k)}</td><td>${nf(v.fp_px, 0)}</td><td>${nf(v.class_px, 0)}</td><td>${pct(v.fp_rate)}%</td></tr>`).join('') : '';
  const concBlocks = Object.entries(conc).map(([pid, r]) => {
    const rows = Object.keys(r.cv).map((m) => `<tr><td>${m}${m === r.serve_model ? ' ★' : ''}${m === r.main ? ' (основная)' : ''}</td><td>${nf(r.cv[m].mae, 1)}</td><td>${nf(r.cv[m].rmse, 1)}</td><td>${nf(r.holdout[m].mae, 1)}</td><td>${nf(r.holdout[m].rmse, 1)}</td></tr>`).join('');
    const tc = r.transfer_check ? `<p class="small">Перенос (обучение на ${r.n_dev} событиях S4 → проверка на ${r.transfer_check.n_events} событиях S3, Северное море, >2 см): MAE ${Object.entries(r.transfer_check).filter(([, v]) => v?.mae != null).map(([m, v]) => `${m} ${nf(v.mae, 0)}`).join(', ')} шт./км²; медианы ${nf(r.transfer_check.target_median_train, 0)} → ${nf(r.transfer_check.target_median_check, 0)}. <b>Перенос на другое море не подтверждён.</b></p>` : '';
    return `<h3>Профиль ${pid}: признаки ${esc(r.features.join(', '))}</h3>
      <table><tr><th>Модель</th><th>CV MAE</th><th>CV RMSE</th><th>Отлож. MAE</th><th>Отлож. RMSE</th></tr>${rows}</table>
      <p class="small">Обучение ${r.n_dev} событий, отложено ${r.n_holdout}, групп ${r.n_groups}. ★ — модель сервиса (минимум MAE на CV). Покрытие 80%-интервала на отложенной выборке: ${pct(r.holdout_interval_coverage['0.8'])}%.` +
      (r.poisson_floor_mae_dev ? ` Нижняя граница MAE из-за счётного шума: ${nf(r.poisson_floor_mae_dev, 1)} шт./км².` : '') + `</p>${tc}`;
  }).join('');
  const pf = metrics?.pair_features || [];
  $('#about').innerHTML = `<div class="about">
    <p>Сервис разделяет три величины: <b>зоны детекции</b> (где со снимка видны скопления), <b>полевые измерения</b> (C = N/A по полосе учёта) и <b>модельную концентрацию</b> в шт./км² для заявленного профиля. Площадь маски в число предметов не переводится.</p>
    <h3>Как считается</h3>
    <ol>
      <li><b>Sentinel-2 L2A</b>, 11 каналов, 10 м. Облака и тени по SCL, постоянная маска воды, маска качества пикселя отдельно от решения «мусор / не мусор».</li>
      <li><b>Нормализация фона</b>: из пикселя вычитаем локальный спектр воды — уходят блик, дымка и разница атмосферной коррекции.</li>
      <li><b>Классификатор XGBoost</b> на MARIDA (спектр, FDI, FAI, NDVI, PI, текстура) + фильтры: пена, соседство, CFAR 5σ, постоянные объекты, кильватер, лёд, шторм ≥ 8 м/с.</li>
      <li><b>Концентрация</b>: модель по полевым данным профиля (признаки доступны при применении: координаты, расстояние до берега, ветер ERA5), групповая проверка, конформные интервалы, область применимости.</li>
      <li><b>Прогноз</b> (доп. функция): лагранжев ансамбль, течения SMOC + 1–3% ветра, выброс на берег; маршрут обследования.</li>
    </ol>
    <h3>Детектор: MARIDA test (${det?.n_test_scenes ?? '—'} сцен, ${det?.n_test_patches ?? '—'} патчей)</h3>
    <table><tr><th>Метод</th><th>P</th><th>R</th><th>F1 (95% ДИ)</th><th>IoU</th></tr>${detRows}</table>
    <p class="small muted">Положительный класс: ${esc(det?.positive_class || '')}. Игнорируются: ${esc(det?.ignored || '')}. Порог основного P ≥ ${nf(det?.thresholds?.xgb, 2)} задан до проверки; пороги базовых подобраны на val.</p>
    ${metrics?.detector_lro ? `<h3>Перенос детектора на новый регион (обучение без региона)</h3>
    <table><tr><th>Регион</th><th>Патчей</th><th>P</th><th>R</th><th>F1</th><th>IoU</th></tr>${Object.entries(metrics.detector_lro.regions).map(([k, v]) =>
      `<tr><td>${esc(k)}</td><td>${v.n_patches}</td><td>${nf(v.xgb_filters.precision, 2)}</td><td>${nf(v.xgb_filters.recall, 2)}</td><td>${nf(v.xgb_filters.f1, 2)}</td><td>${nf(v.xgb_filters.iou, 2)}</td></tr>`).join('')}</table>
    <p class="small muted">Средний F1 ${nf(metrics.detector_lro.mean_f1_filters, 2)}, минимальный ${nf(metrics.detector_lro.min_f1_filters, 2)}: на новом районе качество ниже, чем на официальном test.</p>` : ''}
    <h3>Ложные срабатывания основного алгоритма на сложном фоне</h3>
    <table><tr><th>Класс фона</th><th>FP, пикс.</th><th>Всего</th><th>Доля</th></tr>${fpRows}</table>
    <h3>Концентрация, шт./км²</h3>${concBlocks}
    <h3>Совместные пары «событие ↔ снимок»</h3>
    <p class="small">${pairs ? `Событий ${pairs.events}, кандидатов-сцен ${pairs.candidates}. Итог по событиям: ${Object.entries(pairs.events_by_outcome).map(([k, v]) => `${k} ${v}`).join(', ')}. Причины: ${Object.entries(pairs.rows_by_reason).map(([k, v]) => `${k} ${v}`).join(', ')}.` : 'реестр не построен'}</p>
    ${pf.length ? `<table><tr><th>Событие</th><th>Видно, пикс.</th><th>Детекций</th><th>Зон</th></tr>${pf.map((r) => `<tr><td>${esc(r.event_id)}</td><td>${nf(r.valid_px, 0)}</td><td>${nf(r.det_px, 0)}</td><td>${nf(r.n_zones, 0)}</td></tr>`).join('')}</table>` : ''}
    ${metrics?.transfer ? `<p class="small">${esc(metrics.transfer.conclusion)} Детекций в следах: ${metrics.transfer.detections_total} пикс. на ${nf(metrics.transfer.valid_area_km2_total, 0)} км² видимой воды (${metrics.transfer.pairs_with_detections} из ${metrics.transfer.n_pairs} пар).</p>` : ''}
    ${metrics?.drift_check?.summary?.n_pairs ? `<h3>Проверка прогноза дрейфа (доп. функция)</h3>
    <p class="small">${metrics.drift_check.summary.n_pairs} пар соседних снимков: медианное расстояние от новых детекций до частиц прогноза ${nf(metrics.drift_check.summary.median_km_forecast, 1)} км, до исходного положения («пятно на месте») ${nf(metrics.drift_check.summary.median_km_persistence, 1)} км; прогноз лучше в ${metrics.drift_check.summary.pairs_forecast_better} парах. Прогноз справочный, пока не откалиброван.</p>` : ''}
    <h3>Что где проверено</h3>
    <table><tr><th>Вывод</th><th>Полевые</th><th>MARIDA</th><th>Пары</th></tr>${(metrics?.validated_where || []).map((r) => `<tr><td>${esc(r.claim)}</td><td>${esc(r.field)}</td><td>${esc(r.satellite_labels)}</td><td>${esc(r.pairs)}</td></tr>`).join('')}</table>
    <h3>Ограничения</h3>
    <ul>
      <li>При 10 м видны скопления и полосы (от ~20–30% пикселя), а не отдельные предметы: рассеянный мусор из полевых учётов (десятки–сотни шт./км²) со спутника не виден.</li>
      <li>Профиль B описывает весь плавающий мусор (не только пластик) по данным DOORS 2024 у берегов Болгарии, Турции и Грузии; для побережья РФ это исследовательская оценка.</li>
      <li>Пластик, плавник и водоросли спектрально разделяются не полностью; подтверждать нужно судном или дроном.</li>
      <li>Прогноз течений 1/12° не разрешает мелкие бухты и порты; для водохранилищ — только ветровой дрейф.</li>
    </ul></div>`;
  };

  if (!metrics) return withButtonLoading(trigger, 'Загрузка…', render);
  return render();
}

// ---------- события ----------
$('#aoi').onchange = (e) => loadAoi(e.target.value);
$('#profile').onchange = async (e) => {
  S.profile = e.target.value;
  await loadConc();
  paintZones(); paintHexes(); renderKpis(); syncUrl();
  if (S.sel != null && S.tool === 'hex') renderHexPanel();
};
$('#prev').onclick = () => setDate(S.di - 1);
$('#next').onclick = () => setDate(S.di + 1);
$$('#mode button').forEach((b) => (b.onclick = () => setMode(b.dataset.mode)));
$$('#tools button').forEach((b) => (b.onclick = () => openTool(b.dataset.tool)));
$$('#filter-detection input, #filter-conc input').forEach((cb) => (cb.onchange = () => {
  const set = cb.closest('#filter-detection') ? S.filters.det : S.filters.conc;
  if (cb.checked) set.add(cb.dataset.status); else set.delete(cb.dataset.status);
  paintZones(); paintHexes();
}));
$('#panel-close').onclick = closePanel;
$('#hex-drift').onclick = hexDrift;
$('#draw-a').onclick = () => startDrawing('A');
$('#draw-b').onclick = () => startDrawing('B');
$('#draw-clear').onclick = clearCompare;
$('#drift-run').onclick = runDrift;
$('#drift-play').onclick = playDrift;
$('#drift-hour').oninput = (e) => { if (S.anim) playDrift(); showDriftHour(+e.target.value); };
$('#route-run').onclick = runRoute;
$('#map-fit').onclick = fitAoi;
$('#map-fullscreen').onclick = toggleFullscreen;
$('#exp-zones-geojson').onclick = () => exportLayer('zones', 'geojson');
$('#exp-zones-csv').onclick = () => exportLayer('zones', 'csv');
$('#exp-hexes-geojson').onclick = () => exportLayer('hexes', 'geojson');
$('#exp-hexes-csv').onclick = () => exportLayer('hexes', 'csv');
$('#query-save').onclick = saveQuery;
$('#query-rerun').onclick = rerunQuery;
const vis = (ids, on) => ids.forEach((l) => map.getLayer(l) && map.setLayoutProperty(l, 'visibility', on ? 'visible' : 'none'));
$('#l-rgb').onchange = (e) => vis(['rgb'], e.target.checked);
$('#l-quality').onchange = (e) => vis(['quality'], e.target.checked);
$('#l-debris').onchange = (e) => vis(['debris'], e.target.checked);
$('#l-zones').onchange = (e) => vis(['zones-fill', 'zones-line', 'zones-dot'], e.target.checked);
$('#l-field').onchange = (e) => vis(['field-casing', 'field-line', 'field-pt'], e.target.checked);
$('#l-objects').onchange = async (e) => {
  if (e.target.checked && !S.objects) {
    try { S.objects = await api('/api/field/objects'); map.getSource('objects').setData(S.objects); }
    catch (err) { toast(`Не удалось загрузить предметы: ${err.message}`); }
  }
  vis(['objects'], e.target.checked);
};
$('#l-hex').onchange = (e) => vis(['hex-fill', 'hex-research', 'hex-line'], e.target.checked);
$('#l-sat').onchange = (e) => vis(['sat'], e.target.checked);
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && S.drawing) stopDrawing();
  else if (e.key === 'Escape' && !$('#panel').hidden) closePanel();
  if (e.target.tagName === 'INPUT' || e.target.tagName === 'SELECT') return;
  if (e.key === 'ArrowLeft') setDate(S.di - 1);
  if (e.key === 'ArrowRight') setDate(S.di + 1);
  const tool = ['hex', 'compare', 'drift', 'route', 'about'][Number(e.key) - 1];
  if (tool) openTool(tool);
});

map.on('load', async () => {
  try {
    [S.aois, S.profiles] = await Promise.all([api('/api/aois'), api('/api/profiles')]);
  } catch (err) { toast(`API недоступно: ${err.message}`, 0); return; }
  try { S.field = await api('/api/field'); } catch { S.field = EMPTY; }
  const q = new URLSearchParams(location.search);
  if (q.get('profile') && S.profiles.some((p) => p.id === q.get('profile'))) S.profile = q.get('profile');
  if (q.get('q')) S.queryId = q.get('q');
  renderProfiles();
  $('#aoi').innerHTML = S.aois.map((a) => `<option value="${a.id}">${a.name}</option>`).join('');
  if (S.aois.length) {
    const aoi = S.aois.some((a) => a.id === q.get('aoi')) ? q.get('aoi') : S.aois[0].id;
    await loadAoi(aoi, q.get('date'));
    if (q.get('mode') && q.get('mode') !== S.mode && $(`#mode button[data-mode="${q.get('mode')}"]`)) setMode(q.get('mode'));
  }
});
