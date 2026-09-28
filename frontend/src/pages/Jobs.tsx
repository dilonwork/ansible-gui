import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api, type Host, type JobSummary, type JobTemplate } from '../api'

export default function Jobs() {
  const [hosts, setHosts] = useState<Host[]>([])
  const [templates, setTemplates] = useState<JobTemplate[]>([])
  const [jobs, setJobs] = useState<JobSummary[]>([])
  const [sel, setSel] = useState<Set<string>>(new Set())
  const [tplId, setTplId] = useState('')
  const [tplCheck, setTplCheck] = useState(false)
  const [busy, setBusy] = useState(false)
  const nav = useNavigate()

  useEffect(() => {
    api.listHosts().then(h => { setHosts(h); setSel(new Set(h.map(x => x.id))) }).catch(() => {})
    api.listTemplates().then(setTemplates).catch(() => {})
    const t = setInterval(() => api.listJobs().then(setJobs).catch(() => {}), 3000)
    api.listJobs().then(setJobs).catch(() => {})
    return () => clearInterval(t)
  }, [])

  const toggle = (id: string) => {
    const n = new Set(sel)
    n.has(id) ? n.delete(id) : n.add(id)
    setSel(n)
  }

  const runPing = async () => {
    if (!sel.size) return
    setBusy(true)
    try {
      const { job_id } = await api.createJob({ host_ids: [...sel] })
      nav(`/jobs/${job_id}`)
    } finally { setBusy(false) }
  }

  const runTemplate = async () => {
    if (!tplId) return
    setBusy(true)
    try {
      const { job_id } = await api.createJob({ template_id: tplId, check_mode: tplCheck || undefined })
      nav(`/jobs/${job_id}`)
    } finally { setBusy(false) }
  }

  const tag = (s: JobSummary['status']) =>
    s === 'running' ? <span className="tag t-blue"><span className="pulse" />Running</span>
    : s === 'successful' ? <span className="tag t-green">✓ Success</span>
    : <span className="tag t-red">✕ Failed</span>

  return (
    <>
      <div className="page-head"><h1>Jobs</h1></div>
      <div className="page-sub">Launching from a template freezes a snapshot (playbook content + vars + hosts)</div>

      <div className="grid2">
        <div className="card">
          <h2>Launch from template</h2>
          {templates.length === 0 ? <div className="empty">Create a template first</div> : (<>
            <label>Template</label>
            <select value={tplId} onChange={e => { setTplId(e.target.value); setTplCheck(false) }}>
              <option value="">— select —</option>
              {templates.map(t => (
                <option key={t.id} value={t.id}>
                  {t.name} → {t.playbook_name} ({t.host_ids.length} hosts{t.check_mode ? ', check mode' : ''})
                </option>
              ))}
            </select>
            <label className="checkline">
              <input type="checkbox" checked={tplCheck} onChange={e => setTplCheck(e.target.checked)} />
              Force check mode <small>(dry-run for this run)</small>
            </label>
            <button className="btn primary" onClick={runTemplate} disabled={busy || !tplId}>
              ▶ Run template
            </button>
          </>)}
        </div>

        <div className="card">
          <h2>Ad-hoc ping</h2>
          {hosts.length === 0 ? <div className="empty">Add hosts first</div> : (<>
            {hosts.map(h => (
              <label key={h.id} className="checkline">
                <input type="checkbox" checked={sel.has(h.id)} onChange={() => toggle(h.id)} />
                <span className="mono">{h.name}</span>
                <small>{h.username}@{h.address}</small>
              </label>
            ))}
            <button className="btn primary" onClick={runPing} disabled={busy || !sel.size}>
              ▶ Ping ({sel.size} hosts)
            </button>
          </>)}
        </div>
      </div>

      <div className="card">
        <h2>Job list</h2>
        {jobs.length === 0 ? <div className="empty">No jobs yet</div> : (
          <table>
            <thead><tr><th>Job</th><th>Type</th><th>Mode</th><th>Hosts</th><th>Status</th><th>Started</th></tr></thead>
            <tbody>
              {[...jobs].sort((a, b) => b.created_at - a.created_at).map(j => (
                <tr key={j.id} className="clickable" onClick={() => nav(`/jobs/${j.id}`)}>
                  <td className="mono"><Link to={`/jobs/${j.id}`} onClick={e => e.stopPropagation()}>#{j.id}</Link></td>
                  <td className="mut">{j.kind === 'template' ? j.template_name : 'ad-hoc ping'}</td>
                  <td>{j.check_mode ? <span className="tag t-amber">check</span> : <span className="tag t-gray">normal</span>}</td>
                  <td className="mut">{j.host_ids.length}</td>
                  <td>{tag(j.status)}</td>
                  <td className="mut">{new Date(j.created_at * 1000).toLocaleString('en-US', {hour12: false})}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </>
  )
}
