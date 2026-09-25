import { LocateFixed, Maximize2, Waves } from 'lucide-react';

export function MapChrome() {
  return (
    <>
      <main id="map" aria-label="Карта загрязнения акватории">
        <div className="map-loading"><Waves /><span>Подключаем спутниковые данные</span></div>
      </main>

      <div className="map-actions" aria-label="Управление картой">
        <button id="map-fit" data-tooltip="Показать акваторию целиком" aria-label="Показать акваторию целиком">
          <LocateFixed size={18} />
        </button>
        <button id="map-fullscreen" data-tooltip="Открыть карту на весь экран" aria-label="Полноэкранный режим">
          <Maximize2 size={18} />
        </button>
      </div>
    </>
  );
}
