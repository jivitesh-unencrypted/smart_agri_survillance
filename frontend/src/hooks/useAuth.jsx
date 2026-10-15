import React, {
  createContext,
  useContext,
  useEffect,
  useRef,
  useState,
  useCallback,
  useMemo,
} from 'react'
import { supabase, setToken } from '../lib/api'
import { supabaseConfigError } from '../lib/supabase'
import { UserScopeContext } from '../lib/userScope'

const AuthContext = createContext(null)

/**
 * Supabase Auth session provider.
 *
 * Replaces the old local username/password + JWT flow. supabase-js owns
 * the session (persisted in localStorage under its own key and refreshed
 * automatically), so `user` below is just a projection of the current
 * Supabase session plus the application profile row in `public.users`.
 *
 * `useAuth()` consumers (Layout, Login, ProtectedRoute) are unchanged.
 */
export function AuthProvider({ children }) {
  const [user, setUser] = useState(null)
  const [loading, setLoading] = useState(true)
  const [configError, setConfigError] = useState(null)

  /**
   * Monotonic generation counter guarding against a stale in-flight refresh.
   *
   * `refresh` awaits two network calls (the session, then the profile row).
   * If a refresh started for user A is still awaiting when the user logs out
   * and logs in as user B, A's profile response can resolve AFTER B's and
   * overwrite `user` with A's details - the exact "logged out of A, logged
   * into B, still seeing A" symptom. Each refresh captures the generation it
   * started in and discards its own result if a newer one has begun, so only
   * the newest refresh is ever allowed to commit state.
   */
  const generationRef = useRef(0)

  const refresh = useCallback(async () => {
    const generation = ++generationRef.current
    const isCurrent = () => generationRef.current === generation

    const err = supabaseConfigError()
    setConfigError(err)
    if (err || !supabase) {
      if (!isCurrent()) return
      setUser(null)
      setLoading(false)
      return
    }
    try {
      const { data } = await supabase.auth.getSession()
      const session = data?.session
      if (!isCurrent()) return
      if (!session) {
        setToken(null)
        setUser(null)
        setLoading(false)
        return
      }
      // Mirror the token so <img src> / the WebSocket can authenticate.
      setToken(session.access_token)

      const profile = await supabase
        .from('users')
        .select('*')
        .eq('id', session.user.id)
        .maybeSingle()

      // The user may have been replaced while the profile query was in
      // flight; committing now would resurrect the previous account.
      if (!isCurrent()) return

      setUser({
        id: session.user.id,
        email: session.user.email,
        username:
          profile.data?.username ||
          session.user.user_metadata?.username ||
          session.user.email,
        name: profile.data?.name ?? null,
        role: profile.data?.role || 'user',
        is_active: profile.data ? profile.data.is_active !== false : true,
      })
    } catch {
      if (!isCurrent()) return
      setToken(null)
      setUser(null)
    } finally {
      if (isCurrent()) setLoading(false)
    }
  }, [])

  useEffect(() => {
    refresh()
  }, [refresh])

  // Keep the session, the token mirror and `user` in sync on sign-in,
  // sign-out and automatic token refresh.
  useEffect(() => {
    if (!supabase) return undefined

    const { data: sub } = supabase.auth.onAuthStateChange((event, session) => {
      if (event === 'SIGNED_OUT' || !session) {
        setToken(null)
        setUser(null)
        setLoading(false)
        return
      }
      if (event === 'SIGNED_IN' || event === 'INITIAL_SESSION' || event === 'TOKEN_REFRESHED') {
        setToken(session.access_token)
        refresh()
      }
    })

    return () => sub.subscription.unsubscribe()
  }, [refresh])

  const login = useCallback(async (email, password) => {
    const err = supabaseConfigError()
    if (err) throw new Error(err)

    const { data, error } = await supabase.auth.signInWithPassword({
      email: String(email || '').trim(),
      password,
    })
    if (error) throw new Error(error.message)
    return data
  }, [])

  /**
   * Self-registration, same policy as the original app: any visitor can
   * create an account and gets the same access as everyone else.
   *
   * Supabase only auto-confirms email when the project has "Confirm email"
   * disabled; if confirmation is on, `signUp` returns a user with no
   * session and the user must confirm before signing in.
   */
  const register = useCallback(async (email, password, options = {}) => {
    const err = supabaseConfigError()
    if (err) throw new Error(err)

    const { data, error } = await supabase.auth.signUp({
      email: String(email || '').trim(),
      password,
      options: {
        data: { username: String(email || '').trim().split('@')[0] },
      },
    })
    if (error) throw new Error(error.message)

    if (!data.session) {
      return { ...data, requiresEmailConfirmation: true }
    }
    await refresh()
    return data
  }, [refresh])

  const logout = useCallback(async () => {
    // Invalidate any refresh already in flight BEFORE touching Supabase, so
    // a late response for the outgoing account cannot repopulate `user`.
    generationRef.current += 1
    if (supabase) {
      try {
        await supabase.auth.signOut()
      } catch {
        /* clearing the local session is what matters */
      }
    }
    setToken(null)
    setUser(null)
    setLoading(false)
    // Bump a key every page keys its data fetches off, so no component can
    // keep rendering the previous account's already-fetched rows.
    try {
      window.dispatchEvent(new Event('auth:logout'))
    } catch {
      /* older browsers */
    }
  }, [])

  /**
   * Identity epoch - increments on sign-in, sign-out and any later change of
   * account, so data fetches can be re-scoped instead of rendering rows that
   * were fetched for a different user.
   */
  const [epoch, setEpoch] = useState(0)
  const lastIdentityRef = useRef(null)

  const bumpEpoch = useCallback(() => setEpoch((n) => n + 1), [])

  // `user.id` is the identity. When it changes (or becomes null on logout)
  // every consumer's cached rows belong to the previous account.
  useEffect(() => {
    const identity = user?.id ?? null
    if (lastIdentityRef.current !== identity) {
      lastIdentityRef.current = identity
      setEpoch((n) => n + 1)
    }
  }, [user?.id])

  const value = useMemo(
    () => ({
      user,
      epoch,
      loading,
      configError,
      login,
      register,
      logout,
      refresh,
      isAuthenticated: !!user,
    }),
    [user, epoch, loading, configError, login, register, logout, refresh],
  )

  return (
    <UserScopeContext.Provider value={epoch}>
      <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
    </UserScopeContext.Provider>
  )
}

export function useAuth() {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used within AuthProvider')
  return ctx
}
