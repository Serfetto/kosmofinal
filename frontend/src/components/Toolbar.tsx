import { ArrowLeftRight, CircleHelp, Layers3, Route, Waves, type LucideIcon } from 'lucide-react';

type ToolItem = readonly [id: string, label: string, description: string, icon: LucideIcon];

const tools: readonly ToolItem[] = [
  ['hex', 'Участок', 'Нажмите на ячейку карты и изучите её историю', Layers3],
  ['compare', 'Сравнить', 'Обведите две зоны и сравните уровень загрязнения', ArrowLeftRight],
  ['drift', 'Дрейф', 'Спрогнозируйте перенос мусора на 72 часа', Waves],
  ['route', 'Маршрут', 'Постройте план обследования из ближайшего порта', Route],
  ['about', 'Методика', 'Узнайте, как рассчитаны показатели и прогноз', CircleHelp],
];

type ToolbarProps = { onSelect?: () => void };

export function Toolbar({ onSelect }: ToolbarProps) {
  return (
    <nav id="tools" aria-label="Инструменты карты">
      {tools.map(([tool, label, description, Icon]) => (
        <button
          className="tool-trigger tooltip-up"
          key={tool}
          data-tool={tool}
          data-tooltip={description}
          aria-label={`${label}. ${description}`}
          onClick={onSelect}
        >
          <Icon size={18} strokeWidth={1.9} aria-hidden="true" />
          <span className="tool-label">{label}</span>
        </button>
      ))}
    </nav>
  );
}
