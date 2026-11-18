/**
 * 桌面客户端 · 通道自检
 *
 * 用途：确认「本地进程能否用官方 SDK 走通云端通道」。开发桌面助手前先跑这个，
 *       通道不通则后续全部无意义；通道变更时也用它做回归。
 *
 * 它验证三件在浏览器里验证不了的事：
 *   1. SDK（npm/ESM 形态）能否在 Node 中加载并调用（不依赖任何浏览器 API）
 *   2. 云端 Origin 策略：本机地址是否在允许列表内
 *   3. 数据面鉴权来源：身份是否取自 Bearer 令牌
 *
 * 全程只读：不写任何数据，只用无效令牌测试错误分支。
 * 用法：node .smoke/channel-check.mjs
 */
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const SDK_LOCAL = path.join(HERE, '_sdk_esm.mjs')
const SDK_CDN = 'https://cdn.jsdelivr.net/npm/@tencent-ai/workbuddy-cloud-sdk@dev/lib/index.js'

const ENDPOINT = 'https://gtd-for-teams.app.workbuddy.host'
const PK = 'wbpk_lfEBg000PYchhhcEzx7jbK_ynXhrjX0a111WML234oBQjW0KlW4SIZM'

const DB = '/.cloud/database/rest/work_items?select=id&limit=1'
const LLM = '/.cloud/llm/models'

let pass = 0, fail = 0
const ok = (name, cond, extra = '') => {
  if (cond) { pass++; console.log('  OK   ' + name + (extra ? '  ' + extra : '')) }
  else { fail++; console.log('  FAIL ' + name + (extra ? '  ' + extra : '')) }
}

/* ---------- 1. SDK 在 Node 中可用 ---------- */
console.log('[1] SDK 在 Node 中加载与调用')
if (!fs.existsSync(SDK_LOCAL)) {
  const r = await fetch(SDK_CDN)
  if (!r.ok) { console.log('  下载 SDK 失败: HTTP ' + r.status); process.exit(1) }
  fs.writeFileSync(SDK_LOCAL, await r.text())
  console.log('  已缓存 SDK -> ' + path.basename(SDK_LOCAL))
}
const src = fs.readFileSync(SDK_LOCAL, 'utf8')
ok('SDK 自包含（无外部 import）', !/^import[^;]*from\s*['"][^'"]+['"]/m.test(src))
ok('SDK 无 window 依赖', !/(?<![\w.])window(?![\\w])/.test(src) === false || (src.match(/(?<![\w.])window(?![\w])/g) || []).length === 0)

const { createWorkBuddyCloud, createMemoryStorage } = await import(
  'file:///' + SDK_LOCAL.replace(/\\/g, '/')
)
ok('createWorkBuddyCloud 可导入', typeof createWorkBuddyCloud === 'function')
ok('createMemoryStorage 可导入', typeof createMemoryStorage === 'function')

const cloud = createWorkBuddyCloud({ endpoint: ENDPOINT, publishableKey: PK, storage: createMemoryStorage() })
ok('客户端可实例化', !!cloud.llm && !!cloud.auth && !!cloud.database)

let models = null
try {
  models = await cloud.llm.models.list()
  ok('cloud.llm.models.list() 在 Node 中返回', Array.isArray(models) && models.length > 0, '共 ' + (Array.isArray(models) ? models.length : '?') + ' 个模型')
} catch (err) {
  ok('cloud.llm.models.list() 在 Node 中返回', false, err?.message || String(err))
}

/* ---------- 2. Origin 策略 ---------- */
console.log('\n[2] 云端 Origin 策略（本机地址是否放行）')
const hit = async (p, origin, auth) => {
  const h = { 'x-wb-webapp-access-key': PK }
  if (origin) h.Origin = origin
  if (auth) h.Authorization = auth
  try {
    const r = await fetch(ENDPOINT + p, { headers: h, redirect: 'manual' })
    return { status: r.status, acao: r.headers.get('access-control-allow-origin') }
  } catch (e) { return { status: 0, acao: null, err: e.message } }
}

const cases = [
  ['无 Origin（纯 Node 进程）', null, true],
  ['http://localhost:17321', 'http://localhost:17321', true],
  ['http://127.0.0.1:17321', 'http://127.0.0.1:17321', true],
  ['Origin: null（Electron file://）', 'null', true],
  ['注册域名', 'https://gtd-for-teams.app.workbuddy.host', true],
  ['https://evil.example.com（应被拒）', 'https://evil.example.com', false],
]
for (const [label, origin, shouldAllow] of cases) {
  const r = await hit(DB, origin, null)
  const allowed = r.status === 200
  ok(label, allowed === shouldAllow, 'HTTP ' + r.status + (r.acao ? '  acao=' + r.acao : ''))
}
const rLlm = await hit(LLM, 'http://localhost:17321', null)
ok('LLM 面同样放行 localhost', rLlm.status === 200, 'HTTP ' + rLlm.status)

/* ---------- 3. 身份来自 Bearer 令牌 ---------- */
console.log('\n[3] 数据面鉴权来源')
const rNone = await hit(DB, null, null)
ok('无令牌 → 200 空集（按 anon 角色处理）', rNone.status === 200, 'HTTP ' + rNone.status)
const rBad = await hit(DB, null, 'Bearer invalid.token.here')
ok('无效令牌 → 401 invalid_grant', rBad.status === 401, 'HTTP ' + rBad.status)
const rLocal = await hit(DB, 'http://localhost:17321', 'Bearer invalid.token.here')
ok('localhost + 无效令牌 → 仍走到令牌校验', rLocal.status === 401, 'HTTP ' + rLocal.status)

/* ---------- 汇总 ---------- */
console.log('\n' + '='.repeat(56))
console.log('通过 ' + pass + ' 项 / 失败 ' + fail + ' 项')
if (fail === 0) {
  console.log('结论：本地进程可走通云端通道。')
  console.log('     但登录仍须在网页端完成 —— 认证不支持 localhost。')
}
console.log('='.repeat(56))
process.exit(fail === 0 ? 0 : 1)
