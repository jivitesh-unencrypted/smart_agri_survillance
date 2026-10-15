/**
 * User-scope epoch.
 *
 * Every data fetch in the app is scoped to the signed-in account. Because
 * the account can change without a page reload (logout -> login as someone
 * else), a component that already holds fetched rows must be able to tell
 * that its rows belong to a previous account and refetch rather than keep
 * rendering them.
 *
 * `useUserScope()` returns the current epoch, which changes whenever the
 * authenticated user changes or signs out. Put the epoch in a `useEffect`
 * dependency list to re-run a fetch when the account changes:
 *
 *   const epoch = useUserScope()
 *   useEffect(() => { load() }, [epoch])
 *
 * `bumpUserScope()` is called by the auth provider on every identity change.
 */
import { createContext, useContext } from 'react'

export const UserScopeContext = createContext(0)

export function useUserScope() {
  return useContext(UserScopeContext)
}
