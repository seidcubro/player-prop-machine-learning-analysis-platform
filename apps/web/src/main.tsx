/**
 * Vite/React application bootstrap.
 */

import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter, Routes, Route } from 'react-router-dom'
import App from './App'
import EdgesDashboard from './pages/EdgesDashboard'
import Projections from './pages/Projections'
import TrackRecord from './pages/TrackRecord'
import Faq from './pages/Faq'
import PlayersSearch from './pages/PlayersSearch'
import PlayerDetail from './pages/PlayerDetail'
import './index.css'

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <BrowserRouter>
      <Routes>
        <Route element={<App />}>
          <Route path="/" element={<EdgesDashboard />} />
          <Route path="/projections" element={<Projections />} />
          <Route path="/record" element={<TrackRecord />} />
          <Route path="/faq" element={<Faq />} />
          <Route path="/players" element={<PlayersSearch />} />
          <Route path="/players/:id" element={<PlayerDetail />} />
        </Route>
      </Routes>
    </BrowserRouter>
  </React.StrictMode>,
)
