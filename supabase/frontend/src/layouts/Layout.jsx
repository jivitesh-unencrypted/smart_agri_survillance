import React, { useState } from 'react'
import { NavLink, useNavigate } from 'react-router-dom'
import {
  LayoutDashboard, Video, Camera, ListVideo, Bell, BarChart3, Settings as SettingsIcon,
  Leaf, LogOut, Menu, X, Wifi, WifiOff, FileVideo,
} from 'lucide-react'
import { useAuth } from '../hooks/useAuth.jsx'
import { cn } from '../lib/utils'

const NAV_ITEMS = [
  { to: '/', label: 'Dashboard', icon: LayoutDashboard, end: true },
  { to: '/live', label: 'Live Monitoring', icon: Video },
  { to: '/cameras', label: 'Cameras', icon: Camera },
  { to: '/video-analysis', label: 'Video Analysis', icon: FileVideo },
  { to: '/detections', label: 'Detections', icon: ListVideo },
  { to: '/alerts', label: 'Alerts', icon: Bell },
  { to: '/analytics', label: 'Analytics', icon: BarChart3 },
  { to: '/settings', label: 'Settings', icon: SettingsIcon },
]

export default function Layout({ children, wsConnected, alertCount }) {
  const [mobileOpen, setMobileOpen] = useState(false)
  const { user, logout } = useAuth()
  const navigate = useNavigate()

  const handleLogout = () => {
    logout()
    navigate('/login')
  }

  return (
    <div className="min-h-screen flex bg-slate-950">
      {/* Sidebar - desktop */}
      <aside className="hidden md:flex md:flex-col w-64 shrink-0 border-r border-slate-800 bg-slate-900/40">
        <SidebarContent onNavigate={() => {}} />
      </aside>

      {/* Sidebar - mobile drawer */}
      {mobileOpen && (
        <div className="fixed inset-0 z-40 md:hidden">
          <div className="absolute inset-0 bg-black/60" onClick={() => setMobileOpen(false)} />
          <aside className="absolute left-0 top-0 bottom-0 w-64 bg-slate-950 border-r border-slate-800 flex flex-col">
            <SidebarContent onNavigate={() => setMobileOpen(false)} />
          </aside>
        </div>
      )}

      <div className="flex-1 flex flex-col min-w-0">
        {/* Header */}
        <header className="h-14 shrink-0 border-b border-slate-800 flex items-center justify-between px-4 bg-slate-950/80 backdrop-blur-sm sticky top-0 z-30">
          <button className="md:hidden btn btn-ghost p-2" onClick={() => setMobileOpen(true)}>
            <Menu size={20} />
          </button>
          <div className="flex items-center gap-3 ml-auto">
            <div className={cn('badge', wsConnected ? 'bg-emerald-500/10 text-emerald-400' : 'bg-red-500/10 text-red-400')}>
              {wsConnected ? <Wifi size={12} /> : <WifiOff size={12} />}
              {wsConnected ? 'Live' : 'Disconnected'}
            </div>
            {alertCount > 0 && (
              <NavLink to="/alerts" className="badge bg-red-500/10 text-red-400">
                <Bell size={12} /> {alertCount}
              </NavLink>
            )}
            <div className="hidden sm:flex items-center gap-2 text-sm text-slate-300">
              <div className="w-7 h-7 rounded-full bg-brand-600 flex items-center justify-center text-xs font-semibold text-white">
                {(user?.username || '?').slice(0, 1).toUpperCase()}
              </div>
              {user?.username}
            </div>
            <button className="btn btn-ghost p-2" onClick={handleLogout} title="Log out">
              <LogOut size={18} />
            </button>
          </div>
        </header>

        <main className="flex-1 min-w-0 p-4 md:p-6">{children}</main>
      </div>
    </div>
  )
}

function SidebarContent({ onNavigate }) {
  return (
    <>
      <div className="h-14 flex items-center gap-2 px-4 border-b border-slate-800">
        <div className="w-8 h-8 rounded-lg bg-brand-600 flex items-center justify-center">
          <Leaf size={18} className="text-white" />
        </div>
        <div className="leading-tight">
          <div className="text-sm font-semibold text-slate-100">Smart Agri</div>
          <div className="text-[10px] text-slate-500 tracking-wide">SURVEILLANCE</div>
        </div>
      </div>
      <nav className="flex-1 overflow-y-auto py-3 px-2 space-y-0.5">
        {NAV_ITEMS.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            end={item.end}
            onClick={onNavigate}
            className={({ isActive }) =>
              cn(
                'flex items-center gap-3 px-3 py-2 rounded-lg text-sm font-medium transition-colors',
                isActive ? 'bg-brand-600/15 text-brand-400' : 'text-slate-400 hover:text-slate-100 hover:bg-slate-800/60',
              )
            }
          >
            <item.icon size={18} />
            {item.label}
          </NavLink>
        ))}
      </nav>
      <div className="p-3 text-[11px] text-slate-600 border-t border-slate-800">
        100% local · no cloud AI · no request limits
      </div>
    </>
  )
}
