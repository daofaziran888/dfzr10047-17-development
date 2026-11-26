<template>
  <section ref="rootRef" class="impact-panel" data-testid="impact-scope-panel">
    <div class="impact-head">
      <div>
        <h3>差异字段影响范围单</h3>
        <p>从模板差异字段出发，按获准来源、模板版本和旧表达式核对真实命中批次，受理后生成可恢复的重解析名单。</p>
      </div>
      <span class="badge" data-testid="impact-module-tag">影响范围 / 领取受理</span>
    </div>

    <form class="impact-form" data-testid="impact-search-form" @submit.prevent="runQuery">
      <label>
        <span>来源</span>
        <input v-model="criteria.source" data-testid="impact-source" aria-label="来源" placeholder="例如：甲来源 / 甲" />
      </label>
      <label>
        <span>旧表达式</span>
        <input v-model="criteria.oldExpression" data-testid="impact-old-expression" aria-label="旧表达式" placeholder="例如：amount" />
      </label>
      <label>
        <span>差异字段</span>
        <input v-model="criteria.fieldName" data-testid="impact-field" aria-label="差异字段" placeholder="amount" />
      </label>
      <label>
        <span>获准模板编号</span>
        <input v-model="criteria.templateId" data-testid="impact-template" aria-label="获准模板编号" />
      </label>
      <label>
        <span>模板版本</span>
        <input v-model="criteria.templateVersion" data-testid="impact-template-version" aria-label="模板版本" />
      </label>
      <label class="check-label">
        <input v-model="criteria.includeCompleted" type="checkbox" data-testid="impact-include-completed" />
        <span>检索已完成批次</span>
      </label>
      <button class="btn primary" type="submit" data-testid="impact-query" :disabled="loading">
        {{ loading ? '查询中…' : '按来源和旧表达式查询' }}
      </button>
    </form>

    <div
      class="evidence-bar"
      :class="{ applied: queryResult?.late_evidence?.applied }"
      data-testid="late-evidence-status"
      :data-applied="String(Boolean(queryResult?.late_evidence?.applied))"
    >
      <strong>
        {{ queryResult?.late_evidence?.applied
          ? queryResult.late_evidence.title
          : '暂无已采信迟到证据，候选未扩充' }}
      </strong>
      <span v-if="queryResult?.late_evidence?.applied && queryResult?.late_evidence?.description">
        {{ queryResult.late_evidence.description }}
      </span>
      <span v-else-if="queryResult?.late_evidence?.available">
        检测到迟到源证据待同步；采信并补充说明前，对应批次不会进入候选。
      </span>
      <span v-else>当前没有迟到证据。</span>
    </div>
    <div class="evidence-actions">
      <textarea
        v-model="evidenceNote"
        rows="2"
        data-testid="late-evidence-note"
        aria-label="迟到证据说明"
        placeholder="补到说明 008 也合格的源证据"
      ></textarea>
      <button class="btn" type="button" data-testid="sync-late-evidence" @click="addLateEvidence()">
        同步迟到源证据
      </button>
      <button
        class="btn primary"
        type="button"
        data-testid="add-late-evidence"
        aria-label="补录证据、补充并提交迟到证据以扩充候选"
        @click="addLateEvidence(evidenceNote)"
      >
        补录 / 补充并提交证据（扩充候选）
      </button>
    </div>

    <p v-if="pageMessage" class="form-message" data-testid="impact-message">{{ pageMessage }}</p>
    <p v-if="errorMessage" class="error-text" data-testid="impact-error">{{ errorMessage }}</p>

    <div class="impact-grid">
      <div class="table-card">
        <div class="table-title">
          <h4>待领取候选</h4>
          <span>打开时只列服务端核验通过的待处理批次；当前 {{ candidates.length }} 条</span>
        </div>
        <table class="data-table" data-testid="impact-candidates">
          <thead>
            <tr>
              <th>选择</th><th>批次</th><th>来源</th><th>获准模板/版本</th><th>字段与旧表达式</th><th>证据</th><th>状态</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="row in candidates"
              :key="String(row.id || row.命中编号)"
              :data-testid="`candidate-${row.批次短号 || row.id}`"
              :data-batch="row.批次编号"
            >
              <td>
                <input
                  type="checkbox"
                  :data-testid="`select-${row.批次短号}`"
                  :aria-label="`选择${row.展示批次 || row.批次短号}`"
                  v-model="selected[row.批次编号]"
                />
              </td>
              <td>
                <strong>{{ row.展示批次 || row.批次短号 }}</strong>
                <div class="muted">{{ row.批次编号 }}</div>
              </td>
              <td>{{ row.来源渠道 }}</td>
              <td>{{ row.模板编号 }} / {{ row.模板版本 }}</td>
              <td>{{ row.字段名称 }} ← <code>{{ row.旧表达式 }}</code></td>
              <td>
                {{ row.证据状态 }}
                <em v-if="row.是否迟到证据 === '是' || row.late_evidence" class="late-tag">迟到补证</em>
              </td>
              <td>{{ row.处理状态 }}</td>
            </tr>
            <tr v-if="!candidates.length">
              <td colspan="7" class="empty-state">当前没有可领取的待处理真实命中批次</td>
            </tr>
          </tbody>
        </table>
      </div>

      <div class="table-card">
        <div class="table-title">
          <h4>不符合条件 / 未受理</h4>
          <span>同名字段也不能突破获准来源和模板；共 {{ ineligible.length }} 条</span>
        </div>
        <table class="data-table" data-testid="impact-ineligible">
          <thead><tr><th>批次</th><th>来源/模板</th><th>原因</th><th>选择</th></tr></thead>
          <tbody>
            <tr
              v-for="row in ineligible"
              :key="`bad-${row.id || row.命中编号}`"
              :data-testid="`ineligible-${row.批次短号 || row.id}`"
              class="ineligible-row"
            >
              <td><strong>{{ row.展示批次 || row.批次短号 }}</strong><div class="muted">{{ row.批次编号 }}</div></td>
              <td>{{ row.来源渠道 }}<br />{{ row.模板编号 }}/{{ row.模板版本 }}</td>
              <td class="reason">{{ row.不符合原因 }}</td>
              <td><input type="checkbox" disabled data-testid="`disabled-${row.批次短号}`" aria-label="不合格不可选择" /></td>
            </tr>
            <tr v-if="!ineligible.length"><td colspan="4" class="empty-state">暂无不合格同名批次</td></tr>
          </tbody>
        </table>
      </div>
    </div>

    <div class="table-card completed-card" data-testid="impact-completed-panel">
      <div class="table-title"><h4>已完成批次仍可检索</h4><span>共 {{ completed.length }} 条；完成结果由重解析执行回写</span></div>
      <div class="chip-row" data-testid="impact-completed-list">
        <span v-for="row in completed" :key="row.命中编号" class="chip done" :data-testid="`completed-${row.批次短号}`">
          {{ row.展示批次 || row.批次短号 }} · 已完成
        </span>
        <span v-if="!completed.length" class="muted">暂无已完成批次</span>
      </div>
    </div>

    <div class="submit-bar">
      <strong>已核准选择 {{ selectedCount }} 个待处理批次</strong>
      <button
        class="btn primary"
        type="button"
        data-testid="submit-impact-application"
        :disabled="submitting || selectedCount === 0"
        @click="submitApplication"
      >
        {{ submitting ? '受理中…' : '提交领取申请 / 确认受理并生成重解析任务' }}
      </button>
      <span class="muted">服务端会重新核对真实命中，不信任前端勾选。</span>
    </div>

    <section v-if="receipt" class="receipt-card" data-testid="claim-receipt" :data-scope-id="scope?.id || ''">
      <div class="receipt-head">
        <div>
          <h3>领取回执 {{ receipt.receipt_no }}</h3>
          <p>{{ scope?.范围单号 }} · 任务 {{ receipt.task?.重解析编号 || scope?.关联重解析任务 }}</p>
        </div>
        <span v-if="receipt.duplicate" class="badge warn" data-testid="duplicate-scope-badge">相同申请已存在，未重复建单</span>
        <span class="badge ok">{{ scope?.status }}</span>
      </div>

      <dl class="trace-grid" data-testid="scope-trace">
        <div><dt>原筛选范围</dt><dd data-testid="trace-filter">{{ scope?.原筛选范围 }}</dd></div>
        <div><dt>差异编号</dt><dd>{{ trace.diff_id || scope?.差异编号 }}</dd></div>
        <div><dt>获准模板版本</dt><dd data-testid="trace-template">{{ scope?.模板编号 }} / {{ scope?.模板版本 }}</dd></div>
        <div><dt>幂等键</dt><dd class="mono">{{ scope?.幂等键 }}</dd></div>
      </dl>
      <p v-if="receipt.message" class="form-message">{{ receipt.message }}</p>
      <p v-if="receipt.diff?.最近同步结果" class="form-message" data-testid="diff-sync-result">
        差异服务回写：{{ receipt.diff.最近同步结果 }}（待处理 {{ receipt.diff.待处理批次数 ?? '—' }}，已完成 {{ receipt.diff.已完成批次数 ?? '—' }}）
      </p>

      <div class="receipt-grid">
        <div>
          <h4>本次执行名单（核准字段：{{ scope?.差异字段 }}）</h4>
          <ul class="execution-list" data-testid="execution-list">
            <li
              v-for="item in items"
              :key="`exec-${item.item_id}-${item.batch_id}`"
              :data-testid="`execution-${item.batch_short || item.item_id}`"
              :data-status="item.status"
              class="execution-item"
            >
              <div class="execution-main">
                <strong>{{ item.batch_no }}</strong>
                <span>{{ item.template_id }}/{{ item.template_version }} · <code>{{ item.approved_expression }}</code></span>
                <span class="status-pill" :data-status="item.status">{{ item.status }}</span>
                <em v-if="item.late_evidence" class="late-tag">补证命中，仍从待处理开始</em>
              </div>
              <p v-if="item.result" class="ok-text">{{ item.result }}</p>
              <p v-if="item.error" class="error-text">{{ item.error }}</p>
              <div class="row-actions">
                <button v-if="item.status !== '已完成'" class="link" type="button" :data-testid="`execute-${item.batch_short}`" @click="executeItem(item, 'execute')">执行批次</button>
                <button
                  v-if="item.status !== '已完成'"
                  class="link danger"
                  type="button"
                  :data-testid="`fail-${item.batch_short}`"
                  :aria-label="`标记或模拟 ${item.batch_no} 处理失败`"
                  @click="executeItem(item, 'fail')"
                >
                  模拟处理失败
                </button>
                <button
                  v-if="item.status === '处理失败'"
                  class="btn primary small"
                  type="button"
                  :data-testid="`recover-${item.batch_short}`"
                  :aria-label="`从 ${item.batch_no} 具体批次恢复并重试`"
                  @click="executeItem(item, 'retry')"
                >
                  从 {{ item.batch_no }} 批次恢复并重试
                </button>
              </div>
            </li>
          </ul>
          <div class="action-row">
            <button class="btn primary" type="button" data-testid="execute-all" @click="executeAll()">执行本次名单</button>
            <button class="btn" type="button" data-testid="execute-all-with-007-failure" @click="executeAll('007')">令 007 失败后执行其余批次</button>
            <button
              class="btn ghost"
              type="button"
              data-testid="resubmit-original-scope"
              aria-label="重交原范围、再次提交相同筛选申请，幂等不重复排任务"
              @click="resubmitOriginal"
            >
              重交原范围（相同筛选申请再次送达）
            </button>
          </div>
        </div>

        <div>
          <h4>未受理项目</h4>
          <ul class="unaccepted-list" data-testid="unaccepted-list">
            <li v-for="item in unaccepted" :key="item.item_key" :data-testid="`unaccepted-${item.batch_short || item.item_key}`">
              <strong>{{ item.batch_no || item.item_key }}</strong>
              <p>{{ item.reason }}</p>
              <button class="link" type="button" :data-testid="`open-unaccepted-${item.batch_short || item.item_key}`" @click="openUnaccepted(item)">
                打开未受理项目继续处理
              </button>
            </li>
            <li v-if="!unaccepted.length" class="muted">没有未受理项目</li>
          </ul>

          <h4>下一次可领取的批次</h4>
          <div data-testid="next-claimable" class="next-box">
            <div class="action-row">
              <button class="btn" type="button" data-testid="refresh-next" @click="refreshNext">刷新可领取批次</button>
              <button
                v-if="(nextInfo?.claimable || []).length"
                class="btn primary small"
                type="button"
                data-testid="claim-next"
                @click="claimNext"
              >
                领取下一批
              </button>
            </div>
            <p>{{ nextInfo?.message || '执行并同步后可刷新下一次筛选' }}</p>
            <div class="chip-row">
              <span v-for="row in nextInfo?.claimable || []" :key="row.命中编号" class="chip" :data-testid="`next-batch-${row.批次短号}`">
                {{ row.展示批次 || row.批次短号 }}
              </span>
              <span v-if="!(nextInfo?.claimable || []).length" class="muted" data-testid="next-empty">暂无下一批可领取批次</span>
            </div>
          </div>
        </div>
      </div>
    </section>

    <div v-if="activeUnaccepted" class="modal-backdrop" data-testid="unaccepted-modal" @click.self="activeUnaccepted = null">
      <div class="modal" role="dialog" aria-modal="true" aria-label="未受理项目继续处理">
        <h3>继续处理未受理项目：{{ activeUnaccepted.batch_no || activeUnaccepted.item_key }}</h3>
        <p><strong>当前裁决：</strong>{{ activeUnaccepted.status }}</p>
        <p class="error-text">{{ activeUnaccepted.reason }}</p>
        <label class="create-field">
          <span>补充证据 / 处理说明</span>
          <textarea v-model="supplementNote" rows="4" data-testid="unaccepted-evidence-note"></textarea>
        </label>
        <p v-for="(note, index) in activeUnaccepted.evidence_notes || []" :key="index" class="muted">
          {{ note.at }}：{{ note.note }}
        </p>
        <p v-if="unacceptedMessage" :class="unacceptedAccepted ? 'ok-text' : 'error-text'" data-testid="unaccepted-result">{{ unacceptedMessage }}</p>
        <div class="page-actions">
          <button class="btn" type="button" data-testid="close-unaccepted" @click="activeUnaccepted = null">关闭</button>
          <button class="btn primary" type="button" data-testid="supplement-unaccepted" @click="supplementUnaccepted">补充证据并重新核验</button>
        </div>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, ref } from 'vue'

