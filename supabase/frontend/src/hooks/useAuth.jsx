import React, { createContext, useContext, useEffect, useState, useCallback, useMemo } from 'react'
import { supabase, setToken } from '../lib/api'
import { supabaseConfigError } from '../lib/supabase'

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

  const refresh = useCallback(async () => {
    const err = supabaseConfigError()
    setConfigError(err)
    if (err || !supabase) {
      setUser(null)
      setLoading(false)
      return
    }
    try {
      const { data } = await supabase.auth.getSession()
      const session = data?.session
      if (!session) {
        setToken(null)
        setUser(null)
        return
      }
      // Mirror the token so <img src> / the WebSocket can authenticate.
      setToken(session.access_token)

      const profile = await supabase
        .from('users')
        .select('*')
        .eq('id', session.user.id)
        .maybeSingle()

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
      setToken(null)
      setUser(null)
    } finally {
      setLoading(false)
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
    if (supabase) {
      try {
        await supabase.auth.signOut()
      } catch {
        /* clearing the local session is what matters */
      }
    }
    setToken(null)
    setUser(null)
  }, [])

  const value = useMemo(
    () => ({
      user,
      loading,
      configError,
      login,
      register,
      logout,
      refresh,
      isAuthenticated: !!user,
    }),
    [user, loading, configError, login, register, logout, refresh],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth() {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used within AuthProvider')
  return ctx
}
