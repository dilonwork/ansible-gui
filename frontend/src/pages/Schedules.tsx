import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, type JobTemplate, type ScheduleItem } from '../api'

const TIMEZONES = ['UTC', 'America/Phoenix', 'America/Los_Angeles', 'America/New_York', 'Asia/Taipei', 'Europe/London']

function fmtTs(ts: number | null): string {
  if (!ts) return '—'
  return new Date(ts * 1000).toLocaleString('en-US', { hour12: false })
}

export default function Schedules() {
  const [list, setList] = useState<ScheduleItem[]>([])
  const [templates, setTemplates] = useState<JobTemplate[]>([])
  const [name, setName] = useState('')
  const [templateId, setTemplateId] = useState('')
  const [cron, setCron] = useState('0 2 * * *')
  const [timezone, setTimezone] = useState('UTC')
  const [enabled, setEnabled] = useState(true)
  const [preview, setPreview] = useState<{ human: string; next_runs: number[] } | null>(null)
  const [previewErr, setPreviewErr] = useState('')
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState(false)
  const [editing, setEditing] = useState<ScheduleItem | null>(null)
  const previewTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  const refresh = () => {
    api.listSchedules().then(setList).catch(() => setMsg('✕ Cannot reach backend'))
    api.listTemplates().then(setTemplates).catch(() => {})
  }
  useEffect(() => { refresh() }, [])
  useEffect(() => {
    const t = setInterval(refresh, 10000)
    return () => clearInterval(t)
  }, [])

  // debounced cron preview
  useEffect(() => {
    if (previewTimer.current) clearTimeout(previewTimer.current)
    if (!cron.trim()) { setPreview(null); setPreviewErr(''); return }
    previewTimer.current = setTimeout(() => {
      api.previewSchedule({ cron: cron.trim(), timezone })
        .then(p => { setPreview(p); setPreviewErr('') })
        .catch((e: Error) => { setPreview(null); setPreviewErr(e.message) })
    }, 400)
    return () => { if (previewTimer.current) clearTimeout(previewTimer.current) }
  }, [cron, timezone])

  const resetForm = () => {
    setName(''); setTemplateId(''); setCron('0 2 * * *')
    setTimezone('UTC'); setEnabled(true); setEditing(null)
  }

  const save = async () => {
    if (!name.trim() || !templateId || !cron.trim()) { setMsg('✕ Name, template and cron are required'); return }
    if (previewErr) { setMsg('✕ Fix the cron expression first'); return }
    setBusy(true)
    try {
      const body = { name: name.trim(), template_id: templateId, cron: cron.trim(), timezone, enabled }
      if (editing) {
        await api.updateSchedule(editing.id, body)
        setMsg('✓ Schedule updated')
      } else {
        await api.createSchedule(body)
        setMsg('✓ Schedule created')
      }
      resetForm()
      refresh()
    } catch (e: unknown) { setMsg('✕ ' + (e as Error).message) }
    finally { setBusy(false) }
  }

  const startEdit = (s: ScheduleItem) => {
    setEditing(s)
    setName(s.name); setTemplateId(s.template_id); setCron(s.cron)
    setTimezone(s.timezone); setEnabled(s.enabled)
    window.scrollTo({ top: 0 })
  }

  const toggleEnabled = async (s: ScheduleItem) => {
    try {
      await api.updateSchedule(s.id, {
        name: s.name, template_id: s.template_id, cron: s.cron,
        timezone: s.timezone, enabled: !s.enabled,
      })
      refresh()
    } catch (e: unknown) { setMsg('✕ ' + (e as Error).message) }
  }

  const runNow = async (s: ScheduleItem) => {
    try {
      const { job_id } = await api.runScheduleNow(s.id)
      setMsg(`✓ Launched job ${job_id}`)
      refresh()
    } catch (e: unknown) { setMsg('✕ ' + (e as Error).message) }
  }

  const del = async (s: ScheduleItem) => {
    if (!window.confirm(`Delete schedule "${s.name}"?`)) return
    await api.deleteSchedule(s.id)
    refresh()
  }

  return (
    <>
      <div className="page-head"><h1>Schedules</h1><span className="tag t-gray">{list.length}</span></div>
      <div className="page-sub">Cron schedules launch job templates on a recurring basis. The pinned playbook snapshot is used as-is; schedules never edit a template.</div>

      <div className="grid2">
        <div className="card">
          <h2>{editing ? 'Edit schedule' : 'New schedule'}</h2>
          <label>Name</label>
          <input value={name} onChange={e => setName(e.target.value)} placeholder="nightly-os-patch" />
          <label>Template</label>
          <select value={templateId} onChange={e => setTemplateId(e.target.value)}>
            <option value="">— pick a template —</option>
            {templates.map(t => <option key={t.id} value={t.id}>{t.name}</option>)}
          </select>
          <label>Cron expression</label>
          <input value={cron} onChange={e => setCron(e.target.value)} placeholder="0 2 * * *" className="mono" />
          {preview && !previewErr && (
            <div className="dim" style={{ fontSize: 13, marginTop: 4 }}>
              {preview.human}
              <div className="mono" style={{ marginTop: 2 }}>
                {preview.next_runs.map(ts => <div key={ts}>{fmtTs(ts)}</div>)}
              </div>
            </div>
          )}
          {previewErr && <div style={{ color: 'var(--red)', fontSize: 13, marginTop: 4 }}>✕ {previewErr}</div>}
          <label>Timezone</label>
          <select value={timezone} onChange={e => setTimezone(e.target.value)}>
            {TIMEZONES.map(z => <option key={z} value={z}>{z}</option>)}
          </select>
          <label style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <input type="checkbox" style={{ width: 'auto' }} checked={enabled} onChange={e => setEnabled(e.target.checked)} />
            Enabled
          </label>
          <div style={{ display: 'flex', gap: 8, marginTop: 12 }}>
            <button className="btn primary" disabled={busy} onClick={save}>{editing ? 'Save' : 'Create schedule'}</button>
            {editing && <button className="btn" onClick={resetForm}>Cancel</button>}
          </div>
          {msg && <div className="form-msg">{msg}</div>}
        </div>

        <div className="card">
          <h2>How it works</h2>
          <div className="dim" style={{ fontSize: 13, lineHeight: 1.7 }}>
            <p>A beat process checks every minute and fires due schedules. Next-run state is persisted, so the cadence survives backend restarts.</p>
            <p>Missed occurrences while the backend was down are counted as <b>missed</b> — at most one catch-up run fires, then the normal cadence resumes.</p>
            <p>A schedule never fires while its previous run is still going (<b>no overlapping runs</b>).</p>
            <p><b>Run now</b> launches immediately without shifting the cadence.</p>
          </div>
        </div>
      </div>

      <div className="card">
        <h2>All schedules</h2>
        {list.length === 0 ? <div className="empty">No schedules yet.</div> : (
          <table>
            <thead><tr><th>Name</th><th>Template</th><th>Cron</th><th>Next run</th><th>Last run</th><th>Enabled</th><th></th></tr></thead>
            <tbody>
              {list.map(s => (
                <tr key={s.id}>
                  <td><b>{s.name}</b><div className="dim" style={{ fontSize: 11 }}>{s.human}</div></td>
                  <td className="mut">{s.template_name}</td>
                  <td className="mono" style={{ fontSize: 12 }}>{s.cron}<div className="dim" style={{ fontSize: 11 }}>{s.timezone}</div></td>
                  <td className="mut" style={{ fontSize: 12 }}>{s.enabled ? fmtTs(s.next_run_at) : <span className="dim">paused</span>}</td>
                  <td style={{ fontSize: 12 }}>
                    {s.last_job_id
                      ? <><Link to={`/jobs/${s.last_job_id}`} className="mono">{s.last_job_id.slice(0, 8)}</Link> <span className="dim">{s.last_status}</span></>
                      : <span className="dim">—</span>}
                    {s.missed_count > 0 && <div className="warn" style={{ fontSize: 11 }}>⚠ {s.missed_count} missed</div>}
                    {s.skipped_overlap > 0 && <div className="dim" style={{ fontSize: 11 }}>{s.skipped_overlap} skipped (overlap)</div>}
                  </td>
                  <td>
                    <button className="btn" style={{ padding: '2px 10px', fontSize: 12 }}
                      onClick={() => toggleEnabled(s)}>{s.enabled ? 'On' : 'Off'}</button>
                  </td>
                  <td style={{ whiteSpace: 'nowrap' }}>
                    <button className="btn" style={{ padding: '2px 10px', fontSize: 12 }} onClick={() => runNow(s)}>Run now</button>{' '}
                    <button className="btn" style={{ padding: '2px 10px', fontSize: 12 }} onClick={() => startEdit(s)}>Edit</button>{' '}
                    <button className="btn danger" style={{ padding: '2px 10px', fontSize: 12 }} onClick={() => del(s)}>Delete</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </>
  )
}
