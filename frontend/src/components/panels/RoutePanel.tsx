import { ShipWheel } from 'lucide-react';

export function RoutePanel() {
  return (
    <div className="tool" data-tool="route">
      <p className="muted small">Маршрут из порта к наиболее загрязнённым точкам с поправкой на дрейф к моменту прибытия.</p>
      <div className="form">
        <label>Точек<input type="number" id="r-n" defaultValue="8" min="1" max="20" /></label>
        <label>Скорость, уз<input type="number" id="r-speed" defaultValue="12" min="2" max="40" /></label>
        <label>Выход через, ч<input type="number" id="r-delay" defaultValue="6" min="0" max="48" /></label>
      </div>
      <button className="primary" id="route-run"><ShipWheel size={16} />Построить маршрут</button>
      <div id="route-result" />
    </div>
  );
}
