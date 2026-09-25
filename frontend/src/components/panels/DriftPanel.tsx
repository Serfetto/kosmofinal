import { Waves } from 'lucide-react';

export function DriftPanel() {
  return (
    <div className="tool" data-tool="drift">
      <p className="muted small">Четыре виртуальные частицы для каждого пикселя учитывают течение и 1–3% ветрового переноса. Так видны направление и неопределённость прогноза.</p>
      <div className="small" id="drift-legend">
        <div className="leg-row"><span className="dot particle" />прогнозное положение мусора</div>
        <div className="leg-row"><span className="dot beached" />частица выброшена на берег</div>
        <div className="leg-row"><span className="sw track" />траектории частиц</div>
        <p className="muted">На +0 ч частицы совпадают с красными пикселями детекций.</p>
      </div>
      <button className="primary" id="drift-run" data-tooltip="Запустить моделирование течений и ветрового переноса"><Waves size={16} />Рассчитать прогноз на 72 часа</button>
      <div id="drift-ctrl" hidden>
        <div className="row gap8 mt">
          <button className="icon" id="drift-play" data-tooltip="Запустить или остановить анимацию" aria-label="Пуск или пауза">▶</button>
          <input type="range" id="drift-hour" min="0" max="72" defaultValue="0" />
          <strong id="drift-hlabel">+0 ч</strong>
        </div>
        <div className="kpis" id="drift-kpis" />
        <p className="muted small">Итоговая концентрация доступна в показателе карты «Прогноз».</p>
      </div>
    </div>
  );
}
