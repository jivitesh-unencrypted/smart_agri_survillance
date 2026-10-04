/**
 * Dual data layer.
 *
 * After the Supabase migration this project has TWO data sources, and this
 * module is the single place that knows which is which:
 *
 *   1. Supabase (Postgres + Storage + Auth) - holds ALL the data the UI
 *      shows. Detections, alerts, cameras, analytics and settings are read
 *      and written DIRECTLY from the browser using the anon key, so the
 *      dashboard, detection logs, alerts and analytics keep working even
 *      when the local AI server is switched off. Every query is small
 *      metadata, never image bytes (those live in Storage) and never
 *      camera credentials.
 *
 *   2. The local AI server - holds the things that can only exist where
 *      the cameras and the YOLO weights are: starting/stopping streams,
 *      testing a camera, the MJPEG live feed, the WebSocket telemetry,
 *      video-file analysis, and any camera write that involves a password
 *      (passwords go to the service-role-only `camera_secrets` table, so
 *      they must be proxied through the server).
 *
 * The call sites in `src/pages/*` are unchanged: they still call
 * `api.get('/api/detections?...')` etc. and receive exactly the same JSON
 * shapes they did when a single FastAPI process owned everything.
 */
import { supabase, SUPABASE_URL, SUPABASE_STORAGE_BUCKET, toDetail } from './supabase'

// ---------------------------------------------------------------------
// Configuration
// ---------------------------------------------------------------------
export const AI_SERVER_URL = (
  import.meta.env.VITE_AI_SERVER_URL || 'http://localhost:8000'
).replace(/\/+$/, '')

export const WS_URL = import.meta.env.VITE_WS_URL || 'ws://localhost:8000/ws'

export class ApiError extends Error {
  constructor(message, status, detail) {
    super(message)
    this.status = status
    this.detail = detail
  }
}

/**
 * Access-token mirror.
 *
 * supabase-js owns the real session, but `<img src>` and the WebSocket
 * handshake need a token synchronously, so the active token is cached here
 * and kept in sync by `useAuth`. This is the SAME public JWT the browser
 * already holds - never a service-role key.
 */
let cachedToken = null

export function getToken() {
  if (cachedToken) return cachedToken
  try {
    return localStorage.getItem('access_token') || null
  } catch {
    return null
  }
}

export function setToken(token) {
  cachedToken = token || null
  try {
    if (token) localStorage.setItem('access_token', token)
    else localStorage.removeItem('access_token')
  } catch {
    /* private browsing - in-memory token still works for this tab */
  }
}

// ---------------------------------------------------------------------
// AI server transport
// ---------------------------------------------------------------------
async function request(path, options = {}) {
  const token = getToken()
  const headers = {
    ...(options.body && !(options.body instanceof FormData)
      ? { 'Content-Type': 'application/json' }
      : {}),
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
    ...(options.headers || {}),
  }

  let res
  try {
    res = await fetch(`${AI_SERVER_URL}${path}`, { ...options, headers })
  } catch {
    throw new ApiError(
      'Could not reach the local AI server at ' + AI_SERVER_URL +
        '. Start it with ai-server\\start.bat. Camera streaming and video analysis need it; ' +
        'the dashboard, detection logs, alerts and analytics do not.',
      0,
      null,
    )
  }

  if (res.status === 401) {
    setToken(null)
    if (!path.includes('/auth/login')) {
      supabase?.auth.getSession().then(({ data }) => {
        if (!data?.session) window.location.href = '/login'
      })
    }
  }

  if (!res.ok) {
    let detail = null
    try {
      const body = await res.json()
      detail = body.detail
    } catch {
      /* ignore */
    }
    // FastAPI validation errors arrive as an array of objects rather than
    // a plain string; normalise both shapes so callers can just show it.
    if (Array.isArray(detail)) {
      detail = detail.map((d) => d.msg || JSON.stringify(d)).join('; ')
    }
    throw new ApiError(detail || `Request failed (${res.status})`, res.status, detail)
  }

  if (res.status === 204) return null
  const contentType = res.headers.get('content-type') || ''
  if (contentType.includes('application/json')) return res.json()
  return res.text()
}

