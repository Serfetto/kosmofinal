export function FieldPanel() {
  return (
    <div className="tool" data-tool="field">
      <p className="muted small">
        Полевой реестр кейса: четыре источника из постановки. Для каждого — где и когда шли наблюдения, что нашлось
        в архивах снимков и в каких акваториях сервиса есть снимки на даты измерений.
      </p>
      <div id="field-sources" className="card-body" />
    </div>
  );
}
