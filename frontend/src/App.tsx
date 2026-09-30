import { BrowserRouter, Route, Routes } from 'react-router-dom'
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

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
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
        </Route>
      </Routes>
    </BrowserRouter>
  )
}
