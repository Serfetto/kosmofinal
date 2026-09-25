import { useEffect, useRef, useState } from 'react';
import {
  Activity,
  CircleGauge,
  Check,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  CloudSun,
  Download,
  FileText,
  FlaskConical,
  Focus,
  Layers3,
  MapPin,
  Moon,
  Repeat2,
  RotateCcw,
  Satellite,
  Save,
  ScanSearch,
  ShieldCheck,
  Shapes,
  Sigma,
  Sparkles,
  Sun,
  TrendingUp,
  X,
  type LucideIcon,
} from 'lucide-react';
import { BrandMark } from './BrandMark';

export type SheetView = 'overview' | 'layers' | null;
export type ThemeName = 'light' | 'dark';

type AoiOption = { value: string; label: string; group?: string };

const AOI_GROUPS: Record<string, string> = {
  field: 'Полевые данные кейса · снимки на даты измерений',
  monitoring: 'Оперативный мониторинг',
};

function AoiPicker() {
  const [options, setOptions] = useState<AoiOption[]>([]);
  const [selected, setSelected] = useState('');
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const nativeSelect = useRef<HTMLSelectElement>(null);

  useEffect(() => {
    const select = nativeSelect.current;
    if (!select) return;

    const sync = () => {
      const nextOptions = Array.from(select.options).map((option) => ({
        value: option.value,
        label: option.textContent || option.value,
        group: option.dataset.group,
      }));
      setOptions(nextOptions);
      setSelected(select.value || nextOptions[0]?.value || '');
    };
    const closeOutside = (event: PointerEvent) => {
      if (!root.current?.contains(event.target as Node)) setOpen(false);
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false);
    };
    const observer = new MutationObserver(sync);

    observer.observe(select, { childList: true, subtree: true, attributes: true });
    select.addEventListener('change', sync);
    window.addEventListener('aquaflow-aoi-sync', sync);
    document.addEventListener('pointerdown', closeOutside);
    document.addEventListener('keydown', closeOnEscape);
    sync();

    return () => {
      observer.disconnect();
      select.removeEventListener('change', sync);
      window.removeEventListener('aquaflow-aoi-sync', sync);
      document.removeEventListener('pointerdown', closeOutside);
      document.removeEventListener('keydown', closeOnEscape);
    };
  }, []);

  const choose = (value: string) => {
    const select = nativeSelect.current;
    if (!select) return;
    select.value = value;
    setSelected(value);
    setOpen(false);
    select.dispatchEvent(new Event('change', { bubbles: true }));
  };

  const currentLabel = options.find((option) => option.value === selected)?.label || 'Загрузка акваторий…';
  // Районы полевых данных — первыми: там снимки на даты измерений кейса
  const groups = ['field', 'monitoring']
    .map((key) => ({ key, items: options.filter((option) => (option.group || 'monitoring') === key) }))
    .filter((group) => group.items.length);

  return (
    <div className={`location-control aoi-picker ${open ? 'is-open' : ''}`} ref={root}>
      <MapPin size={17} aria-hidden="true" />
      <div className="aoi-control-body">
        <small>Акватория</small>
        <select
          className="native-aoi-select"
          id="aoi"
          ref={nativeSelect}
          aria-hidden="true"
          tabIndex={-1}
          defaultValue=""
        />
        <button
          className="aoi-select-button"
          type="button"
          onClick={() => setOpen((value) => !value)}
          aria-haspopup="listbox"
          aria-expanded={open}
          aria-controls="aoi-options"
        >
          <span>{currentLabel}</span>
          <ChevronDown size={15} aria-hidden="true" />
        </button>
      </div>

      <div className="aoi-menu" id="aoi-options" role="listbox" aria-label="Доступные акватории">
        <div className="aoi-menu-head">
          <span>Выберите акваторию</span>
          <span>{options.length} доступно</span>
        </div>
        <div className="aoi-option-list">
          {groups.map((group) => [
            <div className="aoi-group-head" key={`g-${group.key}`} role="presentation">{AOI_GROUPS[group.key] || group.key}</div>,
            ...group.items.map((option) => {
              const isSelected = option.value === selected;
              return (
                <button
                  type="button"
                  role="option"
                  aria-selected={isSelected}
                  className={isSelected ? 'is-selected' : ''}
                  key={option.value}
                  onClick={() => choose(option.value)}
                >
                  <span className="aoi-option-mark">{group.key === 'field' ? <FlaskConical size={15} /> : <MapPin size={15} />}</span>
                  <span>{option.label}</span>
                  <Check className="aoi-option-check" size={16} aria-hidden="true" />
                </button>
              );
            }),
          ])}
          {!options.length && <div className="aoi-options-empty">Загружаем список акваторий…</div>}
        </div>
      </div>
    </div>
  );
}

