// @ts-nocheck – модуль постепенно типизируется без риска для проверенной геологики карты.
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
const nf = (v, d = 1) => (v == null || Number.isNaN(v)) ? '–' : Number(v).toLocaleString('ru-RU', { maximumFractionDigits: d });
const ruDate = (d) => d.split('-').reverse().join('.');
const shortDate = (d) => { const [y, m, dd] = d.split('-'); return `${dd}.${m}.${y.slice(2)}`; };

const CLS_COL = ['rgba(0,0,0,0)', '#ffe38a', '#ffb24a', '#ff6a3d', '#d9214f'];
const CLS_NAME = ['не обнаружено', 'низкий', 'умеренный', 'высокий', 'очень высокий'];
const COL_A = '#ffb648', COL_B = '#9b8cff';
const accentColor = () => document.documentElement.dataset.theme === 'light' ? '#087f75' : '#d8ff45';

const S = {
  aois: [], aoi: null, hexes: null, series: null, di: 0, mode: 'date',
  drift: null, accum: null, sel: null, cmp: { A: null, B: null }, drawing: null, draft: [],
  route: null, markers: [], stopMarkers: [], charts: {}, tool: null, anim: null,
};

// ---------- утилиты ----------
async function api(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
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
const monthsFrom0 = (d) => (new Date(d) - new Date(S.series.dates[0])) / (30.4 * 864e5);
function cls(conc, ndet) {
  if (!(ndet > 0) || !(conc > 0)) return 0;
  const e = S.series.class_edges; let k = 0;
  e.forEach((edge, i) => { if (conc > edge) k = i + 1; });
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

// ---------- карта ----------
const initialTheme = document.documentElement.dataset.theme === 'light' ? 'light' : 'dark';

const map = new maplibregl.Map({
  container: 'map',
  style: {
    version: 8,
    sources: {
      dark: { type: 'raster', tiles: ['https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}'], tileSize: 256, maxzoom: 16, attribution: 'Esri, HERE, Garmin, © OpenStreetMap' },
      light: { type: 'raster', tiles: ['https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}'], tileSize: 256, maxzoom: 16, attribution: 'Esri, HERE, Garmin, © OpenStreetMap' },
      sat: { type: 'raster', tiles: ['https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}'], tileSize: 256, attribution: 'Esri World Imagery' },
    },
    layers: [
      { id: 'bg', type: 'background', paint: { 'background-color': initialTheme === 'light' ? '#dfe7e2' : '#071412' } },
      { id: 'dark', type: 'raster', source: 'dark', layout: { visibility: initialTheme === 'dark' ? 'visible' : 'none' } },
      { id: 'light', type: 'raster', source: 'light', layout: { visibility: initialTheme === 'light' ? 'visible' : 'none' } },
      { id: 'sat', type: 'raster', source: 'sat', layout: { visibility: 'none' } },
    ],
  },
  center: [39.8, 43.5], zoom: 10, attributionControl: { compact: true },
});
map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'bottom-right');
map.addControl(new maplibregl.ScaleControl({ unit: 'metric' }), 'bottom-left');

function applyMapTheme(theme) {
  if (!map.isStyleLoaded()) return;
  const isLight = theme === 'light';
  map.setPaintProperty('bg', 'background-color', isLight ? '#dfe7e2' : '#071412');
  map.setLayoutProperty('light', 'visibility', isLight ? 'visible' : 'none');
  map.setLayoutProperty('dark', 'visibility', isLight ? 'none' : 'visible');
  const accent = accentColor();
  if (map.getLayer('tracks')) map.setPaintProperty('tracks', 'line-color', accent);
  if (map.getLayer('route')) map.setPaintProperty('route', 'line-color', accent);
  colorTimeline();
  if (S.charts.hex && S.series) {
    S.charts.hex.data.datasets[0].backgroundColor = S.series.dates.map((d, j) => j === S.di ? accent : S.series.scenes[j].storm ? '#4a5568' : '#ff8a4c');
    S.charts.hex.update('none');
  }
}

window.addEventListener('aquaflow-theme-change', (event) => {
  const theme = event.detail?.theme === 'light' ? 'light' : 'dark';
  if (map.isStyleLoaded()) applyMapTheme(theme);
  else map.once('load', () => applyMapTheme(theme));
});

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
  ['hexes', 'sel', 'accumPts', 'draw', 'cmpA', 'cmpB', 'tracks', 'particles', 'cone', 'coneCenter', 'route', 'routeDrift', 'routeObs'].forEach(addGeo);
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
  map.addLayer({ id: 'sel', type: 'line', source: 'sel', paint: { 'line-color': '#ffffff', 'line-width': 2.2 } });
  for (const [id, col] of [['cmpA', COL_A], ['cmpB', COL_B]]) {
    map.addLayer({ id: `${id}-fill`, type: 'fill', source: id, paint: { 'fill-color': col, 'fill-opacity': 0.08 } });
    map.addLayer({ id: `${id}-line`, type: 'line', source: id, paint: { 'line-color': col, 'line-width': 2, 'line-dasharray': [2, 1] } });
  }
  map.addLayer({ id: 'draw-line', type: 'line', source: 'draw', paint: { 'line-color': '#fff', 'line-width': 1.5, 'line-dasharray': [1, 1] } });
  map.addLayer({ id: 'draw-pts', type: 'circle', source: 'draw', filter: ['==', '$type', 'Point'], paint: { 'circle-radius': 4, 'circle-color': '#fff' } });
  map.addLayer({ id: 'tracks', type: 'line', source: 'tracks', paint: { 'line-color': accentColor(), 'line-opacity': 0.16, 'line-width': 1 } });
  map.addLayer({ id: 'particles', type: 'circle', source: 'particles', paint: {
    'circle-radius': 2.6, 'circle-color': ['case', ['==', ['get', 'b'], 1], '#ff6a3d', '#9ff0ff'],
    'circle-stroke-color': '#04121a', 'circle-stroke-width': 0.5 } });
  map.addLayer({ id: 'cone', type: 'line', source: 'cone', paint: { 'line-color': '#9ff0ff', 'line-opacity': 0.35, 'line-width': 1 } });
  map.addLayer({ id: 'coneCenter', type: 'line', source: 'coneCenter', paint: { 'line-color': '#fff', 'line-width': 2.5 } });
  map.addLayer({ id: 'coneEnd', type: 'circle', source: 'coneCenter', filter: ['==', '$type', 'Point'], paint: { 'circle-radius': 5, 'circle-color': '#fff' } });
  map.addLayer({ id: 'routeDrift', type: 'line', source: 'routeDrift', paint: { 'line-color': '#ffb24a', 'line-width': 1.8 } });
  map.addLayer({ id: 'routeObs', type: 'circle', source: 'routeObs', paint: { 'circle-radius': 3.5, 'circle-color': '#ffb24a' } });
  map.addLayer({ id: 'route', type: 'line', source: 'route', paint: { 'line-color': accentColor(), 'line-width': 3, 'line-dasharray': [2, 1.2] } });

  map.on('mousemove', 'hex-fill', onHexHover);
  map.on('mouseleave', 'hex-fill', () => { $('#tip').hidden = true; map.getCanvas().style.cursor = ''; });
  map.on('click', 'hex-fill', (e) => { if (!S.drawing) selectHex(e.features[0].properties.i); });
  map.on('click', onDrawClick);
  map.on('dblclick', onDrawFinish);
}

