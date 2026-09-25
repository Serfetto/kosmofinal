import { ArrowLeftRight, CircleHelp, Layers3, Route, Waves, type LucideIcon } from 'lucide-react';

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
      {tools.map(([tool, label, Icon, shortcut]) => (
        <button className="group focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-[#7be2d3]" key={tool} data-tool={tool} title={`${label} · ${shortcut}`}>
          <Icon size={16} strokeWidth={1.8} aria-hidden="true" />
          <span>{label}</span>
          <kbd>{shortcut}</kbd>
        </button>
      ))}
    </nav>
  );
}
