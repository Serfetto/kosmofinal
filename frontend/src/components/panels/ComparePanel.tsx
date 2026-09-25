export function ComparePanel() {
  return (
    <div className="tool" data-tool="compare">
      <p className="muted small">Обведите два участка: клики задают вершины, двойной клик завершает контур.</p>
      <div className="row gap8 wrap">
        <button className="chip a" id="draw-a">Участок A</button>
        <button className="chip b" id="draw-b">Участок B</button>
        <button className="ghost" id="draw-clear">Сбросить</button>
      </div>
      <div id="cmp-result" />
      <div className="chart-box tall"><canvas id="cmp-chart" /></div>
    </div>
  );
}