// ---------- загрузка акватории ----------
async function loadAoi(id) {
  S.aoi = S.aois.find((a) => a.id === id);
  $('#workspace-aoi').textContent = S.aoi.name;
  $('#aoi-note').textContent = S.aoi.note || '';
  $('#aoi-note').hidden = !S.aoi.note;
  toast('Загрузка акватории…', 0);
  const [hexes, series] = await Promise.all([api(`/api/aois/${id}/hexes`), api(`/api/aois/${id}/series`)]);
  S.hexes = hexes; S.series = series; S.sel = null; S.accum = null;
  clearDrift(); clearRoute(); clearCompare();
  const di = series.dates.length - 1;
  const urls = sceneUrls(series.dates[di]);
  if (!map.getSource('rgb')) initLayers(urls, series.scenes[di].corners);
  const [x0, y0, x1, y1] = S.aoi.bbox;
  map.fitBounds([[x0, y0], [x1, y1]], { padding: 30, duration: 0 });
  placeMarkers();
  buildTimeline();
  // По умолчанию – самая «грязная» дата из спокойных
  let best = di, bestA = -1;
  series.scenes.forEach((s, i) => { if (!s.storm && s.area_m2 > bestA) { bestA = s.area_m2; best = i; } });
  setDate(best);
  $('#toast').hidden = true;
}
const sceneUrls = (d) => ({ rgb: `/data/${S.aoi.id}/${d}/rgb.jpg`, debris: `/data/${S.aoi.id}/${d}/debris.png` });

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

