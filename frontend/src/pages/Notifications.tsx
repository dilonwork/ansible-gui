import { useEffect, useState } from 'react'
import { api, type NotifyChannel } from '../api'

export default function Notifications() {
  const [list, setList] = useState<NotifyChannel[]>([])
  const [name, setName] = useState('')
  const [url, setUrl] = useState('')
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState(false)
  const [testing, setTesting] = useState<string | null>(null)

  const refresh = () => {
    api.listChannels().then(setList).catch(() => setMsg('✕ Cannot reach backend'))
  }
  useEffect(() => { refresh() }, [])

  const add = async () => {
    if (!name.trim() || !url.trim()) { setMsg('✕ Name and webhook URL are required'); return }
    setBusy(true)
    try {
      await api.addChannel({ name: name.trim(), type: 'webhook', config: { url: url.trim() } })
      setMsg('✓ Channel added')
      setName(''); setUrl('')
      refresh()
    } catch (e: unknown) { setMsg('✕ ' + (e as Error).message) }
    finally { setBusy(false) }
  }

  const test = async (c: NotifyChannel) => {
    setTesting(c.id)
    setMsg('')
    try {
      const r = await api.testChannel(c.id)
      setMsg(`✓ Test delivered to ${c.name} (HTTP ${r.status})`)
    } catch (e: unknown) { setMsg('✕ ' + (e as Error).message) }
    finally { setTesting(null) }
  }

  const del = async (id: string) => {
    if (!window.confirm('Delete this channel?')) return
    await api.deleteChannel(id)
    refresh()
  }

  return (
    <>
      <div className="page-head"><h1>Notifications</h1><span className="tag t-gray">{list.length}</span></div>
      <div className="page-sub">Job outcomes are pushed to enabled channels per each template's policy (always / on failure only / never). v1 supports webhooks; LINE and Slack are next.</div>

      <div className="grid2">
        <div className="card">
          <h2>New webhook channel</h2>
          <label>Name</label>
          <input value={name} onChange={e => setName(e.target.value)} placeholder="ops-webhook" />
          <label>Webhook URL</label>
          <input value={url} onChange={e => setUrl(e.target.value)} placeholder="https://example.com/hook" className="mono" />
          <div className="dim" style={{ fontSize: 12, marginTop: 6, lineHeight: 1.6 }}>
            POSTs JSON on job finish: <span className="mono">event, job_id, status, template_name, hosts_total, hosts_succeeded, failed_hosts, duration_s, link</span>
          </div>
          <div style={{ marginTop: 12 }}>
            <button className="btn primary" disabled={busy} onClick={add}>Add channel</button>
          </div>
          {msg && <div className="form-msg">{msg}</div>}
        </div>

        <div className="card">
          <h2>How it works</h2>
          <div className="dim" style={{ fontSize: 13, lineHeight: 1.7 }}>
            <p>When a template job finishes, Drydock checks the template's notification policy and POSTs the outcome to every enabled channel.</p>
            <p><b>On failure only</b> (default) covers failed, cancelled and interrupted runs. Use <b>Send test</b> to verify a channel before relying on it.</p>
            <p>Ad-hoc ping jobs never notify — you're already watching them.</p>
          </div>
        </div>
      </div>

      <div className="card">
        <h2>Channels</h2>
        {list.length === 0 ? <div className="empty">No channels yet — add a webhook to start receiving job notifications.</div> : (
          <table>
            <thead><tr><th>Name</th><th>Type</th><th>URL</th><th></th></tr></thead>
            <tbody>
              {list.map(c => (
                <tr key={c.id}>
                  <td><b>{c.name}</b></td>
                  <td><span className="tag t-gray">{c.type}</span></td>
                  <td className="mono" style={{ fontSize: 12 }}>{String((c.config as Record<string, unknown>).url || '—')}</td>
                  <td style={{ whiteSpace: 'nowrap', textAlign: 'right' }}>
                    <button className="btn" style={{ padding: '2px 10px', fontSize: 12 }}
                      disabled={testing === c.id} onClick={() => test(c)}>
                      {testing === c.id ? 'Sending…' : 'Send test'}
                    </button>{' '}
                    <button className="btn danger" style={{ padding: '2px 10px', fontSize: 12 }} onClick={() => del(c.id)}>Delete</button>
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
