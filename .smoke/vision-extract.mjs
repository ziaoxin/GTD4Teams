/**
 * 截图 → GTD 收集箱条目（多模态抽取工具）
 *
 * 用法：
 *   node .smoke/vision-extract.mjs <图片路径> [--raw] [--model=xxx]
 *
 * 例：
 *   node .smoke/vision-extract.mjs D:/shots/ticket.png
 *   node .smoke/vision-extract.mjs D:/shots/ticket.jpg --raw
 *
 * 说明：
 *   - 默认模型 deepseek-v4.1-flash（倍率 x0.03，原生支持图片，最便宜一档）
 *   - 图片以 base64 data URL 内联送入，不经过任何中间存储
 *   - 输出为可直接写入 work_items 的字段结构（只打印，不写库）
 */
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const SDK_LOCAL = path.join(HERE, '_sdk_esm.mjs')
const SDK_CDN = 'https://cdn.jsdelivr.net/npm/@tencent-ai/workbuddy-cloud-sdk@dev/lib/index.js'

const ENDPOINT = 'https://gtd-for-teams.app.workbuddy.host'
const PK = 'wbpk_lfEBg000PYchhhcEzx7jbK_ynXhrjX0a111WML234oBQjW0KlW4SIZM'

/* ---------------- 参数 ---------------- */
const argv = process.argv.slice(2)
const imgPath = argv.find((a) => !a.startsWith('--'))
const rawMode = argv.includes('--raw')
const modelArg = (argv.find((a) => a.startsWith('--model=')) || '').split('=')[1]

if (!imgPath) {
  console.error('用法: node .smoke/vision-extract.mjs <图片路径> [--raw] [--model=xxx]')
  process.exit(2)
}
if (!fs.existsSync(imgPath)) {
  console.error('找不到图片: ' + imgPath)
  process.exit(2)
}

const MIME = { '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.webp': 'image/webp', '.gif': 'image/gif', '.bmp': 'image/bmp' }
const ext = path.extname(imgPath).toLowerCase()
const mime = MIME[ext]
if (!mime) {
  console.error('不支持的图片格式: ' + ext + '（支持 ' + Object.keys(MIME).join(' ') + '）')
  process.exit(2)
}

const buf = fs.readFileSync(imgPath)
const b64 = buf.toString('base64')
const dataUrl = `data:${mime};base64,${b64}`
const sizeMB = buf.length / 1048576

console.log('图片     : ' + path.basename(imgPath))
console.log('原始体积 : ' + (sizeMB < 1 ? Math.round(buf.length / 1024) + ' KB' : sizeMB.toFixed(2) + ' MB'))
console.log('base64   : ' + Math.round(dataUrl.length / 1024) + ' KB')
if (sizeMB > 1.5) {
  console.log('⚠ 体积偏大：建议截图前把长边压到 1568px 以内（图像 token 与分辨率相关，过大反而拖慢且不提升识别率）')
}

/* ---------------- SDK ---------------- */
if (!fs.existsSync(SDK_LOCAL)) {
  const r = await fetch(SDK_CDN)
  if (!r.ok) { console.error('下载 SDK 失败: HTTP ' + r.status); process.exit(1) }
  fs.writeFileSync(SDK_LOCAL, await r.text())
  console.log('已缓存 SDK -> _sdk_esm.mjs')
}
const { createWorkBuddyCloud, createMemoryStorage } = await import('file:///' + SDK_LOCAL.replace(/\\/g, '/'))

const cloud = createWorkBuddyCloud({ endpoint: ENDPOINT, publishableKey: PK, storage: createMemoryStorage() })

/* ---------------- 调用 ---------------- */
const MODEL = modelArg || 'deepseek-v4.1-flash'

const SYSTEM = `你是 IT 运维团队的截图信息抽取助手，把截图内容转成 GTD 收集箱条目。

输出 JSON，字段：
- title      : 任务标题，中文，不超过 30 字，动宾结构（如「更换 A1 机房空调压缩机」）
- type       : 从 incident / request / change / problem / routine 中选一个
               incident=故障中断  request=服务请求  change=变更发布  problem=根因治理  routine=例行巡检
- priority   : p1 / p2 / p3 / p4（p1 最高）
- ref        : 图中出现的单据号/工单号，没有则 null
- assignee   : 图中出现的负责人，没有则 null
- due        : 图中出现的截止时间，ISO 格式，没有则 null
- note       : 一句话补充关键信息（设备、位置、现象），不超过 60 字
- confidence : 0~1，你对本次抽取整体的把握度

规则：
- 只依据图中实际可见的内容，不要推测编造。看不到的字段填 null。
- 若截图内容无法构成工作任务（如纯监控曲线、无关聊天），把 title 置为 null 并在 note 说明原因。
- 只输出 JSON，不要 markdown 代码围栏，不要解释。`