// ---------- дата ----------
function buildTimeline() {
  S.charts.timeline?.destroy();
  const s = S.series;
  S.charts.timeline = new Chart($('#timeline'), {
    type: 'bar',
    data: { labels: s.dates.map(shortDate), datasets: [{ data: s.scenes.map((x) => x.area_m2), borderRadius: 2, backgroundColor: [] }] },
    options: {
      responsive: true, maintainAspectRatio: false, animation: { duration: 650, easing: 'easeOutQuart' },
      plugins: { legend: { display: false }, tooltip: { callbacks: {
        title: (c) => ruDate(s.dates[c[0].dataIndex]),
        label: (c) => { const sc = s.scenes[c.dataIndex]; return [`мусор ≈ ${nf(c.raw, 0)} м²`, `море: ${sc.sea}${sc.wind != null ? `, ветер ${nf(sc.wind)} м/с` : ''}`]; } } } },
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
  c.data.datasets[0].backgroundColor = S.series.scenes.map((sc, i) => i === S.di ? accentColor() : sc.storm ? '#4a5568' : '#2c5d7a');
  c.update('none');
}

function setDate(i) {
  const s = S.series;
  S.di = Math.max(0, Math.min(s.dates.length - 1, i));
  const d = s.dates[S.di], sc = s.scenes[S.di];
  $('#date-label').textContent = ruDate(d);
  $('#workspace-date').textContent = `${ruDate(d)} · Sentinel-2`;
  const glint = sc.glint > 0.03 ? 'сильный' : sc.glint > 0.01 ? 'умеренный' : 'слабый';
  $('#scene-info').innerHTML = `Пролёт ${localTime(sc.datetime || d, S.aoi.tz)} (UTC+${S.aoi.tz}) · блик: ${glint} · море: <b>${sc.sea}</b>` +
    (sc.wind != null ? `, ветер ${nf(sc.wind)} м/с` : '') + (sc.storm ? `<br><span style="color:#ffb24a">Ненадёжная сцена (${sc.reason}): оставлены только крупные скопления, дата не входит в статистику</span>` : '');
  const u = sceneUrls(d);
  map.getSource('rgb').updateImage({ url: u.rgb, coordinates: sc.corners });
  map.getSource('debris').updateImage({ url: u.debris, coordinates: sc.corners });
  clearDrift(); clearRoute(); S.accum = null;
  if (S.mode === 'forecast' || S.mode === 'accum') setMode('date');
  paintHexes();
  renderKpis();
  colorTimeline();
  if (S.sel != null) renderHexPanel();
  if (S.cmp.A || S.cmp.B) renderCompare();
}

// ---------- гексы ----------
const PERSIST = [[0, '#34416b'], [0.2, '#6b5cff'], [0.45, '#c04fe0'], [1, '#ff5c8a']];
const ACC = [[0, '#12475a'], [0.5, '#1f9fc4'], [1, '#b7f4ff']];

function paintHexes() {
  if (!S.hexes) return;
  const s = S.series, di = S.di, m = S.mode;
  const trends = S.hexes.features.map((f) => Math.abs(f.properties.trend)).filter((v) => v > 0).sort((a, b) => a - b);
  const tmax = trends.length ? trends[Math.floor(trends.length * 0.95)] || trends[trends.length - 1] : 1;
  const f72 = S.drift?.hexes?.[String(S.drift.hours)] || {};
  for (const f of S.hexes.features) {
    const p = f.properties, i = p.i;
    let col = 'rgba(0,0,0,0)', op = 0;
    if (m === 'date') {
      if (s.valid[di][i] < 0.5) { col = '#6b7688'; op = 0.35; }
      else { const k = cls(s.conc[di][i], s.ndet[di][i]); col = CLS_COL[k]; op = k ? 0.8 : 0; }
    } else if (m === 'mean') {
      const k = cls(p.mean_conc, p.mean_conc > 0 ? 1 : 0); col = CLS_COL[k]; op = k ? 0.8 : 0;
    } else if (m === 'persist') {
      if (p.persistence > 0) { col = lerpColor(PERSIST, p.persistence / 0.5); op = 0.35 + 0.5 * Math.min(1, p.persistence / 0.3); }
    } else if (m === 'trend') {
      const t = p.trend / tmax;
      if (Math.abs(t) > 0.05) { col = t > 0 ? lerpColor([[0, '#5a2a2a'], [1, '#ff4d3d']], t) : lerpColor([[0, '#1d3f55'], [1, '#3cc6e8']], -t); op = 0.3 + 0.55 * Math.min(1, Math.abs(t)); }
    } else if (m === 'accum') {
      const v = S.accum?.factor?.[p.h3] ?? 0;
      if (v > 1.15) { col = lerpColor(ACC, (v - 1.15) / 3); op = 0.3 + 0.5 * Math.min(1, (v - 1.15) / 2); }
    } else if (m === 'forecast') {
      const area = f72[p.h3] || 0;
      const conc = area / Math.max(p.water_km2, 0.05);
      const k = cls(conc, area > 0 ? 1 : 0); col = CLS_COL[k]; op = k ? 0.8 : 0;
    }
    p.col = col; p.op = op;
  }
  map.getSource('hexes').setData(S.hexes);
  renderLegend();
}

function renderLegend() {
  const e = S.series?.class_edges || [0, 15, 40, 100];
  const classes = () => [1, 2, 3, 4].map((k) => `<div class="leg-row"><span class="sw" style="background:${CLS_COL[k]}"></span>${CLS_NAME[k]} <span class="muted">${k < 4 ? `${nf(e[k - 1], 0)}–${nf(e[k], 0)}` : `> ${nf(e[3], 0)}`} м²/км²</span></div>`).join('');
  const L = {
    date: `${classes()}<div class="leg-row"><span class="sw" style="background:#6b7688"></span>нет данных (облака)</div>
      <p class="muted small">Концентрация – эквивалентная площадь плавающего мусора на км² акватории (доля покрытия пикселя × 100 м²).</p>`,
    mean: `${classes()}<p class="muted small">Средняя концентрация по всем безоблачным и нештормовым снимкам.</p>`,
    persist: `<div class="grad" style="background:linear-gradient(90deg,${PERSIST.map((s) => s[1]).join(',')})"></div><div class="grad-lbl small"><span>редко</span><span>≥50% снимков</span></div>
      <p class="muted small">Доля снимков, на которых в гексе был мусор. Устойчивые зоны – места хронического скопления.</p>`,
    trend: `<div class="grad" style="background:linear-gradient(90deg,#3cc6e8,#1d3f55,#5a2a2a,#ff4d3d)"></div><div class="grad-lbl small"><span>снижение</span><span>рост</span></div>
      <p class="muted small">Линейный тренд концентрации за период наблюдений.</p>`,
    accum: `<div class="grad" style="background:linear-gradient(90deg,${ACC.map((s) => s[1]).join(',')})"></div><div class="grad-lbl small"><span>×1,2</span><span>×4 и выше</span></div>
      <p class="muted small">Зоны схождения течений: акваторию равномерно засеяли частицами и за 72 ч посчитали, во сколько раз выросла их плотность. Сюда мусор будет стягиваться.</p>`,
    forecast: `${classes()}<p class="muted small">Прогнозная концентрация через ${S.drift?.hours ?? 72} ч после пролёта (дрейф детекций течением и ветром).</p>`,
  };
  $('#legend').innerHTML = L[S.mode];
}

function onHexHover(e) {
  map.getCanvas().style.cursor = S.drawing ? 'crosshair' : 'pointer';
  const p = e.features[0].properties, i = p.i, s = S.series, di = S.di;
  let txt;
  if (S.mode === 'date') {
    txt = s.valid[di][i] < 0.5 ? 'Нет данных: облака' : `<b>${nf(s.conc[di][i])} м²/км²</b> · ${CLS_NAME[cls(s.conc[di][i], s.ndet[di][i])]}<br>пикселей с мусором: ${s.ndet[di][i]}`;
  } else if (S.mode === 'mean') txt = `Средняя: <b>${nf(p.mean_conc)} м²/км²</b>`;
  else if (S.mode === 'persist') txt = `Мусор на <b>${nf(p.persistence * 100, 0)}%</b> снимков (${p.n_obs} наблюдений)`;
  else if (S.mode === 'trend') txt = `Тренд: <b>${p.trend > 0 ? '+' : ''}${nf(p.trend, 2)}</b> м²/км² в месяц`;
  else if (S.mode === 'accum') txt = `Фактор скопления: <b>×${nf(S.accum?.factor?.[p.h3] ?? 0, 2)}</b>`;
  else {
    const a = S.drift?.hexes?.[String(S.drift.hours)]?.[p.h3] || 0;
    txt = `Через ${S.drift?.hours ?? 72} ч: <b>${nf(a / Math.max(p.water_km2, 0.05))} м²/км²</b>`;
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
  const activeMode = $(`#mode button[data-mode="${m}"]`);
  if ($('#mode-current') && activeMode) $('#mode-current').textContent = activeMode.dataset.label;
  paintHexes();
}

// ---------- сводка ----------
function renderKpis() {
  const s = S.series, di = S.di, sc = s.scenes[di];
  let polluted = 0, high = 0, validKm2 = 0;
  const rows = [];
  S.hexes.features.forEach((f) => {
    const i = f.properties.i;
    if (s.valid[di][i] >= 0.5) validKm2 += f.properties.water_km2 * s.valid[di][i];
    const k = s.valid[di][i] >= 0.5 ? cls(s.conc[di][i], s.ndet[di][i]) : 0;
    if (k > 0) { polluted++; rows.push([i, s.conc[di][i], k]); }
    if (k >= 3) high++;
  });
  $('#kpis').innerHTML = [
    [nf(sc.area_m2, 0), 'м² мусора (эквив. площадь)'],
    [nf(sc.conc, 2), 'м²/км² в среднем'],
    [`${polluted}`, `гексов с мусором из ${S.hexes.features.length}`],
    [`${high}`, 'гексов высокого уровня'],
  ].map(([b, t]) => `<div class="kpi"><b>${b}</b><span>${t}</span></div>`).join('');
  rows.sort((a, b) => b[1] - a[1]);
  $('#hotlist').innerHTML = rows.slice(0, 5).map(([i, c, k]) =>
    `<li data-i="${i}"><span style="color:${CLS_COL[k]}">■</span> ${nf(c)} м²/км² <span class="v">· ${CLS_NAME[k]}</span></li>`).join('') || '<li class="muted">на эту дату скоплений нет</li>';
  $$('#hotlist li[data-i]').forEach((li) => li.onclick = () => {
    const p = S.hexes.features[+li.dataset.i].properties;
    map.flyTo({ center: [p.lon, p.lat], zoom: 13 });
    selectHex(+li.dataset.i);
  });
}

// ---------- панель инструментов ----------
const TOOL_TITLE = { hex: 'Участок акватории', compare: 'Сравнение участков', drift: 'Прогноз распространения', route: 'План обследования', about: 'О методе' };
function openTool(t) {
  if (S.tool === t && !$('#panel').hidden) { closePanel(); return; }
  S.tool = t;
  $('#panel').hidden = false;
  $('#panel-title').textContent = TOOL_TITLE[t];
  $$('.tool').forEach((el) => el.classList.toggle('on', el.dataset.tool === t));
  $$('#tools button').forEach((b) => b.classList.toggle('on', b.dataset.tool === t));
  if (t === 'about') renderAbout();
}
function closePanel() {
  $('#panel').hidden = true; S.tool = null;
  $$('#tools button').forEach((b) => b.classList.remove('on'));
  stopDrawing();
}

// ---------- участок ----------
function selectHex(i) {
  S.sel = i;
  const f = S.hexes.features[i];
  map.getSource('sel').setData({ type: 'FeatureCollection', features: [f] });
  openTool('hex');
  if (S.tool !== 'hex') openTool('hex');
  renderHexPanel();
}
function renderHexPanel() {
  const i = S.sel, p = S.hexes.features[i].properties, s = S.series;
  $('#hex-empty').hidden = true; $('#hex-body').hidden = false;
  const c = s.conc[S.di][i], k = s.valid[S.di][i] < 0.5 ? null : cls(c, s.ndet[S.di][i]);
  $('#hex-kpis').innerHTML = [
    [k == null ? '–' : nf(c), `м²/км² на ${ruDate(s.dates[S.di])}`],
    [k == null ? 'облака' : CLS_NAME[k], 'уровень'],
    [nf(p.mean_conc), 'средняя, м²/км²'],
    [`${nf(p.persistence * 100, 0)}%`, `снимков с мусором (${p.n_obs})`],
    [`${p.trend > 0 ? '+' : ''}${nf(p.trend, 2)}`, 'тренд, м²/км² в мес'],
    [nf(p.water_km2, 2), 'км² воды в гексе'],
  ].map(([b, t]) => `<div class="kpi"><b>${b}</b><span>${t}</span></div>`).join('');
  const data = s.dates.map((d, j) => (s.valid[j][i] >= 0.5 ? s.conc[j][i] : null));
  S.charts.hex?.destroy();
  S.charts.hex = new Chart($('#hex-chart'), {
    type: 'bar',
    data: { labels: s.dates.map(shortDate), datasets: [{ data, backgroundColor: s.dates.map((d, j) => j === S.di ? accentColor() : s.scenes[j].storm ? '#4a5568' : '#ff8a4c'), borderRadius: 2 }] },
    options: { responsive: true, maintainAspectRatio: false, animation: { duration: 650, easing: 'easeOutQuart' },
      plugins: { legend: { display: false }, title: { display: true, text: 'Концентрация по датам, м²/км²', color: '#94a3b8', font: { weight: 'normal' } } },
      scales: { x: { ticks: { maxRotation: 0, autoSkip: true, maxTicksLimit: 5 }, grid: { display: false } }, y: { beginAtZero: true, grid: { color: 'rgba(148,163,184,.12)' }, ticks: { maxTicksLimit: 4 } } },
      onClick: (e, els) => { if (els.length) setDate(els[0].index); } },
  });
  $('#hex-drift-info').textContent = '';
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
      $('#hex-drift-info').innerHTML = `Через 72 ч центр ансамбля сместится на <b>${nf(km)} км</b>, разброс ±${nf(r.spread_km[72])} км` +
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
  toast(`Участок ${which}: кликайте вершины, двойной клик – завершить`, 3500);
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
  // dblclick добавил две одинаковые вершины – убираем дубли
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
    for (const p of inside) { const w = p.water_km2 * s.valid[j][p.i]; vk += w; area += s.conc[j][p.i] * w; }
    return vk > 0.5 * km2 && km2 > 0 ? area / vk : null;
  });
  const okIdx = ser.map((v, j) => (v != null && !s.scenes[j].storm ? j : -1)).filter((j) => j >= 0);
  const vals = okIdx.map((j) => ser[j]);
  const mean = vals.length ? vals.reduce((a, b) => a + b, 0) / vals.length : null;
  return {
    n: inside.length, km2, ser, mean, cur: ser[S.di], max: vals.length ? Math.max(...vals) : null,
    share: vals.length ? vals.filter((v) => v > 0).length / vals.length : null,
    trend: slope(okIdx.map((j) => monthsFrom0(s.dates[j])), vals),
    hot: inside.filter((p) => p.persistence >= 0.2).length,
  };
}
function renderCompare() {
  const A = S.cmp.A ? areaStats(S.cmp.A) : null, B = S.cmp.B ? areaStats(S.cmp.B) : null;
  const col = (st, f) => (st ? f(st) : '–');
  const rows = [
    ['Акватория, км²', (s) => nf(s.km2)],
    ['Гексов', (s) => s.n],
    [`На ${ruDate(S.series.dates[S.di])}, м²/км²`, (s) => nf(s.cur, 2)],
    ['Средняя, м²/км²', (s) => nf(s.mean, 2)],
    ['Максимум, м²/км²', (s) => nf(s.max, 1)],
    ['Доля снимков с мусором', (s) => (s.share == null ? '–' : `${nf(s.share * 100, 0)}%`)],
    ['Устойчивых гексов (≥20%)', (s) => s.hot],
    ['Тренд, м²/км² в мес', (s) => `${s.trend > 0 ? '+' : ''}${nf(s.trend, 2)}`],
  ];
  let verdict = '';
  if (A && B && A.mean != null && B.mean != null) {
    const [hi, lo, nh, nl] = A.mean >= B.mean ? [A, B, 'A', 'B'] : [B, A, 'B', 'A'];
    const ratio = lo.mean > 0 ? hi.mean / lo.mean : null;
    verdict = `<div class="verdict">Участок <b>${nh}</b> загрязнён сильнее: средняя концентрация ${ratio ? `в <b>${nf(ratio, 1)} раза</b> выше` : 'выше (на участке ' + nl + ' мусор не найден)'}.` +
      ` Приоритет обследования – <b>${nh}</b>${hi.trend > 0 ? ', загрязнение растёт' : ''}.</div>`;
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
      plugins: { title: { display: true, text: 'Концентрация по датам, м²/км²', color: '#94a3b8', font: { weight: 'normal' } }, legend: { labels: { boxWidth: 10 } } },
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
      if (!r.n) { toast('На эту дату детекций нет – нечего переносить'); return; }
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
        <div class="kpi"><b>${nf(r.covered_m2, 0)} м²</b><span>мусора на маршруте</span></div>
        <div class="kpi"><b>${nf(r.total_m2 ? (100 * r.covered_m2) / r.total_m2 : 0, 0)}%</b><span>от всех скоплений</span></div>
      </div>
      <ul class="stops">${r.stops.map((s) => `<li>
        <span class="n">${s.order}</span>
        <span>${nf(s.area_m2, 0)} м² · ${s.n_pixels} пикс.<br><span class="meta">дрейф к прибытию ${nf(s.drift_km, 2)} км · переход ${nf(s.leg_km)} км</span></span>
        <span class="eta">${localTime(s.eta, tz)}<br><span class="meta">+${nf(s.eta_h)} ч</span></span></li>`).join('')}</ul>
      <p class="muted small">Время местное (UTC+${tz}). Оранжевые отрезки – смещение пятна от снимка до прибытия судна.</p>
      <div class="row gap8"><button class="ghost" id="exp-geojson">GeoJSON</button><button class="ghost" id="exp-gpx">GPX для навигатора</button></div>`;
    $('#exp-geojson').onclick = exportGeoJSON;
    $('#exp-gpx').onclick = exportGPX;
    const b = r.line.reduce((bb, c) => [[Math.min(bb[0][0], c[0]), Math.min(bb[0][1], c[1])], [Math.max(bb[1][0], c[0]), Math.max(bb[1][1], c[1])]], [[180, 90], [-180, -90]]);
      map.fitBounds(b, { padding: { top: 80, bottom: 40, left: 40, right: 420 }, maxZoom: 13 });
    } catch (err) { toast(`Ошибка маршрута: ${err.message}`); }
  });
}
function exportGeoJSON() {
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
  const gpx = `<?xml version="1.0" encoding="UTF-8"?>\n<gpx version="1.1" creator="AquaFlow" xmlns="http://www.topografix.com/GPX/1/1">\n${wpt}\n  <rte><name>Survey ${S.series.dates[S.di]}</name>\n${rte}\n  </rte>\n</gpx>\n`;
  download(`route_${S.aoi.id}_${S.series.dates[S.di]}.gpx`, gpx, 'application/gpx+xml');
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
async function renderAbout() {
  const trigger = $('#tools button[data-tool="about"]');
  const render = async () => {
  if (!metrics) { try { metrics = await api('/api/metrics'); } catch { metrics = null; } }
  const rep = metrics?.report || {};
  const names = { debris: 'Мусор', organic: 'Водоросли/органика', ship: 'Суда', cloud: 'Облака', water: 'Вода', foam: 'Пена/волны' };
  const rows = Object.keys(names).filter((k) => rep[k]).map((k) =>
    `<tr><td>${names[k]}</td><td>${nf(rep[k].precision, 2)}</td><td>${nf(rep[k].recall, 2)}</td><td>${nf(rep[k]['f1-score'], 2)}</td></tr>`).join('');
  $('#about').innerHTML = `<div class="about">
    <p>Концентрацию оцениваем по доле мусора в каждом пикселе, а не просто маской «мусор / не мусор». Дальше физика течений: куда мусор уйдёт и где будет скапливаться.</p>
    <h3>Как считается</h3>
    <ol>
      <li><b>Sentinel-2 L2A</b>, 11 каналов, 10 м. Маски облаков по SCL, постоянная маска воды по всему ряду снимков.</li>
      <li><b>Нормализация фона</b>: из пикселя вычитаем локальный спектр воды (медиана по блокам 320 м). Уходят солнечный блик, дымка и разница атмосферной коррекции.</li>
      <li><b>Классификатор LightGBM</b> на размеченном архиве MARIDA (спектр, индексы FDI, FAI, NDVI, PI и текстура). Классы: мусор, водоросли, суда, облака, вода, пена.</li>
      <li><b>Фильтры ложных срабатываний</b>: CFAR (аномалия ≥ 5σ локального шума), спектральный тест против пены, маска постоянных объектов (причалы, буи, садки), учёт ветра ERA5 (при ≥ 8 м/с остаются только крупные скопления).</li>
      <li><b>Концентрация</b>: линейное смешение «вода + плотное скопление» даёт долю покрытия пикселя. Сумма по гексу H3 даёт м² мусора на км².</li>
      <li><b>Динамика</b>: устойчивость (доля снимков с мусором) и тренд по каждому гексу.</li>
      <li><b>Прогноз</b>: лагранжев ансамбль частиц. Течения SMOC (включая прилив и стоксов дрейф) + 1–3% ветра ERA5, выброс на берег. Для водохранилищ – только ветровой дрейф.</li>
      <li><b>Обследование</b>: маршрут из порта по целям с максимальной отдачей «площадь / время». Позиции целей пересчитаны на момент прибытия.</li>
    </ol>
    <h3>Качество классификатора (MARIDA, тестовая выборка, ${nf(metrics?.n_test ?? 0, 0)} пикс.)</h3>
    <table><tr><th>Класс</th><th>Точность</th><th>Полнота</th><th>F1</th></tr>${rows}</table>
    <p class="small muted">AP для класса «мусор»: ${nf(metrics?.debris_ap, 3)}.</p>
    <h3>Ограничения</h3>
    <ul>
      <li>Со спутника видны скопления и полосы мусора (от ~20–30% пикселя 10 м), а не отдельные бутылки. Концентрация – эквивалентная площадь покрытия, а не число предметов.</li>
      <li>Мусор и органику (плавник, водоросли) спектрально разделить полностью нельзя. Для подтверждения нужны судно или дрон, и сервис строит для них маршрут.</li>
      <li>Прогноз течений на 0,08° не разрешает мелкие бухты и порты.</li>
    </ul></div>`;
  };

  if (!metrics) return withButtonLoading(trigger, 'Загрузка…', render);
  return render();
}

// ---------- события ----------
$('#aoi').onchange = (e) => loadAoi(e.target.value);
$('#prev').onclick = () => setDate(S.di - 1);
$('#next').onclick = () => setDate(S.di + 1);
$$('#mode button').forEach((b) => (b.onclick = () => setMode(b.dataset.mode)));
$$('#tools button').forEach((b) => (b.onclick = () => openTool(b.dataset.tool)));
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
$('#l-rgb').onchange = (e) => map.setLayoutProperty('rgb', 'visibility', e.target.checked ? 'visible' : 'none');
$('#l-debris').onchange = (e) => map.setLayoutProperty('debris', 'visibility', e.target.checked ? 'visible' : 'none');
$('#l-hex').onchange = (e) => ['hex-fill', 'hex-line'].forEach((l) => map.setLayoutProperty(l, 'visibility', e.target.checked ? 'visible' : 'none'));
$('#l-sat').onchange = (e) => { map.setLayoutProperty('sat', 'visibility', e.target.checked ? 'visible' : 'none'); };
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
    S.aois = await api('/api/aois');
  } catch (err) { toast(`API недоступно: ${err.message}`, 0); return; }
  $('#aoi').innerHTML = S.aois.map((a) => `<option value="${a.id}">${a.name}</option>`).join('');
  if (S.aois.length) await loadAoi(S.aois[0].id);
});