// ---------------------------------------------------------------------
// Supabase transport helpers
// ---------------------------------------------------------------------
function requireSupabase() {
  if (!supabase) {
    throw new ApiError(
      'Supabase is not configured. Set VITE_SUPABASE_URL and VITE_SUPABASE_ANON_KEY ' +
        'in frontend/.env (and in the Cloudflare Pages environment variables).',
      0,
      null,
    )
  }
  return supabase
}

/** Runs a query and unwraps `{ data, error }` into the ApiError contract. */
async function unwrap(promise) {
  const { data, error } = await promise
  if (error) {
    throw new ApiError(toDetail(error) || 'Supabase request failed', 0, toDetail(error))
  }
  return data
}

const num = (v, fallback = 0) => (v === null || v === undefined ? fallback : Number(v))

// ---------------------------------------------------------------------
// Row -> API shape mapping
// (kept explicit so a schema change surfaces as a TypeScript-style
//  surprise here rather than as `undefined` scattered through the UI)
// ---------------------------------------------------------------------
function detectionToOut(row) {
  return {
    id: row.id ?? null,
    client_event_id: row.client_event_id ?? null,
    source: row.source,
    camera_id: row.camera_id ?? null,
    camera_name: row.camera_name ?? null,
    location: row.location ?? null,
    object_name: row.object_name,
    category: row.category,
    severity: row.severity,
    confidence: num(row.confidence),
    avg_confidence: num(row.avg_confidence),
    frame_count: num(row.frame_count, 1),
    start_time: row.start_time,
    end_time: row.end_time,
    duration_seconds: num(row.duration_seconds),
    snapshot_path: row.snapshot_path ?? null,
    bounding_box: row.bounding_box ?? null,
    created_at: row.created_at,
  }
}

function alertToOut(row) {
  return {
    id: row.id ?? null,
    detection_id: row.detection_id ?? null,
    camera_id: row.camera_id ?? null,
    camera_name: row.camera_name ?? null,
    category: row.category ?? null,
    severity: row.severity,
    alert_type: row.alert_type ?? null,
    title: row.title,
    message: row.message,
    acknowledged: !!row.acknowledged,
    resolved: !!row.resolved,
    status: row.status ?? null,
    created_at: row.created_at,
    resolved_at: row.resolved_at ?? null,
  }
}

function settingsToOut(row) {
  return {
    confidence_threshold: num(row.confidence_threshold, 0.25),
    iou_threshold: num(row.iou_threshold, 0.45),
    inference_interval_ms: num(row.inference_interval_ms, 400),
    frame_skip: num(row.frame_skip, 2),
    event_cooldown_seconds: num(row.event_cooldown_seconds, 8),
    alerts_enabled: row.alerts_enabled !== false,
    sound_enabled: row.sound_enabled !== false,
    alert_on_human: row.alert_on_human !== false,
    alert_on_animal: row.alert_on_animal !== false,
    alert_on_vehicle: row.alert_on_vehicle === true,
    snapshot_on_event: row.snapshot_on_event !== false,
    max_snapshot_age_days: num(row.max_snapshot_age_days, 30),
    sync_enabled: row.sync_enabled !== false,
    upload_snapshots_to_cloud: row.upload_snapshots_to_cloud !== false,
    stream_max_width: row.stream_max_width ?? null,
    model_path: row.model_path ?? null,
    updated_at: row.updated_at ?? null,
  }
}

/**
 * Cloud-side camera shape. Used when the AI server cannot be reached, so
 * the dashboard keeps listing cameras. `stream_url_display` is the raw
 * (credential-free) `stream_url`; `has_password` is unknown from here
 * because the secret table is deliberately unreachable with the anon key.
 */
function cameraToOutFromCloud(row) {
  return {
    id: row.id,
    camera_code: row.camera_code,
    name: row.name,
    location: row.location ?? '',
    description: row.description ?? '',
    zone: row.zone ?? '',
    camera_type: row.camera_type,
    connection_type: row.connection_type,
    device_index: row.device_index ?? null,
    stream_url_display:
      row.connection_type === 'local_webcam'
        ? `Device index ${row.device_index ?? 0}`
        : row.stream_url ?? '',
    username: row.username ?? '',
    has_password: false,
    enabled: row.enabled,
    status: row.status,
    last_fps: row.last_fps ?? null,
    last_latency_ms: row.last_latency_ms ?? null,
    last_seen_at: row.last_seen_at ?? null,
    last_error: row.last_error ?? null,
    created_at: row.created_at,
    updated_at: row.updated_at,
  }
}

