import { NavLink, Outlet, useNavigate } from 'react-router-dom'
import { api, clearToken } from '../api'
import { useMe } from '../App'

export default function Layout() {
  const me = useMe()
  const nav = useNavigate()

  const logout = async () => {
    try { await api.logout() } catch { /* token may already be dead */ }
    clearToken()
    nav('/login')
  }

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="logo">
          <div className="logo-mark">⚓</div>
          <div><b>Drydock</b><span>fleet maintenance</span></div>
        </div>
        <div className="nav-sec">OPERATE</div>
        <nav className="nav">
          <NavLink to="/" end className={({isActive}) => isActive ? 'active' : ''}>
            <span className="ico">▦</span>Dashboard
          </NavLink>
          <NavLink to="/hosts" className={({isActive}) => isActive ? 'active' : ''}>
            <span className="ico">🖥</span>Hosts
          </NavLink>
          <NavLink to="/playbooks" className={({isActive}) => isActive ? 'active' : ''}>
            <span className="ico">📜</span>Playbooks
          </NavLink>
          <NavLink to="/templates" className={({isActive}) => isActive ? 'active' : ''}>
            <span className="ico">📋</span>Templates
          </NavLink>
          <NavLink to="/jobs" className={({isActive}) => isActive ? 'active' : ''}>
            <span className="ico">▶</span>Jobs
          </NavLink>
          <NavLink to="/maintenance" className={({isActive}) => isActive ? 'active' : ''}>
            <span className="ico">🛠</span>Maintenance
          </NavLink>
          <NavLink to="/schedules" className={({isActive}) => isActive ? 'active' : ''}>
            <span className="ico">🕐</span>Schedules
          </NavLink>
          <NavLink to="/notifications" className={({isActive}) => isActive ? 'active' : ''}>
            <span className="ico">🔔</span>Notifications
          </NavLink>
          <NavLink to="/clusters" className={({isActive}) => isActive ? 'active' : ''}>
            <span className="ico">☸</span>Clusters
          </NavLink>
        </nav>
        <div className="nav-sec">SYSTEM</div>
        <nav className="nav">
          {me?.role === 'admin' && (
            <NavLink to="/users" className={({isActive}) => isActive ? 'active' : ''}>
              <span className="ico">👥</span>Users
            </NavLink>
          )}
          <a href="#" onClick={e => e.preventDefault()} title="Coming later">
            <span className="ico">🔑</span>Credentials
          </a>
          <a href="#" onClick={e => e.preventDefault()} title="Coming later">
            <span className="ico">⚙</span>Settings
          </a>
        </nav>
        {me && (
          <div style={{ marginTop: 'auto', padding: '12px 16px', borderTop: '1px solid var(--border)' }}>
            <div className="mono" style={{ fontSize: 13 }}><b>{me.username}</b></div>
            <div className="dim" style={{ fontSize: 11, marginBottom: 8 }}>{me.role}</div>
            <button className="btn" style={{ width: '100%', fontSize: 12 }} onClick={logout}>Sign out</button>
          </div>
        )}
      </aside>
      <main className="main">
        <Outlet />
      </main>
    </div>
  )
}
