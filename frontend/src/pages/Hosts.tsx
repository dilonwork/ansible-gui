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

  const refresh = () => api.listHosts().then(setHosts).catch(() => setMsg('✕ 連不上後端'))
  useEffect(() => { refresh() }, [])

  const add = async () => {
    if (!name || !address || !pkey) { setMsg('✕ 名稱、位址、私鑰都要填'); return }
    setBusy(true); setMsg('探測中（ssh-keyscan）…')
    try {
      await api.addHost({ name, address, port: +port || 22, username, private_key: pkey })
      setMsg('✓ 已新增，host key 探測通過')
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
      <div className="page-head"><h1>主機管理</h1><span className="tag t-gray">{hosts.length} 台</span></div>
      <div className="page-sub">新增主機會做 SSH 探測（keyscan），host key 存下來之後執行時驗證</div>

      <div className="grid2">
        <div className="card">
          <h2>新增主機</h2>
          <label>名稱</label>
          <input value={name} onChange={e => setName(e.target.value)} placeholder="k3s-worker-01" />
          <div className="frow">
            <div><label>IP / 主機名</label>
              <input value={address} onChange={e => setAddress(e.target.value)} placeholder="192.168.31.21" /></div>
            <div><label>Port</label>
              <input value={port} onChange={e => setPort(e.target.value)} /></div>
          </div>
          <label>SSH 使用者</label>
          <input value={username} onChange={e => setUsername(e.target.value)} />
          <label>SSH 私鑰</label>
          <textarea rows={4} value={pkey} onChange={e => setPkey(e.target.value)}
            placeholder="-----BEGIN OPENSSH PRIVATE KEY-----" />
          <button className="btn" onClick={add} disabled={busy}>＋ 新增主機</button>
          <div className="form-msg mut">{msg}</div>
        </div>

        <div className="card">
          <h2>主機清單</h2>
          {hosts.length === 0 ? <div className="empty">尚未新增主機</div> : (
            <table>
              <thead><tr><th>名稱</th><th>連線</th><th></th></tr></thead>
              <tbody>
                {hosts.map(h => (
                  <tr key={h.id}>
                    <td className="mono">{h.name}</td>
                    <td className="mut">{h.username}@{h.address}:{h.port}</td>
                    <td style={{textAlign: 'right'}}>
                      <button className="btn danger-ghost" onClick={() => del(h.id)}>刪除</button>
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