// ---------------------------------------------------------------------
// Supabase-backed endpoints (mirroring the original FastAPI responses)
// ---------------------------------------------------------------------
const db = {
  async '/api/auth/me'() {
    const { data: sessionData } = await requireSupabase().auth.getSession()
    const session = sessionData?.session
    if (!session) throw new ApiError('Not authenticated', 401, 'Not authenticated')
    const profile = await unwrap(
      requireSupabase().from('users').select('*').eq('id', session.user.id).maybeSingle(),
    )
    const fallbackName = session.user.user_metadata?.username || session.user.email
    return {
      id: session.user.id,
      username: profile?.username || fallbackName,
      name: profile?.name ?? null,
      email: session.user.email,
      role: profile?.role || 'user',
      is_active: profile ? profile.is_active !== false : true,
    }
  },

  async '/api/detections/summary'() {
    const raw = await unwrap(requireSupabase().rpc('get_detection_summary'))
    return {
      total_events: num(raw?.total_events),
      human_events: num(raw?.human_events),
      animal_events: num(raw?.animal_events),
      vehicle_events: num(raw?.vehicle_events),
      other_events: num(raw?.other_events),
    }
  },

  async '/api/analytics'() {
    return unwrap(requireSupabase().rpc('get_analytics'))
  },

  async '/api/settings'() {
    const row = await unwrap(requireSupabase().from('settings').select('*').eq('id', 1).maybeSingle())
    return settingsToOut(row || { id: 1 })
  },

  async '/api/alerts'(query) {
    let q = requireSupabase().from('alerts').select('*')
    if (query.resolved !== undefined) q = q.eq('resolved', query.resolved === 'true')
    if (query.severity) q = q.eq('severity', query.severity)
    if (query.camera_id) q = q.eq('camera_id', Number(query.camera_id))
    const limit = query.limit ? Number(query.limit) : 100
    const rows = await unwrap(q.order('created_at', { ascending: false }).limit(limit))
    const items = (rows || []).map(alertToOut)
    return { items, total: items.length }
  },

  async '/api/detections'(query) {
    const page = Math.max(1, Number(query.page) || 1)
    const pageSize = Math.min(200, Math.max(1, Number(query.page_size) || 25))

    let q = requireSupabase().from('detections').select('*', { count: 'exact' })
    if (query.source) q = q.eq('source', query.source)
    if (query.category) q = q.eq('category', query.category)
    if (query.severity) q = q.eq('severity', query.severity)
    if (query.camera_id) q = q.eq('camera_id', Number(query.camera_id))
    if (query.date_from) q = q.gte('created_at', query.date_from)
    if (query.date_to) q = q.lte('created_at', query.date_to)
    if (query.search) q = q.ilike('object_name', `%${String(query.search).toLowerCase()}%`)

    const rows = await unwrap(
      q.order('created_at', { ascending: false })
        .range((page - 1) * pageSize, page * pageSize - 1),
    )
    return {
      items: (rows || []).map(detectionToOut),
      total: rows?.length ?? 0,
      page,
      page_size: pageSize,
    }
  },
}

// ---------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------
function splitPath(path) {
  const [rawPath, rawQuery = ''] = path.split('?')
  const query = {}
  new URLSearchParams(rawQuery).forEach((value, key) => {
    query[key] = value
  })
  return { path: rawPath, query }
}

async function get(path) {
  const { path: p, query } = splitPath(path)

  if (db[p]) return db[p](query)

  if (p === '/api/cameras') {
    // Prefer the AI server: it returns `stream_url_display` and
    // `has_password`, which the cloud copy cannot know. Fall back to the
    // Supabase table so the dashboard still lists cameras offline.
    try {
      return await request(path, { method: 'GET' })
    } catch (err) {
      if (err.status !== 0) throw err
      const rows = await unwrap(requireSupabase().from('cameras').select('*').order('id'))
      return (rows || []).map(cameraToOutFromCloud)
    }
  }

  if (p === '/api/system/status') {
    try {
      return await request(path, { method: 'GET' })
    } catch (err) {
      if (err.status !== 0) throw err
      // AI server offline: report what the cloud can tell us honestly.
      return offlineSystemStatus()
    }
  }

  return request(path, { method: 'GET' })
}

