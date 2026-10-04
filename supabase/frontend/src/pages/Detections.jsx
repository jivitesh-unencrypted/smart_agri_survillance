import React, { useEffect, useState, useCallback } from 'react'
import { useSearchParams } from 'react-router-dom'
import { Search, Download, ChevronLeft, ChevronRight, ImageOff } from 'lucide-react'
import { api } from '../lib/api'
import { formatDateTime, formatDuration, categoryColor, severityColor } from '../lib/utils'
import KpiCard from '../components/KpiCard.jsx'
import SnapshotImage from '../components/SnapshotImage.jsx'

const CATEGORIES = ['Human', 'Animals', 'Vehicles', 'Others']
const SEVERITIES = ['critical', 'warning', 'info', 'normal']

export default function Detections() {
  const [searchParams] = useSearchParams()
  const [items, setItems] = useState([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [pageSize] = useState(20)
  const [loading, setLoading] = useState(true)
  const [cameras, setCameras] = useState([])
  const [preview, setPreview] = useState(null)

  const [filters, setFilters] = useState({
    source: searchParams.get('source') || '', category: '', severity: '', camera_id: '', search: '',
  })

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const params = new URLSearchParams({ page: String(page), page_size: String(pageSize) })
      Object.entries(filters).forEach(([k, v]) => { if (v) params.set(k, v) })
      const res = await api.get(`/api/detections?${params.toString()}`)
      setItems(res.items)
      setTotal(res.total)
    } finally {
      setLoading(false)
    }
  }, [page, pageSize, filters])

  useEffect(() => { load() }, [load])
  useEffect(() => { api.get('/api/cameras').then(setCameras).catch(() => {}) }, [])

  const setFilter = (key, value) => { setPage(1); setFilters((f) => ({ ...f, [key]: value })) }

  const handleExport = () => {
    const header = ['Timestamp', 'Source', 'Camera', 'Location', 'Object', 'Category', 'Confidence', 'Duration(s)', 'Severity']
    const rows = items.map((d) => [
      d.created_at, d.source, d.camera_name || '', d.location || '', d.object_name,
      d.category, (d.confidence * 100).toFixed(0) + '%', d.duration_seconds.toFixed(1), d.severity,
    ])
    const csv = [header, ...rows].map((r) => r.map((v) => `"${String(v).replace(/"/g, '""')}"`).join(',')).join('\n')
    const blob = new Blob([csv], { type: 'text/csv' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `detection_logs_export_${new Date().toISOString().slice(0, 19).replace(/[:T]/g, '')}.csv`
    a.click()
    URL.revokeObjectURL(url)
  }

  const totalPages = Math.max(1, Math.ceil(total / pageSize))

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold text-slate-100">Detection Logs</h1>
        <p className="text-sm text-slate-500">Historical record of AI surveillance events.</p>
      </div>

      <div className="card">
        <div className="card-title">Filters</div>
        <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
          <select className="input" value={filters.source} onChange={(e) => setFilter('source', e.target.value)}>
            <option value="">All Sources</option>
            <option value="Live Camera">Live Camera</option>
            <option value="Video Analysis">Video Analysis</option>
          </select>
          <select className="input" value={filters.category} onChange={(e) => setFilter('category', e.target.value)}>
            <option value="">All Categories</option>
            {CATEGORIES.map((c) => <option key={c} value={c}>{c}</option>)}
          </select>
          <select className="input" value={filters.severity} onChange={(e) => setFilter('severity', e.target.value)}>
            <option value="">All Severities</option>
            {SEVERITIES.map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
          <select className="input" value={filters.camera_id} onChange={(e) => setFilter('camera_id', e.target.value)}>
            <option value="">All Cameras</option>
            {cameras.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
          </select>
          <div className="relative">
            <Search size={14} className="absolute left-2.5 top-2.5 text-slate-500" />
            <input className="input pl-8" placeholder="Search object…" value={filters.search} onChange={(e) => setFilter('search', e.target.value)} />
          </div>
        </div>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
        <KpiCard label="Total Events" value={total} />
        <KpiCard label="Human" value={items.filter((d) => d.category === 'Human').length} variant="critical" />
        <KpiCard label="Animals" value={items.filter((d) => d.category === 'Animals').length} variant="warn" />
        <KpiCard label="Vehicles" value={items.filter((d) => d.category === 'Vehicles').length} variant="info" />
        <KpiCard label="Others" value={items.filter((d) => d.category === 'Others').length} />
      </div>

      <div className="card">
        <div className="flex items-center justify-between mb-3">
          <div className="card-title mb-0">Log Table</div>
          <button className="btn btn-secondary text-xs" onClick={handleExport} disabled={items.length === 0}>
            <Download size={13} /> Export CSV
          </button>
        </div>

        {loading ? (
          <p className="text-sm text-slate-500 py-4">Loading…</p>
        ) : items.length === 0 ? (
          <p className="text-sm text-slate-500 py-4">No detection records found for the selected filters.</p>
        ) : (
          <>
            <div className="overflow-x-auto">
              <table className="table-base">
                <thead>
                  <tr>
                    <th>Snapshot</th><th>Timestamp</th><th>Source</th><th>Camera</th>
                    <th>Object</th><th>Confidence</th><th>Duration</th><th>Severity</th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((d) => {
                    const c = categoryColor(d.category)
                    const s = severityColor(d.severity)
                    return (
                      <tr key={d.id}>
                        <td>
                          {d.snapshot_path ? (
                            <SnapshotImage
                              snapshotPath={d.snapshot_path}
                              alt=""
                              className="w-12 h-8 object-cover rounded border border-slate-700"
                              onClick={() => setPreview(d)}
                              onMissing={<div className="w-12 h-8 flex items-center justify-center rounded border border-slate-800 text-slate-700"><ImageOff size={14} /></div>}
                            />
                          ) : (
                            <div className="w-12 h-8 flex items-center justify-center rounded border border-slate-800 text-slate-700">
                              <ImageOff size={14} />
                            </div>
                          )}
                        </td>
                        <td className="whitespace-nowrap">{formatDateTime(d.created_at)}</td>
                        <td className="text-slate-400">{d.source}</td>
                        <td className="text-slate-400">{d.camera_name || '—'}</td>
                        <td><span className={`badge ${c.bg} ${c.text}`}>{d.object_name}</span></td>
                        <td>{Math.round(d.confidence * 100)}%</td>
                        <td>{formatDuration(d.duration_seconds)}</td>
                        <td><span className={s.text}>{d.severity}</span></td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>

            <div className="flex items-center justify-between mt-4 text-sm">
              <span className="text-slate-500">Page {page} of {totalPages} · {total} total</span>
              <div className="flex gap-2">
                <button className="btn btn-secondary px-2 py-1" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>
                  <ChevronLeft size={16} />
                </button>
                <button className="btn btn-secondary px-2 py-1" disabled={page >= totalPages} onClick={() => setPage((p) => p + 1)}>
                  <ChevronRight size={16} />
                </button>
              </div>
            </div>
          </>
        )}
      </div>

      {preview && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4" onClick={() => setPreview(null)}>
          <SnapshotImage
            snapshotPath={preview.snapshot_path}
            alt=""
            className="max-h-[85vh] max-w-full rounded-lg border border-slate-700"
          />
        </div>
      )}
    </div>
  )
}
