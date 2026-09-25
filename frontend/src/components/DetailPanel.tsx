import { X } from 'lucide-react';
import { AboutPanel } from './panels/AboutPanel';
import { CardPanel } from './panels/CardPanel';
import { ComparePanel } from './panels/ComparePanel';
import { DriftPanel } from './panels/DriftPanel';
import { FieldPanel } from './panels/FieldPanel';
import { HexPanel } from './panels/HexPanel';
import { RoutePanel } from './panels/RoutePanel';

export function DetailPanel() {
  return (
    <aside id="panel" hidden aria-label="Детали анализа">
      <div className="panel-head">
        <div><span className="panel-kicker">Инструмент анализа</span><h2 id="panel-title" /></div>
        <button className="icon tooltip-left" id="panel-close" data-tooltip="Закрыть · Esc" aria-label="Закрыть"><X size={18} /></button>
      </div>
      <CardPanel />
      <FieldPanel />
      <HexPanel />
      <ComparePanel />
      <DriftPanel />
      <RoutePanel />
      <AboutPanel />
    </aside>
  );
}