type CommandBarProps = {
  activeSheet: SheetView;
  onOpenSheet: (view: Exclude<SheetView, null>) => void;
  theme: ThemeName;
  onThemeChange: (theme: ThemeName) => void;
};

export function CommandBar({ activeSheet, onOpenSheet, theme, onThemeChange }: CommandBarProps) {
  return (
    <header className="command-bar">
      <div className="command-brand" aria-label="AquaFlow – мониторинг водной среды">
        <BrandMark />
        <div>
          <strong>AquaFlow</strong>
        </div>
      </div>

      <div className="command-separator" />

      <AoiPicker />

      <div className="command-context" aria-live="polite">
        <strong id="workspace-aoi">Оперативная карта</strong>
        <span id="workspace-date">Загрузка спутниковых данных…</span>
      </div>

      <div className="command-date" aria-label="Дата спутникового снимка">
        <button id="prev" data-tooltip="Предыдущий снимок · ←" aria-label="Предыдущая дата"><ChevronLeft size={18} /></button>
        <div>
          <small>Дата снимка</small>
          <strong id="date-label">–</strong>
        </div>
        <button id="next" data-tooltip="Следующий снимок · →" aria-label="Следующая дата"><ChevronRight size={18} /></button>
      </div>

      <div className="command-actions">
        <button
          className={activeSheet === 'overview' ? 'is-active' : ''}
          onClick={() => onOpenSheet('overview')}
          data-tooltip="Динамика, показатели и приоритетные зоны"
          aria-pressed={activeSheet === 'overview'}
        >
          <Activity size={17} />
          <span>Сводка</span>
        </button>
        <button
          className={activeSheet === 'layers' ? 'is-active' : ''}
          onClick={() => onOpenSheet('layers')}
          data-tooltip="Настроить содержимое карты"
          aria-pressed={activeSheet === 'layers'}
        >
          <Layers3 size={17} />
          <span>Слои</span>
        </button>
      </div>

      <div className="theme-switcher" role="group" aria-label="Цветовая тема">
        <button
          className={theme === 'light' ? 'is-active' : ''}
          onClick={() => onThemeChange('light')}
          data-tooltip="Светлая тема"
          aria-label="Включить светлую тему"
          aria-pressed={theme === 'light'}
        >
          <Sun size={16} />
        </button>
        <button
          className={theme === 'dark' ? 'is-active' : ''}
          onClick={() => onThemeChange('dark')}
          data-tooltip="Тёмная тема"
          aria-label="Включить тёмную тему"
          aria-pressed={theme === 'dark'}
        >
          <Moon size={16} />
        </button>
      </div>
    </header>
  );
}

type ModeItem = readonly [id: string, label: string, description: string, icon: LucideIcon];

const modes: readonly ModeItem[] = [
  ['conc', 'Концентрация', 'Модельная оценка количества предметов на км²', CircleGauge],
  ['status', 'Статус', 'Обнаружено, не обнаружено или недостаточно данных', ScanSearch],
  ['quality', 'Качество', 'Доля видимой воды в ячейке на дату снимка', ShieldCheck],
  ['cover', 'Покрытие', 'Оценка площади мусора на км²', Sparkles],
  ['mean', 'Среднее', 'Среднее покрытие за весь период', Sigma],
  ['persist', 'Устойчивость', 'Как часто загрязнение возвращается в зону', Repeat2],
  ['trend', 'Тренд', 'Рост или снижение покрытия со временем', TrendingUp],
  ['accum', 'Скопление', 'Зоны, куда течения стягивают мусор', Focus],
  ['forecast', 'Прогноз', 'Ожидаемое покрытие через 72 часа', CloudSun],
];

