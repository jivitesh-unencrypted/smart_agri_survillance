import React from 'react'
import { cn } from '../lib/utils'

export default function StatusBadge({ status }) {
  const map = {
    online: { label: 'Online', dot: 'bg-emerald-500', text: 'text-emerald-400', bg: 'bg-emerald-500/10' },
    offline: { label: 'Offline', dot: 'bg-red-500', text: 'text-red-400', bg: 'bg-red-500/10' },
    testing: { label: 'Testing…', dot: 'bg-amber-500', text: 'text-amber-400', bg: 'bg-amber-500/10' },
    unknown: { label: 'Not tested', dot: 'bg-slate-500', text: 'text-slate-400', bg: 'bg-slate-500/10' },
  }
  const cfg = map[status] || map.unknown
  return (
    <span className={cn('badge', cfg.bg, cfg.text)}>
      <span className={cn('w-1.5 h-1.5 rounded-full', cfg.dot, status === 'online' && 'pulse-dot')} />
      {cfg.label}
    </span>
  )
}
