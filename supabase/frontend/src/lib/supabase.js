/**
 * Supabase browser client.
 *
 * This module holds the ONLY Supabase credentials that ever reach the
 * browser, and those are the public *anon / publishable* keys. The
 * service-role key lives exclusively in `ai-server/.env` and is never
 * imported by the frontend - Row Level Security on the `cameras`,
 * `detections`, `alerts` and `settings` tables is what protects the data.
 */
import { createClient } from '@supabase/supabase-js'

export const SUPABASE_URL = import.meta.env.VITE_SUPABASE_URL || ''
export const SUPABASE_ANON_KEY = import.meta.env.VITE_SUPABASE_ANON_KEY || ''

export const SUPABASE_STORAGE_BUCKET = 'snapshots'

/** True when the frontend was built with usable Supabase credentials. */
export const isSupabaseConfigured = Boolean(SUPABASE_URL && SUPABASE_ANON_KEY)

export const supabase = isSupabaseConfigured
  ? createClient(SUPABASE_URL, SUPABASE_ANON_KEY, {
      auth: {
        persistSession: true,
        autoRefreshToken: true,
        detectSessionInUrl: true,
      },
    })
  : null

/**
 * Human-readable reason the app cannot talk to Supabase, or null when the
 * configuration looks fine. Shown on the login screen so a misconfigured
 * deploy fails with an explanation instead of a silent spinner.
 */
export function supabaseConfigError() {
  if (isSupabaseConfigured) return null
  if (!SUPABASE_URL) {
    return 'VITE_SUPABASE_URL is missing. Add it to frontend/.env (and to the Cloudflare Pages environment variables).'
  }
  return 'VITE_SUPABASE_ANON_KEY is missing. Add it to frontend/.env (and to the Cloudflare Pages environment variables).'
}

/**
 * Turns a PostgREST error into the readable string the UI already expects,
 * so every caller can just show `err.detail`.
 */
export function toDetail(error) {
  if (!error) return null
  if (typeof error === 'string') return error
  return error.message || error.details || String(error)
}