const detectionStatuses = [
  ['detected', 'обнаружено'],
  ['not_detected', 'не обнаружено'],
  ['insufficient_data', 'недостаточно данных'],
] as const;

const concentrationStatuses = [
  ['model_estimate', 'модельная оценка'],
  ['research_estimate', 'исследовательская оценка'],
  ['unavailable', 'недоступна'],
] as const;

export function ModePicker() {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const close = (event: PointerEvent) => {
      if (!root.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener('pointerdown', close);
    return () => document.removeEventListener('pointerdown', close);
  }, []);

  return (
    <div className={`mode-picker ${open ? 'is-open' : ''}`} ref={root}>
      <button
        className="mode-trigger"
        id="mode-trigger"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        data-tooltip="Изменить показатель, которым окрашена карта"
      >
        <span className="mode-orbit"><Layers3 size={18} /></span>
        <span><small>Показатель карты</small><strong id="mode-current">Концентрация</strong></span>
        <ChevronDown className="mode-chevron" size={16} />
      </button>
      <div className="mode-menu" id="mode" aria-label="Показатель карты">
        <div className="mode-menu-head"><span>Что показать на карте</span><kbd>{modes.length} режимов</kbd></div>
        {modes.map(([mode, label, description, Icon], index) => (
          <button
            key={mode}
            data-mode={mode}
            data-label={label}
            className={index === 0 ? 'on' : ''}
            onClick={() => setOpen(false)}
          >
            <span className="mode-icon"><Icon size={17} /></span>
            <span><strong>{label}</strong><small>{description}</small></span>
          </button>
        ))}
      </div>
    </div>
  );
}

export function MapLegend() {
  const [open, setOpen] = useState(true);

  return (
    <aside className={`map-legend ${open ? 'is-open' : ''}`} aria-label="Легенда карты">
      <button
        className="legend-title"
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        aria-controls="legend-content"
      >
        <span>Легенда</span>
        <span className="legend-toggle-label">{open ? 'Скрыть' : 'Раскрыть'}<ChevronDown size={15} /></span>
      </button>
      <div className="legend-content" id="legend-content">
        <div id="legend" />
        <div id="legend-meta" className="legend-meta muted small" />
      </div>
    </aside>
  );
}

function ProfilePicker() {
  const [options, setOptions] = useState<AoiOption[]>([]);
  const [selected, setSelected] = useState('');
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const nativeSelect = useRef<HTMLSelectElement>(null);

  useEffect(() => {
    const select = nativeSelect.current;
    if (!select) return;

    const sync = () => {
      const nextOptions = Array.from(select.options).map((option) => ({
        value: option.value,
        label: option.textContent || option.value,
      }));
      setOptions(nextOptions);
      setSelected(select.value || nextOptions[0]?.value || '');
    };
    const closeOutside = (event: PointerEvent) => {
      if (!root.current?.contains(event.target as Node)) setOpen(false);
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false);
    };
    const observer = new MutationObserver(sync);

    observer.observe(select, { childList: true, subtree: true, attributes: true });
    select.addEventListener('change', sync);
    window.addEventListener('aquaflow-profile-sync', sync);
    document.addEventListener('pointerdown', closeOutside);
    document.addEventListener('keydown', closeOnEscape);
    sync();

    return () => {
      observer.disconnect();
      select.removeEventListener('change', sync);
      window.removeEventListener('aquaflow-profile-sync', sync);
      document.removeEventListener('pointerdown', closeOutside);
      document.removeEventListener('keydown', closeOnEscape);
    };
  }, []);

  const choose = (value: string) => {
    const select = nativeSelect.current;
    if (!select) return;
    select.value = value;
    setSelected(value);
    setOpen(false);
    select.dispatchEvent(new Event('change', { bubbles: true }));
  };

  const currentLabel = options.find((option) => option.value === selected)?.label || 'Загрузка профилей…';

  return (
    <div className={`profile-picker ${open ? 'is-open' : ''}`} ref={root}>
      <span className="profile-picker-label">Профиль концентрации</span>
      <select
        className="native-profile-select"
        id="profile"
        ref={nativeSelect}
        aria-hidden="true"
        tabIndex={-1}
        defaultValue=""
      />
      <button
        className="profile-select-button"
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls="profile-options"
      >
        <span className="profile-select-icon"><CircleGauge size={16} /></span>
        <span>{currentLabel}</span>
        <ChevronDown size={15} aria-hidden="true" />
      </button>
      <div className="profile-menu" id="profile-options" role="listbox" aria-label="Профили концентрации">
        <div className="profile-menu-head"><span>Выберите профиль</span><span>{options.length}</span></div>
        {options.map((option) => {
          const isSelected = option.value === selected;
          return (
            <button
              type="button"
              role="option"
              aria-selected={isSelected}
              className={isSelected ? 'is-selected' : ''}
              key={option.value}
              onClick={() => choose(option.value)}
            >
              <span className="profile-option-code">{option.value}</span>
              <span>{option.label.replace(new RegExp(`^${option.value}\\s*[·:]?\\s*`), '')}</span>
              <Check size={15} aria-hidden="true" />
            </button>
          );
        })}
        {!options.length && <div className="profile-options-empty">Загружаем профили…</div>}
      </div>
    </div>
  );
}