import {
  impactAction,
  impactApply,
  impactLateEvidence,
  impactQuery,
  impactReceipt,
} from '@/api/backend-service'
import type { EntryRow } from '@/data/types'

const STORAGE_KEY = 'tmpl-diff-impact-search-v1'
const RECEIPT_KEY = 'tmpl-diff-impact-receipt-v1'

const rootRef = ref<HTMLElement | null>(null)
const loading = ref(false)
const submitting = ref(false)
const errorMessage = ref('')
const pageMessage = ref('')
const queryResult = ref<any>(null)
const receipt = ref<any>(null)
const nextInfo = ref<any>(null)
const selected = ref<Record<string, boolean>>({})
const evidenceNote = ref('源系统补传日志与表达式快照证明：甲的 008 批次确实消费过 amount 旧表达式。')
const activeUnaccepted = ref<any>(null)
const supplementNote = ref('')
const unacceptedMessage = ref('')
const unacceptedAccepted = ref(false)

const criteria = ref({
  diffId: 'TD-AMOUNT',
  source: '甲来源',
  oldExpression: 'amount',
  fieldName: 'amount',
  templateId: 'ET-006',
  templateVersion: 'v1.4.0',
  includeCompleted: true,
})

const candidates = computed<any[]>(() => queryResult.value?.claimable || queryResult.value?.candidates || [])
const ineligible = computed<any[]>(() => queryResult.value?.ineligible || [])
const completed = computed<any[]>(() => queryResult.value?.completed || [])
const scope = computed<any>(() => receipt.value?.scope || null)
const items = computed<any[]>(() => receipt.value?.items || scope.value?.items || [])
const unaccepted = computed<any[]>(() => receipt.value?.unaccepted_items || scope.value?.unaccepted_items || [])
const trace = computed<any>(() => receipt.value?.trace || {})
const selectedCount = computed(() => Object.values(selected.value).filter(Boolean).length)

