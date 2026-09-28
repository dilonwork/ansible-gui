import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api, type Host, type Job } from '../api'

export default function Jobs() {
  const [hosts, setHosts] = useState<Host[]>([])
  const [jobs, setJobs] = useState<Job[]>([])
  const [sel, setSel] = useState<Set<string>>(new Set())
  const [busy, setBusy] = useState(false)
  const nav = useNavigate()

  useEffect(() => {
    api.listHosts().then(h => { setHosts(h); setSel(new Set(h.map(x => x.id))) }).catch(() => {})
    const t = setInterval(() => api.listJobs().then(setJobs).catch(() => {}), 3000)
    api.listJobs().then(setJobs).catch(() => {})
    return () => clearInterval(t)
  }, [])

  const toggle = (id: string) => {
    const n = new Set(sel)
    n.has(id) ? n.delete(id) : n.add(id)
    setSel(n)
  }

  const run = async () => {
    if (!sel.size) return
    setBusy(true)
    try {
      const { job_id } = await api.createJob([...sel])
      nav(`/jobs/${job_id}`)
    } finally { setBusy(false) }
  }

  const tag = (s: Job['status']) =>
    s === 'running' ? <span className="tag t-blue"><span className="pulse" />執行中</span>
    : s === 'successful' ? <span className="tag t-green">✓ 成功</span>
    : <span className="tag t-red">✕ 失敗</span>

  return (
    <>
      <div className="page-head"><h1>任務</h1></div>
      <div className="page-sub">skeleton 版只支援 ping 連通測試；Job Template 之後做</div>

      <div className="card">
        <h2>建立 ping 任務</h2>
        {hosts.length === 0 ? <div className="empty">先去「主機」頁新增主機</div> : (<>
          {hosts.map(h => (
            <label key={h.id} className="checkline">
              <input type="checkbox" checked={sel.has(h.id)} onChange={() => toggle(h.id)} />
              <span className="mono">{h.name}</span>
              <small>{h.username}@{h.address}</small>
            </label>
          ))}
          <button className="btn primary" onClick={run} disabled={busy || !sel.size}>
            ▶ 執行 ping（{sel.size} 台）
          </button>
        </>)}
      </div>

      <div className="card">
        <h2>任務列表</h2>
        {jobs.length === 0 ? <div className="empty">還沒有任務</div> : (
          <table>
            <thead><tr><th>任務</th><th>主機數</th><th>狀態</th><th>開始時間</th></tr></thead>
            <tbody>
              {[...jobs].sort((a, b) => b.created_at - a.created_at).map(j => (
                <tr key={j.id} className="clickable" onClick={() => nav(`/jobs/${j.id}`)}>
                  <td className="mono"><Link to={`/jobs/${j.id}`} onClick={e => e.stopPropagation()}>ping #{j.id}</Link></td>
                  <td className="mut">{j.host_ids.length}</td>
                  <td>{tag(j.status)}</td>
                  <td className="mut">{new Date(j.created_at * 1000).toLocaleString('zh-TW', {hour12: false})}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </>
  )
}
