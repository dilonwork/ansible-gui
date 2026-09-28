export interface Host {
  id: string
  name: string
  address: string
  port: number
  username: string
}

export interface JobEvent {
  type: 'job_started' | 'task_start' | 'host_ok' | 'host_failed' | 'host_unreachable' | 'job_finished' | 'eof'
  ts?: number
  host?: string
  task?: string
  msg?: string
}

export interface Job {
  id: string
  host_ids: string[]
  status: 'running' | 'successful' | 'failed'
  created_at: number
  finished_at: number | null
  events?: JobEvent[]
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(path, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...(init?.headers || {}) },
  })
  if (!r.ok) {
    const j = await r.json().catch(() => ({}))
    throw new Error(j.detail || `HTTP ${r.status}`)
  }
  return r.json()
}

export const api = {
  listHosts: () => req<Host[]>('/api/hosts'),
  addHost: (data: { name: string; address: string; port: number; username: string; private_key: string }) =>
    req<{ id: string }>('/api/hosts', { method: 'POST', body: JSON.stringify(data) }),
  delHost: (id: string) => req<{ ok: boolean }>(`/api/hosts/${id}`, { method: 'DELETE' }),
  listJobs: () => req<Job[]>('/api/jobs'),
  createJob: (host_ids: string[]) =>
    req<{ job_id: string }>('/api/jobs', { method: 'POST', body: JSON.stringify({ host_ids }) }),
  getJob: (id: string) => req<Job>(`/api/jobs/${id}`),
}

export function jobWsUrl(id: string): string {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  return `${proto}://${location.host}/ws/jobs/${id}`
}