function payloadCriteria() {
  return {
    diff_id: criteria.value.diffId,
    source: criteria.value.source,
    old_expression: criteria.value.oldExpression,
    field_name: criteria.value.fieldName,
    template_id: criteria.value.templateId,
    template_version: criteria.value.templateVersion,
    include_completed: criteria.value.includeCompleted,
  }
}

function saveDraft() {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(criteria.value))
}

function initializeSelection() {
  const next: Record<string, boolean> = {}
  for (const row of candidates.value) {
    const key = String(row.批次编号 || row.id)
    next[key] = true
  }
  selected.value = next
}

async function runQuery() {
  loading.value = true
  errorMessage.value = ''
  saveDraft()
  try {
    queryResult.value = await impactQuery(payloadCriteria())
    initializeSelection()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '影响范围查询失败'
  } finally {
    loading.value = false
  }
}

async function selectDiff(row: EntryRow) {
  const field = String(row['差异字段'] || row['字段名称'] || 'amount').split(/[,，]/)[0]?.trim() || 'amount'
  criteria.value = {
    ...criteria.value,
    diffId: String(row['差异编号'] || row.id || criteria.value.diffId),
    fieldName: field,
    oldExpression: String(row['旧表达式'] || field),
    templateId: String(row['旧模板编号'] || row['获准模板编号'] || criteria.value.templateId),
    templateVersion: String(row['旧模板版本'] || row['获准模板版本'] || criteria.value.templateVersion),
    source: String(row['获准来源'] || criteria.value.source),
  }
  saveDraft()
  await nextTick()
  rootRef.value?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  await runQuery()
}

