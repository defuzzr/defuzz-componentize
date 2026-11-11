// Run from plugin root: node --import ./desktop/tests/sdk-shim.mjs --test desktop/tests/settingsTimeoutRule.test.mjs
// Client mirror of settings_rule.check_timeout (same messages) and the guarded connection atom.
import test from 'node:test'
import assert from 'node:assert/strict'
import { host } from '@hermes/plugin-sdk'
import { validateSettingsDraft, activeConnectionScope, createScopedRest } from '../plugin.js'

const T_BAD = 'Invalid timeout: enter a finite number greater than 0.'
const T_MAX = 'Invalid timeout: enter at most 86400 seconds (24 hours).'

test('timeout mirror matches the server rule', () => {
  const huge = '1' + '0'.repeat(400)
  for (const [raw, msg] of [
    ['', T_BAD], ['   ', T_BAD], ['0', T_BAD], ['-0', T_BAD], ['-0.0', T_BAD], ['-5', T_BAD],
    ['NaN', T_BAD], ['nan', T_BAD], ['Infinity', T_BAD], ['-Infinity', T_BAD], ['0x10', T_BAD],
    ['0b1', T_BAD], ['0o7', T_BAD], ['1_000', T_BAD], ['12abc', T_BAD], ['1e', T_BAD], ['1e-999', T_BAD],
    [' 12 x', T_BAD], ['\n12', T_BAD], ['1'.repeat(2000), T_BAD], ['true', T_BAD],
    ['1e12', T_MAX], ['86401', T_MAX], ['86400.0001', T_MAX], [huge, T_MAX], ['1e400', T_MAX], ['1e999999', T_MAX]
  ]) {
    const res = validateSettingsDraft('', raw)
    assert.equal(res.timeErr, msg, JSON.stringify(raw).slice(0, 40))
    assert.equal(res.valid, false)
    assert.equal(res.normalizedTimeout, 900)
  }
  for (const [raw, num] of [['120', 120], [' 120\t', 120], ['1e3', 1000], ['+5', 5], ['.5', 0.5], ['86400', 86400]]) {
    const res = validateSettingsDraft('', raw)
    assert.equal(res.timeErr, '', raw)
    assert.equal(res.normalizedTimeout, num)
  }
})

test('connection atom is optional and guarded', t => {
  const prev = host.state
  t.after(() => { host.state = prev })
  for (const state of [undefined, {}, { connectionId: null }, { connectionId: {} },
    { connectionId: { get: 5 } }, { connectionId: { get: () => { throw new Error('x') } } },
    { connectionId: { get: () => 42 } }]) {
    host.state = state
    assert.equal(activeConnectionScope(), null)
  }
  host.state = { connectionId: { get: () => 'remote-1' } }
  assert.equal(activeConnectionScope(), 'remote-1')

  // Without the atom, a profile switch is still detected.
  let profile = 'default'
  host.state = { profile: { get: () => profile } }
  const rest = createScopedRest({ rest: async () => ({ ok: true }) })
  rest.check()
  profile = 'other'
  assert.throws(() => rest.check(), /Connection or profile changed/)
})
