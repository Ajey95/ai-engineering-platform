import React, { useEffect, useState } from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import { setCsrfToken } from './api'
import './styles.css'

type BrowserSession = {
  authenticated: boolean; tenant_id: string; subject: string
  csrf: string | null; development: boolean
}

function AuthGate() {
  const [session, setSession] = useState<BrowserSession | null>(null)
  const [loading, setLoading] = useState(true)
  const [message, setMessage] = useState('')
  const [tenantId, setTenantId] = useState('')

  useEffect(() => {
    let active = true
    void fetch('/v1/auth/session', { credentials: 'same-origin', cache: 'no-store' })
      .then(async response => {
        if (!response.ok) throw new Error(response.status === 401 ? 'Sign in to continue.' : 'Session check failed.')
        return response.json() as Promise<BrowserSession>
      })
      .then(value => { if (active) { setCsrfToken(value.csrf); setSession(value); setMessage('') } })
      .catch(error => { if (active) { setSession(null); setMessage(error instanceof Error ? error.message : 'Session check failed.') } })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [])

  if (loading) return <main className="auth-page"><div className="content-panel"><p>Checking session…</p></div></main>
  if (!session) return <main className="auth-page"><form className="content-panel" onSubmit={event => {
    event.preventDefault()
    if (tenantId.trim()) window.location.assign(`/v1/auth/start?tenant_id=${encodeURIComponent(tenantId.trim())}`)
  }}><h1>Sign in</h1><p>{message}</p><label>Workspace ID<input value={tenantId} onChange={event => setTenantId(event.target.value)} maxLength={36} required /></label><button className="primary-button">Continue with identity provider</button></form></main>
  return <App identity={session} onSignOut={session.development ? undefined : () => {
    void fetch('/v1/auth/logout', {
      method: 'POST', credentials: 'same-origin', headers: { 'X-AIP-CSRF': session.csrf || '' },
    }).finally(() => { setCsrfToken(null); window.location.reload() })
  }} />
}

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode><AuthGate /></React.StrictMode>,
)
