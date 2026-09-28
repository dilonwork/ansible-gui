import { useEffect, useState } from 'react'
import { api, type Host } from '../api'

export default function Hosts() {
  const [hosts, setHosts] = useState<Host[]>([])
  const [name, setName] = useState('')
  const [address, setAddress] = useState('')
  const [port, setPort] = useState('22')
  const [username, setUsername] = useState('root')
  const [pkey, setPkey] = useState('')
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState(false)

  const refresh = () => api.listHosts().then(setHosts).catch(() => setMsg('✕ Cannot reach backend'))
  useEffect(() => { refresh() }, [])

  const add = async () => {
    if (!name || !address || !pkey) { setMsg('✕ Name, address and private key are required'); return }
    setBusy(true); setMsg('Probing host (ssh-keyscan)…')
    try {
      await api.addHost({ name, address, port: +port || 22, username, private_key: pkey })
      setMsg('✓ Host added, host key verified')
      setName(''); setAddress(''); setPkey('')
      refresh()
    } catch (e: any) {
      setMsg('✕ ' + e.message)
    } finally { setBusy(false) }
  }

  const del = async (id: string) => {
    await api.delHost(id)
    refresh()
  }

  return (
    <>
      <div className="page-head"><h1>Hosts</h1><span className="tag t-gray">{hosts.length}</span></div>
      <div className="page-sub">Adding a host runs an SSH probe (keyscan); the host key is verified on every run</div>

      <div className="grid2">
        <div className="card">
          <h2>Add host</h2>
          <label>Name</label>
          <input value={name} onChange={e => setName(e.target.value)} placeholder="k3s-worker-01" />
          <div className="frow">
            <div><label>IP / hostname</label>
              <input value={address} onChange={e => setAddress(e.target.value)} placeholder="192.168.31.21" /></div>
            <div><label>Port</label>
              <input value={port} onChange={e => setPort(e.target.value)} /></div>
          </div>
          <label>SSH user</label>
          <input value={username} onChange={e => setUsername(e.target.value)} />
          <label>SSH private key</label>
          <textarea rows={4} value={pkey} onChange={e => setPkey(e.target.value)}
            placeholder="-----BEGIN OPENSSH PRIVATE KEY-----" />
          <button className="btn" onClick={add} disabled={busy}>＋ Add host</button>
          <div className="form-msg mut">{msg}</div>
        </div>

        <div className="card">
          <h2>Host list</h2>
          {hosts.length === 0 ? <div className="empty">No hosts yet</div> : (
            <table>
              <thead><tr><th>Name</th><th>Connection</th><th></th></tr></thead>
              <tbody>
                {hosts.map(h => (
                  <tr key={h.id}>
                    <td className="mono">{h.name}</td>
                    <td className="mut">{h.username}@{h.address}:{h.port}</td>
                    <td style={{textAlign: 'right'}}>
                      <button className="btn danger-ghost" onClick={() => del(h.id)}>Delete</button>
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
