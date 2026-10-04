import React from 'react'
import { Routes, Route, Navigate, useLocation } from 'react-router-dom'
import { Loader2 } from 'lucide-react'

import { useAuth } from './hooks/useAuth.jsx'
import { ToastProvider } from './hooks/useToast.jsx'
import { useLiveSocket } from './hooks/useLiveSocket.js'
import Layout from './layouts/Layout.jsx'

import Login from './pages/Login.jsx'
import Dashboard from './pages/Dashboard.jsx'
import LiveMonitoring from './pages/LiveMonitoring.jsx'
import Cameras from './pages/Cameras.jsx'
import VideoAnalysis from './pages/VideoAnalysis.jsx'
import Detections from './pages/Detections.jsx'
import Alerts from './pages/Alerts.jsx'
import Analytics from './pages/Analytics.jsx'
import SettingsPage from './pages/Settings.jsx'

function ProtectedRoute({ children }) {
  const { isAuthenticated, loading } = useAuth()
  const location = useLocation()

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-slate-950">
        <Loader2 className="animate-spin text-brand-500" size={28} />
      </div>
    )
  }
  if (!isAuthenticated) {
    return <Navigate to="/login" state={{ from: location.pathname }} replace />
  }
  return children
}

export default function App() {
  const { isAuthenticated } = useAuth()
  const socket = useLiveSocket({ maxEvents: 100 })

  return (
    <ToastProvider>
      <Routes>
        <Route path="/login" element={isAuthenticated ? <Navigate to="/" replace /> : <Login />} />

        <Route
          path="/*"
          element={
            <ProtectedRoute>
              <AppShell socket={socket} />
            </ProtectedRoute>
          }
        />
      </Routes>
    </ToastProvider>
  )
}

function AppShell({ socket }) {
  const recentAlerts = socket.events.filter((e) => e.type === 'alert').length

  return (
    <Layout wsConnected={socket.connected} alertCount={recentAlerts}>
      <Routes>
        <Route path="/" element={<Dashboard socket={socket} />} />
        <Route path="/live" element={<LiveMonitoring socket={socket} />} />
        <Route path="/cameras" element={<Cameras socket={socket} />} />
        <Route path="/video-analysis" element={<VideoAnalysis />} />
        <Route path="/detections" element={<Detections />} />
        <Route path="/alerts" element={<Alerts socket={socket} />} />
        <Route path="/analytics" element={<Analytics />} />
        <Route path="/settings" element={<SettingsPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </Layout>
  )
}
