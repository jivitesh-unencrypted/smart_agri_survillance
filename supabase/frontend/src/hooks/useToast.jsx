import React, { createContext, useCallback, useContext, useState } from 'react'
import { CheckCircle2, XCircle, Info, X } from 'lucide-react'
import { cn } from '../lib/utils'

const ToastContext = createContext(null)

let idCounter = 0

export function ToastProvider({ children }) {
  const [toasts, setToasts] = useState([])

  const dismiss = useCallback((id) => {
    setToasts((prev) => prev.filter((t) => t.id !== id))
  }, [])

  const push = useCallback((message, variant = 'info', timeout = 4000) => {
    const id = ++idCounter
    setToasts((prev) => [...prev, { id, message, variant }])
    if (timeout) setTimeout(() => dismiss(id), timeout)
  }, [dismiss])

  const toast = {
    success: (msg) => push(msg, 'success'),
    error: (msg) => push(msg, 'error'),
    info: (msg) => push(msg, 'info'),
  }

  return (
    <ToastContext.Provider value={toast}>
      {children}
      <div className="fixed bottom-4 right-4 z-[100] flex flex-col gap-2 w-80">
        {toasts.map((t) => (
          <div
            key={t.id}
            className={cn(
              'flex items-start gap-2 rounded-lg border px-3 py-2.5 shadow-lg backdrop-blur-sm text-sm',
              t.variant === 'success' && 'bg-brand-900/90 border-brand-700 text-brand-100',
              t.variant === 'error' && 'bg-red-950/90 border-red-800 text-red-100',
              t.variant === 'info' && 'bg-slate-900/90 border-slate-700 text-slate-100',
            )}
          >
            {t.variant === 'success' && <CheckCircle2 size={18} className="mt-0.5 shrink-0" />}
            {t.variant === 'error' && <XCircle size={18} className="mt-0.5 shrink-0" />}
            {t.variant === 'info' && <Info size={18} className="mt-0.5 shrink-0" />}
            <span className="flex-1">{t.message}</span>
            <button onClick={() => dismiss(t.id)} className="opacity-60 hover:opacity-100">
              <X size={14} />
            </button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  )
}

export function useToast() {
  const ctx = useContext(ToastContext)
  if (!ctx) throw new Error('useToast must be used within ToastProvider')
  return ctx
}
