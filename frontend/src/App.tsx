import { createContext, useContext, useEffect, useState } from 'react'
import { BrowserRouter, Navigate, Route, Routes, useLocation, useNavigate } from 'react-router-dom'
import Layout from './components/Layout'
import Dashboard from './pages/Dashboard'
import Hosts from './pages/Hosts'
import Jobs from './pages/Jobs'
import JobDetail from './pages/JobDetail'
import Clusters from './pages/Clusters'
import Maintenance from './pages/Maintenance'
import MaintenanceDetail from './pages/MaintenanceDetail'
import Notifications from './pages/Notifications'
import Playbooks from './pages/Playbooks'
import Schedules from './pages/Schedules'
import Templates from './pages/Templates'
import Users from './pages/Users'
import Login, { Setup } from './pages/Login'
import { api, clearToken, getToken } from './api'

export interface Me { username: string; role: string; can_write: boolean }
const MeCtx = createContext<Me | null>(null)
export const useMe = () => useContext(MeCtx)

function AuthGate({ children }: { children: React.ReactNode }) {
  const [me, setMe] = useState<Me | null>(null)
  const [ready, setReady] = useState(false)
  const loc = useLocation()
  const nav = useNavigate()

  // Re-verify on every route change so setup/login always land in the app.
  useEffect(() => {
    let cancelled = false
    setReady(false)
    api.authStatus().then(s => {
      if (cancelled) return
      if (s.setup_required) {
        if (loc.pathname !== '/setup') nav('/setup', { replace: true })
        else setReady(true)
        return
      }
      if (!getToken()) {
        if (loc.pathname !== '/login') nav('/login', { replace: true })
        else setReady(true)
        return
      }
      api.authMe().then(m => {
        if (cancelled) return
        setMe(m)
        if (loc.pathname === '/login' || loc.pathname === '/setup') nav('/', { replace: true })
        else setReady(true)
      }).catch(() => {
        if (cancelled) return
        clearToken()
        if (loc.pathname !== '/login') nav('/login', { replace: true })
        else setReady(true)
      })
    }).catch(() => { if (!cancelled) setReady(true) })
    return () => { cancelled = true }
  }, [loc.pathname]) // eslint-disable-line react-hooks/exhaustive-deps

  if (!ready) return <div className="dim" style={{ padding: 40 }}>Loading…</div>
  return <MeCtx.Provider value={me}>{children}</MeCtx.Provider>
}

function AdminOnly({ children }: { children: React.ReactNode }) {
  const me = useMe()
  if (!me) return null
  if (me.role !== 'admin') return <Navigate to="/" replace />
  return <>{children}</>
}

export default function App() {
  return (
    <BrowserRouter>
      <AuthGate>
        <Routes>
          <Route path="/login" element={<Login />} />
          <Route path="/setup" element={<Setup />} />
          <Route element={<Layout />}>
            <Route path="/" element={<Dashboard />} />
            <Route path="/hosts" element={<Hosts />} />
            <Route path="/playbooks" element={<Playbooks />} />
            <Route path="/templates" element={<Templates />} />
            <Route path="/jobs" element={<Jobs />} />
            <Route path="/jobs/:id" element={<JobDetail />} />
            <Route path="/maintenance" element={<Maintenance />} />
            <Route path="/maintenance/:id" element={<MaintenanceDetail />} />
            <Route path="/schedules" element={<Schedules />} />
            <Route path="/notifications" element={<Notifications />} />
            <Route path="/clusters" element={<Clusters />} />
            <Route path="/users" element={<AdminOnly><Users /></AdminOnly>} />
          </Route>
        </Routes>
      </AuthGate>
    </BrowserRouter>
  )
}
