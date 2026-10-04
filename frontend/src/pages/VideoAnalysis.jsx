import React, { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { UploadCloud, FileVideo, Loader2, CheckCircle2, XCircle, Ban, ArrowRight } from 'lucide-react'
import { api } from '../lib/api'
import { formatDateTime } from '../lib/utils'
import { useToast } from '../hooks/useToast.jsx'

const POLL_INTERVAL_MS = 1500

export default function VideoAnalysis() {
  const [jobs, setJobs] = useState([])
  const [uploading, setUploading] = useState(false)
  const [dragOver, setDragOver] = useState(false)
  const fileInputRef = useRef(null)
  const pollRef = useRef(null)
  const toast = useToast()
  const navigate = useNavigate()

  const loadJobs = useCallback(async () => {
    try {
      const res = await api.get('/api/video-analysis/jobs')
      setJobs(res.items)
    } catch {
      /* silent - polling */
    }
  }, [])

  useEffect(() => {
    loadJobs()
    pollRef.current = setInterval(() => {
      // Only poll while something is actually in flight, to avoid
      // hammering the API once everything has settled.
      setJobs((current) => {
        const hasActive = current.some((j) => j.status === 'queued' || j.status === 'processing')
        if (hasActive) loadJobs()
        return current
      })
    }, POLL_INTERVAL_MS)
    return () => clearInterval(pollRef.current)
  }, [loadJobs])

  const handleFile = async (file) => {
    if (!file) return
    const allowed = ['.mp4', '.avi', '.mov', '.mkv', '.webm', '.m4v']
    const ext = '.' + (file.name.split('.').pop() || '').toLowerCase()
    if (!allowed.includes(ext)) {
      toast.error(`Unsupported format ${ext}. Allowed: ${allowed.join(', ')}`)
      return
    }
    setUploading(true)
    try {
      const formData = new FormData()
      formData.append('file', file)
      await api.upload('/api/video-analysis/upload', formData)
      toast.success(`${file.name} uploaded — analysis started`)
      loadJobs()
    } catch (err) {
      toast.error(err.detail || 'Upload failed')
    } finally {
      setUploading(false)
      if (fileInputRef.current) fileInputRef.current.value = ''
    }
  }

  const handleCancel = async (job) => {
    try {
      await api.post(`/api/video-analysis/jobs/${job.id}/cancel`)
      toast.info('Cancelling…')
      loadJobs()
    } catch (err) {
      toast.error(err.detail || 'Could not cancel job')
    }
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold text-slate-100">Video Analysis</h1>
        <p className="text-sm text-slate-500">Upload a recorded video to run it through the same YOLO detection pipeline used for live cameras.</p>
      </div>

      <div
        className={`card border-dashed border-2 text-center py-10 transition-colors cursor-pointer ${
          dragOver ? 'border-brand-500 bg-brand-500/5' : 'border-slate-700'
        }`}
        onClick={() => fileInputRef.current?.click()}
        onDragOver={(e) => { e.preventDefault(); setDragOver(true) }}
        onDragLeave={() => setDragOver(false)}
        onDrop={(e) => {
          e.preventDefault()
          setDragOver(false)
          const file = e.dataTransfer.files?.[0]
          if (file) handleFile(file)
        }}
      >
        <input
          ref={fileInputRef}
          type="file"
          accept=".mp4,.avi,.mov,.mkv,.webm,.m4v"
          className="hidden"
          onChange={(e) => handleFile(e.target.files?.[0])}
        />
        {uploading ? (
          <>
            <Loader2 className="mx-auto mb-3 animate-spin text-brand-400" size={32} />
            <p className="text-sm text-slate-300">Uploading…</p>
          </>
        ) : (
          <>
            <UploadCloud className="mx-auto mb-3 text-slate-500" size={32} />
            <p className="text-sm text-slate-300 font-medium">Click to select or drag & drop a video</p>
            <p className="text-xs text-slate-500 mt-1">MP4, AVI, MOV, MKV, WEBM, M4V — no file size limit imposed by the app itself</p>
          </>
        )}
      </div>

      <div className="card">
        <div className="card-title">Analysis Jobs</div>
        {jobs.length === 0 ? (
          <p className="text-sm text-slate-500 py-4">No videos analyzed yet. Upload one above to get started.</p>
        ) : (
          <div className="space-y-2">
            {jobs.map((job) => (
              <JobRow key={job.id} job={job} onCancel={handleCancel} onViewDetections={() =>
                navigate(`/detections?source=${encodeURIComponent('Video Analysis')}`)
              } />
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

function JobRow({ job, onCancel, onViewDetections }) {
  const statusCfg = {
    queued: { icon: Loader2, spin: true, text: 'text-slate-400', label: 'Queued' },
    processing: { icon: Loader2, spin: true, text: 'text-brand-400', label: 'Processing' },
    completed: { icon: CheckCircle2, spin: false, text: 'text-emerald-400', label: 'Completed' },
    failed: { icon: XCircle, spin: false, text: 'text-red-400', label: 'Failed' },
    cancelled: { icon: Ban, spin: false, text: 'text-slate-500', label: 'Cancelled' },
  }[job.status] || { icon: FileVideo, spin: false, text: 'text-slate-400', label: job.status }

  const Icon = statusCfg.icon
  const isActive = job.status === 'queued' || job.status === 'processing'

  return (
    <div className="flex items-center gap-3 py-2.5 border-b border-slate-800/60 last:border-0">
      <Icon size={18} className={`shrink-0 ${statusCfg.text} ${statusCfg.spin ? 'animate-spin' : ''}`} />
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <span className="text-sm font-medium text-slate-200 truncate">{job.original_filename}</span>
          <span className={`text-xs ${statusCfg.text}`}>{statusCfg.label}</span>
        </div>
        {isActive ? (
          <div className="w-full h-1.5 bg-slate-800 rounded-full mt-1.5 overflow-hidden">
            <div
              className="h-full bg-brand-500 transition-all"
              style={{ width: `${Math.max(3, job.progress_percent)}%` }}
            />
          </div>
        ) : (
          <p className="text-xs text-slate-500 mt-0.5">
            {formatDateTime(job.completed_at || job.created_at)}
            {job.status === 'completed' && ` · ${job.event_count} detection event${job.event_count === 1 ? '' : 's'}`}
            {job.status === 'failed' && job.error_message && ` · ${job.error_message}`}
          </p>
        )}
      </div>
      {isActive ? (
        <button className="btn btn-secondary text-xs px-2 py-1" onClick={() => onCancel(job)}>
          Cancel
        </button>
      ) : job.status === 'completed' && job.event_count > 0 ? (
        <button className="btn btn-ghost text-xs px-2 py-1 flex items-center gap-1" onClick={onViewDetections}>
          View <ArrowRight size={12} />
        </button>
      ) : null}
    </div>
  )
}
