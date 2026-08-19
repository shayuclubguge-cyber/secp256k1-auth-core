import { Routes, Route } from 'react-router'
import Home from './pages/Home'
import Tutorial from './pages/Tutorial'

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<Home />} />
      <Route path="/tutorial" element={<Tutorial />} />
    </Routes>
  )
}
