import { request } from './client'
import { moduleMeta } from '@/data/modules'
import type { ActionResult, EntryRow, OverviewResult, PageResult } from '@/data/types'

export { moduleMeta }

export async function listEntries(
  key: string,
  filters: Record<string, string> = {},
): Promise<PageResult> {
  const query = new URLSearchParams(filters).toString()
  const response = await request(`/api/${key}?${query}`)
  if (!response.ok) throw new Error('业务数据读取失败')
  return await response.json()
}

export async function createEntry(
  key: string,
  values: Record<string, string>,
): Promise<ActionResult> {
  const response = await request(`/api/${key}`, {
    method: 'POST',
    body: JSON.stringify({ values }),
  })
  return await response.json()
}

export async function runAction(
  key: string,
  id: number,
  action: string,
): Promise<ActionResult> {
  const response = await request(`/api/${key}/${id}/actions`, {
    method: 'POST',
    body: JSON.stringify({ action }),
  })
  return await response.json()
}

export async function downloadEntries(
  key: string,
  filters: Record<string, string> = {},
): Promise<void> {
  const query = new URLSearchParams(filters).toString()
  const response = await request(`/api/${key}/export?${query}`)
  if (!response.ok) throw new Error('业务数据导出失败')
  const url = URL.createObjectURL(await response.blob())
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `${key}.csv`
  document.body.appendChild(anchor)
  anchor.click()
  document.body.removeChild(anchor)
  URL.revokeObjectURL(url)
}

export async function loadOverview(): Promise<OverviewResult> {
  const response = await request('/api/overview')
  if (!response.ok) throw new Error('运营概览读取失败')
  return await response.json()
}

async function readJson<T>(response: Response, fallbackMessage: string): Promise<T> {
  const data = await response.json().catch(() => ({}))
  if (!response.ok) {
    throw new Error((data as { message?: string; detail?: string })?.message
      || (data as { detail?: string })?.detail
      || fallbackMessage)
  }
  return data as T
}

export async function impactQuery(params: Record<string, unknown> = {}): Promise<any> {
  const response = await request('/api/impact-scope/query', {
    method: 'POST',
    body: JSON.stringify(params),
  })
  return readJson<any>(response, '影响范围查询失败')
}

export async function impactLateEvidence(params: Record<string, unknown> = {}): Promise<any> {
  const response = await request('/api/impact-scope/late-evidence', {
    method: 'POST',
    body: JSON.stringify(params),
  })
  return readJson<any>(response, '迟到证据同步失败')
}

export async function impactApply(params: Record<string, unknown>): Promise<any> {
  const response = await request('/api/impact-scope/apply', {
    method: 'POST',
    body: JSON.stringify(params),
  })
  return readJson<any>(response, '影响范围受理失败')
}

export async function impactAction(
  scopeId: string | number,
  path: string,
  params: Record<string, unknown> = {},
): Promise<any> {
  const response = await request(`/api/impact-scope/${encodeURIComponent(String(scopeId))}/${path}`, {
    method: 'POST',
    body: JSON.stringify(params),
  })
  return readJson<any>(response, '影响范围操作失败')
}

export async function impactReceipt(scopeId: string | number): Promise<any> {
  const response = await request(`/api/impact-scope/${encodeURIComponent(String(scopeId))}/receipt`)
  const data = await readJson<any>(response, '领取回执读取失败')
  if (data.ok === false) throw new Error(data.message || '领取回执不存在')
  return data
}
