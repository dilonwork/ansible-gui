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
  type: 'job_started' | 'task_start' | 'host_ok' | 'host_failed' | 'host_unreachable' | 'job_finished' | 'job_cancelled' | 'job_interrupted' | 'job_error' | 'eof'
  ts?: number
  host?: string
  task?: string
  msg?: string
}

export interface Job {
  id: string
  host_ids: string[]
  status: 'running' | 'successful' | 'failed' | 'cancelled' | 'interrupted'
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
  cancelJob: (id: string) => req<{ ok: boolean }>(`/api/jobs/${id}/cancel`, { method: 'POST' }),
  retryJob: (id: string) => req<{ job_id: string }>(`/api/jobs/${id}/retry`, { method: 'POST' }),

  listMaintenance: () => req<MaintSummary[]>('/api/maintenance'),
  createMaintenance: (body: { name: string; workflow: string; node_ids: string[]; kubeconfig?: string; params?: Record<string, unknown> }) =>
    req<{ run_id: string }>('/api/maintenance', { method: 'POST', body: JSON.stringify(body) }),
  getMaintenance: (id: string) => req<MaintRun>(`/api/maintenance/${id}`),
  pauseMaintenance: (id: string) => req<{ ok: boolean }>(`/api/maintenance/${id}/pause`, { method: 'POST' }),
  resumeMaintenance: (id: string) => req<{ ok: boolean }>(`/api/maintenance/${id}/resume`, { method: 'POST' }),
  retryMaintNode: (id: string) => req<{ ok: boolean }>(`/api/maintenance/${id}/retry-node`, { method: 'POST' }),
  skipMaintNode: (id: string) => req<{ ok: boolean }>(`/api/maintenance/${id}/skip-node`, { method: 'POST' }),
  abortMaintenance: (id: string) => req<{ ok: boolean }>(`/api/maintenance/${id}/abort`, { method: 'POST' }),
}

export function maintWsUrl(id: string): string {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  return `${proto}://${location.host}/ws/maintenance/${id}`
}

export function jobWsUrl(id: string): string {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  return `${proto}://${location.host}/ws/jobs/${id}`
}

export interface MaintNodeStep {
  node_id: string
  node_name: string
  k8s_name: string
  state: 'pending' | 'running' | 'done' | 'failed' | 'skipped'
  attempts: number
  step_states: Record<string, 'pending' | 'running' | 'done' | 'failed' | 'skipped'>
  failed_step: string | null
}

export interface MaintCheck {
  name: string
  status: 'passed' | 'warning' | 'failed'
  detail: string
}

export interface MaintEvent {
  type: string
  ts?: number
  node?: string
  step?: string
  task?: string
  host?: string
  msg?: string
  ok?: boolean
  failed?: string[]
  workflow?: string
}

export interface MaintRun {
  id: string
  name: string
  workflow: 'os-patch' | 'kubelet-upgrade'
  node_ids: string[]
  status: 'running' | 'pausing' | 'paused' | 'failed' | 'completed' | 'aborted'
  steps: MaintNodeStep[]
  preflight: MaintCheck[]
  snapshot: Record<string, unknown>
  events: MaintEvent[]
  created_at: number
  finished_at: number | null
  has_k8s: boolean
}

export interface MaintSummary {
  id: string
  name: string
  workflow: string
  node_ids: string[]
  status: MaintRun['status']
  nodes_done: number
  nodes_total: number
  created_at: number
  finished_at: number | null
}
