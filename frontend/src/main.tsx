import { createRoot } from 'react-dom/client';
import '@fontsource/montserrat/latin-400.css';
import '@fontsource/montserrat/latin-500.css';
import '@fontsource/montserrat/latin-600.css';
import '@fontsource/montserrat/latin-700.css';
import '@fontsource/montserrat/cyrillic-400.css';
import '@fontsource/montserrat/cyrillic-500.css';
import '@fontsource/montserrat/cyrillic-600.css';
import '@fontsource/montserrat/cyrillic-700.css';
import 'maplibre-gl/dist/maplibre-gl.css';
import './styles.css';
import App from './App';

const root = document.getElementById('root');

if (!root) throw new Error('Не найден корневой элемент приложения');

createRoot(root).render(<App />);

import('./legacy');
