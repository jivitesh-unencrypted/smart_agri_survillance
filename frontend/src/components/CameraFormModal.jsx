import React, { useEffect, useState } from 'react'
import { X } from 'lucide-react'
import { api } from '../lib/api'
import { useToast } from '../hooks/useToast.jsx'
import { isNetworkCamera, privateCameraWarning } from '../lib/cameras'

const CONNECTION_TYPES = [
  {
    value: 'browser_webcam',
    label: 'Browser Webcam / USB',
    hint: 'The camera on the device you open the dashboard on (built-in or plugged-in USB). Works from any browser over HTTPS. Pick the camera on Live Monitoring.',
    wide: true,
  },
  {
    value: 'local_webcam',
    label: 'USB (AI server host)',
    hint: 'A USB camera wired to the computer that runs the AI server. Only works when that computer is the server.',
  },
  { value: 'ip_camera', label: 'IP Camera', hint: 'A network camera stream the AI server opens by URL.' },
  { value: 'rtsp', label: 'RTSP Stream', hint: 'An rtsp:// URL the AI server opens.' },
  { value: 'http_mjpeg', label: 'HTTP/MJPEG Stream', hint: 'An http:// MJPEG URL the AI server opens.' },
]

const CAMERA_TYPES = ['Browser Webcam', 'Local Webcam', 'USB Camera', 'IP Camera', 'RTSP Camera', 'Network Camera']

const DEFAULT_FORM = {
  name: '', location: '', description: '', zone: '',
  camera_type: 'Browser Webcam', connection_type: 'browser_webcam',
  device_index: 0, stream_url: '', username: '', password: '',
  password_is_env: false, enabled: true,
}

