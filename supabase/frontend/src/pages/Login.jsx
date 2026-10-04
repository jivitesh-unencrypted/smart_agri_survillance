import React, { useState } from 'react'
import { useNavigate, useLocation } from 'react-router-dom'
import { Leaf, Loader2, ShieldCheck, Radio, Cpu, UserPlus, LogIn, AlertTriangle } from 'lucide-react'
import { useAuth } from '../hooks/useAuth.jsx'
import { cn } from '../lib/utils'

const FEATURES = [
  { icon: Cpu, text: 'Local YOLO inference — video never leaves your network' },
  { icon: Radio, text: 'Real-time detection, alerts, and camera status over WebSocket' },
  { icon: ShieldCheck, text: 'Cloud-hosted UI, cloud database, zero cloud AI cost' },
]

export default function Login() {
  const [mode, setMode] = useState('signin') // 'signin' | 'register'

  return (
    <div className="min-h-screen flex bg-slate-950">
      {/* Left brand panel - hidden on small screens, matches the app's sidebar accent color */}
      <div className="hidden lg:flex lg:w-1/2 flex-col justify-between p-12 bg-gradient-to-br from-brand-950 via-slate-950 to-slate-950 border-r border-slate-800">
        <div className="flex items-center gap-2">
          <div className="w-9 h-9 rounded-lg bg-brand-600 flex items-center justify-center">
            <Leaf size={20} className="text-white" />
          </div>
          <div className="leading-tight">
            <div className="text-sm font-semibold text-slate-100">Smart Agri</div>
            <div className="text-[10px] text-slate-500 tracking-wide">SURVEILLANCE</div>
          </div>
        </div>

        <div className="space-y-6 max-w-sm">
          <h1 className="text-3xl font-semibold text-slate-100 leading-tight">
            AI-powered farm security, with detection running on your own hardware.
          </h1>
          <div className="space-y-4">
            {FEATURES.map((f) => (
              <div key={f.text} className="flex items-start gap-3">
                <div className="shrink-0 rounded-lg p-2 bg-brand-500/10 text-brand-400">
                  <f.icon size={16} />
                </div>
                <p className="text-sm text-slate-400 pt-1.5">{f.text}</p>
              </div>
            ))}
          </div>
        </div>

        <p className="text-xs text-slate-600">Local AI · Supabase data · Cloudflare Pages UI</p>
      </div>

      {/* Right form panel */}
      <div className="flex-1 flex items-center justify-center p-4">
        <div className="w-full max-w-sm">
          <div className="flex flex-col items-center mb-6 lg:hidden">
            <div className="w-12 h-12 rounded-xl bg-brand-600 flex items-center justify-center mb-3">
              <Leaf size={24} className="text-white" />
            </div>
            <h1 className="text-lg font-semibold text-slate-100">Smart Agri Surveillance</h1>
            <p className="text-sm text-slate-500">AI-powered farm security</p>
          </div>

          <div className="card">
            <div className="flex rounded-lg bg-slate-800/60 p-1 mb-5">
              <TabButton active={mode === 'signin'} onClick={() => setMode('signin')} icon={LogIn} label="Sign In" />
              <TabButton active={mode === 'register'} onClick={() => setMode('register')} icon={UserPlus} label="Create Account" />
            </div>

            <ConfigWarning />

            {mode === 'signin' ? <SignInForm /> : <RegisterForm />}
          </div>
        </div>
      </div>
    </div>
  )
}

function TabButton({ active, onClick, icon: Icon, label }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        'flex-1 flex items-center justify-center gap-1.5 rounded-md py-1.5 text-sm font-medium transition-colors',
        active ? 'bg-brand-600 text-white' : 'text-slate-400 hover:text-slate-200',
      )}
    >
      <Icon size={14} />
      {label}
    </button>
  )
}

/** Surfaces a missing Supabase configuration instead of a silent spinner. */
function ConfigWarning() {
  const { configError } = useAuth()
  if (!configError) return null
  return (
    <div className="mb-4 rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2">
      <div className="flex items-start gap-2">
        <AlertTriangle size={15} className="mt-0.5 shrink-0 text-amber-400" />
        <p className="text-xs text-amber-300">{configError}</p>
      </div>
    </div>
  )
}

function SignInForm() {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const { login } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()

  const handleSubmit = async (e) => {
    e.preventDefault()
    setError('')
    setLoading(true)
    try {
      await login(email, password)
      const dest = location.state?.from || '/'
      navigate(dest, { replace: true })
    } catch (err) {
      setError(err.detail || err.message || 'Login failed')
    } finally {
      setLoading(false)
    }
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      <div>
        <label className="label">Email</label>
        <input
          type="email"
          className="input"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          placeholder="you@example.com"
          autoFocus
          autoComplete="username"
          required
        />
      </div>
      <div>
        <label className="label">Password</label>
        <input
          type="password"
          className="input"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          autoComplete="current-password"
          required
        />
      </div>
      {error && <p className="text-sm text-red-400">{error}</p>}
      <button type="submit" disabled={loading} className="btn btn-primary w-full">
        {loading && <Loader2 size={16} className="animate-spin" />}
        Sign in
      </button>
      <p className="text-xs text-slate-500 text-center">
        Accounts are managed by Supabase Auth.
      </p>
    </form>
  )
}

function RegisterForm() {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [loading, setLoading] = useState(false)
  const { register } = useAuth()
  const navigate = useNavigate()

  const handleSubmit = async (e) => {
    e.preventDefault()
    setError('')
    setNotice('')

    if (password !== confirmPassword) {
      setError('Passwords do not match')
      return
    }
    if (password.length < 6) {
      setError('Password must be at least 6 characters')
      return
    }

    setLoading(true)
    try {
      const res = await register(email, password)
      if (res?.requiresEmailConfirmation) {
        setNotice('Account created. Check your email to confirm the address, then sign in.')
      } else {
        navigate('/', { replace: true })
      }
    } catch (err) {
      setError(err.detail || err.message || 'Could not create account')
    } finally {
      setLoading(false)
    }
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      <div>
        <label className="label">Email</label>
        <input
          type="email"
          className="input"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          placeholder="you@example.com"
          autoFocus
          autoComplete="username"
          required
        />
      </div>
      <div>
        <label className="label">Password</label>
        <input
          type="password"
          className="input"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          autoComplete="new-password"
          required
          minLength={6}
          placeholder="At least 6 characters"
        />
      </div>
      <div>
        <label className="label">Confirm Password</label>
        <input
          type="password"
          className="input"
          value={confirmPassword}
          onChange={(e) => setConfirmPassword(e.target.value)}
          autoComplete="new-password"
          required
        />
      </div>
      {error && <p className="text-sm text-red-400">{error}</p>}
      {notice && <p className="text-sm text-emerald-400">{notice}</p>}
      <button type="submit" disabled={loading} className="btn btn-primary w-full">
        {loading && <Loader2 size={16} className="animate-spin" />}
        Create account
      </button>
      <p className="text-xs text-slate-500 text-center">
        No approval needed — every account has the same access, exactly as before.
      </p>
    </form>
  )
}
