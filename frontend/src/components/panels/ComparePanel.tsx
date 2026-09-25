export function ComparePanel() {
  return (
    <div className="tool" data-tool="compare">
      <p className="muted small mb-2">Обведите два участка: клики задают вершины, двойной клик завершает контур.</p>
      <div className="row gap8 wrap">
        <button className="chip a" id="draw-a" data-tooltip="Начать рисовать границы первого участка">Участок A</button>
        <button className="chip b" id="draw-b" data-tooltip="Начать рисовать границы второго участка">Участок B</button>
        <button className="ghost" id="draw-clear" data-tooltip="Удалить оба контура и результат сравнения">Сбросить</button>
      </div>
      <div id="cmp-result" />
      <div className="chart-box tall"><canvas id="cmp-chart" /></div>
    </div>
  );
}
