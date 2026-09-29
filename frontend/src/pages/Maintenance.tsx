import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, type Host, type MaintSummary } from '../api'

const STATUS_TAG: Record<string, string> = {
  running: 't-blue', pausing: 't-blue', paused: 't-amber',
  failed: 't-red', completed: 't-green', aborted: 't-gray',
}

export default function Maintenance() {
  const [runs, setRuns] = useState<MaintSummary[]>([])
  const [hosts, setHosts] = useState<Host[]>([])
  const [showForm, setShowForm] = useState(false)
  const [name, setName] = useState('')
  const [workflow, setWorkflow] = useState('os-patch')
  const [sel, setSel] = useState<string[]>([])
  const [kubeconfig, setKubeconfig] = useState('')
  const [kubeletVersion, setKubeletVersion] = useState('')
  const [msg, setMsg] = useState('')

  const load = () => {
    api.listMaintenance().then(setRuns).catch(() => {})
    api.listHosts().then(setHosts).catch(() => {})
  }
  useEffect(load, [])
  useEffect(() => {
    const t = setInterval(() => api.listMaintenance().then(setRuns).catch(() => {}), 5000)
    return () => clearInterval(t)
  }, [])

  const toggle = (id: string) =>
    setSel(s => s.includes(id) ? s.filter(x => x !== id) : [...s, id])

  const create = async () => {
    setMsg('')
    if (!name.trim() || sel.length === 0) { setMsg('Name and at least one node are required.'); return }
    if (workflow === 'kubelet-upgrade' && !kubeletVersion.trim()) { setMsg('Kubelet version is required for kubelet-upgrade.'); return }
    try {
      const body: Record<string, unknown> = {
        name: name.trim(), workflow, node_ids: sel,
        params: workflow === 'kubelet-upgrade' ? { kubelet_version: kubeletVersion.trim() } : {},
      }
      if (kubeconfig.trim()) body.kubeconfig = kubeconfig.trim()
      const { run_id } = await api.createMaintenance(body as never)
      window.location.href = `/maintenance/${run_id}`
    } catch (e) {
      setMsg((e as Error).message)
    }
  }

  return (
    <>
      <div className="page-head">
        <h1>Node maintenance</h1>
        <span style={{ marginLeft: 'auto' }}>
          <button className="btn primary" onClick={() => setShowForm(v => !v)}>+ New run</button>
        </span>
      </div>
      <div className="page-sub">One-click rolling maintenance: preflight → cordon → drain → patch/upgrade → verify → uncordon, one node at a time.</div>

      {showForm && (
        <div className="card">
          <h2>New maintenance run</h2>
          <label>Run name</label>
          <input value={name} onChange={e => setName(e.target.value)} placeholder="e.g. kubelet upgrade v1.30.5 → v1.31.2" />
          <label>Workflow</label>
          <select value={workflow} onChange={e => setWorkflow(e.target.value)}>
            <option value="os-patch">OS patch (rolling, no Kubernetes needed)</option>
            <option value="kubelet-upgrade">Kubelet upgrade (needs kubeconfig for cordon/drain)</option>
          </select>
          {workflow === 'kubelet-upgrade' && (
            <>
              <label>Target kubelet version</label>
              <input value={kubeletVersion} onChange={e => setKubeletVersion(e.target.value)} placeholder="e.g. 1.31.2-1.1" className="mono" />
            </>
          )}
          <label>Nodes (in rolling order)</label>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
            {hosts.map(h => (
              <label key={h.id} style={{ display: 'flex', gap: 6, alignItems: 'center', fontSize: 13, margin: 0 }}>
                <input type="checkbox" style={{ width: 'auto' }} checked={sel.includes(h.id)} onChange={() => toggle(h.id)} />
                <span className="mono">{h.name}</span>
              </label>
            ))}
            {hosts.length === 0 && <span className="dim">No hosts yet — add some first.</span>}
          </div>
          <label>Kubeconfig (optional — enables cordon/drain/uncordon + K8s preflight)</label>
          <textarea value={kubeconfig} onChange={e => setKubeconfig(e.target.value)} rows={3}
            placeholder="Paste kubeconfig YAML here, or leave empty for OS-only rolling patch" />
          {msg && <div className="form-msg" style={{ color: 'var(--red)' }}>{msg}</div>}
          <div style={{ marginTop: 12 }}>
            <button className="btn primary" onClick={create}>Start run</button>
          </div>
        </div>
      )}

      <div className="card">
        <h2>Runs</h2>
        {runs.length === 0 ? <div className="empty">No maintenance runs yet.</div> : (
          <table>
            <thead><tr><th>Name</th><th>Workflow</th><th>Status</th><th>Progress</th><th>Started</th></tr></thead>
            <tbody>
              {runs.map(r => (
                <tr key={r.id}>
                  <td><Link to={`/maintenance/${r.id}`} className="mono">{r.name}</Link></td>
                  <td className="mut">{r.workflow}</td>
                  <td><span className={`tag ${STATUS_TAG[r.status] || 't-gray'}`}>{r.status}</span></td>
                  <td className="mut">{r.nodes_done}/{r.nodes_total} nodes</td>
                  <td className="mut">{new Date(r.created_at * 1000).toLocaleString('en-US', { hour12: false })}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </>
  )
}
