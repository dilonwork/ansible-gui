import { useEffect, useRef, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api, jobWsUrl, type Job, type JobEvent } from '../api'

function renderLine(e: JobEvent, i: number) {
  switch (e.type) {
    case 'job_started':
      return <div key={i}><span className="b">▶ Job started</span></div>
    case 'task_start':
      return <div key={i}><span className="dim">TASK</span> [{e.task}]</div>
    case 'host_ok':
      return <div key={i}><span className="g">✓ ok</span> <span className="dim">[{e.host}]</span> {e.task}</div>
    case 'host_failed':
      return <div key={i}><span className="r">✕ failed</span> <span className="dim">[{e.host}]</span> {e.task} — {e.msg}</div>
    case 'host_unreachable':
      return <div key={i}><span className="r">✕ unreachable</span> <span className="dim">[{e.host}]</span> — {e.msg}</div>
    case 'job_finished':
      return <div key={i}><span className="b">■ Job finished</span></div>
    case 'job_cancelled':
      return <div key={i}><span className="b">■ Job cancelled</span></div>
    case 'job_interrupted':
      return <div key={i}><span className="r">■ Job interrupted — {e.msg}</span></div>
    case 'job_error':
      return <div key={i}><span className="r">✕ error — {e.msg}</span></div>
    default:
      return null
  }
}

export default function JobDetail() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const [job, setJob] = useState<Job | null>(null)
  const [events, setEvents] = useState<JobEvent[]>([])
  const [busy, setBusy] = useState(false)
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
          if (e.type === 'job_finished' || e.type === 'job_cancelled') {
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

  if (!job) return <div className="empty">Loading…</div>

  const okHosts = new Set(events.filter(e => e.type === 'host_ok').map(e => e.host))
  const badHosts = new Set(events.filter(e => e.type === 'host_failed' || e.type === 'host_unreachable').map(e => e.host))
  const snap = job.snapshot

  const doCancel = async () => {
    if (!id || busy || !window.confirm('Cancel this running job?')) return
    setBusy(true)
    try {
      await api.cancelJob(id)
      setJob(await api.getJob(id))
    } catch (e) {
      alert(`Cancel failed: ${(e as Error).message}`)
    } finally {
      setBusy(false)
    }
  }

  const doRetry = async () => {
    if (!id || busy) return
    setBusy(true)
    try {
      const { job_id } = await api.retryJob(id)
      navigate(`/jobs/${job_id}`)
    } catch (e) {
      alert(`Retry failed: ${(e as Error).message}`)
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <div className="page-sub"><Link to="/jobs" className="mut">Jobs</Link> / <span className="mono">#{job.id}</span></div>
      <div className="page-head">
        <h1 className="mono">#{job.id}</h1>
        {job.status === 'running' && <span className="tag t-blue"><span className="pulse" />Running</span>}
        {job.status === 'successful' && <span className="tag t-green">✓ Success</span>}
        {job.status === 'failed' && <span className="tag t-red">✕ Failed</span>}
        {job.status === 'cancelled' && <span className="tag t-amber">■ Cancelled</span>}
        {job.status === 'interrupted' && <span className="tag t-red">■ Interrupted</span>}
        {snap.check_mode && <span className="tag t-amber">check mode (dry-run)</span>}
        <span style={{ marginLeft: 'auto', display: 'flex', gap: 8 }}>
          {job.status === 'running' && (
            <button className="btn btn-danger" onClick={doCancel} disabled={busy}>■ Cancel</button>
          )}
          {job.status === 'failed' && badHosts.size > 0 && (
            <button className="btn" onClick={doRetry} disabled={busy}>↻ Retry failed nodes ({badHosts.size})</button>
          )}
        </span>
      </div>
      <div className="page-sub">
        {new Date(job.created_at * 1000).toLocaleString('en-US', {hour12: false})}
        {job.finished_at && <> → {new Date(job.finished_at * 1000).toLocaleString('en-US', {hour12: false})}</>}
      </div>

      <div className="card">
        <h2>Snapshot (frozen at launch)</h2>
        <table>
          <tbody>
            <tr><td className="mut" style={{width: 130}}>Type</td><td>{snap.kind === 'template' ? `Template: ${snap.template_name}` : 'Ad-hoc ping'}</td></tr>
            <tr><td className="mut">Playbook</td><td className="mono">{snap.playbook_name}</td></tr>
            <tr><td className="mut">Mode</td><td>{snap.check_mode ? 'check (dry-run, --check)' : 'normal'}</td></tr>
            <tr><td className="mut">Targets</td><td className="mono">{snap.host_ids.length} host(s)</td></tr>
            <tr><td className="mut">extra_vars</td><td className="mono">{JSON.stringify(snap.extra_vars)}</td></tr>
          </tbody>
        </table>
      </div>

      <div className="kpis" style={{gridTemplateColumns: 'repeat(3, 1fr)'}}>
        <div className="kpi"><div className="lbl">Hosts OK</div><div className="val ok">{okHosts.size}</div></div>
        <div className="kpi"><div className="lbl">Hosts failed</div><div className="val bad">{badHosts.size}</div></div>
        <div className="kpi"><div className="lbl">Events</div><div className="val">{events.length}</div></div>
      </div>

      <div className="card">
        <h2>Live log</h2>
        <div className="log" ref={logRef}>
          {events.length === 0
            ? <div className="dim">// waiting for events…</div>
            : events.map(renderLine)}
        </div>
      </div>
    </>
  )
}
