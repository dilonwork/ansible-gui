import { NavLink, Outlet } from 'react-router-dom'

export default function Layout() {
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
          <a href="#" onClick={e => e.preventDefault()} title="Coming later">
            <span className="ico">🔑</span>Credentials
          </a>
          <a href="#" onClick={e => e.preventDefault()} title="Coming later">
            <span className="ico">⚙</span>Settings
          </a>
        </nav>
      </aside>
      <main className="main">
        <Outlet />
      </main>
    </div>
  )
}