type WorkspaceSheetProps = {
  view: SheetView;
  onClose: () => void;
  onChangeView: (view: SheetView) => void;
};

export function WorkspaceSheet({ view, onClose, onChangeView }: WorkspaceSheetProps) {
  return (
    <aside id="side" className={`data-sheet ${view ? 'is-open' : ''}`} aria-label="Центр данных" aria-hidden={!view}>
      <div className="sheet-handle" aria-hidden="true" />
      <header className="sheet-header">
        <div>
          <span className="eyebrow">Центр данных</span>
          <h2>{view === 'layers' ? 'Слои карты' : 'Картина наблюдений'}</h2>
        </div>
        <div className="sheet-tabs" aria-label="Раздел центра данных">
          <button className={view === 'overview' ? 'is-active' : ''} onClick={() => onChangeView('overview')}><Activity size={16} />Сводка</button>
          <button className={view === 'layers' ? 'is-active' : ''} onClick={() => onChangeView('layers')}><Layers3 size={16} />Слои</button>
        </div>
        <button className="sheet-close" onClick={onClose} data-tooltip="Закрыть центр данных" aria-label="Закрыть"><X size={19} /></button>
      </header>

      <div className={`sheet-view overview-view ${view === 'overview' ? 'is-active' : ''}`} aria-hidden={view !== 'overview'}>
        <section className="observation-card">
          <div className="card-heading">
            <div><span className="eyebrow">Спутниковый ряд</span><h3>Динамика загрязнения</h3></div>
            <span className="live-badge"><i /> Sentinel-2</span>
          </div>
          <div className="profile-control">
            <ProfilePicker />
            <div id="profile-note" className="muted small" />
          </div>
          <div className="chart-box timeline-chart"><canvas id="timeline" /></div>
          <div id="scene-info" className="scene-info muted small" />
          <div id="aoi-note" className="aoi-note muted small" hidden />
          <div id="aoi-field" hidden>
            <div className="subhead">Полевые измерения в акватории</div>
            <ul id="aoi-field-list" className="hotlist field-list" />
          </div>
        </section>

        <section className="summary-card">
          <div className="card-heading">
            <div><span className="eyebrow">Выбранная дата</span><h3>Ключевые показатели</h3></div>
          </div>
          <div className="kpis" id="kpis" />
          <div className="subhead">Приоритетные зоны</div>
          <ol id="hotlist" className="hotlist" />
          <div className="filter-block">
            <div className="subhead">Фильтр детекции</div>
            <div className="status-filter" id="filter-detection">
              {detectionStatuses.map(([key, label]) => (
                <label key={key} className={`st-chip st-${key}`}><input type="checkbox" data-status={key} defaultChecked /><span>{label}</span></label>
              ))}
            </div>
            <div className="subhead">Фильтр концентрации</div>
            <div className="status-filter" id="filter-conc">
              {concentrationStatuses.map(([key, label]) => (
                <label key={key} className={`st-chip st-${key}`}><input type="checkbox" data-status={key} defaultChecked /><span>{label}</span></label>
              ))}
            </div>
          </div>
          <div className="subhead">Выгрузка и запрос</div>
          <div className="export-grid">
            <button className="report-btn" id="exp-report" data-tooltip="PDF с картой, снимками и крупнейшими зонами"><FileText size={14} />Отчёт PDF</button>
            <button className="ghost" id="exp-zones-geojson"><Download size={13} />Зоны GeoJSON</button>
            <button className="ghost" id="exp-zones-csv"><Download size={13} />Зоны CSV</button>
            <button className="ghost" id="exp-hexes-geojson"><Download size={13} />Гексы GeoJSON</button>
            <button className="ghost" id="exp-hexes-csv"><Download size={13} />Гексы CSV</button>
            <button className="ghost" id="query-save"><Save size={13} />Сохранить</button>
            <button className="ghost" id="query-rerun"><RotateCcw size={13} />Повторить</button>
          </div>
          <div id="query-info" className="muted small query-info" />
        </section>
      </div>

      <div className={`sheet-view layers-view ${view === 'layers' ? 'is-active' : ''}`} aria-hidden={view !== 'layers'}>
        <div className="layers-intro">
          <span className="eyebrow">Конструктор карты</span>
          <h3>Оставьте только нужный контекст</h3>
          <p>Включайте источники независимо друг от друга. Изменения сразу применяются к карте.</p>
        </div>
        <div className="layers-grid layers">
          <label>
            <span className="layer-icon"><Satellite size={19} /></span>
            <span><strong>Снимок Sentinel-2</strong><small>Естественные цвета акватории</small></span>
            <input type="checkbox" id="l-rgb" defaultChecked />
          </label>
          <label>
            <span className="layer-icon accent-coral"><Sparkles size={19} /></span>
            <span><strong>Детекции мусора</strong><small>Результат спектрального анализа</small></span>
            <input type="checkbox" id="l-debris" defaultChecked />
          </label>
          <label>
            <span className="layer-icon accent-sky"><ShieldCheck size={19} /></span>
            <span><strong>Маска качества</strong><small>Видимость и надёжность сцены</small></span>
            <input type="checkbox" id="l-quality" />
          </label>
          <label>
            <span className="layer-icon accent-coral"><Shapes size={19} /></span>
            <span><strong>Зоны детекции</strong><small>Контуры найденных скоплений</small></span>
            <input type="checkbox" id="l-zones" defaultChecked />
          </label>
          <label>
            <span className="layer-icon"><FlaskConical size={19} /></span>
            <span><strong>Полевые измерения</strong><small>Контрольные маршруты и пробы</small></span>
            <input type="checkbox" id="l-field" defaultChecked />
          </label>
          <label>
            <span className="layer-icon accent-coral"><MapPin size={19} /></span>
            <span><strong>Отдельные предметы</strong><small>Контекст полевых наблюдений</small></span>
            <input type="checkbox" id="l-objects" />
          </label>
          <label>
            <span className="layer-icon accent-lime"><Layers3 size={19} /></span>
            <span><strong>Аналитическая сетка</strong><small>Ячейки H3 · около 0,7 км²</small></span>
            <input type="checkbox" id="l-hex" defaultChecked />
          </label>
          <label>
            <span className="layer-icon accent-sky"><CloudSun size={19} /></span>
            <span><strong>Спутниковая подложка</strong><small>Контекст береговой линии</small></span>
            <input type="checkbox" id="l-sat" />
          </label>
        </div>
      </div>

      <footer className="sheet-footer">
        <span>Sentinel-2 L2A · SMOC · ERA5</span>
      </footer>
    </aside>
  );
}