async function addLateEvidence(note?: string) {
  errorMessage.value = ''
  pageMessage.value = ''
  try {
    const result = await impactLateEvidence({
      ...payloadCriteria(),
      batch: '008',
      note: note || evidenceNote.value,
    })
    queryResult.value = result
    initializeSelection()
    pageMessage.value = result.message || '迟到证据已采信，008 已扩充进候选'
    saveDraft()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '迟到证据同步失败'
  }
}

async function submitApplication() {
  submitting.value = true
  errorMessage.value = ''
  pageMessage.value = ''
  try {
    const selectedBatches = Object.entries(selected.value)
      .filter(([, checked]) => checked)
      .map(([key]) => key)
    const result = await impactApply({
      ...payloadCriteria(),
      selected_batches: selectedBatches,
    })
    if (!result.ok) {
      errorMessage.value = result.message || '没有可受理批次'
      if (result.unaccepted_items) receipt.value = { ...(receipt.value || {}), unaccepted_items: result.unaccepted_items }
      return
    }
    receipt.value = result
    nextInfo.value = result.next_claimable || null
    localStorage.setItem(RECEIPT_KEY, String(result.scope?.id || result.scope_no || ''))
    pageMessage.value = result.message
    await runQuery()
    await refreshNext()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '提交领取申请失败'
  } finally {
    submitting.value = false
  }
}

