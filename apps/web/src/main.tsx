/**
 * Vite/React application bootstrap.
 */

import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter, Routes, Route } from 'react-router-dom'
import App from './App'
import EdgesDashboard from './pages/EdgesDashboard'
import PlayersSearch from './pages/PlayersSearch'
import PlayerDetail from './pages/PlayerDetail'
import './index.css'

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <BrowserRouter>
      <Routes>
        <Route element={<App />}>
          <Route path="/" element={<EdgesDashboard />} />
          <Route path="/players" element={<PlayersSearch />} />
          <Route path="/players/:id" element={<PlayerDetail />} />
        </Route>
      </Routes>
    </BrowserRouter>
  </React.StrictMode>,
)
