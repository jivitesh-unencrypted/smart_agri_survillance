import React, { useEffect, useState, useCallback } from 'react'
import { CheckCircle2, Circle, AlertTriangle } from 'lucide-react'
import { api } from '../lib/api'
import { formatDateTime, severityColor } from '../lib/utils'
import { useToast } from '../hooks/useToast.jsx'

export default function Alerts({ socket }) {
  const [alerts, setAlerts] = useState([])
  const [tab, setTab] = useState('active') // active | all
  const [loading, setLoading] = useState(true)
  const toast = useToast()

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const params = tab === 'active' ? '?resolved=false' : ''
      const res = await api.get(`/api/alerts${params}`)
      setAlerts(res.items)
    } finally {
      setLoading(false)
    }
  }, [tab])

  useEffect(() => { load() }, [load])

  useEffect(() => {
    return socket?.subscribe((evt) => {
      if (evt.type === 'alert') load()
    })
  }, [socket?.subscribe, load])

  const handleAcknowledge = async (alert) => {
    try {
      await api.patch(`/api/alerts/${alert.id}`, { acknowledged: !alert.acknowledged })
      load()
    } catch (err) {
      toast.error(err.detail || 'Failed to update alert')
    }
  }

  const handleResolve = async (alert) => {
    try {
      await api.patch(`/api/alerts/${alert.id}`, { resolved: true })
      toast.success('Alert resolved')
      load()
    } catch (err) {
      toast.error(err.detail || 'Failed to resolve alert')
    }
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold text-slate-100">Alerts</h1>
        <p className="text-sm text-slate-500">Review and manage surveillance alerts.</p>
      </div>

      <div className="flex gap-2">
        <button className={`btn ${tab === 'active' ? 'btn-primary' : 'btn-secondary'}`} onClick={() => setTab('active')}>Active</button>
        <button className={`btn ${tab === 'all' ? 'btn-primary' : 'btn-secondary'}`} onClick={() => setTab('all')}>All</button>
      </div>

      <div className="card">
        {loading ? (
          <p className="text-sm text-slate-500 py-4">Loading…</p>
        ) : alerts.length === 0 ? (
          <p className="text-sm text-slate-500 py-4">
            {tab === 'active' ? 'No active alerts. Alerts appear here as detections are logged.' : 'No alerts recorded yet.'}
          </p>
        ) : (
          <div className="space-y-2">
            {alerts.map((a) => {
              const c = severityColor(a.severity)
              return (
                <div key={a.id} className={`flex items-start gap-3 rounded-lg border px-3 py-3 ${c.bg} ${c.border}`}>
                  <AlertTriangle size={18} className={`mt-0.5 shrink-0 ${c.text}`} />
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className={`font-medium ${c.text}`}>{a.title}</span>
                      <span className={`badge ${c.bg} ${c.text} border ${c.border}`}>{a.severity}</span>
                      {a.resolved && <span className="badge bg-slate-700/50 text-slate-400">Resolved</span>}
                    </div>
                    <p className="text-sm text-slate-300 mt-0.5">{a.message}</p>
                    <p className="text-xs text-slate-500 mt-1">
                      {formatDateTime(a.created_at)}{a.camera_name ? ` · ${a.camera_name}` : ''}
                    </p>
                  </div>
                  <div className="flex flex-col gap-1.5 shrink-0">
                    <button
                      className="btn btn-ghost text-xs px-2 py-1"
                      onClick={() => handleAcknowledge(a)}
                      title={a.acknowledged ? 'Acknowledged' : 'Mark acknowledged'}
                    >
                      {a.acknowledged ? <CheckCircle2 size={14} className="text-emerald-400" /> : <Circle size={14} />}
                    </button>
                    {!a.resolved && (
                      <button className="btn btn-secondary text-xs px-2 py-1" onClick={() => handleResolve(a)}>
                        Resolve
                      </button>
                    )}
                  </div>
                </div>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}
