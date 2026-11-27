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

export type DependentStatus = {
  module: string
  entry_id: number
  entry: Record<string, unknown>
  upstream: { module: string; id: number; name: string; status: string }[]
  downstream: { module: string; id: number; name: string; status: string }[]
}

export async function fetchDependentStatus(
  moduleKey: string,
  entryId: number,
): Promise<DependentStatus> {
  const response = await request(`/api/cross-module/dependent-status/${moduleKey}/${entryId}`)
  if (!response.ok) throw new Error('跨模块状态查询失败')
  return await response.json()
}

export async function fetchDependencyGraph(): Promise<{ nodes: unknown[]; edges: unknown[] }> {
  const response = await request('/api/cross-module/dependency-graph')
  if (!response.ok) throw new Error('依赖图加载失败')
  return await response.json()
}

// ── 档案入库清单（凭据）──────────────────────────────────────────────
export type IntakeCredential = {
  id: number
  '档案编号': number
  '档案名称': string
  '档案类别': string
  '形成日期': string
  '来源验收记录': number | null
  '来源验收工序': string
  '来源验收日期': string
  '凭据版本': number
  '关联状态': string
  '关联方式': string
  '最近动作号': string
  '备注说明': string
  [field: string]: string | number | null
}

export type IntakeMigrationState = {
  '状态': string
  '迁移次数': number
  '回滚次数': number
  '更新时间': string
}

export type IntakeListResult = {
  items: IntakeCredential[]
  total: number
  page: number
  size: number
  migration: IntakeMigrationState
}

export type IntakeEvent = {
  id: number
  '动作': string
  '动作号': string
  '来源验收记录': number | null
  '凭据版本': number
  '时间': string
  '备注说明': string
}

export async function listIntake(filters: Record<string, string> = {}): Promise<IntakeListResult> {
  const query = new URLSearchParams(filters).toString()
  const response = await request(`/api/archive-intake?${query}`)
  if (!response.ok) throw new Error('档案入库清单读取失败')
  return await response.json()
}

export async function fetchIntakeEvents(credentialId: number): Promise<{ events: IntakeEvent[] }> {
  const response = await request(`/api/archive-intake/${credentialId}/events`)
  if (!response.ok) throw new Error('入库凭据动作轨迹读取失败')
  return await response.json()
}

export async function migrateIntake(): Promise<{ ok: boolean; message: string }> {
  const response = await request('/api/archive-intake/migrate', { method: 'POST' })
  return await response.json()
}

export async function rollbackIntake(): Promise<{ ok: boolean; message: string }> {
  const response = await request('/api/archive-intake/rollback', { method: 'POST' })
  return await response.json()
}

export async function restoreIntake(): Promise<{ ok: boolean; message: string }> {
  const response = await request('/api/archive-intake/restore', { method: 'POST' })
  return await response.json()
}

export async function confirmIntakeSource(
  archiveId: number,
  sourceId: number,
): Promise<ActionResult> {
  const response = await request(`/api/archive_mgmt/${archiveId}/actions`, {
    method: 'POST',
    body: JSON.stringify({
      action: '确认入库来源',
      values: { 来源验收记录: sourceId },
    }),
  })
  return await response.json()
}
