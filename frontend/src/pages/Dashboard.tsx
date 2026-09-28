import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, type Host, type Job } from '../api'

function statusTag(s: Job['status']) {
  if (s === 'running') return <span className="tag t-blue"><span className="pulse" />執行中</span>
  if (s === 'successful') return <span className="tag t-green">✓ 成功</span>
  return <span className="tag t-red">✕ 失敗</span>
}

export default function Dashboard() {
  const [hosts, setHosts] = useState<Host[]>([])
  const [jobs, setJobs] = useState<Job[]>([])

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
      <div className="page-head"><h1>總覽儀表板</h1></div>
      <div className="page-sub">skeleton 版：數字來自真實 API，K8s 叢集尚未接入</div>

      <div className="kpis">
        <div className="kpi"><div className="lbl">管理主機</div><div className="val">{hosts.length}</div></div>
        <div className="kpi"><div className="lbl">任務總數</div><div className="val">{jobs.length}</div></div>
        <div className="kpi"><div className="lbl">任務成功率</div><div className="val" style={{color: rate >= 80 ? '#3fb950' : '#d29922'}}>{rate}%</div></div>
        <div className="kpi"><div className="lbl">執行中任務</div><div className="val run">{running}</div></div>
      </div>

      <div className="grid2">
        <div className="card">
          <h2>最近任務</h2>
          {recent.length === 0 ? <div className="empty">還沒有任務，去「任務」頁跑一次 ping 吧</div> : (
            <table>
              <thead><tr><th>任務</th><th>主機數</th><th>狀態</th><th>開始</th></tr></thead>
              <tbody>
                {recent.map(j => (
                  <tr key={j.id} className="clickable">
                    <td><Link to={`/jobs/${j.id}`} className="mono">ping #{j.id}</Link></td>
                    <td className="mut">{j.host_ids.length}</td>
                    <td>{statusTag(j.status)}</td>
                    <td className="mut">{new Date(j.created_at * 1000).toLocaleString('zh-TW', {hour12: false})}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
        <div className="card">
          <h2>Worker 節點</h2>
          <div className="empty">尚未接入 K8s 叢集<br /><span style={{fontSize: 11}}>（M4 叢集管理完成後這裡會顯示節點表）</span></div>
        </div>
      </div>
    </>
  )
}
