import { Routes, Route } from 'react-router-dom';
import Layout from './components/Layout';
import { ToastProvider } from './components/Toast';
import Dashboard from './pages/Dashboard';
import Week from './pages/Week';
import Replay from './pages/Replay';
import Insights from './pages/Insights';
import Compare from './pages/Compare';
import Heatmap from './pages/Heatmap';
import Urls from './pages/Urls';
import Wiki from './pages/Wiki';

export default function App() {
  return (
    <ToastProvider>
      <Routes>
        <Route element={<Layout />}>
          <Route path="/" element={<Dashboard />} />
          <Route path="/week" element={<Week />} />
          <Route path="/replay" element={<Replay />} />
          <Route path="/insights" element={<Insights />} />
          <Route path="/compare" element={<Compare />} />
          <Route path="/heatmap" element={<Heatmap />} />
          <Route path="/urls" element={<Urls />} />
          <Route path="/wiki" element={<Wiki />} />
        </Route>
      </Routes>
    </ToastProvider>
  );
}
