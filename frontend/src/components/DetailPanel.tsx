import { X } from 'lucide-react';
import { AboutPanel } from './panels/AboutPanel';
import { CardPanel } from './panels/CardPanel';
import { ComparePanel } from './panels/ComparePanel';
import { DriftPanel } from './panels/DriftPanel';
import { HexPanel } from './panels/HexPanel';
import { RoutePanel } from './panels/RoutePanel';

export function DetailPanel() {
  return (
    <aside id="panel" hidden aria-label="Детали анализа">
      <div className="panel-head">
        <div><span className="panel-kicker">Рабочая область</span><h2 id="panel-title" /></div>
        <button className="icon transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-tide/40" id="panel-close" title="Закрыть · Esc" aria-label="Закрыть"><X size={18} /></button>
      </div>
      <CardPanel />
      <HexPanel />
      <ComparePanel />
      <DriftPanel />
      <RoutePanel />
      <AboutPanel />
    </aside>
  );
}
