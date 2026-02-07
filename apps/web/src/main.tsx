import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter, Routes, Route } from 'react-router-dom'
import PlayersSearch from './pages/PlayersSearch'
import PlayerDetail from './pages/PlayerDetail'
import './index.css'

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<PlayersSearch />} />
        <Route path="/players/:id" element={<PlayerDetail />} />
      </Routes>
    </BrowserRouter>
  </React.StrictMode>,
)
