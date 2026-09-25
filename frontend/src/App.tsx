import { useEffect, useState } from 'react';
import { AmbientBackground } from './components/AmbientBackground';
import { DetailPanel } from './components/DetailPanel';
import { MapChrome } from './components/MapChrome';
import { Toolbar } from './components/Toolbar';
import { CommandBar, MapLegend, ModePicker, WorkspaceSheet, type SheetView, type ThemeName } from './components/WorkspaceControls';

const THEME_KEY = 'aquaflow-theme';

function readTheme(): ThemeName {
  try {
    const saved = window.localStorage.getItem(THEME_KEY);
    if (saved === 'light' || saved === 'dark') return saved;
  } catch {
  }
  return window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
}

const initialTheme = readTheme();
document.documentElement.dataset.theme = initialTheme;

function App() {
  const [sheet, setSheet] = useState<SheetView>(null);
  const [theme, setTheme] = useState<ThemeName>(initialTheme);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try {
      window.localStorage.setItem(THEME_KEY, theme);
    } catch {
    }
    window.dispatchEvent(new CustomEvent('aquaflow-theme-change', { detail: { theme } }));
  }, [theme]);

  const toggleSheet = (next: Exclude<SheetView, null>): void => {
    const panel = document.getElementById('panel');
    if (panel && !panel.hidden) document.getElementById('panel-close')?.click();
    setSheet((current) => current === next ? null : next);
  };

  return (
    <div id="app-shell" className="isolate bg-[#0d1917]">
      <AmbientBackground />
      <MapChrome />
      <CommandBar activeSheet={sheet} onOpenSheet={toggleSheet} theme={theme} onThemeChange={setTheme} />
      <ModePicker />
      <MapLegend />
      <Toolbar onSelect={() => setSheet(null)} />
      <button
        className={`surface-scrim ${sheet ? 'is-visible' : ''}`}
        onClick={() => setSheet(null)}
        aria-label="Закрыть центр данных"
        tabIndex={sheet ? 0 : -1}
      />
      <WorkspaceSheet view={sheet} onClose={() => setSheet(null)} onChangeView={setSheet} />
      <DetailPanel />
      <div id="tip" hidden />
      <div id="toast" hidden />
    </div>
  );
}

export default App;
