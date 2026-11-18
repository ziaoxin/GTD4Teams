/**
 * 版本串一致性契约测试。
 *
 * 为什么需要它：这个项目有三套东西必须共用**同一个**版本串，但它们互相看不见 ——
 *
 *   1. `js/config.js` 的 `APP_VERSION`      —— 前端展示用（页面底部 / 关于信息）
 *   2. `index.html` 里 css / js 的 `?v=`    —— 浏览器缓存键
 *   3. `js/*.js` 里 `import '...?v='`       —— 模块图的缓存键（ESM 按完整 URL 缓存）
 *   4. `pyapp/__init__.py` 的 `APP_VERSION` —— 服务端 `/api/health` 回传的 version
 *   5. `.smoke/config.mjs` 的 `APP_VERSION` —— 前端测试的镜像副本（2026-11-18 实测补入）
 *
 * 第 5 类曾是真盲区：它不在 scanTargets 里，前面 4 类都对、它也一致时全绿，
 * 但只要有人漏改它，测试夹具读到的版本就和实际产物不同，而 CI 不会报警。
 * 注意：只把 `.smoke/config.mjs` 这一个文件加入扫描，不能整目录扫 `.smoke/`——
 * 本文件（version-contract.mjs）的注释里就有形如 20261114f 的历史示例串。
 *
 * 漏改任何一个，后果都不是"报错"，而是**看起来正常但跑的是旧代码**：
 *   · `?v=` 没改 → 用户拿到缓存的旧 JS，界面行为与服务端契约不符，且很难复现
 *   · 模块内 import 没改 → 入口 JS 是新的，但它加载的子模块还是旧的（最隐蔽的一种）
 *   · 前后端不一致 → 页面显示 v1.2、实际 API 是 v1.1，排障时会往错误的方向找
 *
 * 历史上这个项目靠"发布前人工统一递增 + 冒烟里比对一处 ?v="来防，靠的是人记得。
 * 这里把它变成可执行断言：**全仓只有一种版本串值，否则红**。
 *
 * 断言：
 *   [1] 前端与服务端两处 APP_VERSION 定义存在且相等
 *   [2] `js/*.js` 里所有 `?v=` 都等于定义值
 *   [3] `index.html` 里所有 `?v=` 都等于定义值
 *   [4] `index.html` 的每个本地 css/js 引用都带 `?v=`（新增资源忘了带 → 缓存穿透）
 *   [5] 扫描范围内不存在"另一个版本串"（形如 20261114f），即没人漏改
 *   [6] `.smoke/config.mjs` 的 APP_VERSION 镜像副本存在且等于基准版本串
 *
 * 运行：node .smoke/version-contract.mjs
 */
import { readFileSync, readdirSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const here = path.dirname(fileURLToPath(import.meta.url))
const root = path.resolve(here, '..')

let pass = 0
let fail = 0

function assert(cond, name, detail = '') {
  if (cond) { pass++; console.log('  OK   ' + name) }
  else { fail++; console.log('  FAIL ' + name + (detail ? ' -> ' + detail : '')) }
}

function read(rel) {
  return readFileSync(path.join(root, rel), 'utf8')
}

// ── 版本串的形态：8 位日期 + 一个小写字母（如 20261114f）
// 注意：带 g 的正则用 test() 会推进 lastIndex，导致后续 matchAll 漏匹配。
// 所以"单次判断"和"全部匹配"用两个独立的字面量，不要复用同一个对象。
const VERSION_ONE = /\b20\d{6}[a-z]\b/
const VERSION_RE = /\b20\d{6}[a-z]\b/g

// 收集被测文件清单（只查会参与构建/服务的代码，docs 里的历史版本串不在此列）
const jsFiles = readdirSync(path.join(root, 'js'))
  .filter((f) => f.endsWith('.js'))
  .map((f) => 'js/' + f)
  .sort()
const pyFiles = readdirSync(path.join(root, 'pyapp'))
  .filter((f) => f.endsWith('.py'))
  .map((f) => 'pyapp/' + f)
  .sort()
// `.smoke/config.mjs` 是第 5 类落点：前端测试用的镜像副本。必须显式单点加入——
// 整目录扫 .smoke/ 会把本文件注释里的历史示例串当成"另一个版本串"误报。
const smokeConfig = '.smoke/config.mjs'
const scanTargets = [...jsFiles, 'index.html', ...pyFiles, smokeConfig]

// ── [1] 两处定义
console.log('\n[1] 两处 APP_VERSION 定义')
const configSrc = read('js/config.js')
const initSrc = read('pyapp/__init__.py')
const frontMatch = configSrc.match(/export\s+const\s+APP_VERSION\s*=\s*'([^']+)'/)
const backMatch = initSrc.match(/^APP_VERSION\s*=\s*'([^']+)'/m)

