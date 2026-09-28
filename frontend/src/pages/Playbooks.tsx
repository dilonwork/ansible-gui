import { useEffect, useState } from 'react'
import { api, type Playbook } from '../api'

const SAMPLE = `- name: example
  hosts: all
  gather_facts: false
  tasks:
    - name: show hostname
      ansible.builtin.shell: hostname
      register: out
    - name: print result
      ansible.builtin.debug:
        var: out.stdout
`

export default function Playbooks() {
  const [list, setList] = useState<Playbook[]>([])
  const [name, setName] = useState('')
  const [content, setContent] = useState('')
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState(false)
  const [viewId, setViewId] = useState<string | null>(null)
  const [viewContent, setViewContent] = useState('')
  const [checking, setChecking] = useState<string | null>(null)
  const [checkResult, setCheckResult] = useState<Record<string, { ok: boolean; output: string }>>({})

  const refresh = () => api.listPlaybooks().then(setList).catch(() => setMsg('✕ Cannot reach backend'))
  useEffect(() => { refresh() }, [])

  const add = async () => {
    if (!name || !content.trim()) { setMsg('✕ Name and YAML content are required'); return }
    setBusy(true)
    try {
      await api.addPlaybook({ name, content })
      setMsg('✓ Playbook added (inline YAML; Git sync comes with M2.1)')
      setName(''); setContent('')
      refresh()
    } catch (e: any) { setMsg('✕ ' + e.message) }
    finally { setBusy(false) }
  }

  const view = async (id: string) => {
    if (viewId === id) { setViewId(null); return }
    const pb = await api.getPlaybook(id)
    setViewId(id); setViewContent(pb.content || '')
  }

  const check = async (id: string) => {
    setChecking(id)
    try {
      const r = await api.syntaxCheck(id)
      setCheckResult(prev => ({ ...prev, [id]: r }))
    } finally { setChecking(null) }
  }

  const del = async (id: string) => {
    try {
      await api.delPlaybook(id)
      refresh()
    } catch (e: any) { setMsg('✕ ' + e.message) }
  }

  return (
    <>
      <div className="page-head"><h1>Playbooks</h1><span className="tag t-gray">{list.length}</span></div>
      <div className="page-sub">Skeleton: paste YAML directly. Git is the only source of truth (M2.1) — sync UI comes later.</div>

      <div className="grid2">
        <div className="card">
          <h2>Add playbook</h2>
          <label>Name</label>
          <input value={name} onChange={e => setName(e.target.value)} placeholder="os-security-update" />
          <label>YAML content</label>
          <textarea rows={12} value={content} onChange={e => setContent(e.target.value)} placeholder={SAMPLE} />
          <div style={{display: 'flex', gap: 8}}>
            <button className="btn" onClick={add} disabled={busy}>＋ Add playbook</button>
            <button className="btn" onClick={() => setContent(SAMPLE)}>Fill example</button>
          </div>
          <div className="form-msg mut">{msg}</div>
        </div>

        <div className="card">
          <h2>Playbook list</h2>
          {list.length === 0 ? <div className="empty">No playbooks yet</div> : (
            <table>
              <thead><tr><th>Name</th><th>Added</th><th></th></tr></thead>
              <tbody>
                {list.map(p => (
                  <tr key={p.id}>
                    <td className="mono">{p.name}</td>
                    <td className="mut">{new Date(p.created_at * 1000).toLocaleString('en-US', {hour12: false})}</td>
                    <td style={{textAlign: 'right', whiteSpace: 'nowrap'}}>
                      <button className="btn danger-ghost" onClick={() => view(p.id)}>View</button>
                      <button className="btn danger-ghost" onClick={() => check(p.id)} disabled={checking === p.id}>
                        {checking === p.id ? 'Checking…' : 'Syntax check'}
                      </button>
                      <button className="btn danger-ghost" onClick={() => del(p.id)}>Delete</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {viewId && (
            <div style={{marginTop: 10}}>
              <div className="mut" style={{marginBottom: 4}}>YAML (read-only)</div>
              <pre className="log" style={{maxHeight: 260}}>{viewContent}</pre>
            </div>
          )}
          {Object.entries(checkResult).map(([id, r]) => (
            <div key={id} style={{marginTop: 8}}>
              <span className={r.ok ? 'ok' : 'bad'}>{r.ok ? '✓ Syntax OK' : '✕ Syntax error'}</span>
              {!r.ok && <pre className="log" style={{maxHeight: 200, marginTop: 4}}>{r.output}</pre>}
            </div>
          ))}
        </div>
      </div>
    </>
  )
}
