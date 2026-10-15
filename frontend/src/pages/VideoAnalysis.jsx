import React, { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { UploadCloud, FileVideo, Loader2, CheckCircle2, XCircle, Ban, ArrowRight } from 'lucide-react'
import { formatDateTime } from '../lib/utils'
import { useToast } from '../hooks/useToast.jsx'
import { useUserScope } from '../lib/userScope'
import { supabase } from '../lib/api'

const POLL_INTERVAL_MS = 2500
const MODAL_TRIGGER_URL = (import.meta.env.VITE_MODAL_TRIGGER_URL || '').trim()

export default function VideoAnalysis() {
  const [jobs, setJobs] = useState([])
  const [uploading, setUploading] = useState(false)
  const [dragOver, setDragOver] = useState(false)
  const fileInputRef = useRef(null)
  const toast = useToast()
  const navigate = useNavigate()
  const epoch = useUserScope()

  const loadJobs = useCallback(async () => {
    if (!supabase) return
    try {
      const { data: sessionData } = await supabase.auth.getSession()
      const userId = sessionData?.session?.user?.id
      if (!userId) { setJobs([]); return }
      const { data, error } = await supabase
        .from('video_analysis_jobs')
        .select('*')
        .eq('user_id', userId)
        .order('created_at', { ascending: false })
        .limit(50)
      if (error) throw error
      setJobs(data || [])
    } catch {
      // Keep the last successful result while polling.
    }
  }, [])

  useEffect(() => {
    setJobs([])
    loadJobs()
    const timer = setInterval(() => {
      setJobs(current => {
        if (current.some(j => j.status === 'queued' || j.status === 'processing')) loadJobs()
        return current
      })
    }, POLL_INTERVAL_MS)
    return () => clearInterval(timer)
  }, [loadJobs, epoch])

  const handleFile = async (file) => {
    if (!file) return
    const allowed = ['.mp4', '.avi', '.mov', '.mkv', '.webm', '.m4v']
    const ext = '.' + (file.name.split('.').pop() || '').toLowerCase()
    if (!allowed.includes(ext)) {
      toast.error(`Unsupported format ${ext}. Allowed: ${allowed.join(', ')}`)
      return
    }
    if (!supabase) {
      toast.error('Supabase is not configured.')
      return
    }
    if (!MODAL_TRIGGER_URL) {
      toast.error('Set VITE_MODAL_TRIGGER_URL in Vercel environment variables, then redeploy.')
      return
    }

    setUploading(true)
    let uploadedPath = null
    let createdJobId = null
    try {
      const { data: sessionData, error: sessionError } = await supabase.auth.getSession()
      if (sessionError) throw sessionError
      const session = sessionData?.session
      if (!session?.access_token || !session.user?.id) {
        throw new Error('Please sign in again before uploading.')
      }

      const userId = session.user.id
      const safeName = file.name.replace(/[^\w.\- ]/g, '_').slice(0, 140)
      uploadedPath = `${userId}/${crypto.randomUUID()}${ext}`
      const contentType = file.type || ({
        '.mp4': 'video/mp4', '.m4v': 'video/x-m4v', '.avi': 'video/x-msvideo',
        '.mov': 'video/quicktime', '.mkv': 'video/x-matroska', '.webm': 'video/webm',
      }[ext] || 'application/octet-stream')

      const { error: storageError } = await supabase.storage
        .from('videos')
        .upload(uploadedPath, file, { contentType, upsert: false })
      if (storageError) throw storageError

      const { data: job, error: jobError } = await supabase
        .from('video_analysis_jobs')
        .insert({
          user_id: userId,
          original_filename: safeName,
          status: 'queued',
          client_job_id: crypto.randomUUID(),
          video_path: uploadedPath,
        })
        .select('*')
        .single()
      if (jobError) throw jobError
      createdJobId = job.id

      const response = await fetch(MODAL_TRIGGER_URL, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${session.access_token}`,
        },
        body: JSON.stringify({ job_id: job.id }),
      })
      if (!response.ok) {
        let detail = 'Modal trigger failed'
        try { detail = (await response.json()).detail || detail } catch {}
        throw new Error(`${detail}. The uploaded job may remain queued; refresh and check its status.`)
      }

      toast.success(`${safeName} uploaded — analysis queued`)
      await loadJobs()
    } catch (err) {
      // Keep uploaded data for diagnosis/retry rather than deleting a job that may already be queued.
      toast.error(err?.message || 'Upload or analysis trigger failed')
      await loadJobs()
    } finally {
      setUploading(false)
      if (fileInputRef.current) fileInputRef.current.value = ''
    }
  }

  const handleCancel = async (job) => {
    if (!supabase) return
    try {
      const { error } = await supabase
        .from('video_analysis_jobs')
        .update({ cancel_requested: true })
        .eq('id', job.id)
      if (error) throw error
      toast.info('Cancellation requested')
      loadJobs()
    } catch (err) {
      toast.error(err?.message || 'Could not cancel job')
    }
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold text-slate-100">Video Analysis</h1>
        <p className="text-sm text-slate-500">Upload a recorded video for YOLO analysis on Modal.</p>
      </div>

      <div
        className={`card border-dashed border-2 text-center py-10 transition-colors cursor-pointer ${
          dragOver ? 'border-brand-500 bg-brand-500/5' : 'border-slate-700'
        }`}
        onClick={() => !uploading && fileInputRef.current?.click()}
        onDragOver={(e) => { e.preventDefault(); setDragOver(true) }}
        onDragLeave={() => setDragOver(false)}
        onDrop={(e) => {
          e.preventDefault()
          setDragOver(false)
          if (!uploading && e.dataTransfer.files?.[0]) handleFile(e.dataTransfer.files[0])
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
            <p className="text-sm text-slate-300">Uploading and starting analysis…</p>
          </>
        ) : (
          <>
            <UploadCloud className="mx-auto mb-3 text-slate-500" size={32} />
            <p className="text-sm text-slate-300 font-medium">Click to select or drag & drop a video</p>
            <p className="text-xs text-slate-500 mt-1">MP4, AVI, MOV, MKV, WEBM, M4V</p>
          </>
        )}
      </div>

      <div className="card">
        <div className="card-title">Analysis Jobs</div>
        {jobs.length === 0 ? (
          <p className="text-sm text-slate-500 py-4">No videos analyzed yet. Upload one above to get started.</p>
        ) : (
          <div className="space-y-2">
            {jobs.map(job => (
              <JobRow
                key={job.id}
                job={job}
                onCancel={handleCancel}
                onViewDetections={() => navigate(`/detections?source=${encodeURIComponent('Video Analysis')}`)}
              />
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
          <>
            <div className="w-full h-1.5 bg-slate-800 rounded-full mt-1.5 overflow-hidden">
              <div className="h-full bg-brand-500 transition-all" style={{ width: `${Math.max(3, Number(job.progress_percent) || 0)}%` }} />
            </div>
            <p className="text-xs text-slate-500 mt-1">{job.processed_frames || 0} / {job.total_frames || '?'} frames</p>
          </>
        ) : (
          <p className="text-xs text-slate-500 mt-0.5">
            {formatDateTime(job.completed_at || job.created_at)}
            {job.status === 'completed' && ` · ${job.event_count || 0} detection event${job.event_count === 1 ? '' : 's'}`}
            {job.status === 'failed' && job.error_message && ` · ${job.error_message}`}
          </p>
        )}
      </div>
      {isActive ? (
        <button className="btn btn-secondary text-xs px-2 py-1" onClick={() => onCancel(job)}>Cancel</button>
      ) : job.status === 'completed' && job.event_count > 0 ? (
        <button className="btn btn-ghost text-xs px-2 py-1 flex items-center gap-1" onClick={onViewDetections}>
          View <ArrowRight size={12} />
        </button>
      ) : null}
    </div>
  )
}