async function offlineSystemStatus() {
  let health = { database_ok: false, total_cameras: 0, active_cameras: 0 }
  try {
    health = (await unwrap(requireSupabase().rpc('get_system_health'))) || health
  } catch {
    /* leave the pessimistic defaults */
  }
  return {
    model_loaded: false,
    model_name: 'unavailable',
    model_error: 'The local AI server is not running - camera streaming is unavailable.',
    total_cameras: num(health.total_cameras),
    active_cameras: num(health.active_cameras),
    database_ok: !!health.database_ok,
    uptime_seconds: 0,
    cloud_configured: true,
    cloud_online: !!health.database_ok,
    cloud_last_error: null,
    sync_enabled: false,
    pending_sync_events: 0,
  }
}

const post = (path, body) => request(path, {
  method: 'POST',
  body: body !== undefined ? JSON.stringify(body) : undefined,
})

async function put(path, body) {
  // Settings live in Supabase and carry no secrets.
  const { path: p } = splitPath(path)
  if (p === '/api/settings') {
    const row = await unwrap(
      requireSupabase().from('settings')
        .update({ ...body, id: 1, updated_at: new Date().toISOString() })
        .eq('id', 1)
        .select()
        .single(),
    )
    return settingsToOut(row)
  }
  // Camera writes (which may carry a password) are proxied through the
  // local AI server, which holds the service-role key.
  return request(path, { method: 'PUT', body: JSON.stringify(body) })
}

async function patch(path, body) {
  const { path: p } = splitPath(path)
  const alertMatch = p.match(/^\/api\/alerts\/(\d+)$/)
  if (alertMatch) {
    const updates = {}
    if (body.acknowledged !== undefined) updates.acknowledged = !!body.acknowledged
    if (body.resolved !== undefined) {
      updates.resolved = !!body.resolved
      updates.resolved_at = body.resolved ? new Date().toISOString() : null
    }
    const row = await unwrap(
      requireSupabase().from('alerts').update(updates).eq('id', Number(alertMatch[1])).select().single(),
    )
    // `status` is a STORED generated column, so it is already consistent.
    return alertToOut(row)
  }
  return request(path, { method: 'PATCH', body: JSON.stringify(body) })
}

const del = (path) => request(path, { method: 'DELETE' })
const upload = (path, formData) => request(path, { method: 'POST', body: formData })

export const api = { get, post, put, patch, delete: del, upload }

// ---------------------------------------------------------------------
// Media URLs
// ---------------------------------------------------------------------
function encodeKey(key) {
  return String(key)
    .split('/')
    .map((segment) => encodeURIComponent(segment))
    .join('/')
}

/**
 * Preferred snapshot URL: Supabase Storage.
 *
 * The `snapshots` bucket is public-read and snapshots are pushed there by
 * the AI server, so a `<img src>` works without any backend round-trip.
 */
export function mediaUrl(relativeStoragePath) {
  if (!relativeStoragePath) return null
  if (!SUPABASE_URL) return localMediaUrl(relativeStoragePath)
  return `${SUPABASE_URL}/storage/v1/object/public/${SUPABASE_STORAGE_BUCKET}/${encodeKey(relativeStoragePath)}`
}

/**
 * Fallback snapshot URL served by the local AI server.
 *
 * This is what keeps the UI complete while offline: an event that is still
 * sitting in the local outbox has its JPEG on disk but nothing in Storage
 * yet, so `SnapshotImage` transparently retries against this URL.
 */
export function localMediaUrl(relativeStoragePath) {
  if (!relativeStoragePath) return null
  return `${AI_SERVER_URL}/storage/${encodeKey(relativeStoragePath)}`
}

/**
 * MJPEG stream URL. The browser cannot attach an Authorization header to
 * an `<img src>`, so the same public JWT is passed as `?token=`, which the
 * AI server accepts for this route only.
 */
export function streamUrl(cameraId) {
  const token = getToken()
  const base = `${AI_SERVER_URL}/api/cameras/${cameraId}/stream`
  return token ? `${base}?token=${encodeURIComponent(token)}` : base
}

export { supabase }