const USER = rawMode
  ? '逐行读出图中全部文字，保持原有顺序，不要解释。'
  : '把这张截图抽取为一条 GTD 收集箱条目。'

const request = {
  model: MODEL,
  stream: true,                       // 必须为 true，否则 SDK 在发请求前就抛错
  max_tokens: 800,
  stream_options: { include_usage: true },
  messages: [
    { role: 'system', content: rawMode ? '你是截图识别助手，只输出识别到的文字。' : SYSTEM },
    {
      role: 'user',
      content: [
        { type: 'text', text: USER },
        { type: 'image_url', image_url: { url: dataUrl, detail: 'high' } },
      ],
    },
  ],
}
if (!rawMode) request.response_format = { type: 'json_object' }

const t0 = Date.now()
const ac = new AbortController()
const timer = setTimeout(() => ac.abort(), 90000)

let content = '', reasoning = '', usage = null, chunks = 0
try {
  const stream = await cloud.llm.chat.completions.create({ ...request, signal: ac.signal })
  for await (const chunk of stream) {
    const d = chunk.choices?.[0]?.delta
    if (!d) continue
    chunks++
    // 两条独立通道：可见回复 与 思考过程
    if (typeof d.content === 'string') content += d.content
    if (typeof d.reasoning_content === 'string') reasoning += d.reasoning_content
    if (chunk.usage) usage = chunk.usage
  }
} catch (err) {
  clearTimeout(timer)
  const msg = err?.message || String(err)
  console.error('\n调用失败: ' + msg)
  if (/stream/i.test(msg)) console.error('提示：stream 必须显式为 true。')
  if (/401|invalid_grant/.test(msg)) console.error('提示：会话已失效，需要在网页端重新登录并重新配对。')
  process.exit(1)
}
clearTimeout(timer)

const ms = Date.now() - t0
console.log('\n模型     : ' + MODEL + '   耗时 ' + (ms / 1000).toFixed(1) + 's   分片 ' + chunks)
if (usage) {
  console.log('用量     : prompt=' + usage.prompt_tokens + '  completion=' + usage.completion_tokens
    + '  total=' + usage.total_tokens + (usage.credit != null ? '  credit=' + usage.credit : ''))
}
if (reasoning.trim()) console.log('（模型有思考输出 ' + reasoning.length + ' 字，已略）')

console.log('\n──── 输出 ────')
console.log(content.trim())

/* ---------------- 收尾：剥围栏 + 解析 ---------------- */
if (!rawMode) {
  /** 模型有时会包 ```json 围栏，即便已声明 json_object 模式，必须剥离 */
  const strip = (s) => {
    const t = String(s || '').trim()
    const m = t.match(/^```(?:json)?\s*([\s\S]*?)\s*```$/)
    if (m) return m[1].trim()
    const i = t.indexOf('{'), j = t.lastIndexOf('}')
    return i >= 0 && j > i ? t.slice(i, j + 1) : t
  }
  const cleaned = strip(content)
  let obj = null
  try { obj = JSON.parse(cleaned) } catch { /* 解析失败 */ }

  console.log('\n──── 解析结果 ────')
  if (obj) {
    for (const [k, v] of Object.entries(obj)) console.log('  ' + k.padEnd(12) + ' = ' + JSON.stringify(v))
    console.log('\n可直接写入 work_items 的载荷（归属字段由列默认值 auth.uid() 决定，不要手工传 owner_id）：')
    console.log(JSON.stringify({
      title: obj.title,
      type: obj.type,
      priority: obj.priority ?? 'p3',
      source: 'desktop-capture',
      reported_by: obj.ref ?? null,
      note: obj.note ?? null,
      tags: obj.ref ? [String(obj.ref)] : [],
    }, null, 2))
  } else {
    console.log('  ✗ 不是合法 JSON。原文见上方「输出」。')
  }
}
