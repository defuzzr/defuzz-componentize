// Run from plugin root: node --import ./desktop/tests/sdk-shim.mjs --test desktop/tests/settingsValidation.test.mjs
// The library_root rule mirrored on the page and enforced by the server, and SDK-only host use.
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { host } from '@hermes/plugin-sdk'
import { createSettingsPageModel, validateSettingsDraft, ComponentizeSettingsPage } from '../plugin.js'

const SOURCE = readFileSync(fileURLToPath(new URL('../plugin.js', import.meta.url)), 'utf8')

// Same wording as settings_rule.py on the server.
const M_ABS = 'Enter an absolute path starting with /, such as /home/user/components.'
const M_DOTDOT = 'Remove ".." segments: enter the full path of the folder.'
const M_ROOT = 'Refused: pick a dedicated folder, not the filesystem root (/).'
const M_HOME = 'Refused: pick a dedicated folder inside your home folder, not the home folder itself.'
const M_SYSTEM = 'Refused: pick a dedicated folder, not a system folder such as /etc or /usr.'
const M_HERMES = 'Refused: pick a dedicated folder outside Hermes home (such as ~/.hermes).'
const T_MAX = 'Invalid timeout: enter at most 86400 seconds (24 hours).'

test('client mirror refuses the obvious classes with the server wording', () => {
  for (const [raw, msg] of [
    ['/etc', M_SYSTEM],
    ['/usr/bin', M_SYSTEM],
    ['/etc/cz-probe', M_SYSTEM],
    ['/var', M_SYSTEM],
    ['/home', M_SYSTEM],
    ['/lib64', M_SYSTEM],
    ['/', M_ROOT],
    ['relative/lib', M_ABS],
    ['~user/lib', M_ABS],
    ['/srv/../etc', M_DOTDOT],
    ['~/a/../b', M_DOTDOT],
    ['~', M_HOME],
    ['/home/user/.hermes/lib', M_HERMES]
  ]) {
    const res = validateSettingsDraft(raw, '900')
    assert.equal(res.valid, false, raw)
    assert.equal(res.libErr, msg, raw)
  }
  // Deferred to the server: ~/..., symlinks and Hermes homes it cannot see.
  assert.equal(validateSettingsDraft('~/components', '900').valid, true)
  assert.equal(validateSettingsDraft('/srv/components', '900').valid, true)
  assert.equal(validateSettingsDraft('/etcetera/lib', '900').valid, true)
  assert.equal(validateSettingsDraft('/srv/lib', '86401').timeErr, T_MAX)
})

test('page uses only documented host surface (host.request, host.state.*)', () => {
  assert.doesNotMatch(SOURCE, /host\.activeConnectionId|host\.getGateway|getGateway\?\./)
})

function setup(t, validateReply) {
  const prev = { state: host.state, request: host.request }
  const rpc = []
  host.state = { profile: { get: () => 'default' }, connectionId: { get: () => 'local' } }
  host.request = async (method, params) => {
    rpc.push({ method, params })
    if (params?.action === 'list') {
      return { plugins: [{ key: 'defuzz-componentize', settings_schema: [{ key: 'library_root', value: '' }, { key: 'timeout', value: 900 }] }] }
    }
    return { ok: true }
  }
  t.after(() => Object.assign(host, prev))
  const rest = []
  const ctx = {
    onDispose() {},
    setTimeout(fn) { fn(); return () => {} },
    os: { async writeClipboard() { return true } },
    async rest(path, opts) {
      rest.push({ path, opts })
      if (path === '/settings/validate') {
        if (validateReply instanceof Error) throw validateReply
        return validateReply
      }
      if (path === '/status') return { ok: true, settings: { library_root: '', timeout: 900 }, library: { configured: false, ok: false, state: 'not_set', count: null, index_rel: null, message: 'x' } }
      if (path === '/kits') return { ok: true, kits: [] }
      throw Object.assign(new Error('Not Found'), { status: 404 })
    }
  }
  return { ctx, rpc, rest }
}

function renderLib(model, ctx) {
  const slots = []
  let cursor = 0
  globalThis.__czTestUseState = init => {
    const idx = cursor++
    if (!(idx in slots)) slots[idx] = typeof init === 'function' ? init() : init
    return [slots[idx], next => { slots[idx] = typeof next === 'function' ? next(slots[idx]) : next }]
  }
  try {
    slots[0] = model.get()
    const tree = ComponentizeSettingsPage({ ctx, model })
    const find = (n, id) => {
      if (!n || typeof n !== 'object') return null
      if (n.props?.id === id) return n
      for (const k of [n.props?.children].flat(Infinity)) {
        const hit = find(k, id)
        if (hit) return hit
      }
      return null
    }
    return find(tree, 'cz-msg-library-root')
  } finally {
    delete globalThis.__czTestUseState
  }
}

test('server refusal is shown next to the field and nothing is saved', async t => {
  const { ctx, rpc, rest } = setup(t, { ok: false, errors: { library_root: M_SYSTEM } })
  const model = createSettingsPageModel(ctx)
  await model.load()
  model.editDraft({ library_root: '/srv/link-to-etc' })
  assert.equal(await model.save(), false)
  const call = rest.find(r => r.path === '/settings/validate')
  assert.equal(call.opts.method, 'POST')
  assert.deepEqual(call.opts.body, { library_root: '/srv/link-to-etc', timeout: 900 })
  assert.equal(rpc.some(c => c.params?.action === 'settings'), false)
  assert.equal(renderLib(model, ctx).props.children, M_SYSTEM)
  model.editDraft({ library_root: '/srv/other' })
  assert.equal(renderLib(model, ctx).props.children.includes(M_SYSTEM), false)
})

test('the saved value is the server-normalized path', async t => {
  const { ctx, rpc } = setup(t, { ok: true, values: { library_root: '/home/user/lib', timeout: 1200 } })
  const model = createSettingsPageModel(ctx)
  await model.load()
  model.editDraft({ library_root: '~/lib', timeout: '1200' })
  assert.equal(await model.save(), true)
  const saved = rpc.find(c => c.params?.action === 'settings')
  assert.deepEqual(saved.params.values, { library_root: '/home/user/lib', timeout: 1200 })
  assert.equal(model.get().savedSettings.library_root, '/home/user/lib')
})

test('no save when the validate route cannot be reached', async t => {
  const { ctx, rpc } = setup(t, Object.assign(new Error('Not Found'), { status: 404 }))
  const model = createSettingsPageModel(ctx)
  await model.load()
  model.editDraft({ library_root: '/srv/lib' })
  assert.equal(await model.save(), false)
  assert.equal(rpc.some(c => c.params?.action === 'settings'), false)
  assert.match(model.get().errorBanner, /nothing was saved/)
})
