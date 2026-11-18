/**
 * 视图层 ↔ 事件委托的静态契约测试。
 *
 * 为什么需要它：views.js 是纯渲染（只产出 HTML 字符串），交互全靠 `data-action` 表达，
 * 真正的处理在 app.js 的 `dispatch()` 里。这两侧一旦对不上，用户看到的就是
 * **点击完全没有反应** —— 没有报错、没有日志、测试也未必能覆盖到那个按钮。
 * 「修改密码」整条链路就是这样断的：按钮渲染了 `data-action="change-password"`，
 * 但 `#pwd-form` 从未被渲染，`submit-password` 分支永远没有触发源，
 * `api.changePassword()` 成了死接口。这类缺陷必须在静态层面兜住。
 *
 * 断言：两侧的双向差集 ⊆ {'noop'}
 *   · data-action 有值但 dispatch 没有分支 → 点了没反应（按钮是死的）
 *   · dispatch 有分支但没有任何 data-action 触发 → 不可达分支（功能实际不存在）
 *   `noop` 是合法值：app.js 在进入 dispatch 之前就 `if (action === 'noop') return`，
 *   视图层用它标记"这个元素不需要处理"（例如外链 `<a>`）。
 *
 * 第 3 节核对 WRITE_ACTIONS（写操作重入锁清单）：建号是"点两次就多一个账号"的动作，
 * 漏进清单的后果比普通写操作严重得多，必须在静态层面守住。
 *
 * 运行：node .smoke/contract-test.mjs
 */
import { readFileSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const here = path.dirname(fileURLToPath(import.meta.url))
const root = path.resolve(here, '..')

const read = (f) => readFileSync(path.join(root, 'js', f), 'utf8')
const viewsSrc = read('views.js')
const appSrc = read('app.js')

/** 视图侧：所有字面量 data-action="..."（本项目不允许动态拼接，动态值会被单独报错） */
const viewActions = new Set(
  [...viewsSrc.matchAll(/data-action="([^"$]+)"/g)].map((m) => m[1].trim()).filter(Boolean),
)
const dynamicActions = [...viewsSrc.matchAll(/data-action="\$\{/g)].length

/** 委托侧：dispatch() 里所有的 `case '...'` */
const dispatchCases = new Set([...appSrc.matchAll(/case '([A-Za-z0-9_-]+)':/g)].map((m) => m[1]))

/** noop 在 dispatch 之前被拦截，不参与比对 */
const EXEMPT = new Set(['noop'])

const deadButtons = [...viewActions].filter((a) => !dispatchCases.has(a) && !EXEMPT.has(a)).sort()
const deadBranches = [...dispatchCases].filter((a) => !viewActions.has(a) && !EXEMPT.has(a)).sort()

let pass = 0
let fail = 0
function assert(cond, name, detail = '') {
  if (cond) { pass++; console.log('  OK   ' + name) }
  else { fail++; console.log('  FAIL ' + name + (detail ? ' -> ' + detail : '')) }
}

console.log('\n[1] data-action ↔ dispatch 双向一致性')
{
  assert(dynamicActions === 0, 'views.js 不存在动态拼接的 data-action（否则无法静态校验）', `count=${dynamicActions}`)
  console.log(`    视图侧 data-action ${viewActions.size} 个 · 委托侧 case ${dispatchCases.size} 个`)
  assert(deadButtons.length === 0, '每个 data-action 都有 dispatch 分支（没有点了不反应的按钮）', deadButtons.join(', '))
  assert(deadBranches.length === 0, '每个 dispatch 分支都有触发源（没有不可达分支 / 死功能）', deadBranches.join(', '))
}

console.log('\n[2] 写操作动作清单可核对')
{
  // 这些动作会写库，必须出现在两侧；漏一个就意味着某条写链路不通
  const required = [
    'save-new-item', 'save-item', 'save-project', 'save-context', 'save-team-new', 'save-join',
    'save-team', 'save-wip', 'save-member', 'save-resource', 'save-oncall', 'submit-password',
    'delete-item', 'delete-project', 'delete-context', 'delete-resource', 'delete-oncall',
    'add-progress', 'archive-team', 'rotate-invite', 'remove-member', 'finish-review',
    // CR-2026-001：建号是"点两次就多一个账号"的写操作
    'save-account',
    // 证书管理：上传/移除都会让后台重新加载监听，连点会连续应用两次
    'upload-cert', 'delete-cert',
  ]
  for (const a of required) {
    assert(dispatchCases.has(a) && viewActions.has(a), `写操作 ${a} 两侧都齐备`,
      `dispatch=${dispatchCases.has(a)} view=${viewActions.has(a)}`)
  }
}

console.log('\n[3] WRITE_ACTIONS 重入锁清单与 dispatch 一致')
{
  // WRITE_ACTIONS 决定"哪些动作在进入前加锁"。它一旦漏项，连点就会重复写库
  // （浮层有 180ms 退场动画，这段时间按钮仍然可点）。建号漏项的后果是**多出一个账号**，
  // 而账号没有"撤销"按钮，只能靠运维删库 —— 所以单独守一条。
  const block = appSrc.match(/const WRITE_ACTIONS = new Set\(\[([\s\S]*?)\]\)/)
  assert(!!block, 'app.js 中能定位到 WRITE_ACTIONS 清单')
  const locked = new Set([...(block?.[1] || '').matchAll(/'([A-Za-z0-9_-]+)'/g)].map((m) => m[1]))

  const mustLock = ['save-account', 'submit-password', 'upload-cert', 'delete-cert']
  for (const a of mustLock) {
    assert(locked.has(a), `${a} 已加入 WRITE_ACTIONS（防连点重复提交）`)
  }
  const orphan = [...locked].filter((a) => !dispatchCases.has(a)).sort()
  assert(orphan.length === 0, 'WRITE_ACTIONS 里没有已不存在于 dispatch 的动作', orphan.join(', '))
}

console.log('\n[4] 证书管理：动作与渲染入口齐备')
{
  // 文件选择是 change 驱动的（不经过点击），必须仍然登记成一个可核对的 data-action；
  // 其余三个是普通交互。漏一个就会出现"按钮在、点了没反应"。
  for (const a of ['load-cert', 'upload-cert', 'delete-cert', 'cert-file-picked', 'toggle-cert-paste']) {
    assert(dispatchCases.has(a) && viewActions.has(a), `证书动作 ${a} 两侧都齐备`,
      `dispatch=${dispatchCases.has(a)} view=${viewActions.has(a)}`)
  }
}

console.log('\n' + (fail ? `>>> 失败 ${fail} 项，通过 ${pass} 项` : `>>> 全部通过（${pass} 项）`))
process.exit(fail ? 1 : 0)
