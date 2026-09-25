import { DetailPanel } from './components/DetailPanel';
import { MapChrome } from './components/MapChrome';
import { Sidebar } from './components/Sidebar';
import { Toolbar } from './components/Toolbar';

function App() {
  const toggleSidebar = (): void => {
    document.getElementById('app-shell')?.classList.toggle('sidebar-compact');
    window.setTimeout(() => window.dispatchEvent(new Event('resize')), 240);
  };

  return (
    <div id="app-shell" className="isolate bg-[#0d1917]">
      <AmbientBackground />
      <Sidebar />
      <MapChrome onToggleSidebar={toggleSidebar} />
      <Toolbar />
      <DetailPanel />
      <div id="tip" hidden />
      <div id="toast" hidden />
    </div>
  );
}

export default App;
import { AmbientBackground } from './components/AmbientBackground';
