import React from 'react'
import { cn } from '../lib/utils'

export default function KpiCard({ label, value, sub, icon: Icon, variant = 'neutral' }) {
  const variants = {
    critical: 'text-red-400 bg-red-500/10',
    warn: 'text-amber-400 bg-amber-500/10',
    info: 'text-blue-400 bg-blue-500/10',
    neutral: 'text-slate-300 bg-slate-500/10',
    brand: 'text-brand-400 bg-brand-500/10',
  }
  return (
    <div className="card flex items-center justify-between">
      <div>
        <div className="text-xs text-slate-400 font-medium">{label}</div>
        <div className="text-2xl font-semibold text-slate-50 mt-1">{value}</div>
        {sub && <div className="text-xs text-slate-500 mt-1">{sub}</div>}
      </div>
      {Icon && (
        <div className={cn('rounded-lg p-2.5', variants[variant])}>
          <Icon size={20} />
        </div>
      )}
    </div>
  )
}
