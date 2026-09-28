import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, type Host, type JobSummary } from '../api'

function statusTag(s: JobSummary['status']) {
  if (s === 'running') return <span className="tag t-blue"><span className="pulse" />Running</span>
  if (s === 'successful') return <span className="tag t-green">✓ Success</span>
  return <span className="tag t-red">✕ Failed</span>
}

export default function Dashboard() {
  const [hosts, setHosts] = useState<Host[]>([])
  const [jobs, setJobs] = useState<JobSummary[]>([])

  useEffect(() => {
    api.listHosts().then(setHosts).catch(() => {})
    api.listJobs().then(setJobs).catch(() => {})
  }, [])

  const ok = jobs.filter(j => j.status === 'successful').length
  const running = jobs.filter(j => j.status === 'running').length
  const rate = jobs.length ? Math.round((ok / jobs.length) * 100) : 0
  const recent = [...jobs].sort((a, b) => b.created_at - a.created_at).slice(0, 5)

  return (
    <>
      <div className="page-head"><h1>Dashboard</h1></div>
      <div className="page-sub">Skeleton build: numbers come from the real API; no K8s cluster connected yet</div>

      <div className="kpis">
        <div className="kpi"><div className="lbl">Managed hosts</div><div className="val">{hosts.length}</div></div>
        <div className="kpi"><div className="lbl">Total jobs</div><div className="val">{jobs.length}</div></div>
        <div className="kpi"><div className="lbl">Job success rate</div><div className="val" style={{color: rate >= 80 ? '#3fb950' : '#d29922'}}>{rate}%</div></div>
        <div className="kpi"><div className="lbl">Running jobs</div><div className="val run">{running}</div></div>
      </div>

      <div className="grid2">
        <div className="card">
          <h2>Recent jobs</h2>
          {recent.length === 0 ? <div className="empty">No jobs yet — run a ping from the Jobs page</div> : (
            <table>
              <thead><tr><th>Job</th><th>Playbook</th><th>Hosts</th><th>Status</th><th>Started</th></tr></thead>
              <tbody>
                {recent.map(j => (
                  <tr key={j.id} className="clickable">
                    <td><Link to={`/jobs/${j.id}`} className="mono">#{j.id}</Link></td>
                    <td className="mut">{j.kind === 'template' ? j.template_name : 'ad-hoc ping'}</td>
                    <td className="mut">{j.host_ids.length}</td>
                    <td>{statusTag(j.status)}</td>
                    <td className="mut">{new Date(j.created_at * 1000).toLocaleString('en-US', {hour12: false})}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
        <div className="card">
          <h2>Worker nodes</h2>
          <div className="empty">No Kubernetes cluster connected yet<br /><span style={{fontSize: 11}}>(Cluster management, M4, is not implemented yet)</span></div>
        </div>
      </div>
    </>
  )
}
