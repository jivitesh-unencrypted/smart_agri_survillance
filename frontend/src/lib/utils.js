export function cn(...classes) {
  return classes.filter(Boolean).join(' ')
}

// Backend timestamps are always UTC. After the backend fix they arrive
// with an explicit offset (e.g. "...+00:00"), but this stays as a safety
// net: an ISO datetime string with NO timezone marker is treated as
// local time by the JS Date parser per spec, which would silently shift
// every timestamp by the browser's UTC offset. Anything already carrying
// an explicit Z/offset is left untouched.
function toUtcDate(isoString) {
  if (!isoString) return null
  const hasOffset = /Z$|[+-]\d{2}:?\d{2}$/.test(isoString)
  return new Date(hasOffset ? isoString : isoString + 'Z')
}

export function formatTime(isoString) {
  if (!isoString) return '—'
  try {
    return toUtcDate(isoString).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
  } catch {
    return '—'
  }
}

export function formatDateTime(isoString) {
  if (!isoString) return '—'
  try {
    return toUtcDate(isoString).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' })
  } catch {
    return '—'
  }
}

export function formatDuration(seconds) {
  if (seconds === null || seconds === undefined) return '—'
  const s = Math.max(0, Math.round(seconds))
  const m = Math.floor(s / 60)
  const r = s % 60
  return `${m}:${String(r).padStart(2, '0')}`
}

export function categoryColor(category) {
  switch (category) {
    case 'Human':
      return { text: 'text-red-400', bg: 'bg-red-500/10', border: 'border-red-500/30', dot: 'bg-red-500' }
    case 'Animals':
      return { text: 'text-amber-400', bg: 'bg-amber-500/10', border: 'border-amber-500/30', dot: 'bg-amber-500' }
    case 'Vehicles':
      return { text: 'text-blue-400', bg: 'bg-blue-500/10', border: 'border-blue-500/30', dot: 'bg-blue-500' }
    default:
      return { text: 'text-slate-400', bg: 'bg-slate-500/10', border: 'border-slate-500/30', dot: 'bg-slate-500' }
  }
}

export function severityColor(severity) {
  switch ((severity || '').toLowerCase()) {
    case 'critical':
      return { text: 'text-red-400', bg: 'bg-red-500/10', border: 'border-red-500/30' }
    case 'warning':
      return { text: 'text-amber-400', bg: 'bg-amber-500/10', border: 'border-amber-500/30' }
    case 'info':
      return { text: 'text-blue-400', bg: 'bg-blue-500/10', border: 'border-blue-500/30' }
    default:
      return { text: 'text-slate-400', bg: 'bg-slate-500/10', border: 'border-slate-500/30' }
  }
}
