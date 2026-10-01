import { useEffect, useState } from 'react'
import { api, type UserItem } from '../api'

export default function Users() {
  const [users, setUsers] = useState<UserItem[]>([])
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [role, setRole] = useState('operator')
  const [msg, setMsg] = useState('')

  const refresh = () => api.listUsers().then(setUsers).catch((e: Error) => setMsg('✕ ' + e.message))
  useEffect(() => { refresh() }, [])

  const add = async () => {
    setMsg('')
    try {
      await api.createUser({ username: username.trim(), password, role })
      setUsername(''); setPassword(''); setRole('operator')
      setMsg('✓ User created')
      refresh()
    } catch (e: unknown) { setMsg('✕ ' + (e as Error).message) }
  }

  const changeRole = async (u: UserItem, newRole: string) => {
    setMsg('')
    try { await api.updateUser(u.id, { role: newRole }); refresh() }
    catch (e: unknown) { setMsg('✕ ' + (e as Error).message) }
  }

  const resetPw = async (u: UserItem) => {
    const pw = window.prompt(`New password for ${u.username} (min 8 chars):`)
    if (!pw) return
    setMsg('')
    try { await api.updateUser(u.id, { password: pw }); setMsg('✓ Password updated') }
    catch (e: unknown) { setMsg('✕ ' + (e as Error).message) }
  }

  const del = async (u: UserItem) => {
    if (!window.confirm(`Delete user "${u.username}"?`)) return
    setMsg('')
    try { await api.deleteUser(u.id); refresh() }
    catch (e: unknown) { setMsg('✕ ' + (e as Error).message) }
  }

  return (
    <>
      <div className="page-head"><h1>Users</h1><span className="tag t-gray">{users.length}</span></div>
      <div className="page-sub">Who can board this ship. Admins manage accounts; operators run everything; viewers are read-only.</div>

      <div className="grid2">
        <div className="card">
          <h2>Add user</h2>
          <label>Username</label>
          <input value={username} onChange={e => setUsername(e.target.value)} />
          <label>Password</label>
          <input type="password" value={password} onChange={e => setPassword(e.target.value)} />
          <label>Role</label>
          <select value={role} onChange={e => setRole(e.target.value)}>
            <option value="operator">operator — run jobs & maintenance</option>
            <option value="viewer">viewer — read-only</option>
            <option value="admin">admin — full control</option>
          </select>
          <div style={{ marginTop: 12 }}>
            <button className="btn primary" onClick={add}>Add user</button>
          </div>
          {msg && <div className="form-msg">{msg}</div>}
        </div>

        <div className="card">
          <h2>Accounts</h2>
          <table>
            <thead><tr><th>Username</th><th>Role</th><th></th></tr></thead>
            <tbody>
              {users.map(u => (
                <tr key={u.id}>
                  <td className="mono"><b>{u.username}</b></td>
                  <td>
                    <select value={u.role} onChange={e => changeRole(u, e.target.value)}
                      style={{ padding: '2px 6px', fontSize: 12 }}>
                      <option value="admin">admin</option>
                      <option value="operator">operator</option>
                      <option value="viewer">viewer</option>
                    </select>
                  </td>
                  <td style={{ whiteSpace: 'nowrap', textAlign: 'right' }}>
                    <button className="btn" style={{ padding: '2px 10px', fontSize: 12 }}
                      onClick={() => resetPw(u)}>Reset password</button>{' '}
                    <button className="btn danger" style={{ padding: '2px 10px', fontSize: 12 }}
                      onClick={() => del(u)}>Delete</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </>
  )
}