function scopeRef(): string | number {
  return receipt.value?.scope?.id || receipt.value?.scope_no || scope.value?.id || ''
}

async function resubmitOriginal() {
  errorMessage.value = ''
  try {
    const result = await impactAction(scopeRef(), 'resubmit', payloadCriteria())
    receipt.value = result
    pageMessage.value = result.message
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '重交原范围失败'
  }
}

async function executeAll(failBatch = '') {
  errorMessage.value = ''
  try {
    const result = await impactAction(scopeRef(), 'execute-all', failBatch ? { fail_batch: failBatch } : {})
    if (result.scope) receipt.value = result
    pageMessage.value = result.message
    nextInfo.value = result.next_claimable || null
    queryResult.value = await impactQuery(payloadCriteria())
    initializeSelection()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '执行名单失败'
  }
}

async function executeItem(item: any, mode: 'execute' | 'fail' | 'retry') {
  errorMessage.value = ''
  const batch = encodeURIComponent(String(item.batch_short || item.batch_id))
  try {
    const result = await impactAction(scopeRef(), `items/${batch}/${mode}`, {})
    if (result.scope) receipt.value = result
    pageMessage.value = result.message
    nextInfo.value = result.next_claimable || null
    queryResult.value = await impactQuery(payloadCriteria())
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '批次操作失败'
  }
}

