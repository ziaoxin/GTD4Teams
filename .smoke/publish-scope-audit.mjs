/**
 * 发布范围审计 —— 检查线上站点是否暴露了非站点文件。
 *
 * 背景：本平台的静态托管是「累加上传」，不会删除线上已存在的文件；
 * 且发布能力没有忽略规则（仅排除 node_modules/.git/构建产物）。
 * 因此项目目录里任何出现过的文件（测试夹具、日志、笔记）都会永久留在公开 URL 上。
 *
 * 用法：
 *   node .smoke/publish-scope-audit.mjs
 * 退出码：0 = 只有站点文件；1 = 存在非站点文件暴露。
 */
import fs from 'node:fs'
import path from 'node:path'

const ORIGIN = process.env.SITE_ORIGIN || 'https://gtd-for-teams.app.workbuddy.host'
const ROOT = path.resolve(import.meta.dirname, '..')
/** 站内允许公开的顶层路径 */
const SITE_RE = /^(index\.html$|js\/|css\/)/

async function listDir(dirRel) {
  const res = await fetch(`${ORIGIN}/${dirRel}`, { redirect: 'follow' })
  if (!res.ok) return []
  const html = await res.text()
  const out = []
  for (const m of html.matchAll(/<a href="([^"]+)">([^<]*)<\/a>/g)) {
    const href = m[1]
    if (href.startsWith('?') || href.startsWith('/') || href.startsWith('http')) continue
    out.push({ href, isDir: href.endsWith('/') })
  }
  return out
}

const seen = new Set()
const files = []
async function walk(dirRel) {
  if (seen.has(dirRel)) return
  seen.add(dirRel)
  for (const e of await listDir(dirRel)) {
    const p = dirRel + e.href
    if (e.isDir) await walk(p)
    else if (!files.includes(p)) files.push(p)
  }
}

export async function audit() {
  /* 目录列表默认开启，因此可以从顶层目录穷举 */
  const localTop = fs.readdirSync(ROOT, { withFileTypes: true })
    .filter((e) => e.isDirectory() && !e.name.startsWith('.'))
    .map((e) => e.name + '/')
  const seeds = ['js/', 'css/', '.smoke/', '.workbuddy/', ...localTop]
  for (const s of seeds) await walk(s)

  files.sort()
  const site = files.filter((f) => SITE_RE.test(f))
  const nonSite = files.filter((f) => !SITE_RE.test(f))
  return { origin: ORIGIN, total: files.length, site, nonSite }
}

if (import.meta.filename === process.argv[1]) {
  const r = await audit()
  console.log(`站点：${r.origin}`)
  console.log(`可访问文件 ${r.total} 个 —— 站点文件 ${r.site.length}，非站点文件 ${r.nonSite.length}\n`)
  if (r.nonSite.length) {
    console.log('非站点文件（不应公开）：')
    r.nonSite.forEach((f) => console.log('  /' + f))
    console.log('\n处置：把 .smoke / .workbuddy 移出目录后再发布，可阻止继续上传；')
    console.log('      已被上传的文件无法删除，只能原位覆盖为占位内容。')
    process.exit(1)
  }
  console.log('未发现非站点文件暴露。')
}
