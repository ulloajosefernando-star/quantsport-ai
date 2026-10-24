import { BrowserRouter, Route, Routes } from 'react-router-dom'
import Layout from './components/Layout'
import Home from './pages/Home'
import Fixtures from './pages/Fixtures'
import Opportunities from './pages/Opportunities'
import EventDetail from './pages/EventDetail'
import Performance from './pages/Performance'
import Sources from './pages/Sources'
import Settings from './pages/Settings'
import Tools from './pages/Tools'
import Markets from './pages/Markets'

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<Layout />}>
          <Route path="/" element={<Home />} />
          <Route path="/cartelera" element={<Fixtures />} />
          <Route path="/oportunidades" element={<Opportunities />} />
          <Route path="/eventos/:id" element={<EventDetail />} />
          <Route path="/rendimiento" element={<Performance />} />
          <Route path="/fuentes" element={<Sources />} />
          <Route path="/configuracion" element={<Settings />} />
          <Route path="/mercados" element={<Markets />} />
          <Route path="/herramientas" element={<Tools />} />
          <Route path="*" element={<p className="text-ink-400">Página no encontrada.</p>} />
        </Route>
      </Routes>
    </BrowserRouter>
  )
}
