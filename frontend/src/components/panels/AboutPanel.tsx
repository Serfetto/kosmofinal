export function AboutPanel() {
  return (
    <div className="tool" data-tool="about">
      <div className="about-tabs" role="tablist" aria-label="Разделы методики">
        <button type="button" role="tab" data-about="method" className="on" aria-selected="true">Методика</button>
        <button type="button" role="tab" data-about="checks" aria-selected="false">Проверка</button>
        <button type="button" role="tab" data-about="data" aria-selected="false">Данные</button>
      </div>
      <div id="about" />
    </div>
  );
}
