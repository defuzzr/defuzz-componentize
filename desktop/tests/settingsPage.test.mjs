// Run from plugin root: node --import ./desktop/tests/sdk-shim.mjs --test desktop/tests/settingsPage.test.mjs
import test from 'node:test'
import assert from 'node:assert/strict'
import { readdirSync, readFileSync, mkdtempSync, writeFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { fileURLToPath } from 'node:url'
import path from 'node:path'
import { host } from '@hermes/plugin-sdk'
import plugin, {
  PLUGIN_ID,
  ISSUE_TRACKER_URL,
  BUNDLED_KITS_FALLBACK,
  KIT_FILTER_GROUPS,
  createSettingsPageModel,
  validateSettingsDraft,
  ComponentizeSettingsPage
} from '../plugin.js'

const PLUGIN_JS_PATH = fileURLToPath(new URL('../plugin.js', import.meta.url))
const PLUGIN_ROOT = path.resolve(path.dirname(PLUGIN_JS_PATH), '..')

function walkNodes(node, predicate) {
  if (!node || typeof node !== 'object') return []
  const matched = predicate(node) ? [node] : []
  const rawChildren = node.props?.children
  const list = Array.isArray(rawChildren) ? rawChildren.flat(Infinity) : [rawChildren]
  return [...matched, ...list.flatMap(child => walkNodes(child, predicate))]
}

function textOf(node) {
  if (node === null || node === undefined || typeof node === 'boolean') return ''
  if (typeof node === 'string' || typeof node === 'number') return String(node)
  if (Array.isArray(node)) return node.map(textOf).join('')
  if (typeof node === 'object') {
    if (node.type === 'style') return ''
    return textOf(node.props?.children)
  }
  return ''
}

function byId(tree, id) {
  return walkNodes(tree, n => n.props?.id === id)[0]
}

function makeWebAppPreview() {
  const raw = JSON.parse(readFileSync(path.join(PLUGIN_ROOT, 'vendor/kits/web-app/kit.json'), 'utf8'))
  const dirs = raw.dirs.map(d => ({ dir: d.path, summary: d.summary, why: d.why || 'declared by the web-app kit' }))
  const pathSet = new Set(['AGENTS.md', '.gitignore', '.gitattributes'])
  for (const d of raw.dirs) {
    const parts = d.path.split('/')
    for (let i = 1; i <= parts.length; i += 1) {
      pathSet.add(parts.slice(0, i).join('/') + '/')
    }
  }
  for (const f of raw.files || []) {
    const parts = f.path.split('/')
    for (let i = 1; i < parts.length; i += 1) {
      pathSet.add(parts.slice(0, i).join('/') + '/')
    }
    pathSet.add(f.path)
  }
  return {
    schema: 'componentize.kit/1',
    ok: true,
    kit: BUNDLED_KITS_FALLBACK.find(k => k.id === 'web-app'),
    declared_dirs: dirs,
    placement_rules: [],
    tree: [...pathSet].sort(),
    agents_block: '<!-- kit:web-app v1 begin -->\n<!-- kit:web-app end -->\n'
  }
}

function renderWithModel(t, ctx, model) {
  const slots = []
  let cursor = 0
  globalThis.__czTestUseState = init => {
    const idx = cursor++
    if (!(idx in slots)) {
      slots[idx] = typeof init === 'function' ? init() : init
    }
    return [
      slots[idx],
      next => {
        slots[idx] = typeof next === 'function' ? next(slots[idx]) : next
      }
    ]
  }
  t.after(() => {
    delete globalThis.__czTestUseState
  })
  return () => {
    cursor = 0
    slots[0] = model.get()
    return ComponentizeSettingsPage({ ctx, model })
  }
}

function createMockHost(t, { rpcHandler, profile = 'default', connectionId = 'local' } = {}) {
  const prevState = host.state
  const prevConn = host.activeConnectionId
  const prevReq = host.request
  let currentProfile = profile
  let currentConn = connectionId
  const rpcCalls = []

  host.state = {
    profile: { get: () => currentProfile },
    connectionId: { get: () => currentConn }
  }
  host.activeConnectionId = () => currentConn
  host.request = async (method, params) => {
    rpcCalls.push({ method, params })
    if (rpcHandler) return rpcHandler(method, params)
    return { plugins: [] }
  }

  t.after(() => {
    host.state = prevState
    host.activeConnectionId = prevConn
    host.request = prevReq
  })

  return {
    rpcCalls,
    setProfile(next) {
      currentProfile = next
    },
    setConnection(next) {
      currentConn = next
    }
  }
}

function createFixture({ restMap = {}, clipboardSink = [] } = {}) {
  const restCalls = []
  const disposers = []
  const ctx = {
    onDispose(fn) {
      disposers.push(fn)
    },
    setTimeout(fn) {
      fn()
      return () => {}
    },
    os: {
      async writeClipboard(text) {
        clipboardSink.push(text)
        return true
      }
    },
    async rest(routePath, opts) {
      restCalls.push({ path: routePath, opts })
      const entry = restMap[routePath]
      if (entry instanceof Error) throw entry
      if (typeof entry === 'function') return entry(routePath, opts)
      if (entry !== undefined) return entry
      throw Object.assign(new Error('Not Found'), { status: 404 })
    }
  }
  return {
    ctx,
    restCalls,
    clipboardSink,
    disposeAll() {
      for (const fn of disposers) fn()
    }
  }
}

test('static surface check: SDK imports only, zero fixed colours, single allowed external URL, no register-time calls', () => {
  const source = readFileSync(PLUGIN_JS_PATH, 'utf8')
  const imports = [
    ...source.matchAll(/(?:^|\n)\s*import\b[\s\S]*?\bfrom\s+['"]([^'"]+)['"]|(?:^|\n)\s*import\s+['"]([^'"]+)['"]/g)
  ].map(m => m[1] || m[2])
  assert.equal(imports.length, 3)
  for (const spec of imports) {
    assert.ok(['react', 'react/jsx-runtime', '@hermes/plugin-sdk'].includes(spec), `unexpected import: ${spec}`)
  }

  // No fixed hex, rgb/rgba, or hsl/hsla colours anywhere in desktop/plugin.js
  assert.doesNotMatch(source, /#[0-9a-fA-F]{3,8}\b/)
  assert.doesNotMatch(source, /\brgba?\s*\(|\bhsla?\s*\(/)

  // Only the issue tracker URL is allowed
  const urls = source.match(/https?:\/\/[^\s'")>`]+/g) ?? []
  assert.deepEqual(urls, [ISSUE_TRACKER_URL])

  // No forbidden DOM, storage, or script-injection constructs
  assert.doesNotMatch(
    source,
    /localStorage|sessionStorage|document\.|window\.|hermesDesktop|<script\b|eval\(|new\s+Function\(|\[data-(?:slot|tour|sidebar|testid)\b/
  )

  // Registration registers Settings page without making any REST or RPC calls
  const f = createFixture()
  const registeredPages = []
  plugin.register({
    ...f.ctx,
    registerSettingsPage: page => registeredPages.push(page)
  })
  assert.equal(plugin.id, PLUGIN_ID)
  assert.equal(registeredPages.length, 1)
  assert.equal(registeredPages[0].id, 'settings')
  assert.equal(registeredPages[0].title, 'Componentize')
  assert.equal(registeredPages[0].icon, 'package')
  assert.equal(registeredPages[0].render().type, ComponentizeSettingsPage)
  assert.equal(f.restCalls.length, 0)
})

test('bundled kit fallback matches all 14 vendor/kits/*/kit.json manifests on disk', () => {
  const kitsDir = path.join(PLUGIN_ROOT, 'vendor/kits')
  const diskIds = readdirSync(kitsDir).sort()
  assert.equal(diskIds.length, 14)
  assert.equal(BUNDLED_KITS_FALLBACK.length, 14)
  assert.deepEqual(
    BUNDLED_KITS_FALLBACK.map(k => k.id),
    diskIds
  )
  for (const row of BUNDLED_KITS_FALLBACK) {
    const raw = JSON.parse(readFileSync(path.join(kitsDir, row.id, 'kit.json'), 'utf8'))
    assert.equal(row.name, raw.name, row.id)
    assert.equal(row.summary, raw.summary, row.id)
    assert.equal(row.version, raw.version, row.id)
    assert.equal(row.availability, raw.availability || 'available', row.id)
    assert.deepEqual(row.tags, raw.tags, row.id)
  }
})

test('synthetic coming soon kit is muted, disabled and cannot open an apply action', async t => {
  createMockHost(t)
  const folder = mkdtempSync(path.join(tmpdir(), 'future-kit-'))
  t.after(() => rmSync(folder, { recursive: true, force: true }))
  const manifest = path.join(folder, 'kit.json')
  writeFileSync(manifest, JSON.stringify({
    schema_version: 1, id: 'future-kit', name: 'Future kit', version: 1,
    summary: 'Synthetic planned kit.', tags: [], availability: 'coming_soon',
    dirs: [{ path: 'src', summary: 'Sources.' }], agents: { purpose: ['Future project.'] }
  }))
  const f = createFixture({ restMap: {
    '/status': { ok: true },
    '/kits': { ok: true, kits: [JSON.parse(readFileSync(manifest, 'utf8'))] }
  } })
  const model = createSettingsPageModel(f.ctx)
  await model.load()
  const render = renderWithModel(t, f.ctx, model)
  const row = walkNodes(render(), n => n.props?.['data-kit-id'] === 'future-kit')[0]
  assert.equal(row.props['aria-disabled'], 'true')
  assert.match(row.props.className, /cz-kit-unavailable/)
  assert.match(textOf(row), /Coming soon/)
  const button = walkNodes(row, n => n.props?.['data-toggle-kit'] === 'future-kit')[0]
  assert.equal(button.props.disabled, true)
  await model.toggleKit('future-kit')
  assert.notEqual(model.get().openKitId, 'future-kit')
  assert.equal(walkNodes(render(), n => n.props?.['data-copy-kit'] === 'future-kit').length, 0)
})

test('loading state renders busy skeleton, disables form actions, and renders all 7 sections, SVGs, ordered lists, and 14 kits', t => {
  createMockHost(t)
  const f = createFixture()
  const model = createSettingsPageModel(f.ctx)
  const render = renderWithModel(t, f.ctx, model)
  const tree = render()
  const allText = textOf(tree)

  const loadingBox = byId(tree, 'cz-state-loading')
  assert.ok(loadingBox)
  assert.equal(loadingBox.props['aria-busy'], 'true')
  assert.match(textOf(loadingBox), /Reading plugin settings and checking component library status/)
  assert.equal(byId(tree, 'cz-save-btn').props.disabled, true)
  assert.equal(byId(tree, 'cz-discard-btn').props.disabled, true)

  // All 7 sections in order
  const sections = walkNodes(tree, n => n.type === 'section')
  assert.deepEqual(
    sections.map(s => s.props.id),
    ['sec-what', 'sec-flow-kits', 'sec-flow-comp', 'sec-settings', 'sec-machine', 'sec-kits', 'sec-help']
  )

  // Both accessible inline SVGs and both ordered text-equivalent lists
  const svgs = walkNodes(tree, n => n.type === 'svg')
  assert.equal(svgs.length, 2)
  for (const svg of svgs) {
    assert.equal(svg.props.role, 'img')
    assert.ok(svg.props['aria-labelledby'])
  }
  const kitsSteps = walkNodes(byId(tree, 'cz-kits-step-list'), n => n.type === 'li')
  const compSteps = walkNodes(byId(tree, 'cz-comp-step-list'), n => n.type === 'li')
  assert.equal(kitsSteps.length, 6)
  assert.equal(compSteps.length, 9)

  // All 14 kits rendered
  const kitItems = walkNodes(byId(tree, 'cz-kit-list'), n => n.props?.role === 'listitem')
  assert.equal(kitItems.length, 14)

  // Disclosure rows and Help items
  const disclosureRows = walkNodes(tree, n => n.props?.className === 'cz-disclosure-row')
  assert.equal(disclosureRows.length, 5)
  const faqItems = walkNodes(tree, n => n.type === 'details')
  assert.equal(faqItems.length, 5)
  assert.match(allText, /https:\/\/github\.com\/defuzzr\/defuzz-componentize\/issues/)
})

test('configured state renders READY status, indexed count, Defuzz status, and expanded web-app folder preview', async t => {
  createMockHost(t, {
    rpcHandler: async (method, params) => {
      if (method === 'plugins.manage' && params.action === 'list') {
        return {
          plugins: [
            {
              key: 'defuzz-componentize',
              name: 'defuzz-componentize',
              version: '0.2.0',
              settings_schema: [
                { key: 'library_root', value: '/home/user/components' },
                { key: 'timeout', value: 900 }
              ]
            },
            { key: 'defuzz', name: 'defuzz', status: 'enabled' }
          ]
        }
      }
      return {}
    }
  })

  const webAppPreview = makeWebAppPreview()
  const f = createFixture({
    restMap: {
      '/status': {
        ok: true,
        version: '0.2.0',
        checker_version: '1.3.0',
        kits_count: 14,
        settings: { library_root: '/home/user/components', timeout: 900 },
        library: {
          configured: true,
          ok: true,
          state: 'ready',
          count: 12,
          index_rel: 'components/COMPONENTS.md',
          message: 'Component library index is ready.'
        }
      },
      '/kits': { schema: 'componentize.kit/1', ok: true, kits: BUNDLED_KITS_FALLBACK },
      '/kits/web-app': webAppPreview
    }
  })

  const model = createSettingsPageModel(f.ctx)
  await model.load()
  const render = renderWithModel(t, f.ctx, model)
  const tree = render()

  assert.match(textOf(byId(tree, 'cz-st-library-val')), /READY.*\/home\/user\/components/)
  assert.equal(textOf(byId(tree, 'cz-st-count-val')), '12 components')
  assert.equal(textOf(byId(tree, 'cz-st-defuzz-val')), 'INSTALLED & ENABLED')
  assert.equal(byId(tree, 'cz-input-library-root').props.value, '/home/user/components')
  assert.equal(byId(tree, 'cz-input-timeout').props.value, '900')
  assert.equal(byId(tree, 'cz-banner-empty'), undefined)
  assert.equal(byId(tree, 'cz-banner-missing-folder'), undefined)

  const previewPanel = byId(tree, 'cz-kit-preview-web-app')
  assert.ok(previewPanel)
  assert.match(textOf(previewPanel), /22 declared folders · 44 total paths/)
  assert.match(textOf(previewPanel), /Git LFS rules included/)
})

test('first-run empty state renders FIRST RUN callout, NOT SET YET status, and copyable init command', async t => {
  createMockHost(t, {
    rpcHandler: async () => ({
      plugins: [
        {
          key: 'defuzz-componentize',
          name: 'defuzz-componentize',
          version: '0.2.0',
          settings_schema: [
            { key: 'library_root', value: '' },
            { key: 'timeout', value: 900 }
          ]
        }
      ]
    })
  })
  const clipboard = []
  const f = createFixture({
    clipboardSink: clipboard,
    restMap: {
      '/status': {
        ok: true,
        version: '0.2.0',
        checker_version: '1.3.0',
        kits_count: 14,
        settings: { library_root: '', timeout: 900 },
        library: {
          configured: false,
          ok: false,
          state: 'not_set',
          count: null,
          index_rel: null,
          message: 'No component library folder is configured.'
        }
      },
      '/kits': { schema: 'componentize.kit/1', ok: true, kits: BUNDLED_KITS_FALLBACK },
      '/kits/web-app': makeWebAppPreview()
    }
  })

  const model = createSettingsPageModel(f.ctx)
  await model.load()
  const render = renderWithModel(t, f.ctx, model)
  const tree = render()

  const emptyBanner = byId(tree, 'cz-banner-empty')
  assert.ok(emptyBanner)
  assert.equal(emptyBanner.props.role, 'status')
  assert.match(textOf(emptyBanner), /FIRST RUN/)
  assert.match(textOf(emptyBanner), /No component library folder is set yet/)
  assert.equal(textOf(byId(tree, 'cz-st-library-val')), 'NOT SET YET')
  assert.equal(textOf(byId(tree, 'cz-st-count-val')), 'None (no library set)')
  assert.equal(textOf(byId(tree, 'cz-st-defuzz-val')), 'NOT INSTALLED')

  await byId(tree, 'cz-copy-init-btn').props.onClick()
  assert.deepEqual(clipboard, ['hermes componentize init /home/user/components'])
})

test('folder-missing and timed-out states render accessible alert banners and plain recovery copy', async t => {
  createMockHost(t, {
    rpcHandler: async () => ({
      plugins: [
        {
          key: 'defuzz-componentize',
          name: 'defuzz-componentize',
          version: '0.2.0',
          settings_schema: [
            { key: 'library_root', value: '/home/user/old-components' },
            { key: 'timeout', value: 900 }
          ]
        }
      ]
    })
  })

  // Missing folder state
  const fMissing = createFixture({
    restMap: {
      '/status': {
        ok: true,
        version: '0.2.0',
        checker_version: '1.3.0',
        kits_count: 14,
        settings: { library_root: '/home/user/old-components', timeout: 900 },
        library: {
          configured: true,
          ok: false,
          state: 'missing',
          count: null,
          index_rel: null,
          message: 'Configured library folder does not exist on disk.'
        }
      },
      '/kits': { schema: 'componentize.kit/1', ok: true, kits: BUNDLED_KITS_FALLBACK },
      '/kits/web-app': makeWebAppPreview()
    }
  })
  const mMissing = createSettingsPageModel(fMissing.ctx)
  await mMissing.load()
  const treeMissing = renderWithModel(t, fMissing.ctx, mMissing)()
  const missingBanner = byId(treeMissing, 'cz-banner-missing-folder')
  assert.ok(missingBanner)
  assert.equal(missingBanner.props.role, 'alert')
  assert.match(textOf(missingBanner), /ERROR · LIBRARY MISSING/)
  assert.match(textOf(missingBanner), /hermes componentize init \/home\/user\/old-components/)
  assert.match(textOf(byId(treeMissing, 'cz-st-library-val')), /NOT FOUND.*\/home\/user\/old-components/)
  assert.equal(textOf(byId(treeMissing, 'cz-st-count-val')), 'Unknown (folder missing)')

  // Timed-out state
  const timeoutErr = Object.assign(new Error('Request timed out'), { name: 'TimeoutError' })
  const fTimeout = createFixture({
    restMap: {
      '/status': timeoutErr
    }
  })
  const mTimeout = createSettingsPageModel(fTimeout.ctx)
  await mTimeout.load()
  const treeTimeout = renderWithModel(t, fTimeout.ctx, mTimeout)()
  const timeoutBanner = byId(treeTimeout, 'cz-banner-timeout')
  assert.ok(timeoutBanner)
  assert.equal(timeoutBanner.props.role, 'alert')
  assert.match(textOf(timeoutBanner), /ERROR · EXIT 2/)
  assert.match(textOf(timeoutBanner), /Status probe timed out before finishing/)
  assert.match(textOf(byId(treeTimeout, 'cz-st-library-val')), /PROBE TIMED OUT.*\/home\/user\/old-components/)
  assert.equal(textOf(byId(treeTimeout, 'cz-st-count-val')), 'Unknown (probe timed out)')
})

test('draft validation, discard draft, save via plugins.manage settings, kit filters, and profile-change guard', async t => {
  // Direct validator unit checks
  for (const badPath of ['/', '~', '/home', 'relative/lib', '/home/user/../etc', '/home/user/.hermes/lib', '/a//b']) {
    const res = validateSettingsDraft(badPath, '900')
    assert.equal(res.valid, false, badPath)
    assert.ok(res.libErr, badPath)
  }
  for (const badTime of ['', '0', '-10', 'NaN', 'Infinity']) {
    const res = validateSettingsDraft('/home/user/components', badTime)
    assert.equal(res.valid, false, badTime)
    assert.ok(res.timeErr, badTime)
  }
  assert.equal(validateSettingsDraft('', '900').valid, true)
  assert.equal(validateSettingsDraft('/home/user/components', '1200').valid, true)

  const hostCtl = createMockHost(t, {
    rpcHandler: async (method, params) => {
      if (method === 'plugins.manage' && params.action === 'list') {
        return {
          plugins: [
            {
              key: 'defuzz-componentize',
              name: 'defuzz-componentize',
              version: '0.2.0',
              settings_schema: [
                { key: 'library_root', value: '/home/user/components' },
                { key: 'timeout', value: 900 }
              ]
            }
          ]
        }
      }
      if (method === 'plugins.manage' && params.action === 'settings') {
        return { ok: true, written: ['library_root', 'timeout'] }
      }
      return {}
    }
  })

  const f = createFixture({
    restMap: {
      '/status': {
        ok: true,
        version: '0.2.0',
        checker_version: '1.3.0',
        kits_count: 14,
        settings: { library_root: '/home/user/components', timeout: 900 },
        library: {
          configured: true,
          ok: true,
          state: 'ready',
          count: 4,
          index_rel: 'components/COMPONENTS.md',
          message: 'Component library index is ready.'
        }
      },
      '/settings/validate': (_p, opts) => ({
        ok: true,
        values: { library_root: opts.body.library_root, timeout: opts.body.timeout }
      }),
      '/kits': { schema: 'componentize.kit/1', ok: true, kits: BUNDLED_KITS_FALLBACK },
      '/kits/web-app': makeWebAppPreview(),
      '/kits/component-library': {
        schema: 'componentize.kit/1',
        ok: true,
        kit: BUNDLED_KITS_FALLBACK.find(k => k.id === 'component-library'),
        declared_dirs: [{ dir: 'components', summary: 'Components.', why: 'reuse' }],
        placement_rules: [],
        tree: ['AGENTS.md', '.gitignore', 'components/', 'components/COMPONENTS.md'],
        agents_block: ''
      }
    }
  })

  const model = createSettingsPageModel(f.ctx)
  await model.load()
  const render = renderWithModel(t, f.ctx, model)

  // Invalid path blocks save and sets aria-invalid
  model.editDraft({ library_root: '/home/user/.hermes/test' })
  let tree = render()
  assert.equal(byId(tree, 'cz-input-library-root').props['aria-invalid'], 'true')
  assert.equal(byId(tree, 'cz-save-btn').props.disabled, true)
  assert.equal(await model.save(), false)

  // Discard restores saved values
  byId(tree, 'cz-discard-btn').props.onClick()
  tree = render()
  assert.equal(byId(tree, 'cz-input-library-root').props.value, '/home/user/components')
  assert.equal(textOf(byId(tree, 'cz-save-feedback')), 'Draft changes discarded.')

  // Valid edit saves via plugins.manage settings
  model.editDraft({ library_root: '/home/user/new-library', timeout: '1200' })
  tree = render()
  assert.equal(byId(tree, 'cz-save-btn').props.disabled, false)
  assert.equal(await model.save(), true)
  const settingsRpc = hostCtl.rpcCalls.find(c => c.method === 'plugins.manage' && c.params.action === 'settings')
  assert.deepEqual(settingsRpc.params, {
    action: 'settings',
    key: 'defuzz-componentize',
    values: { library_root: '/home/user/new-library', timeout: 1200 }
  })
  assert.equal(textOf(byId(render(), 'cz-save-feedback')), 'Saved to plugins.entries.defuzz-componentize.settings.')

  // Kit category chip filtering and search
  assert.equal(KIT_FILTER_GROUPS.length, 10)
  model.setKitGroup('python')
  tree = render()
  const pyItems = walkNodes(byId(tree, 'cz-kit-list'), n => n.props?.role === 'listitem')
  assert.equal(pyItems.length, 1)
  assert.equal(pyItems[0].props['data-kit-id'], 'component-library')

  await model.toggleKit('component-library')
  tree = render()
  assert.ok(byId(tree, 'cz-kit-preview-component-library'))

  model.setKitGroup('all')
  model.setKitSearch('nonexistent-xyz-query')
  assert.match(textOf(byId(render(), 'cz-kit-list')), /No kits match that filter/)
  model.setKitSearch('')
  assert.equal(walkNodes(byId(render(), 'cz-kit-list'), n => n.props?.role === 'listitem').length, 14)

  // Profile change fails closed before any further RPC/REST write
  hostCtl.setProfile('secondary-profile')
  model.editDraft({ timeout: '1500' })
  assert.equal(await model.save(), false)
  assert.match(textOf(byId(render(), 'cz-banner-error')), /Connection or profile changed/)
})
