// Run from plugin root: node --import ./desktop/tests/sdk-shim.mjs --test desktop/tests/settingsPage.test.mjs
import { registerHooks } from 'node:module'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'

const STUBS = {
  'react':
    'export function useState(init) {\n' +
    '  if (globalThis.__czTestUseState) return globalThis.__czTestUseState(init)\n' +
    '  const val = typeof init === "function" ? init() : init\n' +
    '  const setter = (next) => { if (typeof globalThis.__czTestStateHook === "function") globalThis.__czTestStateHook(next) }\n' +
    '  return [val, setter]\n' +
    '}\n' +
    'export function useEffect(fn, deps) { globalThis.__czTestUseEffect?.(fn, deps) }\n' +
    'export function useRef(init) { return useState(() => ({ current: init }))[0] }\n',
  'react/jsx-runtime':
    'export function jsx(type, props, key) { return { type, props, key } }\nexport const jsxs = jsx\n',
  '@hermes/plugin-sdk': [
    'export const host = {',
    '  state: { profile: { get: () => "default" }, connectionId: { get: () => "local" } },',
    '  activeConnectionId: () => "local",',
    '  request: async () => ({}),',
    '  getGateway: () => null',
    '}',
    'export const Button = "Button"',
    'export const Input = "Input"',
    'export const Badge = "Badge"',
    'export const Separator = "Separator"',
    'export const Codicon = "Codicon"',
    'export async function writeClipboardText() { return true }'
  ].join('\n')
}

registerHooks({
  resolve(specifier, context, nextResolve) {
    if (Object.prototype.hasOwnProperty.call(STUBS, specifier)) {
      return {
        url: 'data:text/javascript;charset=utf-8,' + encodeURIComponent(STUBS[specifier]),
        shortCircuit: true
      }
    }
    return nextResolve(specifier, context)
  },
  load(url, context, nextLoad) {
    if (url.endsWith('/desktop/plugin.js')) {
      return {
        format: 'module',
        source: readFileSync(fileURLToPath(url), 'utf8'),
        shortCircuit: true
      }
    }
    return nextLoad(url, context)
  }
})
