import { Activity, Menu, MousePointer2, PanelLeftClose, Waves } from 'lucide-react';

type MapChromeProps = {
  onToggleSidebar: () => void;
};

export function MapChrome({ onToggleSidebar }: MapChromeProps) {
  return (
    <>
      <main id="map" aria-label="Карта загрязнения акватории">
        <div className="map-loading"><Waves /><span>Подключаем спутниковые данные</span></div>
      </main>

      <div className="map-meta" aria-hidden="true">
        <span>FLUX / OCEAN INTELLIGENCE</span>
        <span>LIVE TELEMETRY</span>
      </div>

      <div className="map-guide" aria-label="Подсказка по работе с картой">
        <div className="guide-icon"><MousePointer2 size={15} /></div>
        <div><b>Начните с карты</b><span>Нажмите на гекс, чтобы увидеть историю участка</span></div>
        <div className="guide-status"><Activity size={12} /><span>данные готовы</span></div>
      </div>

      <button className="transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#7be2d3]" id="sidebar-toggle" onClick={onToggleSidebar} title="Свернуть панель" aria-label="Свернуть панель">
        <PanelLeftClose size={17} />
      </button>
      <button className="transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#7be2d3]" id="mobile-menu" onClick={onToggleSidebar} aria-label="Открыть панель"><Menu size={19} /></button>
    </>
  );
}