async function openUnaccepted(item: any) {
  activeUnaccepted.value = item
  supplementNote.value = ''
  unacceptedMessage.value = ''
  try {
    const key = encodeURIComponent(String(item.batch_short || item.item_key))
    const result = await impactAction(scopeRef(), `unaccepted/${key}/open`, {})
    if (result.receipt) receipt.value = result.receipt
    if (result.item) activeUnaccepted.value = result.item
  } catch (error) {
    unacceptedMessage.value = error instanceof Error ? error.message : '打开失败'
  }
}

async function supplementUnaccepted() {
  if (!activeUnaccepted.value) return
  const key = encodeURIComponent(String(activeUnaccepted.value.batch_short || activeUnaccepted.value.item_key))
  try {
    const result = await impactAction(scopeRef(), `unaccepted/${key}/supplement`, {
      note: supplementNote.value || '补充处理证据',
    })
    unacceptedMessage.value = result.message
    unacceptedAccepted.value = Boolean(result.accepted)
    if (result.receipt) receipt.value = result.receipt
    if (result.accepted) {
      activeUnaccepted.value = null
    } else if (result.item) {
      activeUnaccepted.value = result.item
    }
    await runQuery()
  } catch (error) {
    unacceptedMessage.value = error instanceof Error ? error.message : '补充核验失败'
    unacceptedAccepted.value = false
  }
}

async function refreshNext() {
  if (!scopeRef()) {
    nextInfo.value = await impactQuery(payloadCriteria())
    return
  }
  try {
    nextInfo.value = await impactAction(scopeRef(), 'next', payloadCriteria())
  } catch {
    nextInfo.value = await impactQuery(payloadCriteria())
  }
}

async function claimNext() {
  await runQuery()
  await submitApplication()
}

onMounted(async () => {
  const saved = localStorage.getItem(STORAGE_KEY)
  if (saved) {
    try {
      criteria.value = { ...criteria.value, ...JSON.parse(saved) }
    } catch {
      localStorage.removeItem(STORAGE_KEY)
    }
  }
  await runQuery()
  const lastScope = localStorage.getItem(RECEIPT_KEY)
  if (lastScope) {
    try {
      receipt.value = await impactReceipt(lastScope)
      await refreshNext()
    } catch {
      localStorage.removeItem(RECEIPT_KEY)
    }
  }
})

defineExpose({ selectDiff, runQuery })
</script>

