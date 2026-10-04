import React, { useEffect, useState } from 'react'
import { Plus, Pencil, Trash2, PlugZap, Play, Square, MapPin } from 'lucide-react'
import { api } from '../lib/api'
import { useToast } from '../hooks/useToast.jsx'
import StatusBadge from '../components/StatusBadge.jsx'
import ConfirmDialog from '../components/ConfirmDialog.jsx'
import CameraFormModal from '../components/CameraFormModal.jsx'

export default function Cameras({ socket }) {
  const [cameras, setCameras] = useState([])
  const [loading, setLoading] = useState(true)
  const [formOpen, setFormOpen] = useState(false)
  const [editing, setEditing] = useState(null)
  const [deleteTarget, setDeleteTarget] = useState(null)
  const [testingId, setTestingId] = useState(null)
  const toast = useToast()

  const load = async () => {
    try {
      const list = await api.get('/api/cameras')
      setCameras(list)
    } catch (err) {
      toast.error(err.detail || 'Failed to load cameras')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [])

  useEffect(() => {
    return socket?.subscribe((evt) => {
      if (evt.type === 'camera_status') load()
    })
  }, [socket?.subscribe])

  const handleTest = async (camera) => {
    setTestingId(camera.id)
    try {
      const result = await api.post(`/api/cameras/${camera.id}/test`)
      if (result.success) {
        toast.success(`${camera.name}: stream is available (${result.latency_ms?.toFixed(0)} ms)`)
      } else {
        toast.error(`${camera.name}: ${result.message}`)
      }
      load()
    } catch (err) {
      toast.error(err.detail || 'Connection test failed')
    } finally {
      setTestingId(null)
    }
  }

  const handleStartStop = async (camera) => {
    try {
      if (camera.status === 'online') {
        await api.post(`/api/cameras/${camera.id}/stop`)
        toast.info(`${camera.name} stopped`)
      } else {
        await api.post(`/api/cameras/${camera.id}/start`)
        toast.success(`${camera.name} starting…`)
      }
      setTimeout(load, 800)
    } catch (err) {
      toast.error(err.detail || 'Action failed')
    }
  }

  const handleDelete = async () => {
    if (!deleteTarget) return
    try {
      await api.delete(`/api/cameras/${deleteTarget.id}`)
      toast.success(`Camera ${deleteTarget.name} deleted`)
      setDeleteTarget(null)
      load()
    } catch (err) {
      toast.error(err.detail || 'Failed to delete camera')
    }
  }

  const online = cameras.filter((c) => c.status === 'online').length
  const offline = cameras.filter((c) => c.status === 'offline').length

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-xl font-semibold text-slate-100">Camera Management</h1>
          <p className="text-sm text-slate-500">Manage local and remote surveillance cameras.</p>
        </div>
        <button className="btn btn-primary" onClick={() => { setEditing(null); setFormOpen(true) }}>
          <Plus size={16} /> Add Camera
        </button>
      </div>

      <div className="card">
        <div className="grid grid-cols-3 gap-4 text-center">
          <div>
            <div className="text-2xl font-semibold text-slate-100">{cameras.length}</div>
            <div className="text-xs text-slate-500">Cameras</div>
          </div>
          <div>
            <div className="text-2xl font-semibold text-emerald-400">{online}</div>
            <div className="text-xs text-slate-500">Online</div>
          </div>
          <div>
            <div className="text-2xl font-semibold text-red-400">{offline}</div>
            <div className="text-xs text-slate-500">Offline</div>
          </div>
        </div>
      </div>

      {loading ? (
        <p className="text-sm text-slate-500">Loading cameras…</p>
      ) : cameras.length === 0 ? (
        <div className="card text-center py-10">
          <p className="text-slate-400 mb-3">No cameras configured yet.</p>
          <button className="btn btn-primary" onClick={() => setFormOpen(true)}>
            <Plus size={16} /> Add your first camera
          </button>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {cameras.map((c) => (
            <div key={c.id} className="card">
              <div className="flex items-start justify-between mb-2">
                <div>
                  <div className="font-medium text-slate-100">{c.name}</div>
                  <div className="text-xs text-slate-500 flex items-center gap-1 mt-0.5">
                    {c.camera_code} · {c.connection_type}
                    {c.location && (<><MapPin size={11} className="ml-1" /> {c.location}</>)}
                  </div>
                </div>
                <StatusBadge status={c.status} />
              </div>

              <div className="grid grid-cols-3 gap-2 my-3 text-center">
                <MiniMetric label="FPS" value={c.last_fps ? c.last_fps.toFixed(0) : '—'} />
                <MiniMetric label="Latency" value={c.last_latency_ms ? `${c.last_latency_ms.toFixed(0)}ms` : '—'} />
                <MiniMetric label="Status" value={c.enabled ? 'Enabled' : 'Disabled'} />
              </div>

              {c.last_error && c.status === 'offline' && (
                <p className="text-xs text-red-400 mb-2">⚠ {c.last_error}</p>
              )}

              <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 mt-2">
                <button
                  className="btn btn-secondary text-xs px-2 py-1.5"
                  onClick={() => handleStartStop(c)}
                >
                  {c.status === 'online' ? <Square size={13} /> : <Play size={13} />}
                  {c.status === 'online' ? 'Stop' : 'Start'}
                </button>
                <button
                  className="btn btn-secondary text-xs px-2 py-1.5"
                  disabled={testingId === c.id}
                  onClick={() => handleTest(c)}
                >
                  <PlugZap size={13} /> Test
                </button>
                <button
                  className="btn btn-secondary text-xs px-2 py-1.5"
                  onClick={() => { setEditing(c); setFormOpen(true) }}
                >
                  <Pencil size={13} /> Edit
                </button>
                <button
                  className="btn btn-secondary text-xs px-2 py-1.5 hover:!bg-red-600/20 hover:!text-red-400"
                  onClick={() => setDeleteTarget(c)}
                >
                  <Trash2 size={13} /> Delete
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      <CameraFormModal
        open={formOpen}
        camera={editing}
        onClose={() => setFormOpen(false)}
        onSaved={() => { setFormOpen(false); load() }}
      />

      <ConfirmDialog
        open={!!deleteTarget}
        title="Delete camera?"
        message={`This will permanently remove "${deleteTarget?.name}" and its detection history cannot be recovered.`}
        confirmLabel="Delete"
        onConfirm={handleDelete}
        onCancel={() => setDeleteTarget(null)}
      />
    </div>
  )
}

function MiniMetric({ label, value }) {
  return (
    <div className="bg-slate-800/50 rounded-lg py-1.5">
      <div className="text-sm font-medium text-slate-200">{value}</div>
      <div className="text-[10px] text-slate-500 uppercase">{label}</div>
    </div>
  )
}