assert(Boolean(frontMatch), "js/config.js 存在 `export const APP_VERSION = '…'`")
assert(Boolean(backMatch), "pyapp/__init__.py 存在 `APP_VERSION = '…'`")

const frontVer = frontMatch ? frontMatch[1] : null
const backVer = backMatch ? backMatch[1] : null
const version = frontVer || backVer

if (!version) {
  console.log('\n>>> 无法确定基准版本串，后续断言跳过')
  process.exit(1)
}
console.log(`    基准版本串 ${version}（js/config.js）`)

assert(frontVer === backVer, '前端与服务端的 APP_VERSION 相等', `front=${frontVer} back=${backVer}`)
assert(VERSION_ONE.test(version), '版本串形态符合 8 位日期 + 字母', version)

// ── [2] js 模块内的 ?v=
console.log('\n[2] js/*.js 里的 ?v=')
const jsRefs = []
for (const f of jsFiles) {
  const src = read(f)
  for (const m of src.matchAll(/\?v=([0-9a-zA-Z._-]+)/g)) {
    jsRefs.push({ file: f, value: m[1] })
  }
}
const jsBad = jsRefs.filter((r) => r.value !== version)
assert(jsRefs.length > 0, '确实找到了 ?v= 引用（否则本项检查是空转）', `found=${jsRefs.length}`)
assert(jsBad.length === 0, 'js 模块内所有 ?v= 都等于基准版本串',
  jsBad.map((r) => `${r.file}:${r.value}`).join(', '))
console.log(`    ${jsFiles.length} 个模块 · ${jsRefs.length} 处 ?v=`)

// ── [3] index.html 的 ?v=
console.log('\n[3] index.html 里的 ?v=')
const htmlSrc = read('index.html')
const htmlRefs = [...htmlSrc.matchAll(/\?v=([0-9a-zA-Z._-]+)/g)].map((m) => m[1])
const htmlBad = htmlRefs.filter((v) => v !== version)
assert(htmlRefs.length > 0, 'index.html 至少有一处 ?v=', `found=${htmlRefs.length}`)
assert(htmlBad.length === 0, 'index.html 所有 ?v= 都等于基准版本串', htmlBad.join(', '))
console.log(`    共 ${htmlRefs.length} 处 ?v=`)

// ── [4] index.html 的本地资源必须都带 ?v=
console.log('\n[4] index.html 本地资源是否都带缓存键')
// 只关心本地相对路径（./ 开头）；外链不该有，由冒烟测试另行断言
const localScripts = [...htmlSrc.matchAll(/<script[^>]+src="(\.\/[^"]+)"/g)].map((m) => m[1])
const localStyles = [...htmlSrc.matchAll(/<link[^>]+href="(\.\/[^"]+)"/g)].map((m) => m[1])
const localAssets = [...localScripts, ...localStyles]
const missingV = localAssets.filter((a) => !/\?v=/.test(a))

assert(localAssets.length > 0, '找到了本地 css/js 引用', `found=${localAssets.length}`)
assert(missingV.length === 0, '每个本地 css/js 引用都带 ?v=',
  missingV.join(', '))
console.log(`    ${localScripts.length} 个 script · ${localStyles.length} 个 link`)

// ── [5] 全仓不得残留另一个版本串
console.log('\n[5] 是否存在"另一个版本串"（漏改检测）')
const strays = []
for (const f of scanTargets) {
  const src = read(f)
  for (const m of src.matchAll(VERSION_RE)) {
    if (m[0] !== version) strays.push(`${f}:${m[0]}`)
  }
}
assert(strays.length === 0, '扫描范围内只有一个版本串值，没有漏改的', strays.join(', '))
console.log(`    扫描 ${scanTargets.length} 个文件（js/ + index.html + pyapp/ + .smoke/config.mjs）`)

// ── [6] 测试夹具里的镜像版本串
console.log('\n[6] .smoke/config.mjs 的 APP_VERSION 镜像副本')
const smokeSrc = read(smokeConfig)
const smokeMatch = smokeSrc.match(/export\s+const\s+APP_VERSION\s*=\s*'([^']+)'/)
assert(Boolean(smokeMatch), '.smoke/config.mjs 存在 `export const APP_VERSION = \'…\'`')
const smokeVer = smokeMatch ? smokeMatch[1] : null
assert(smokeVer === version, '镜像副本的 APP_VERSION 等于基准版本串',
  `mirror=${smokeVer} baseline=${version}`)
console.log(`    镜像值 ${smokeVer}`)

console.log('\n' + (fail ? `>>> 失败 ${fail} 项，通过 ${pass} 项` : `>>> 全部通过（${pass} 项）`))
process.exit(fail ? 1 : 0)
