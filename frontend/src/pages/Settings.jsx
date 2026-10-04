import React, { useEffect, useState } from 'react'
import { Save, Loader2 } from 'lucide-react'
import { api } from '../lib/api'
import { useToast } from '../hooks/useToast.jsx'

export default function SettingsPage() {
  const [form, setForm] = useState(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const toast = useToast()

  useEffect(() => {
    api.get('/api/settings').then(setForm).catch(() => {}).finally(() => setLoading(false))
  }, [])

  const set = (key, value) => setForm((f) => ({ ...f, [key]: value }))

  const handleSave = async (e) => {
    e.preventDefault()
    setSaving(true)
    try {
      const updated = await api.put('/api/settings', form)
      setForm(updated)
      toast.success('Settings saved')
    } catch (err) {
      toast.error(err.detail || err.message || 'Failed to save settings')
    } finally {
      setSaving(false)
    }
  }

  if (loading || !form) return <p className="text-sm text-slate-500">Loading settings…</p>

  return (
    <form onSubmit={handleSave} className="space-y-6 max-w-3xl">
      <div>
        <h1 className="text-xl font-semibold text-slate-100">Settings</h1>
        <p className="text-sm text-slate-500">Tune detection performance, alerts, and storage behavior.</p>
      </div>

      <div className="card space-y-4">
        <div className="card-title">Detection</div>

        <SliderField
          label="Confidence Threshold"
          value={form.confidence_threshold}
          min={0.05} max={0.95} step={0.05}
          onChange={(v) => set('confidence_threshold', v)}
          format={(v) => `${Math.round(v * 100)}%`}
        />
        <SliderField
          label="IoU Threshold"
          value={form.iou_threshold}
          min={0.1} max={0.9} step={0.05}
          onChange={(v) => set('iou_threshold', v)}
          format={(v) => `${Math.round(v * 100)}%`}
        />
        <div className="grid grid-cols-2 gap-4">
          <NumberField label="Inference Interval (ms)" value={form.inference_interval_ms} min={0} step={50}
            onChange={(v) => set('inference_interval_ms', v)} hint="Minimum time between YOLO runs per camera" />
          <NumberField label="Frame Skip" value={form.frame_skip} min={0} step={1}
            onChange={(v) => set('frame_skip', v)} hint="Only every Nth frame is eligible for inference" />
        </div>
        <NumberField label="Event Cooldown (seconds)" value={form.event_cooldown_seconds} min={1} step={1}
          onChange={(v) => set('event_cooldown_seconds', v)} hint="Gap before a continuous detection is treated as a new event" />
      </div>

      <div className="card space-y-3">
        <div className="card-title">Alerts & Sound</div>
        <ToggleField label="Enable Alerts" checked={form.alerts_enabled} onChange={(v) => set('alerts_enabled', v)} />
        <ToggleField label="Enable Alert Sound" checked={form.sound_enabled} onChange={(v) => set('sound_enabled', v)} />
        <div className="grid grid-cols-3 gap-3 pt-2">
          <ToggleField label="Alert on Human" checked={form.alert_on_human} onChange={(v) => set('alert_on_human', v)} />
          <ToggleField label="Alert on Animal" checked={form.alert_on_animal} onChange={(v) => set('alert_on_animal', v)} />
          <ToggleField label="Alert on Vehicle" checked={form.alert_on_vehicle} onChange={(v) => set('alert_on_vehicle', v)} />
        </div>
      </div>

      <div className="card space-y-3">
        <div className="card-title">Storage</div>
        <ToggleField label="Save Snapshot on Detection Event" checked={form.snapshot_on_event} onChange={(v) => set('snapshot_on_event', v)} />
        <NumberField label="Max Snapshot Age (days)" value={form.max_snapshot_age_days} min={1} step={1}
          onChange={(v) => set('max_snapshot_age_days', v)} hint="Informational — automated cleanup is not scheduled by the backend yet; see README." />
        <NumberField label="Stream Downscale Width (px)" value={form.stream_max_width ?? ''} min={160} step={32}
          onChange={(v) => set('stream_max_width', v ? Number(v) : null)}
          hint="Optional: shrink each frame before inference to cut CPU. Leave blank for full resolution." />
      </div>

      <div className="card space-y-3">
        <div className="card-title">Cloud Sync</div>
        <ToggleField label="Sync Events to Supabase" checked={form.sync_enabled} onChange={(v) => set('sync_enabled', v)} />
        <ToggleField label="Upload Snapshots to Cloud Storage" checked={form.upload_snapshots_to_cloud}
          onChange={(v) => set('upload_snapshots_to_cloud', v)} />
        <p className="text-xs text-slate-500">
          Turn sync off to run fully local — detection keeps working and events queue in
          <code className="mx-1 text-slate-400">ai-server/data/outbox.db</code>
          until you re-enable it. Turn snapshot upload off to keep every JPEG on the local
          machine while still syncing the small event rows.
        </p>
      </div>

      <div className="card">
        <div className="card-title">Model</div>
        <p className="text-sm text-slate-400">{form.model_path || 'Configured via ai-server/.env → MODEL_PATH'}</p>
      </div>

      <button type="submit" disabled={saving} className="btn btn-primary">
        {saving ? <Loader2 size={16} className="animate-spin" /> : <Save size={16} />}
        Save Settings
      </button>
    </form>
  )
}

function SliderField({ label, value, min, max, step, onChange, format }) {
  return (
    <div>
      <div className="flex items-center justify-between mb-1">
        <label className="label mb-0">{label}</label>
        <span className="text-sm text-slate-300">{format ? format(value) : value}</span>
      </div>
      <input
        type="range" min={min} max={max} step={step} value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="w-full accent-brand-500"
      />
    </div>
  )
}

function NumberField({ label, value, min, step, onChange, hint }) {
  return (
    <div>
      <label className="label">{label}</label>
      <input type="number" className="input" value={value ?? ''} min={min} step={step}
        onChange={(e) => onChange(e.target.value === '' ? null : Number(e.target.value))} />
      {hint && <p className="text-xs text-slate-500 mt-1">{hint}</p>}
    </div>
  )
}

function ToggleField({ label, checked, onChange }) {
  return (
    <label className="flex items-center justify-between gap-3 text-sm text-slate-300 cursor-pointer">
      <span>{label}</span>
      <button
        type="button"
        onClick={() => onChange(!checked)}
        className={`w-10 h-5 rounded-full transition-colors relative shrink-0 ${checked ? 'bg-brand-600' : 'bg-slate-700'}`}
      >
        <span className={`absolute top-0.5 w-4 h-4 rounded-full bg-white transition-transform ${checked ? 'translate-x-5' : 'translate-x-0.5'}`} />
      </button>
    </label>
  )
}
