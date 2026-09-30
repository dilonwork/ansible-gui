import { useEffect, useState } from 'react'
import { api, type Host, type JobTemplate, type Playbook } from '../api'

export default function Templates() {
  const [list, setList] = useState<JobTemplate[]>([])
  const [playbooks, setPlaybooks] = useState<Playbook[]>([])
  const [hosts, setHosts] = useState<Host[]>([])
  const [name, setName] = useState('')
  const [pbId, setPbId] = useState('')
  const [sel, setSel] = useState<Set<string>>(new Set())
  const [varsText, setVarsText] = useState('{}')
  const [checkMode, setCheckMode] = useState(false)
  const [notifyPolicy, setNotifyPolicy] = useState('failure_only')
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState(false)

  const refresh = () => {
    api.listTemplates().then(setList).catch(() => setMsg('✕ Cannot reach backend'))
    api.listPlaybooks().then(setPlaybooks).catch(() => {})
    api.listHosts().then(setHosts).catch(() => {})
  }
  useEffect(() => { refresh() }, [])

  const toggle = (id: string) => {
    const n = new Set(sel)
    n.has(id) ? n.delete(id) : n.add(id)
    setSel(n)
  }

  const add = async () => {
    let extra_vars: Record<string, unknown> = {}
    try { extra_vars = varsText.trim() ? JSON.parse(varsText) : {} }
    catch { setMsg('✕ extra_vars is not valid JSON'); return }
    if (!name || !pbId || sel.size === 0) { setMsg('✕ Name, playbook and at least one host are required'); return }
    setBusy(true)
    try {
      await api.addTemplate({ name, playbook_id: pbId, host_ids: [...sel], extra_vars, check_mode: checkMode, notification_policy: notifyPolicy })
      setMsg('✓ Template created')
      setName(''); setPbId(''); setSel(new Set()); setVarsText('{}'); setCheckMode(false)
      refresh()
    } catch (e: any) { setMsg('✕ ' + e.message) }
    finally { setBusy(false) }
  }

  const del = async (id: string) => {
    await api.delTemplate(id)
    refresh()
  }

  return (
    <>
      <div className="page-head"><h1>Job Templates</h1><span className="tag t-gray">{list.length}</span></div>
      <div className="page-sub">Bind playbook + hosts + vars into a repeatable unit. Launching a job freezes a snapshot.</div>

      <div className="grid2">
        <div className="card">
          <h2>New template</h2>
          <label>Name</label>
          <input value={name} onChange={e => setName(e.target.value)} placeholder="weekly-os-update" />
          <label>Playbook</label>
          <select value={pbId} onChange={e => setPbId(e.target.value)}>
            <option value="">— select —</option>
            {playbooks.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
          <label>Target hosts</label>
          {hosts.length === 0 ? <div className="empty">Add hosts first</div> :
            hosts.map(h => (
              <label key={h.id} className="checkline">
                <input type="checkbox" checked={sel.has(h.id)} onChange={() => toggle(h.id)} />
                <span className="mono">{h.name}</span>
                <small>{h.username}@{h.address}</small>
              </label>
            ))}
          <label>extra_vars (JSON)</label>
          <textarea rows={3} value={varsText} onChange={e => setVarsText(e.target.value)} placeholder='{"pkg": "vim"}' />
          <label className="checkline">
            <input type="checkbox" checked={checkMode} onChange={e => setCheckMode(e.target.checked)} />
            Check mode by default <small>(dry-run, --check)</small>
          </label>
          <label>Notifications</label>
          <select value={notifyPolicy} onChange={e => setNotifyPolicy(e.target.value)}>
            <option value="failure_only">On failure only</option>
            <option value="always">Always</option>
            <option value="never">Never</option>
          </select>
          <button className="btn" onClick={add} disabled={busy}>＋ Create template</button>
          <div className="form-msg mut">{msg}</div>
        </div>

        <div className="card">
          <h2>Template list</h2>
          {list.length === 0 ? <div className="empty">No templates yet</div> : (
            <table>
              <thead><tr><th>Name</th><th>Playbook</th><th>Hosts</th><th>Mode</th><th>Notify</th><th></th></tr></thead>
              <tbody>
                {list.map(t => (
                  <tr key={t.id}>
                    <td className="mono">{t.name}</td>
                    <td className="mut">{t.playbook_name}</td>
                    <td className="mut">{t.host_ids.length}</td>
                    <td>{t.check_mode ? <span className="tag t-amber">check</span> : <span className="tag t-gray">normal</span>}</td>
                    <td className="mut" style={{ fontSize: 12 }}>{t.notification_policy === 'always' ? 'always' : t.notification_policy === 'never' ? 'never' : 'on failure'}</td>
                    <td style={{textAlign: 'right'}}>
                      <button className="btn danger-ghost" onClick={() => del(t.id)}>Delete</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </>
  )
}
