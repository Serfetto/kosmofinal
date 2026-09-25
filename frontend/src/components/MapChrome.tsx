import { LocateFixed, Maximize2, Satellite, Waves, X } from 'lucide-react';

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

      <div id="ext-scene" className="ext-scene-chip" hidden>
        <Satellite size={15} aria-hidden="true" />
        <span id="ext-scene-label" />
        <button id="ext-scene-close" data-tooltip="Убрать снимок с карты" aria-label="Убрать снимок с карты"><X size={14} /></button>
      </div>
    </>
  );
}
