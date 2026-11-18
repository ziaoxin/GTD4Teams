/**
 * 在 Node 中加载云服务 SDK 全局包，枚举其真实 API 表面。
 * 目的：写 api.js 前确认 from() 建造器与 auth 模块的确切方法名，避免猜测。
 */
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const here = path.dirname(fileURLToPath(import.meta.url))
const src = fs.readFileSync(path.join(here, 'sdk.js'), 'utf8')

// 最小浏览器环境垫片
globalThis.window = globalThis
globalThis.location = {
  origin: 'https://gtd-workbench-85110.app.workbuddy.host',
  href: 'https://gtd-workbench-85110.app.workbuddy.host/',
}
globalThis.document = undefined
const store = new Map()
globalThis.localStorage = {
  getItem: (k) => (store.has(k) ? store.get(k) : null),
  setItem: (k, v) => store.set(k, String(v)),
  removeItem: (k) => store.delete(k),
}

const lines = []
const log = (s) => lines.push(s)

let WB = null
try {
  const fn = new Function(`${src}\n;return (typeof WorkBuddyCloud!=="undefined"?WorkBuddyCloud:(globalThis.WorkBuddyCloud||null))`)
  WB = fn()
} catch (e) {
  log('LOAD_FAIL: ' + e.message)
}

if (WB) {
  log('exports: ' + Object.keys(WB).sort().join(', '))
  const cloud = WB.createWorkBuddyCloud({
    endpoint: 'https://gtd-workbench-85110.app.workbuddy.host',
    publishableKey: 'wbpk_yeUVKgEbdlH12NXE0uNVr9_NFEbFeBpiZ3nhQ5S1CA1rBdg8nnqZdc5',
  })

  const protoChain = (obj) => {
    const names = new Set()
    let p = Object.getPrototypeOf(obj)
    while (p && p !== Object.prototype) {
      Object.getOwnPropertyNames(p).forEach((n) => names.add(n))
      p = Object.getPrototypeOf(p)
    }
    return [...names].sort()
  }

  log('client own keys: ' + Object.keys(cloud).sort().join(', '))
  log('client proto: ' + protoChain(cloud).join(', '))
  log('auth proto: ' + protoChain(cloud.auth).join(', '))
  log('database proto: ' + protoChain(cloud.database).join(', '))

  try {
    const q = cloud.database.from('tasks')
    log('from("tasks") proto: ' + protoChain(q).join(', '))
    const q2 = q.select('id')
    log('after .select proto: ' + protoChain(q2).join(', '))
    log('select result keys: ' + Object.keys(q2).join(', '))
  } catch (e) {
    log('FROM_FAIL: ' + e.message)
  }
  try {
    log('rpc is fn: ' + (typeof cloud.database.rpc))
  } catch (e) { log('RPC_FAIL ' + e.message) }
}

fs.writeFileSync(path.join(here, '_api_surface.log'), lines.join('\n'), 'utf8')
console.log('written')
