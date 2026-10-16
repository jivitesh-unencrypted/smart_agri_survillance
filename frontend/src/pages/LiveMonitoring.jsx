import React, { useEffect, useMemo, useState } from 'react'
import { Plus, Gauge, Camera as CameraIcon } from 'lucide-react'
import { api } from '../lib/api'
import { useToast } from '../hooks/useToast.jsx'
import { categoryColor, severityColor } from '../lib/utils'
import { useUserScope } from '../lib/userScope'
import ConfirmDialog from '../components/ConfirmDialog.jsx'
import CameraFormModal from '../components/CameraFormModal.jsx'
import CameraTile from '../components/live/CameraTile.jsx'

/**
 * Live Monitoring: every configured camera on one screen. Browser webcams, USB
 * cameras on the AI host and IP/RTSP/HTTP cameras all use the same tile, and
 * cameras are added, edited and removed through the same Add Camera form as
 * the Cameras page.
 */
export default function LiveMonitoring({ socket }) {
  const [cameras, setCameras] = useState([])
  const [loading, setLoading] = useState(true)
  const [formOpen, setFormOpen] = useState(false)
  const [editing, setEditing] = useState(null)
  const [deleteTarget, setDeleteTarget] = useState(null)
  const [expandedId, setExpandedId] = useState(null)
  const [liveEvents, setLiveEvents] = useState([])
  const toast = useToast()
  // Camera list and live events are per user. Clearing them on an account
  // change stops the previous account's data from showing.
  const epoch = useUserScope()

  const load = async () => {
    try {
      const list = await api.get('/api/cameras')
      setCameras(list)
    } catch (err) {
      // The AI server is unreachable: the page stays usable, with no cameras to show.
      setCameras([])
      if (err.status !== 0) toast.error(err.detail || 'Failed to load cameras')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    setCameras([])
    setLiveEvents([])
    setExpandedId(null)
    setLoading(true)
    load()
  }, [epoch])

  useEffect(() => {
    if (!socket) return undefined
    return socket.subscribe((evt) => {
      if (evt.type === 'detection' && evt.is_new_event) {
        setLiveEvents((prev) => [evt, ...prev].slice(0, 30))
      }
    })
  }, [socket?.subscribe, epoch])

  const nameById = useMemo(() => Object.fromEntries(cameras.map((c) => [c.id, c.name])), [cameras])

  const handleDelete = async () => {
    if (!deleteTarget) return
    try {
      await api.delete(`/api/cameras/${deleteTarget.id}`)
      toast.success(`Camera ${deleteTarget.name} deleted`)
      setDeleteTarget(null)
      if (expandedId === deleteTarget.id) setExpandedId(null)
      load()
    } catch (err) {
      toast.error(err.detail || 'Failed to delete camera')
    }
  }

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-xl font-semibold text-slate-100">Live Monitoring</h1>
          <p className="text-sm text-slate-500">
            Browser, USB and IP cameras on one screen, with YOLO object detection on every feed.
          </p>
        </div>
        <button className="btn btn-primary" onClick={() => { setEditing(null); setFormOpen(true) }}>
          <Plus size={16} /> Add Camera
        </button>
      </div>

      {loading ? (
        <p className="text-sm text-slate-500">Loading cameras…</p>
      ) : cameras.length === 0 ? (
        <div className="card text-center py-10">
          <CameraIcon size={28} className="mx-auto mb-3 text-slate-600" />
          <p className="text-slate-400 mb-3">No cameras configured yet.</p>
          <button className="btn btn-primary" onClick={() => setFormOpen(true)}>
            <Plus size={16} /> Add your first camera
          </button>
        </div>
      ) : (
        <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
          {cameras.map((camera) => (
            <div
              key={`${epoch}-${camera.id}`}
              className={expandedId === camera.id ? 'xl:col-span-2' : ''}
            >
              <CameraTile
                camera={camera}
                socket={socket}
                expanded={expandedId === camera.id}
                onToggleExpand={() => setExpandedId((id) => (id === camera.id ? null : camera.id))}
                onEdit={() => { setEditing(camera); setFormOpen(true) }}
                onDelete={() => setDeleteTarget(camera)}
                onChanged={load}
              />
            </div>
          ))}
        </div>
      )}

      <div className="card">
        <div className="card-title flex items-center gap-2"><Gauge size={14} /> Live Detection Events</div>
        {liveEvents.length === 0 ? (
          <p className="text-sm text-slate-500">No objects detected yet across your cameras.</p>
        ) : (
          <div className="space-y-1.5 max-h-72 overflow-y-auto">
            {liveEvents.map((evt, i) => {
              const c = categoryColor(evt.category)
              const s = severityColor(evt.severity)
              return (
                <div key={`${evt.id}-${i}`} className="flex items-center justify-between gap-3 text-sm py-1.5 border-b border-slate-800/60 last:border-0">
                  <div className="flex items-center gap-2 min-w-0">
                    <span className={`badge ${c.bg} ${c.text}`}>{evt.object_name}</span>
                    <span className="text-slate-500 text-xs">{Math.round((evt.confidence || 0) * 100)}%</span>
                    <span className="text-slate-500 text-xs truncate">{nameById[evt.camera_id] || evt.camera_name || ''}</span>
                  </div>
                  <span className={`text-xs ${s.text}`}>{evt.severity}</span>
                </div>
              )
            })}
          </div>
        )}
      </div>

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
