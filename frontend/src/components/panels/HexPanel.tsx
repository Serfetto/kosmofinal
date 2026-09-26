import { Navigation } from 'lucide-react';

export function HexPanel() {
  return (
    <div className="tool" data-tool="hex">
      <p className="muted" id="hex-empty">Выберите гекс на карте, чтобы открыть историю загрязнения.</p>
      <div id="hex-body" hidden>
        <div className="kpis" id="hex-kpis" />
        <div id="hex-conc" className="conc-box" />
        <div id="hex-calc" className="calc-box" />
        <div id="hex-fusion" />
        <div className="chart-box tall"><canvas id="hex-chart" /></div>
        <button className="primary mb-2" id="hex-drift" data-tooltip="Смоделировать перенос из центра выбранной ячейки"><Navigation size={16} />Прогноз дрейфа из этого гекса</button>
        <p className="muted small" id="hex-drift-info" />
      </div>
    </div>
  );
}
