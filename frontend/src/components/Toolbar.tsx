import { ArrowLeftRight, CircleHelp, Layers3, LocateFixed, Maximize2, Route, Waves, type LucideIcon } from 'lucide-react';

type ToolItem = readonly [id: string, label: string, icon: LucideIcon, shortcut: string];

const tools: readonly ToolItem[] = [
  ['hex', 'Участок', Layers3, '1'],
  ['compare', 'Сравнить', ArrowLeftRight, '2'],
  ['drift', 'Дрейф', Waves, '3'],
  ['route', 'Маршрут', Route, '4'],
  ['about', 'Метод', CircleHelp, '5'],
];

export function Toolbar() {
  return (
    <nav id="tools" aria-label="Инструменты карты">
      <div className="workspace-heading">
        <strong id="workspace-aoi">Оперативная карта</strong>
        <span id="workspace-date">Загрузка данных…</span>
      </div>
      <div className="toolbar-divider" />
      {tools.map(([tool, label, Icon, shortcut]) => (
        <button className="group focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-[#7be2d3]" key={tool} data-tool={tool} title={`${label} · ${shortcut}`}>
          <Icon size={16} strokeWidth={1.8} aria-hidden="true" />
          <span>{label}</span>
        </button>
      ))}
      <div className="toolbar-divider action-divider" />
      <div className="toolbar-actions" aria-label="Управление видом карты">
        <button id="map-fit" title="Показать всю акваторию" aria-label="Показать всю акваторию"><LocateFixed size={16} /></button>
        <button id="map-fullscreen" title="Полноэкранный режим" aria-label="Полноэкранный режим"><Maximize2 size={16} /></button>
      </div>
    </nav>
  );
}
