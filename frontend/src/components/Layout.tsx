import { NavLink, Outlet } from 'react-router-dom'

export default function Layout() {
  return (
    <div className="app">
      <aside className="sidebar">
        <div className="logo">
          <div className="logo-mark">A</div>
          <div><b>Ansible GUI</b><span>server management</span></div>
        </div>
        <div className="nav-sec">維運</div>
        <nav className="nav">
          <NavLink to="/" end className={({isActive}) => isActive ? 'active' : ''}>
            <span className="ico">▦</span>總覽儀表板
          </NavLink>
          <NavLink to="/hosts" className={({isActive}) => isActive ? 'active' : ''}>
            <span className="ico">🖥</span>主機
          </NavLink>
          <NavLink to="/jobs" className={({isActive}) => isActive ? 'active' : ''}>
            <span className="ico">▶</span>任務
          </NavLink>
        </nav>
        <div className="nav-sec">系統</div>
        <nav className="nav">
          <a href="#" onClick={e => e.preventDefault()} title="之後做">
            <span className="ico">🔑</span>憑證
          </a>
          <a href="#" onClick={e => e.preventDefault()} title="之後做">
            <span className="ico">⚙</span>設定
          </a>
        </nav>
      </aside>
      <main className="main">
        <Outlet />
      </main>
    </div>
  )
}