export default function CameraFormModal({ open, camera, onClose, onSaved }) {
  const [form, setForm] = useState(DEFAULT_FORM)
  const [saving, setSaving] = useState(false)
  const toast = useToast()
  const isEdit = !!camera

  useEffect(() => {
    if (camera) {
      setForm({
        name: camera.name || '',
        location: camera.location || '',
        description: camera.description || '',
        zone: camera.zone || '',
        camera_type: camera.camera_type || 'Local Webcam',
        connection_type: camera.connection_type || 'browser_webcam',
        device_index: camera.device_index ?? 0,
        // Only network cameras keep a URL; the other types show a label, not a stream address.
        stream_url: isNetworkCamera(camera) ? (camera.stream_url_display || '') : '',
        username: camera.username || '',
        password: '',
        password_is_env: false,
        enabled: camera.enabled ?? true,
      })
    } else {
      setForm(DEFAULT_FORM)
    }
  }, [camera, open])

  if (!open) return null

  const set = (key, value) => setForm((f) => ({ ...f, [key]: value }))
  const selectedType = CONNECTION_TYPES.find((ct) => ct.value === form.connection_type)
  const networkWarning = isNetworkCamera(form) ? privateCameraWarning(form.stream_url) : ''

  const handleSubmit = async (e) => {
    e.preventDefault()
    if (!form.name.trim()) {
      toast.error('Camera name is required')
      return
    }
    if (isNetworkCamera(form) && !form.stream_url.trim()) {
      toast.error('Stream URL is required for IP / RTSP / HTTP cameras')
      return
    }

    setSaving(true)
    try {
      const payload = { ...form, device_index: Number(form.device_index) || 0 }
      if (isEdit) {
        payload.keep_existing_password = !form.password
        await api.put(`/api/cameras/${camera.id}`, payload)
        toast.success(`Camera "${form.name}" updated`)
      } else {
        await api.post('/api/cameras', payload)
        toast.success(`Camera "${form.name}" added`)
      }
      onSaved()
    } catch (err) {
      toast.error(err.detail || 'Failed to save camera')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4">
      <div className="card w-full max-w-lg max-h-[90vh] overflow-y-auto">
        <div className="flex items-center justify-between mb-4">
          <h2 className="font-semibold text-slate-100">{isEdit ? `Edit Camera — ${camera.camera_code}` : 'Add Camera'}</h2>
          <button className="btn btn-ghost p-1.5" onClick={onClose}><X size={18} /></button>
        </div>

        <form onSubmit={handleSubmit} className="space-y-3">
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="label">Camera Name</label>
              <input className="input" value={form.name} onChange={(e) => set('name', e.target.value)} placeholder="Farm Main Gate" required />
            </div>
            <div>
              <label className="label">Location</label>
              <input className="input" value={form.location} onChange={(e) => set('location', e.target.value)} placeholder="North Field" />
            </div>
          </div>

          <div>
            <label className="label">Description</label>
            <textarea className="input" rows={2} value={form.description} onChange={(e) => set('description', e.target.value)} placeholder="Main entrance surveillance camera" />
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="label">Camera Type</label>
              <select className="input" value={form.camera_type} onChange={(e) => set('camera_type', e.target.value)}>
                {CAMERA_TYPES.map((t) => <option key={t} value={t}>{t}</option>)}
              </select>
            </div>
            <div>
              <label className="label">Zone (optional)</label>
              <input className="input" value={form.zone} onChange={(e) => set('zone', e.target.value)} placeholder="North Field Zone" />
            </div>
          </div>

          <div>
            <label className="label">Connection Type</label>
            <div className="grid grid-cols-2 gap-2">
              {CONNECTION_TYPES.map((ct) => (
                <button
                  type="button"
                  key={ct.value}
                  onClick={() => set('connection_type', ct.value)}
                  className={`text-xs rounded-lg py-2 px-2 border transition-colors ${ct.wide ? 'col-span-2' : ''} ${
                    form.connection_type === ct.value
                      ? 'bg-brand-600/20 border-brand-600 text-brand-300'
                      : 'bg-slate-900 border-slate-700 text-slate-400 hover:border-slate-600'
                  }`}
                >
                  {ct.label}
                </button>
              ))}
            </div>
            {selectedType && <p className="text-[11px] text-slate-500 mt-1.5">{selectedType.hint}</p>}
          </div>

          {form.connection_type === 'browser_webcam' ? (
            <p className="text-xs text-slate-400 rounded-lg border border-slate-800 bg-slate-900/60 p-3">
              No address needed. Press Start on Live Monitoring and allow camera access in your browser.
              Your camera choice is remembered in that browser.
            </p>
          ) : form.connection_type === 'local_webcam' ? (
            <div>
              <label className="label">USB Device Index (on AI server host)</label>
              <select className="input" value={form.device_index} onChange={(e) => set('device_index', e.target.value)}>
                {[0, 1, 2, 3, 4].map((i) => <option key={i} value={i}>Webcam {i}</option>)}
              </select>
            </div>
          ) : (
            <>
              <div>
                <label className="label">Stream URL</label>
                <input
                  className="input"
                  value={form.stream_url}
                  onChange={(e) => set('stream_url', e.target.value)}
                  placeholder={form.connection_type === 'rtsp' ? 'rtsp://192.168.1.100:554/stream' : 'http://192.168.1.100:8080/video'}
                />
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="label">Username (optional)</label>
                  <input className="input" value={form.username} onChange={(e) => set('username', e.target.value)} />
                </div>
                <div>
                  <label className="label">Password (optional)</label>
                  <input
                    type="password"
                    className="input"
                    value={form.password}
                    onChange={(e) => set('password', e.target.value)}
                    placeholder={isEdit ? 'leave blank to keep unchanged' : 'leave blank for no password'}
                  />
                </div>
              </div>
              <label className="flex items-center gap-2 text-xs text-slate-400">
                <input type="checkbox" checked={form.password_is_env} onChange={(e) => set('password_is_env', e.target.checked)} />
                Store as environment variable reference (recommended) — the password field above becomes the variable name.
              </label>
              {networkWarning && (
                <p role="alert" className="text-xs text-amber-400 rounded-md border border-amber-500/30 bg-amber-500/10 p-2">
                  {networkWarning}
                </p>
              )}
            </>
          )}

          <label className="flex items-center gap-2 text-sm text-slate-300">
            <input type="checkbox" checked={form.enabled} onChange={(e) => set('enabled', e.target.checked)} />
            Enabled
          </label>

          <div className="flex gap-2 pt-2">
            <button type="button" className="btn btn-secondary flex-1" onClick={onClose}>Cancel</button>
            <button type="submit" disabled={saving} className="btn btn-primary flex-1">
              {isEdit ? 'Save Camera' : 'Add Camera'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}
