import { ChevronLeft, ChevronRight, Download, FlaskConical, Layers3, MapPin, RotateCcw, Satellite, Save, ShieldCheck, Shapes, Sparkles } from 'lucide-react';
import { BrandMark } from './BrandMark';
import { Section } from './Section';

const modes = [
  ['conc', 'Концентрация', 'Модельная оценка, шт./км², для выбранного профиля'],
  ['status', 'Статус', 'Обнаружено / не обнаружено / недостаточно данных'],
  ['quality', 'Качество', 'Доля видимой воды в гексе на дату снимка'],
  ['cover', 'Покрытие', 'Вспомогательный показатель детектора: м² мусора на км²'],
  ['mean', 'Среднее', 'Среднее покрытие за период'],
  ['persist', 'Устойчивость', 'Как часто загрязнение повторяется'],
  ['trend', 'Тренд', 'Рост или снижение покрытия'],
  ['accum', 'Скопление', 'Прогноз: зоны схождения течений'],
  ['forecast', 'Прогноз', 'Прогноз: покрытие через 72 часа'],
] as const;

const detectionStatuses = [
  ['detected', 'обнаружено'],
  ['not_detected', 'не обнаружено'],
  ['insufficient_data', 'недостаточно данных'],
] as const;

const concStatuses = [
  ['model_estimate', 'модельная оценка'],
  ['research_estimate', 'исследовательская оценка'],
  ['unavailable', 'недоступна'],
] as const;

export function Sidebar() {
  return (
    <aside id="side" className="bg-paper text-ink" aria-label="Параметры мониторинга">
      <header className="brand">
        <BrandMark />
        <div className="brand-copy">
          <div className="brand-topline"><span>Flux</span><i aria-hidden="true">.</i></div>
          <div className="brand-signature" aria-hidden="true"><span /><span /><span /></div>
        </div>
      </header>

      <Section title="Акватория">
        <div className="select-wrap">
          <select id="aoi" className="focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-tide/30" aria-label="Выберите акваторию" defaultValue="" />
          <ChevronRight size={15} aria-hidden="true" />
        </div>
        <div id="aoi-note" className="muted small scene-info" hidden />
      </Section>

      <Section title="Наблюдение">
        <div className="date-control">
          <button className="icon transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-tide/40" id="prev" title="Предыдущая дата · ←" aria-label="Предыдущая дата"><ChevronLeft size={17} /></button>
          <strong id="date-label">—</strong>
          <button className="icon transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-tide/40" id="next" title="Следующая дата · →" aria-label="Следующая дата"><ChevronRight size={17} /></button>
        </div>
        <div className="chart-box"><canvas id="timeline" /></div>
        <div id="scene-info" className="muted small scene-info" />
      </Section>

      <Section title="Профиль концентрации">
        <div className="select-wrap">
          <select id="profile" className="focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-tide/30" aria-label="Профиль целевой величины" />
          <ChevronRight size={15} aria-hidden="true" />
        </div>
        <div id="profile-note" className="muted small scene-info" />
      </Section>

      <Section title="Слой анализа">
        <div className="seg" id="mode">
          {modes.map(([mode, label, description], index) => (
            <button
              key={mode}
              data-mode={mode}
              className={`${index === 0 ? 'on' : ''} transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-tide`}
              title={description}
            >
              {label}
            </button>
          ))}
        </div>
        <div id="legend" />
        <div id="legend-meta" className="muted small legend-meta" />
      </Section>

      <Section title="Фильтр по статусу">
        <div className="subhead">Детекция</div>
        <div className="status-filter" id="filter-detection">
          {detectionStatuses.map(([key, label]) => (
            <label key={key} className={`st-chip st-${key}`}><input type="checkbox" data-status={key} defaultChecked /><span>{label}</span></label>
          ))}
        </div>
        <div className="subhead">Концентрация</div>
        <div className="status-filter" id="filter-conc">
          {concStatuses.map(([key, label]) => (
            <label key={key} className={`st-chip st-${key}`}><input type="checkbox" data-status={key} defaultChecked /><span>{label}</span></label>
          ))}
        </div>
      </Section>

      <Section title="Сводка">
        <div className="kpis" id="kpis" />
        <div className="subhead">Зоны для обследования</div>
        <ol id="hotlist" className="hotlist" />
      </Section>

      <Section title="Отображение" className="layers">
        <label className="transition-colors hover:text-tide"><input type="checkbox" id="l-rgb" defaultChecked /><span>Снимок Sentinel-2</span><Satellite size={14} /></label>
        <label className="transition-colors hover:text-tide"><input type="checkbox" id="l-quality" /><span>Маска качества</span><ShieldCheck size={14} /></label>
        <label className="transition-colors hover:text-tide"><input type="checkbox" id="l-debris" defaultChecked /><span>Пиксели детекций</span><Sparkles size={14} /></label>
        <label className="transition-colors hover:text-tide"><input type="checkbox" id="l-zones" defaultChecked /><span>Зоны детекции</span><Shapes size={14} /></label>
        <label className="transition-colors hover:text-tide"><input type="checkbox" id="l-field" defaultChecked /><span>Полевые измерения</span><FlaskConical size={14} /></label>
        <label className="transition-colors hover:text-tide"><input type="checkbox" id="l-objects" /><span>Отдельные предметы (контекст)</span><MapPin size={14} /></label>
        <label className="transition-colors hover:text-tide"><input type="checkbox" id="l-hex" defaultChecked /><span>Сетка H3 · ~0,7 км²</span><Layers3 size={14} /></label>
        <label className="transition-colors hover:text-tide"><input type="checkbox" id="l-sat" /><span>Спутниковая подложка</span><Satellite size={14} /></label>
      </Section>

      <Section title="Выгрузка и запрос">
        <div className="export-grid">
          <button className="ghost" id="exp-zones-geojson" title="Зоны детекции с концентрацией и статусами"><Download size={13} />Зоны · GeoJSON</button>
          <button className="ghost" id="exp-zones-csv"><Download size={13} />Зоны · CSV</button>
          <button className="ghost" id="exp-hexes-geojson"><Download size={13} />Гексы · GeoJSON</button>
          <button className="ghost" id="exp-hexes-csv"><Download size={13} />Гексы · CSV</button>
          <button className="ghost" id="query-save" title="Сохранить параметры и отпечаток результата"><Save size={13} />Сохранить запрос</button>
          <button className="ghost" id="query-rerun" title="Повторить сохранённый запрос и сравнить результат"><RotateCcw size={13} />Повторить</button>
        </div>
        <div id="query-info" className="muted small" />
      </Section>

      <footer>
        <span className="status-dot" />
        <span>Sentinel-2 L2A · полевые данные · ERA5</span>
      </footer>
    </aside>
  );
}