<style scoped>
.impact-panel { background: #f8fafc; border: 1px solid var(--border); border-radius: 12px; padding: 14px; margin: 12px 0 16px; }
.impact-head { display: flex; justify-content: space-between; gap: 12px; align-items: flex-start; }
.impact-head h3 { margin: 0 0 4px; }
.impact-head p { margin: 0; color: var(--muted); font-size: 13px; }
.badge { display: inline-flex; align-items: center; border-radius: 999px; padding: 3px 10px; background: #e0ecff; color: #175cd3; font-size: 12px; white-space: nowrap; }
.badge.ok { background: #dcfae6; color: #067647; }
.badge.warn { background: #fef0c7; color: #b54708; }
.impact-form { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 10px; align-items: end; background: #fff; border: 1px solid var(--border); border-radius: 8px; padding: 12px; margin: 12px 0; }
.impact-form label { display: flex; flex-direction: column; gap: 4px; font-size: 12px; color: var(--muted); }
.impact-form input:not([type='checkbox']), .evidence-actions textarea, .modal textarea { border: 1px solid var(--border); border-radius: 6px; padding: 7px 9px; font-size: 13px; color: #1f2937; width: 100%; }
.check-label { flex-direction: row !important; align-items: center; gap: 6px !important; color: #344054 !important; }
.evidence-bar { border-left: 4px solid #94a3b8; background: #fff; padding: 9px 12px; border-radius: 6px; display: grid; gap: 3px; font-size: 13px; }
.evidence-bar.applied { border-left-color: #12b76a; background: #f6fef9; }
.evidence-actions { display: grid; grid-template-columns: 1fr auto auto; gap: 8px; margin: 8px 0 12px; align-items: center; }
.impact-grid { display: grid; grid-template-columns: minmax(0, 1.25fr) minmax(280px, .75fr); gap: 12px; }
.table-card { background: #fff; border: 1px solid var(--border); border-radius: 8px; padding: 10px; min-width: 0; }
.table-title { display: flex; justify-content: space-between; gap: 8px; align-items: baseline; margin-bottom: 8px; }
.table-title h4, .receipt-card h3, .receipt-card h4 { margin: 0; }
.table-title span { color: var(--muted); font-size: 12px; }
.table-card .data-table { font-size: 12px; }
.muted { color: var(--muted); font-size: 12px; }
.reason { color: #b42318; min-width: 180px; }
.ineligible-row { background: #fffafa; }
.completed-card { margin-top: 12px; }
.chip-row { display: flex; flex-wrap: wrap; gap: 6px; }
.chip { border: 1px solid #bfdbfe; background: #eff6ff; color: #1d4ed8; border-radius: 999px; padding: 3px 9px; font-size: 12px; }
.chip.done { border-color: #abefc6; background: #ecfdf3; color: #067647; }
.submit-bar { display: flex; align-items: center; gap: 12px; margin: 12px 0; flex-wrap: wrap; }
.form-message { color: #175cd3; font-size: 13px; margin: 6px 0; }
.ok-text { color: #067647; font-size: 12px; margin: 4px 0; }
.error-text { color: #b42318; font-size: 12px; margin: 4px 0; }
.receipt-card { background: #fff; border: 1px solid #b2ddff; border-radius: 10px; padding: 14px; margin-top: 12px; }
.receipt-head { display: flex; justify-content: space-between; gap: 10px; align-items: center; }
.trace-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 8px; margin: 12px 0; }
.trace-grid div { background: #f8fafc; border-radius: 6px; padding: 8px; }
.trace-grid dt { color: var(--muted); font-size: 12px; }
.trace-grid dd { margin: 3px 0 0; font-size: 13px; }
.mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; word-break: break-all; }
.receipt-grid { display: grid; grid-template-columns: minmax(0, 1.2fr) minmax(280px, .8fr); gap: 16px; }
.execution-list, .unaccepted-list { list-style: none; margin: 8px 0; padding: 0; display: grid; gap: 8px; }
.execution-item { border: 1px solid var(--border); border-radius: 8px; padding: 8px; }
.execution-main { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; font-size: 13px; }
.status-pill { border-radius: 999px; padding: 2px 8px; background: #e4e7ec; font-size: 12px; }
.status-pill[data-status='已完成'] { background: #dcfae6; color: #067647; }
.status-pill[data-status='处理失败'] { background: #fee4e2; color: #b42318; }
.status-pill[data-status='执行中'] { background: #dbeafe; color: #1d4ed8; }
.late-tag { color: #b54708; background: #fef0c7; border-radius: 4px; padding: 1px 5px; font-style: normal; font-size: 12px; }
.action-row { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 8px; }
.btn.small { padding: 3px 8px; font-size: 12px; }
.link.danger { color: #b42318; }
.next-box { border: 1px dashed var(--border); border-radius: 8px; padding: 8px; }
.unaccepted-list li { border-bottom: 1px solid #eef2f6; padding-bottom: 8px; font-size: 13px; }
.unaccepted-list p { margin: 4px 0; color: #b42318; font-size: 12px; }
.modal-backdrop { position: fixed; inset: 0; background: rgb(15 23 42 / 35%); display: grid; place-items: center; z-index: 30; padding: 20px; }
.modal { width: min(620px, 100%); background: #fff; border-radius: 12px; padding: 20px; max-height: 90vh; overflow: auto; }
@media (max-width: 980px) { .impact-grid, .receipt-grid { grid-template-columns: 1fr; } .evidence-actions { grid-template-columns: 1fr; } }
</style>
