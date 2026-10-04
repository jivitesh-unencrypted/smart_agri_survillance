import React, { useEffect, useState, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { Users, PawPrint, Truck, Boxes, Camera as CameraIcon, Activity, ArrowRight } from 'lucide-react'
import { api } from '../lib/api'
import { formatTime, categoryColor, severityColor } from '../lib/utils'
import KpiCard from '../components/KpiCard.jsx'
import StatusBadge from '../components/StatusBadge.jsx'

export default function Dashboard({ socket }) {
  const [summary, setSummary] = useState(null)
  const [cameras, setCameras] = useState([])
  const [recentDetections, setRecentDetections] = useState([])
  const [alerts, setAlerts] = useState([])
  const [systemStatus, setSystemStatus] = useState(null)
  const [loading, setLoading] = useState(true)
  const navigate = useNavigate()

  const loadAll = useCallback(async () => {
    try {
      const [summaryRes, camerasRes, detectionsRes, alertsRes, statusRes] = await Promise.all([
        api.get('/api/detections/summary'),
        api.get('/api/cameras'),
        api.get('/api/detections?page=1&page_size=8'),
        api.get('/api/alerts?limit=6'),
        api.get('/api/system/status'),
      ])
      setSummary(summaryRes)
      setCameras(camerasRes)
      setRecentDetections(detectionsRes.items)
      setAlerts(alertsRes.items)
      setSystemStatus(statusRes)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    loadAll()
    const interval = setInterval(loadAll, 15000)
    return () => clearInterval(interval)
  }, [loadAll])

  // Refresh once shortly after any live detection/alert event comes in.
  useEffect(() => {
    return socket.subscribe((evt) => {
      if (evt.type === 'detection' && evt.is_new_event) loadAll()
      if (evt.type === 'alert') loadAll()
    })
  }, [socket.subscribe, loadAll])

  const online = cameras.filter((c) => c.status === 'online').length
  const offline = cameras.filter((c) => c.status === 'offline').length

  if (loading) {
    return <div className="text-slate-500 text-sm">Loading dashboard…</div>
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold text-slate-100">Smart Agri Surveillance</h1>
        <p className="text-sm text-slate-500">Real-time AI monitoring for agricultural security</p>
      </div>

      {/* KPI row */}
      <div className="grid grid-cols-2 lg:grid-cols-5 gap-4">
        <KpiCard label="Total Detections" value={summary?.total_events ?? 0} icon={Activity} variant="brand" />
        <KpiCard label="Humans" value={summary?.human_events ?? 0} icon={Users} variant={summary?.human_events ? 'critical' : 'neutral'} />
        <KpiCard label="Animals" value={summary?.animal_events ?? 0} icon={PawPrint} variant={summary?.animal_events ? 'warn' : 'neutral'} />
        <KpiCard label="Vehicles" value={summary?.vehicle_events ?? 0} icon={Truck} variant={summary?.vehicle_events ? 'info' : 'neutral'} />
        <KpiCard label="Others" value={summary?.other_events ?? 0} icon={Boxes} />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Left / main column */}
        <div className="lg:col-span-2 space-y-6">
          <div className="card">
            <div className="card-title">Surveillance Status</div>
            <div className="space-y-2 text-sm">
              <StatusRow label="AI Engine" value={systemStatus?.model_loaded ? 'Online' : 'Offline'} ok={systemStatus?.model_loaded} />
              <StatusRow label="Active Cameras" value={`${systemStatus?.active_cameras ?? 0} streaming`} ok={(systemStatus?.active_cameras ?? 0) > 0} />
              <StatusRow label="Database" value={systemStatus?.database_ok ? 'Connected' : 'Unavailable'} ok={systemStatus?.database_ok} />
              {!systemStatus?.model_loaded && systemStatus?.model_error && (
                <p className="text-xs text-red-400 pt-1">Model error: {systemStatus.model_error}</p>
              )}
            </div>
          </div>

          <div className="card">
            <div className="flex items-center justify-between mb-3">
              <div className="card-title mb-0">Camera Network</div>
              <button onClick={() => navigate('/cameras')} className="text-xs text-brand-400 hover:text-brand-300 flex items-center gap-1">
                Manage <ArrowRight size={12} />
              </button>
            </div>
            {cameras.length === 0 ? (
              <EmptyState text="No cameras configured yet." />
            ) : (
              <div className="space-y-2">
                {cameras.map((c) => (
                  <div key={c.id} className="flex items-center justify-between py-1.5 border-b border-slate-800/60 last:border-0">
                    <div className="flex items-center gap-2 text-sm text-slate-200">
                      <CameraIcon size={14} className="text-slate-500" />
                      {c.name}
                    </div>
                    <StatusBadge status={c.status} />
                  </div>
                ))}
                <p className="text-xs text-slate-500 pt-1">{online} / {cameras.length} cameras online{offline ? `, ${offline} offline` : ''}</p>
              </div>
            )}
          </div>

          <div className="card">
            <div className="flex items-center justify-between mb-3">
              <div className="card-title mb-0">Recent Detections</div>
              <button onClick={() => navigate('/detections')} className="text-xs text-brand-400 hover:text-brand-300 flex items-center gap-1">
                View all <ArrowRight size={12} />
              </button>
            </div>
            {recentDetections.length === 0 ? (
              <EmptyState text="No detection records found." />
            ) : (
              <table className="table-base">
                <thead>
                  <tr>
                    <th>Time</th><th>Object</th><th>Camera</th><th>Confidence</th>
                  </tr>
                </thead>
                <tbody>
                  {recentDetections.map((d) => {
                    const c = categoryColor(d.category)
                    return (
                      <tr key={d.id}>
                        <td className="whitespace-nowrap">{formatTime(d.created_at)}</td>
                        <td>
                          <span className={`badge ${c.bg} ${c.text}`}>{d.object_name}</span>
                        </td>
                        <td className="text-slate-400">{d.camera_name || '—'}</td>
                        <td>{Math.round(d.confidence * 100)}%</td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            )}
          </div>
        </div>

        {/* Right column: alerts */}
        <div className="space-y-6">
          <div className="card">
            <div className="card-title">Recent Alerts</div>
            {alerts.length === 0 ? (
              <EmptyState text="No alerts yet. They'll appear here as detections are logged." />
            ) : (
              <div className="space-y-2">
                {alerts.map((a) => {
                  const c = severityColor(a.severity)
                  return (
                    <div key={a.id} className={`flex items-start gap-2 rounded-lg border px-3 py-2 ${c.bg} ${c.border}`}>
                      <div className="flex-1">
                        <div className={`text-sm font-medium ${c.text}`}>{a.title}</div>
                        <div className="text-xs text-slate-400">{a.message}</div>
                        <div className="text-[11px] text-slate-500 mt-0.5">{formatTime(a.created_at)}</div>
                      </div>
                    </div>
                  )
                })}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}

function StatusRow({ label, value, ok }) {
  return (
    <div className="flex items-center justify-between">
      <span className="text-slate-400">{label}</span>
      <span className={ok ? 'text-emerald-400' : 'text-slate-500'}>{value}</span>
    </div>
  )
}

function EmptyState({ text }) {
  return <p className="text-sm text-slate-500 py-2">{text}</p>
}
