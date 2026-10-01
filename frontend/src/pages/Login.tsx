import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, setToken } from '../api'

export function AuthShell({ title, sub, children }: { title: string; sub: string; children: React.ReactNode }) {
  return (
    <div style={{ minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
      <div className="card" style={{ width: 380 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 4 }}>
          <span style={{ fontSize: 28 }}>⚓</span>
          <div>
            <div style={{ fontWeight: 700, fontSize: 18 }}>Drydock</div>
            <div className="dim" style={{ fontSize: 12 }}>fleet maintenance</div>
          </div>
        </div>
        <h2 style={{ marginTop: 12 }}>{title}</h2>
        <div className="dim" style={{ fontSize: 13, marginBottom: 16 }}>{sub}</div>
        {children}
      </div>
    </div>
  )
}

export default function Login() {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState(false)
  const nav = useNavigate()

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    setBusy(true); setMsg('')
    try {
      const r = await api.login({ username: username.trim(), password })
      setToken(r.token)
      nav('/')
    } catch (err: unknown) { setMsg('✕ ' + (err as Error).message) }
    finally { setBusy(false) }
  }

  return (
    <AuthShell title="Sign in" sub="Your fleet is waiting.">
      <form onSubmit={submit}>
        <label>Username</label>
        <input value={username} onChange={e => setUsername(e.target.value)} autoFocus />
        <label>Password</label>
        <input type="password" value={password} onChange={e => setPassword(e.target.value)} />
        <div style={{ marginTop: 16 }}>
          <button className="btn primary" style={{ width: '100%' }} disabled={busy}>
            {busy ? 'Signing in…' : 'Sign in'}
          </button>
        </div>
        {msg && <div className="form-msg">{msg}</div>}
      </form>
    </AuthShell>
  )
}

export function Setup() {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState(false)
  const nav = useNavigate()

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    setBusy(true); setMsg('')
    try {
      const r = await api.authSetup({ username: username.trim(), password })
      setToken(r.token)
      nav('/')
    } catch (err: unknown) { setMsg('✕ ' + (err as Error).message) }
    finally { setBusy(false) }
  }

  return (
    <AuthShell title="Create admin account" sub="First run: this account owns the ship. Passwords need at least 8 characters.">
      <form onSubmit={submit}>
        <label>Username</label>
        <input value={username} onChange={e => setUsername(e.target.value)} autoFocus placeholder="admin" />
        <label>Password</label>
        <input type="password" value={password} onChange={e => setPassword(e.target.value)} />
        <div style={{ marginTop: 16 }}>
          <button className="btn primary" style={{ width: '100%' }} disabled={busy}>
            {busy ? 'Creating…' : 'Create admin'}
          </button>
        </div>
        {msg && <div className="form-msg">{msg}</div>}
      </form>
    </AuthShell>
  )
}
