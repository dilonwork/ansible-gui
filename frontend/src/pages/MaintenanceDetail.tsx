import { useEffect, useRef, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api, maintWsUrl, type MaintEvent, type MaintRun } from '../api'

const STEP_LABELS: Record<string, string> = {
  cordon: 'Cordon', drain: 'Drain', maintain: 'Patch / upgrade',
  verify: 'Verify', uncordon: 'Uncordon',
}

function renderEvent(e: MaintEvent, i: number) {
  switch (e.type) {
    case 'maint_run_start':
      return <div key={i}><span className="b">▶ Maintenance run started</span> <span className="dim">[{e.workflow}]</span></div>
    case 'maint_preflight_start':
      return <div key={i}><span className="b">✓ Preflight checks</span></div>
    case 'maint_preflight_done':
      return <div key={i}><span className="g">✓ Preflight passed</span></div>
    case 'maint_preflight_failed':
      return <div key={i}><span className="r">✕ Preflight failed: {(e.failed || []).join(', ')}</span></div>
    case 'maint_node_start':
      return <div key={i}><span className="b">▸ Node {e.node}</span></div>
    case 'maint_node_done':
      return <div key={i}><span className="g">✓ Node {e.node} done</span></div>
    case 'maint_node_failed':
      return <div key={i}><span className="r">✕ Node {e.node} failed at {e.step}</span></div>
    case 'maint_node_retry':
      return <div key={i}><span className="b">↻ Retrying node {e.node}</span></div>
    case 'maint_node_skipped':
      return <div key={i}><span className="warn">⏭ Skipped node {e.node}</span></div>
    case 'maint_step_start':
      return <div key={i}><span className="dim">STEP</span> [{e.step}] <span className="dim">on {e.node}</span></div>
    case 'maint_step_done':
      return <div key={i}>{e.ok ? <span className="g">✓</span> : <span className="r">✕</span>} <span className="dim">[{e.step}] {e.node}</span></div>
    case 'maint_paused':
      return <div key={i}><span className="warn">⏸ Paused{e.node ? ` at ${e.node}` : ''} — waiting for operator</span></div>
    case 'maint_aborted':
      return <div key={i}><span className="b">■ Run aborted</span></div>
    case 'maint_run_done':
      return <div key={i}><span className="g">■ Run completed</span></div>
    case 'maint_error':
      return <div key={i}><span className="r">✕ {e.msg}</span></div>
    case 'task_start':
      return <div key={i}><span className="dim">TASK</span> [{e.task}] <span className="dim">{e.node}{e.step ? `/${e.step}` : ''}</span></div>
    case 'host_ok':
      return <div key={i}><span className="g">✓ ok</span> <span className="dim">[{e.host}]</span> {e.task}</div>
    case 'host_failed':
      return <div key={i}><span className="r">✕ failed</span> <span className="dim">[{e.host}]</span> {e.task} — {e.msg}</div>
    case 'host_unreachable':
      return <div key={i}><span className="r">✕ unreachable</span> <span className="dim">[{e.host}]</span> — {e.msg}</div>
    case 'check_warning':
      return <div key={i}><span className="warn">⚠ {e.task}</span> <span className="dim">{e.msg}</span></div>
    case 'job_finished':
      return <div key={i}><span className="dim">■ step finished</span></div>
    default:
      return null
  }
}

const STATUS_TAG: Record<string, string> = {
  running: 't-blue', pausing: 't-blue', paused: 't-amber',
  failed: 't-red', completed: 't-green', aborted: 't-gray',
}

