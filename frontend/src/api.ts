export interface Host {
  id: string
  name: string
  address: string
  port: number
  username: string
}

export interface Playbook {
  id: string
  name: string
  created_at: number
  content?: string
}

export interface JobTemplate {
  id: string
  name: string
  playbook_id: string
  playbook_name: string
  host_ids: string[]
  extra_vars: Record<string, unknown>
  check_mode: boolean
  created_at: number
}

export interface JobSnapshot {
  kind: 'ad-hoc' | 'template'
  template_id?: string
  template_name?: string
  playbook_id?: string
  playbook_name: string
  playbook_content: string
  host_ids: string[]
  extra_vars: Record<string, unknown>
  check_mode: boolean
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
  snapshot: JobSnapshot
  events?: JobEvent[]
}

export interface JobSummary {
  id: string
  kind: 'ad-hoc' | 'template'
  template_name?: string
  playbook_name: string
  check_mode: boolean
  host_ids: string[]
  status: Job['status']
  created_at: number
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

  listPlaybooks: () => req<Playbook[]>('/api/playbooks'),
  getPlaybook: (id: string) => req<Playbook>(`/api/playbooks/${id}`),
  addPlaybook: (data: { name: string; content: string }) =>
    req<{ id: string }>('/api/playbooks', { method: 'POST', body: JSON.stringify(data) }),
  delPlaybook: (id: string) => req<{ ok: boolean }>(`/api/playbooks/${id}`, { method: 'DELETE' }),
  syntaxCheck: (id: string) =>
    req<{ ok: boolean; output: string }>(`/api/playbooks/${id}/syntax-check`, { method: 'POST' }),

  listTemplates: () => req<JobTemplate[]>('/api/templates'),
  addTemplate: (data: { name: string; playbook_id: string; host_ids: string[]; extra_vars: Record<string, unknown>; check_mode: boolean }) =>
    req<{ id: string }>('/api/templates', { method: 'POST', body: JSON.stringify(data) }),
  delTemplate: (id: string) => req<{ ok: boolean }>(`/api/templates/${id}`, { method: 'DELETE' }),

  listJobs: () => req<JobSummary[]>('/api/jobs'),
  createJob: (body: { host_ids?: string[]; template_id?: string; check_mode?: boolean; extra_vars?: Record<string, unknown> }) =>
    req<{ job_id: string }>('/api/jobs', { method: 'POST', body: JSON.stringify(body) }),
  getJob: (id: string) => req<Job>(`/api/jobs/${id}`),
}

export function jobWsUrl(id: string): string {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  return `${proto}://${location.host}/ws/jobs/${id}`
}
