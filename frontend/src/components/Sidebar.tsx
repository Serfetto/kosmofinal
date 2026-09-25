import { ChevronLeft, ChevronRight, Layers3, Satellite, Sparkles } from 'lucide-react';
import { BrandMark } from './BrandMark';
import { Section } from './Section';

const modes = [
  ['date', 'На дату', 'Состояние на выбранном снимке'],
  ['mean', 'Среднее', 'Средняя концентрация за весь период'],
  ['persist', 'Устойчивость', 'Как часто загрязнение повторяется'],
  ['trend', 'Тренд', 'Рост или снижение концентрации'],
  ['accum', 'Скопление', 'Зоны схождения течений'],
  ['forecast', 'Прогноз', 'Ожидаемая концентрация через 72 часа'],
] as const;

export function Sidebar() {
  return (
    <aside id="side" className="bg-paper text-ink" aria-label="Параметры мониторинга">
      <header className="brand">
        <BrandMark />
        <div className="brand-copy">
          <div className="brand-topline"><span>Flux</span><em>OCEAN INTELLIGENCE</em></div>
          <p>Мониторинг океанического пластика в реальном времени</p>
        </div>
      </header>

      <Section index="01" title="Акватория">
        <div className="select-wrap">
          <select id="aoi" className="focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-tide/30" aria-label="Выберите акваторию" defaultValue="" />
          <ChevronRight size={15} aria-hidden="true" />
        </div>
      </Section>

      <Section index="02" title="Наблюдение">
        <div className="date-control">
          <button className="icon transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-tide/40" id="prev" title="Предыдущая дата · ←" aria-label="Предыдущая дата"><ChevronLeft size={17} /></button>
          <strong id="date-label">—</strong>
          <button className="icon transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-tide/40" id="next" title="Следующая дата · →" aria-label="Следующая дата"><ChevronRight size={17} /></button>
        </div>
        <div className="chart-box"><canvas id="timeline" /></div>
        <div id="scene-info" className="muted small scene-info" />
      </Section>

      <Section index="03" title="Слой анализа">
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
      </Section>

      <Section index="04" title="Сводка">
        <div className="kpis" id="kpis" />
        <div className="subhead">Приоритетные зоны</div>
        <ol id="hotlist" className="hotlist" />
      </Section>

      <Section index="05" title="Отображение" className="layers">
        <label className="transition-colors hover:text-tide"><input type="checkbox" id="l-rgb" defaultChecked /><span>Снимок Sentinel-2</span><Satellite size={14} /></label>
        <label className="transition-colors hover:text-tide"><input type="checkbox" id="l-debris" defaultChecked /><span>Пиксели детекций</span><Sparkles size={14} /></label>
        <label className="transition-colors hover:text-tide"><input type="checkbox" id="l-hex" defaultChecked /><span>Сетка H3 · ~0,7 км²</span><Layers3 size={14} /></label>
        <label className="transition-colors hover:text-tide"><input type="checkbox" id="l-sat" /><span>Спутниковая подложка</span><Satellite size={14} /></label>
      </Section>

      <footer>
        <span className="status-dot" />
        <span>Sentinel-2 L2A · SMOC · ERA5</span>
      </footer>
    </aside>
  );
}
