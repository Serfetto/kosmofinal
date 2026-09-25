import { Menu, PanelLeftClose, Waves } from 'lucide-react';

type MapChromeProps = {
  onToggleSidebar: () => void;
};

export function MapChrome({ onToggleSidebar }: MapChromeProps) {
  return (
    <>
      <main id="map" aria-label="Карта загрязнения акватории">
        <div className="map-loading"><Waves /><span>Подключаем спутниковые данные</span></div>
      </main>

      <button className="transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#7be2d3]" id="sidebar-toggle" onClick={onToggleSidebar} title="Свернуть панель" aria-label="Свернуть панель">
        <PanelLeftClose size={17} />
      </button>
      <button className="transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#7be2d3]" id="mobile-menu" onClick={onToggleSidebar} aria-label="Открыть панель"><Menu size={19} /></button>
    </>
  );
}
