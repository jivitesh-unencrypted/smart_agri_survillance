import React, { useEffect, useState } from 'react'
import {
  ResponsiveContainer, LineChart, Line, BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, Legend, Cell,
} from 'recharts'
import { api } from '../lib/api'

const CATEGORY_COLORS = { Human: '#f87171', Animals: '#fbbf24', Vehicles: '#60a5fa', Others: '#94a3b8' }

export default function Analytics() {
  const [data, setData] = useState(null)
  const [range, setRange] = useState('weekly') // daily | weekly | monthly

  useEffect(() => {
    api.get('/api/analytics').then(setData).catch(() => {})
  }, [])

  if (!data) return <p className="text-sm text-slate-500">Loading analytics…</p>

  const series = data[range] || []
  const byCategoryData = Object.entries(data.by_category).map(([category, count]) => ({ category, count }))
  const peakHoursData = data.peak_hours || []

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-xl font-semibold text-slate-100">Analytics</h1>
          <p className="text-sm text-slate-500">Detection trends across your camera network.</p>
        </div>
        <div className="flex gap-2">
          {['daily', 'weekly', 'monthly'].map((r) => (
            <button key={r} className={`btn text-xs ${range === r ? 'btn-primary' : 'btn-secondary'}`} onClick={() => setRange(r)}>
              {r[0].toUpperCase() + r.slice(1)}
            </button>
          ))}
        </div>
      </div>

      <div className="card">
        <div className="card-title">Detections Over Time</div>
        <div className="h-72">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={series}>
              <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
              <XAxis dataKey="bucket" tick={{ fontSize: 11, fill: '#64748b' }} minTickGap={20} />
              <YAxis tick={{ fontSize: 11, fill: '#64748b' }} allowDecimals={false} />
              <Tooltip contentStyle={{ background: '#0f172a', border: '1px solid #1e293b', borderRadius: 8, fontSize: 12 }} />
              <Legend wrapperStyle={{ fontSize: 12 }} />
              {Object.keys(CATEGORY_COLORS).map((cat) => (
                <Line key={cat} type="monotone" dataKey={cat} stroke={CATEGORY_COLORS[cat]} strokeWidth={2} dot={false} />
              ))}
            </LineChart>
          </ResponsiveContainer>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <div className="card">
          <div className="card-title">Detections by Category</div>
          <div className="h-64">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={byCategoryData}>
                <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
                <XAxis dataKey="category" tick={{ fontSize: 11, fill: '#64748b' }} />
                <YAxis tick={{ fontSize: 11, fill: '#64748b' }} allowDecimals={false} />
                <Tooltip contentStyle={{ background: '#0f172a', border: '1px solid #1e293b', borderRadius: 8, fontSize: 12 }} />
                <Bar dataKey="count" radius={[4, 4, 0, 0]}>
                  {byCategoryData.map((d) => (
                    <Cell key={d.category} fill={CATEGORY_COLORS[d.category] || '#94a3b8'} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>

        <div className="card">
          <div className="card-title">Camera-wise Detections</div>
          {data.by_camera.length === 0 ? (
            <p className="text-sm text-slate-500 py-6">No camera detection data yet.</p>
          ) : (
            <div className="h-64">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={data.by_camera} layout="vertical">
                  <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
                  <XAxis type="number" tick={{ fontSize: 11, fill: '#64748b' }} allowDecimals={false} />
                  <YAxis type="category" dataKey="camera_name" width={100} tick={{ fontSize: 11, fill: '#64748b' }} />
                  <Tooltip contentStyle={{ background: '#0f172a', border: '1px solid #1e293b', borderRadius: 8, fontSize: 12 }} />
                  <Bar dataKey="count" fill="#43cb80" radius={[0, 4, 4, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </div>
          )}
        </div>
      </div>

      <div className="card">
        <div className="card-title">Peak Detection Hours</div>
        <div className="h-56">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={peakHoursData}>
              <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
              <XAxis dataKey="hour" tick={{ fontSize: 11, fill: '#64748b' }} tickFormatter={(h) => `${h}:00`} />
              <YAxis tick={{ fontSize: 11, fill: '#64748b' }} allowDecimals={false} />
              <Tooltip contentStyle={{ background: '#0f172a', border: '1px solid #1e293b', borderRadius: 8, fontSize: 12 }} labelFormatter={(h) => `${h}:00`} />
              <Bar dataKey="count" fill="#60a5fa" radius={[4, 4, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>
    </div>
  )
}
