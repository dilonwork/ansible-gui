import { useEffect, useRef, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api, jobWsUrl, type Job, type JobEvent } from '../api'

function renderLine(e: JobEvent, i: number) {
  switch (e.type) {
    case 'job_started':
      return <div key={i}><span className="b">▶ 任務開始</span></div>
    case 'task_start':
      return <div key={i}><span className="dim">TASK</span> [{e.task}]</div>
    case 'host_ok':
      return <div key={i}><span className="g">✓ ok</span> <span className="dim">[{e.host}]</span> {e.task}</div>
    case 'host_failed':
      return <div key={i}><span className="r">✕ failed</span> <span className="dim">[{e.host}]</span> {e.task} — {e.msg}</div>
    case 'host_unreachable':
      return <div key={i}><span className="r">✕ unreachable</span> <span className="dim">[{e.host}]</span> — {e.msg}</div>
    case 'job_finished':
      return <div key={i}><span className="b">■ 任務結束</span></div>
    default:
      return null
  }
}

export default function JobDetail() {
  const { id } = useParams<{ id: string }>()
  const [job, setJob] = useState<Job | null>(null)
  const [events, setEvents] = useState<JobEvent[]>([])
  const logRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!id) return
    let ws: WebSocket | null = null
    let alive = true

    api.getJob(id).then(j => {
      if (!alive) return
      setJob(j)
      setEvents(j.events || [])
      if (j.status === 'running') {
        ws = new WebSocket(jobWsUrl(id))
        ws.onmessage = ev => {
          const e = JSON.parse(ev.data) as JobEvent
          if (e.type === 'eof') { ws?.close(); return }
          setEvents(prev => [...prev, e])
          if (e.type === 'job_finished') {
            setTimeout(() => api.getJob(id).then(setJob).catch(() => {}), 500)
            setTimeout(() => ws?.close(), 500)
          }
        }
      }
    }).catch(() => {})

    return () => { alive = false; ws?.close() }
  }, [id])

  useEffect(() => {
    const el = logRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [events])

  if (!job) return <div className="empty">載入中…</div>

  const okHosts = new Set(events.filter(e => e.type === 'host_ok').map(e => e.host))
  const badHosts = new Set(events.filter(e => e.type === 'host_failed' || e.type === 'host_unreachable').map(e => e.host))

  return (
    <>
      <div className="page-sub"><Link to="/jobs" className="mut">任務</Link> / <span className="mono">#{job.id}</span></div>
      <div className="page-head">
        <h1 className="mono">ping #{job.id}</h1>
        {job.status === 'running' && <span className="tag t-blue"><span className="pulse" />執行中</span>}
        {job.status === 'successful' && <span className="tag t-green">✓ 成功</span>}
        {job.status === 'failed' && <span className="tag t-red">✕ 失敗</span>}
      </div>
      <div className="page-sub">
        目標 {job.host_ids.length} 台 · 開始 {new Date(job.created_at * 1000).toLocaleString('zh-TW', {hour12: false})}
        {job.finished_at && <> · 結束 {new Date(job.finished_at * 1000).toLocaleString('zh-TW', {hour12: false})}</>}
      </div>

      <div className="kpis" style={{gridTemplateColumns: 'repeat(3, 1fr)'}}>
        <div className="kpi"><div className="lbl">成功主機</div><div className="val ok">{okHosts.size}</div></div>
        <div className="kpi"><div className="lbl">失敗主機</div><div className="val bad">{badHosts.size}</div></div>
        <div className="kpi"><div className="lbl">事件數</div><div className="val">{events.length}</div></div>
      </div>

      <div className="card">
        <h2>即時日誌</h2>
        <div className="log" ref={logRef}>
          {events.length === 0
            ? <div className="dim">// 等待事件…</div>
            : events.map(renderLine)}
        </div>
      </div>
    </>
  )
}
