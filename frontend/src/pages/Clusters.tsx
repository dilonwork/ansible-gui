import { useEffect, useState } from 'react'
import { api, type ClusterEvent, type ClusterItem, type ClusterNode, type ClusterOverview, type ClusterWorkload } from '../api'

function fmtTs(ts: number | null | undefined): string {
  if (!ts) return '—'
  return new Date(ts * 1000).toLocaleString('en-US', { hour12: false })
}

function fmtAge(ts: number | null | undefined): string {
  if (!ts) return '—'
  const s = Math.max(0, Date.now() / 1000 - ts)
  if (s < 3600) return `${Math.floor(s / 60)}m`
  if (s < 86400) return `${Math.floor(s / 3600)}h`
  return `${Math.floor(s / 86400)}d`
}

type Tab = 'overview' | 'nodes' | 'workloads' | 'events'

export default function Clusters() {
  const [clusters, setClusters] = useState<ClusterItem[]>([])
  const [selected, setSelected] = useState<string | null>(null)
  const [overview, setOverview] = useState<ClusterOverview | null>(null)
  const [nodes, setNodes] = useState<ClusterNode[]>([])
  const [nodesStale, setNodesStale] = useState(false)
  const [workloads, setWorkloads] = useState<ClusterWorkload[]>([])
  const [events, setEvents] = useState<ClusterEvent[]>([])
  const [tab, setTab] = useState<Tab>('overview')
  const [name, setName] = useState('')
  const [kubeconfig, setKubeconfig] = useState('')
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState(false)

  const refreshList = () => {
    api.listClusters().then(cs => {
      setClusters(cs)
      if (!selected && cs.length > 0) setSelected(cs[0].id)
      if (selected && !cs.find(c => c.id === selected)) {
        setSelected(cs.length > 0 ? cs[0].id : null)
      }
    }).catch(() => setMsg('✕ Cannot reach backend'))
  }

  const refreshDetail = (cid: string) => {
    api.clusterOverview(cid).then(setOverview).catch(() => {})
    api.clusterNodes(cid).then(r => { setNodes(r.nodes || []); setNodesStale(!!r.stale) }).catch(() => {})
    api.clusterWorkloads(cid).then(r => setWorkloads(r.workloads || [])).catch(() => {})
    api.clusterEvents(cid).then(r => setEvents(r.events || [])).catch(() => {})
  }

  useEffect(() => { refreshList() }, [])
  useEffect(() => {
    if (!selected) return
    refreshDetail(selected)
    const t = setInterval(() => { refreshDetail(selected); refreshList() }, 30000)
    return () => clearInterval(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected])

  const add = async () => {
    if (!name.trim() || !kubeconfig.trim()) { setMsg('✕ Name and kubeconfig are required'); return }
    setBusy(true)
    setMsg('')
    try {
      const r = await api.addCluster({ name: name.trim(), kubeconfig: kubeconfig.trim() })
      setMsg(`✓ Connected: ${r.server} (${r.k8s_version})`)
      setName(''); setKubeconfig('')
      const cs = await api.listClusters()
      setClusters(cs)
      setSelected(r.id)
    } catch (e: unknown) { setMsg('✕ ' + (e as Error).message) }
    finally { setBusy(false) }
  }

  const retest = async (id: string) => {
    try {
      const r = await api.testCluster(id)
      setMsg(r.ok ? `✓ ${r.detail || 'reachable'}` : `✕ ${r.error}`)
    } catch (e: unknown) { setMsg('✕ ' + (e as Error).message) }
    refreshList()
    refreshDetail(id)
  }

  const del = async (c: ClusterItem) => {
    if (!window.confirm(`Delete cluster "${c.name}"?`)) return
    await api.deleteCluster(c.id)
    setOverview(null); setNodes([])
    refreshList()
  }

  const sel = clusters.find(c => c.id === selected) || null
  const stale = overview?.stale || nodesStale

  return (
    <>
      <div className="page-head"><h1>Clusters</h1><span className="tag t-gray">{clusters.length}</span></div>
      <div className="page-sub">Kubernetes context for your fleet: health overview and the node battle map. Workloads are look-but-don't-touch — changing machines is Ansible's job.</div>

      <div className="grid2">
        <div className="card">
          <h2>Onboard a cluster</h2>
          <label>Name</label>
          <input value={name} onChange={e => setName(e.target.value)} placeholder="homelab-k3s" />
          <label>Kubeconfig</label>
          <textarea value={kubeconfig} onChange={e => setKubeconfig(e.target.value)} rows={5}
            placeholder="Paste kubeconfig YAML — stored encrypted, never shown again" className="mono" />
          <div style={{ marginTop: 12 }}>
            <button className="btn primary" disabled={busy} onClick={add}>
              {busy ? 'Testing connection…' : 'Add cluster'}
            </button>
          </div>
          {msg && <div className="form-msg">{msg}</div>}
        </div>

        <div className="card">
          <h2>Clusters</h2>
          {clusters.length === 0 ? <div className="empty">No clusters yet — paste a kubeconfig to onboard one.</div> : (
            <table>
              <thead><tr><th>Name</th><th>Version</th><th>Status</th><th></th></tr></thead>
              <tbody>
                {clusters.map(c => (
                  <tr key={c.id} onClick={() => setSelected(c.id)}
                    style={{ cursor: 'pointer', background: c.id === selected ? 'var(--panel2)' : undefined }}>
                    <td><b className="mono">{c.name}</b><div className="dim" style={{ fontSize: 11 }}>{c.server}</div></td>
                    <td className="mut" style={{ fontSize: 12 }}>{c.k8s_version || '—'}</td>
                    <td>{c.status === 'ok'
                      ? <span className="tag t-green">ok</span>
                      : c.status === 'error' ? <span className="tag t-red" title={c.last_error || ''}>error</span>
                      : <span className="tag t-gray">unknown</span>}</td>
                    <td style={{ whiteSpace: 'nowrap', textAlign: 'right' }}>
                      <button className="btn" style={{ padding: '2px 10px', fontSize: 12 }}
                        onClick={e => { e.stopPropagation(); retest(c.id) }}>Retest</button>{' '}
                      <button className="btn danger" style={{ padding: '2px 10px', fontSize: 12 }}
                        onClick={e => { e.stopPropagation(); del(c) }}>Delete</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>

      {sel && (
        <>
          {stale && (
            <div className="card" style={{ borderColor: 'var(--amber)' }}>
              <span className="warn">⚠ Data stale</span>
              <span className="dim" style={{ marginLeft: 8, fontSize: 13 }}>
                {overview?.error || 'cluster unreachable'} — last sync {fmtTs(sel.last_sync_at)}
              </span>
            </div>
          )}
          <div className="card">
            <div style={{ display: 'flex', gap: 4, marginBottom: 16 }}>
              {(['overview', 'nodes', 'workloads', 'events'] as Tab[]).map(t => (
                <button key={t} className={tab === t ? 'btn primary' : 'btn'}
                  style={{ textTransform: 'capitalize' }}
                  onClick={() => setTab(t)}>
                  {t}{t === 'events' && events.some(e => e.type === 'Warning') ? ' ⚠' : ''}
                </button>
              ))}
            </div>

            {tab === 'overview' && (
              <>
                <h2>Overview — <span className="mono">{sel.name}</span></h2>
                {!overview || overview.stale ? <div className="dim">No fresh data.</div> : (
                  <div style={{ display: 'flex', gap: 24, flexWrap: 'wrap' }}>
                    <div><div className="dim" style={{ fontSize: 11 }}>VERSION</div><b className="mono">{overview.version}</b></div>
                    <div><div className="dim" style={{ fontSize: 11 }}>NODES READY</div><b>{overview.nodes_ready}/{overview.nodes_total}</b></div>
                    <div><div className="dim" style={{ fontSize: 11 }}>PODS</div><b>{overview.pods_total}</b>
                      <span className="dim" style={{ fontSize: 12 }}> {Object.entries(overview.pods_by_phase || {}).map(([k, v]) => `${k}:${v}`).join(' ')}</span></div>
                    {(overview.abnormal_pods?.length || 0) > 0 && (
                      <div><div className="dim" style={{ fontSize: 11 }}>ABNORMAL PODS</div>
                        {(overview.abnormal_pods || []).map(p => (
                          <div key={p.namespace + '/' + p.name} style={{ fontSize: 12 }}>
                            <span className="warn">⚠</span> <span className="mono">{p.namespace}/{p.name}</span>
                            <span className="dim"> {p.reason || p.phase} restarts:{p.restarts}</span>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                )}
              </>
            )}

            {tab === 'nodes' && (
              <>
                <h2>Nodes</h2>
                {nodes.length === 0 ? <div className="empty">No node data.</div> : (
                  <table>
                    <thead><tr><th>Name</th><th>Roles</th><th>Status</th><th>Kubelet</th><th>CRI</th><th>OS</th><th>CPU / Mem</th></tr></thead>
                    <tbody>
                      {nodes.map(n => (
                        <tr key={n.name}>
                          <td className="mono"><b>{n.name}</b></td>
                          <td className="mut" style={{ fontSize: 12 }}>{n.roles.join(', ')}</td>
                          <td>
                            {n.ready ? <span className="tag t-green">Ready</span> : <span className="tag t-red">NotReady</span>}{' '}
                            {n.unschedulable && <span className="tag t-amber">cordoned</span>}
                          </td>
                          <td className="mono" style={{ fontSize: 12 }}>{n.kubelet_version}</td>
                          <td className="mut" style={{ fontSize: 12 }}>{n.cri.replace('containerd://', 'containerd ')}</td>
                          <td className="mut" style={{ fontSize: 12 }}>{n.os_image}</td>
                          <td className="mut" style={{ fontSize: 12 }}>{n.cpu} / {n.memory}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </>
            )}

            {tab === 'workloads' && (
              <>
                <h2>Workloads</h2>
                {workloads.length === 0 ? <div className="empty">No workloads found.</div> : (
                  <table>
                    <thead><tr><th>Namespace / Name</th><th>Kind</th><th>Ready</th><th>Status</th><th>Image</th><th>Age</th></tr></thead>
                    <tbody>
                      {workloads.map(w => (
                        <tr key={w.kind + '/' + w.namespace + '/' + w.name}>
                          <td><span className="dim" style={{ fontSize: 11 }}>{w.namespace}/</span><span className="mono"><b>{w.name}</b></span></td>
                          <td className="mut" style={{ fontSize: 12 }}>{w.kind}</td>
                          <td className="mono" style={{ fontSize: 12 }}>{w.ready}/{w.desired}</td>
                          <td>{w.status === 'ready'
                            ? <span className="tag t-green">ready</span>
                            : w.status === 'progressing' ? <span className="tag t-amber">progressing</span>
                            : <span className="tag t-red">degraded</span>}</td>
                          <td className="mut mono" style={{ fontSize: 11 }}>{w.images.join(', ') || '—'}</td>
                          <td className="mut" style={{ fontSize: 12 }}>{fmtAge(w.created_at)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </>
            )}

            {tab === 'events' && (
              <>
                <h2>Events</h2>
                {events.length === 0 ? <div className="empty">No recent events.</div> : (
                  <table>
                    <thead><tr><th></th><th>Reason</th><th>Object</th><th>Message</th><th>Count</th><th>Last seen</th></tr></thead>
                    <tbody>
                      {events.map((e, i) => (
                        <tr key={i} style={e.type === 'Warning' ? { background: 'var(--panel2)' } : undefined}>
                          <td>{e.type === 'Warning' ? <span className="warn">⚠</span> : <span className="dim">ℹ</span>}</td>
                          <td className="mono" style={{ fontSize: 12 }}><b>{e.reason}</b></td>
                          <td className="mut" style={{ fontSize: 12 }}>{e.kind}/{e.namespace ? e.namespace + '/' : ''}{e.name}</td>
                          <td style={{ fontSize: 12, maxWidth: 420 }}>{e.message}</td>
                          <td className="mut" style={{ fontSize: 12 }}>{e.count > 1 ? `×${e.count}` : '—'}</td>
                          <td className="mut" style={{ fontSize: 12 }}>{fmtAge(e.last_seen)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </>
            )}
          </div>
        </>
      )}
    </>
  )
}