export default function MaintenanceDetail() {
  const { id } = useParams<{ id: string }>()
  const [run, setRun] = useState<MaintRun | null>(null)
  const [events, setEvents] = useState<MaintEvent[]>([])
  const [busy, setBusy] = useState(false)
  const logRef = useRef<HTMLDivElement>(null)
  const wsRef = useRef<WebSocket | null>(null)

  const refresh = () => {
    if (!id) return
    api.getMaintenance(id).then(r => { setRun(r); setEvents(r.events || []) }).catch(() => {})
  }

  const connect = () => {
    if (!id) return
    wsRef.current?.close()
    const ws = new WebSocket(maintWsUrl(id))
    wsRef.current = ws
    ws.onmessage = ev => {
      const e = JSON.parse(ev.data) as MaintEvent
      if (e.type === 'eof') { ws.close(); refresh(); return }
      setEvents(prev => [...prev, e])
      if (e.type === 'maint_run_done' || e.type === 'maint_preflight_failed') {
        setTimeout(refresh, 500)
        setTimeout(() => ws.close(), 500)
      }
      if (e.type === 'maint_node_failed') setTimeout(refresh, 500)
    }
    ws.onclose = () => { wsRef.current = null }
  }

  useEffect(() => {
    refresh()
    connect()
    const t = setInterval(refresh, 5000)
    return () => { clearInterval(t); wsRef.current?.close() }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id])

  useEffect(() => {
    const el = logRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [events])

  if (!run) return <div className="empty">Loading…</div>

  const failedNode = run.steps.find(n => n.state === 'failed')
  const active = run.status === 'running' || run.status === 'pausing'
  const stepOrder = run.steps.length > 0 ? Object.keys(run.steps[0].step_states) : []

  const act = async (fn: () => Promise<unknown>, confirmMsg?: string) => {
    if (!id || busy) return
    if (confirmMsg && !window.confirm(confirmMsg)) return
    setBusy(true)
    try {
      await fn()
      if (fn !== api.pauseMaintenance) connect() // reattach live stream after resume/retry/skip
      refresh()
    } catch (e) {
      alert(`Action failed: ${(e as Error).message}`)
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <div className="page-sub"><Link to="/maintenance" className="mut">Node maintenance</Link> / <span className="mono">{run.name}</span></div>
      <div className="page-head">
        <h1>{run.name}</h1>
        <span className={`tag ${STATUS_TAG[run.status] || 't-gray'}`}>{run.status}</span>
        <span className="tag t-gray">{run.workflow}</span>
        {run.has_k8s && <span className="tag t-blue">k8s steps on</span>}
        <span style={{ marginLeft: 'auto', display: 'flex', gap: 8 }}>
          {active && (
            <button className="btn" disabled={busy} onClick={() => act(() => api.pauseMaintenance(id!))}>⏸ Pause</button>
          )}
          {run.status === 'paused' && (
            <button className="btn primary" disabled={busy} onClick={() => act(() => api.resumeMaintenance(id!))}>▶ Resume</button>
          )}
          {run.status === 'paused' && failedNode && (
            <>
              <button className="btn" disabled={busy} onClick={() => act(() => api.retryMaintNode(id!))}>↻ Retry node</button>
              <button className="btn" disabled={busy} onClick={() => act(() => api.skipMaintNode(id!), `Skip ${failedNode.node_name}?`)}>⏭ Skip node</button>
            </>
          )}
          {(active || run.status === 'paused') && (
            <button className="btn danger" disabled={busy} onClick={() => act(() => api.abortMaintenance(id!), 'Abort this run? Nodes left cordoned will be uncordoned.')}>■ Abort</button>
          )}
        </span>
      </div>
      <div className="page-sub">
        Target <b>{run.node_ids.length} node(s)</b> · strategy <b>rolling serial=1</b> · on failure <b>pause batch</b>
        {run.finished_at && <> · finished {new Date(run.finished_at * 1000).toLocaleString('en-US', { hour12: false })}</>}
      </div>

      <div className="card">
        <h2>Preflight</h2>
        {run.preflight.length === 0 ? <div className="dim">Not run yet.</div> : (
          <table><tbody>
            {run.preflight.map((c, i) => (
              <tr key={i}>
                <td>{c.status === 'passed' ? <span className="g">✓</span> : c.status === 'warning' ? <span className="warn">⚠</span> : <span className="r">✕</span>}</td>
                <td>{c.name}</td>
                <td className="mut" style={{ whiteSpace: 'pre-wrap' }}>{c.detail}</td>
              </tr>
            ))}
          </tbody></table>
        )}
      </div>

      <div className="card">
        <h2>Node queue — one at a time, batch pauses on any node failure</h2>
        {run.steps.map(n => {
          const done = Object.values(n.step_states).filter(s => s === 'done').length
          const total = Object.keys(n.step_states).length
          return (
            <div key={n.node_id} className="node-row" style={{ border: '1px solid var(--border)', borderRadius: 8, padding: '10px 14px', marginBottom: 8 }}>
              <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
                <b className="mono">{n.node_name}</b>
                <span className={`tag ${n.state === 'done' ? 't-green' : n.state === 'failed' ? 't-red' : n.state === 'running' ? 't-blue' : n.state === 'skipped' ? 't-gray' : 't-gray'}`}>{n.state}{n.attempts > 1 ? ` (attempt ${n.attempts})` : ''}</span>
                {n.failed_step && <span className="r" style={{ fontSize: 12 }}>failed at {n.failed_step}</span>}
                <span className="mut" style={{ marginLeft: 'auto', fontSize: 12 }}>{done}/{total} steps</span>
              </div>
              <div style={{ display: 'flex', gap: 6, marginTop: 8 }}>
                {stepOrder.map(s => {
                  const st = n.step_states[s]
                  return (
                    <span key={s} title={`${STEP_LABELS[s] || s}: ${st}`}
                      style={{
                        fontSize: 11, padding: '2px 8px', borderRadius: 10,
                        background: st === 'done' ? 'rgba(63,185,80,.14)' : st === 'failed' ? 'rgba(248,81,73,.14)' : st === 'running' ? 'rgba(88,166,255,.14)' : 'var(--panel2)',
                        color: st === 'done' ? 'var(--green)' : st === 'failed' ? 'var(--red)' : st === 'running' ? 'var(--blue)' : 'var(--muted)',
                      }}>
                      {st === 'done' ? '✓ ' : st === 'failed' ? '✕ ' : ''}{STEP_LABELS[s] || s}
                    </span>
                  )
                })}
              </div>
            </div>
          )
        })}
      </div>

      <div className="card">
        <h2>Live log</h2>
        <div className="log" ref={logRef}>
          {events.length === 0 ? <div className="dim">// waiting for events…</div> : events.map(renderEvent)}
        </div>
      </div>
    </>
  )
}
