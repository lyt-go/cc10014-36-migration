<template>
  <section class="intake-panel" data-testid="archive-intake-panel">
    <header class="intake-head">
      <div>
        <h3>档案入库清单</h3>
        <p class="intake-desc">
          每份档案一张入库凭据，按原档案编号追溯来源验收。名称、类别、形成日期保持原值。
        </p>
      </div>
      <div class="intake-actions">
        <button class="btn" data-testid="intake-migrate" type="button" :disabled="busy" @click="run('migrate')">
          迁移旧档案关联
        </button>
        <button class="btn ghost" data-testid="intake-rollback" type="button" :disabled="busy" @click="run('rollback')">
          回滚迁移
        </button>
        <button class="btn ghost" data-testid="intake-restore" type="button" :disabled="busy" @click="run('restore')">
          恢复迁移
        </button>
      </div>
    </header>

    <p class="intake-state" data-testid="intake-state">
      迁移状态：{{ migration?.['状态'] ?? '—' }}
      <span v-if="migration">（迁移 {{ migration['迁移次数'] }} 次 / 回滚 {{ migration['回滚次数'] }} 次）</span>
      <span v-if="message" class="intake-message" data-testid="intake-message">{{ message }}</span>
    </p>

    <table class="data-table">
      <thead>
        <tr>
          <th>档案编号</th>
          <th>档案名称</th>
          <th>档案类别</th>
          <th>形成日期</th>
          <th>来源验收记录</th>
          <th>来源工序</th>
          <th>凭据版本</th>
          <th>关联状态</th>
          <th>操作</th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="row in items"
          :key="String(row.id)"
          :data-testid="`intake-row-${row['档案编号']}`"
        >
          <td>{{ row['档案编号'] }}</td>
          <td>{{ row['档案名称'] }}</td>
          <td>{{ row['档案类别'] }}</td>
          <td>{{ row['形成日期'] }}</td>
          <td>{{ row['来源验收记录'] ?? '待确认' }}</td>
          <td>{{ row['来源验收工序'] || '—' }}</td>
          <td>{{ row['凭据版本'] }}</td>
          <td>{{ row['关联状态'] }}</td>
          <td class="intake-row-actions">
            <template v-if="row['关联状态'] === '待确认'">
              <input
                v-model="sourceInputs[row['档案编号']]"
                :data-testid="`intake-source-${row['档案编号']}`"
                placeholder="来源验收编号"
              />
              <button
                class="link"
                type="button"
                :disabled="busy"
                :data-testid="`intake-confirm-${row['档案编号']}`"
                @click="confirmSource(row)"
              >
                确认来源
              </button>
            </template>
            <button class="link" type="button" @click="toggleEvents(row)">
              {{ expanded === row.id ? '收起轨迹' : '查看轨迹' }}
            </button>
          </td>
        </tr>
        <tr v-if="!items.length">
          <td colspan="9" class="empty-state">暂无入库凭据，可先迁移旧档案关联</td>
        </tr>
      </tbody>
    </table>

    <div v-if="expanded && events.length" class="intake-events" data-testid="intake-events">
      <h4>关联动作轨迹</h4>
      <ul>
        <li v-for="event in events" :key="event.id">
          <span class="event-time">{{ event['时间'] }}</span>
          <strong>{{ event['动作'] }}</strong>
          <span>动作号 {{ event['动作号'] }}</span>
          <span>来源验收 {{ event['来源验收记录'] ?? '—' }}</span>
          <span>凭据 v{{ event['凭据版本'] }}</span>
          <em>{{ event['备注说明'] }}</em>
        </li>
      </ul>
    </div>

    <p v-if="errorMessage" class="error-text" data-testid="intake-error">{{ errorMessage }}</p>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import {
  confirmIntakeSource,
  fetchIntakeEvents,
  listIntake,
  migrateIntake,
  restoreIntake,
  rollbackIntake,
} from '@/api/backend-service'
import type { IntakeCredential, IntakeEvent, IntakeMigrationState } from '@/api/backend-service'

const items = ref<IntakeCredential[]>([])
const migration = ref<IntakeMigrationState | null>(null)
const sourceInputs = ref<Record<number, string>>({})
const expanded = ref<number | null>(null)
const events = ref<IntakeEvent[]>([])
const busy = ref(false)
const message = ref('')
const errorMessage = ref('')

async function reload() {
  errorMessage.value = ''
  try {
    const payload = await listIntake()
    items.value = payload.items
    migration.value = payload.migration
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '入库清单读取失败'
  }
}

async function run(kind: 'migrate' | 'rollback' | 'restore') {
  busy.value = true
  message.value = ''
  errorMessage.value = ''
  try {
    const action = kind === 'migrate' ? migrateIntake : kind === 'rollback' ? rollbackIntake : restoreIntake
    const result = await action()
    message.value = result.message
    if (!result.ok) throw new Error(result.message)
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '操作失败'
  } finally {
    busy.value = false
  }
}

async function confirmSource(row: IntakeCredential) {
  const sourceId = Number(sourceInputs.value[row['档案编号']])
  if (!sourceId) {
    errorMessage.value = '请填写来源验收记录编号'
    return
  }
  busy.value = true
  message.value = ''
  errorMessage.value = ''
  try {
    const result = await confirmIntakeSource(row['档案编号'], sourceId)
    if (!result.ok) throw new Error(result.message)
    message.value = result.message
    sourceInputs.value[row['档案编号']] = ''
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '确认来源失败'
  } finally {
    busy.value = false
  }
}

async function toggleEvents(row: IntakeCredential) {
  if (expanded.value === row.id) {
    expanded.value = null
    events.value = []
    return
  }
  expanded.value = row.id
  try {
    events.value = (await fetchIntakeEvents(row.id)).events
  } catch (error) {
    events.value = []
    errorMessage.value = error instanceof Error ? error.message : '动作轨迹读取失败'
  }
}

onMounted(reload)
</script>

<style scoped>
.intake-panel { margin-top: 20px; border-top: 2px solid var(--border, #d8dee6); padding-top: 16px; }
.intake-head { display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; }
.intake-desc { color: var(--muted, #64748b); font-size: 13px; margin: 4px 0 0; }
.intake-actions { display: flex; gap: 8px; }
.intake-state { font-size: 13px; color: var(--muted, #64748b); margin: 10px 0; }
.intake-message { color: #1f6feb; margin-left: 8px; }
.intake-row-actions { display: flex; gap: 6px; align-items: center; }
.intake-row-actions input { width: 110px; padding: 4px 6px; border: 1px solid var(--border, #d8dee6); border-radius: 4px; }
.intake-events { margin-top: 12px; background: #fff; border: 1px solid var(--border, #d8dee6); border-radius: 8px; padding: 12px; }
.intake-events ul { list-style: none; margin: 0; padding: 0; }
.intake-events li { display: flex; flex-wrap: wrap; gap: 10px; font-size: 13px; padding: 6px 0; border-bottom: 1px solid #f0f2f5; }
.intake-events li em { color: var(--muted, #64748b); font-style: normal; }
.event-time { color: var(--muted, #64748b); }
</style>
