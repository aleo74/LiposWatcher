const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const test = require('node:test')
const ts = require('typescript')

function load(name) {
  const source = fs.readFileSync(path.join(__dirname, '..', 'src', name), 'utf8')
  const code = ts.transpileModule(source, {compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022}}).outputText
  const exports = {}
  new Function('exports', code)(exports)
  return exports
}

const {parseQrIdentifier} = load('qr.ts')
const {draftKey, loadDraft, saveDraft, clearDraft} = load('drafts.ts')
const {ampsFromMahAndC} = load('chargeMath.ts')
const {filterMeasurements,lastDeviceChoice} = load('sessionDevices.ts')
const id = '11111111-2222-4333-8444-555555555555'

test('versioned and legacy QR yield only a UUID', () => {
  assert.equal(parseQrIdentifier(`lipowatcher:v1:${id}`), id)
  assert.equal(parseQrIdentifier(`https://old.example/b/${id}`), id)
  assert.equal(parseQrIdentifier(`http://previous.local/b/${id}/?foo=bar`), id)
  assert.equal(parseQrIdentifier(`https://evil.example/b/${id}/edit`), null)
  assert.equal(parseQrIdentifier('javascript:alert(1)'), null)
  assert.equal(parseQrIdentifier('lipowatcher:v2:' + id), null)
  assert.equal(parseQrIdentifier('https://old.example/b/../../admin'), null)
})

test('drafts are isolated by user, battery and entry', () => {
  const values = new Map()
  const storage = {getItem:key => values.get(key) ?? null, setItem:(key,value) => values.set(key,value), removeItem:key => values.delete(key)}
  saveDraft(storage, 'alice', 'battery-a', 'new', {added_mah:'780'})
  assert.deepEqual(loadDraft(storage, 'alice', 'battery-a', 'new'), {added_mah:'780'})
  assert.equal(loadDraft(storage, 'bob', 'battery-a', 'new'), null)
  assert.equal(loadDraft(storage, 'alice', 'battery-b', 'new'), null)
  assert.equal(loadDraft(storage, 'alice', 'battery-a', 'other-entry'), null)
  values.set(draftKey('bob', 'battery-a', 'new'), values.get(draftKey('alice', 'battery-a', 'new')))
  assert.equal(loadDraft(storage, 'bob', 'battery-a', 'new'), null)
  clearDraft(storage, 'alice', 'battery-a', 'new')
  assert.equal(loadDraft(storage, 'alice', 'battery-a', 'new'), null)
})

test('ampere calculation uses entered values and rejects missing or non-finite inputs', () => {
  assert.equal(ampsFromMahAndC(1300, 1), 1.3)
  assert.ok(Math.abs(ampsFromMahAndC(1300, 1.5) - 1.95) < 1e-10)
  assert.equal(ampsFromMahAndC(1300, null), null)
  assert.equal(ampsFromMahAndC(1300, 0), null)
  assert.equal(ampsFromMahAndC(1300, Number.POSITIVE_INFINITY), null)
  assert.equal(ampsFromMahAndC(Number.NaN, 1), null)
})

test('measurements distinguish physical devices, channels, and historical text', () => {
  const entries=[{id:'1',charger_id:'a',charger:'Same model',charger_channel:2},{id:'2',charger_id:'b',charger:'Same model',charger_channel:1},{id:'3',charger_id:null,charger:'Same model',charger_channel:null}]
  assert.deepEqual(filterMeasurements(entries,'id:a','2').map(e=>e.id),['1'])
  assert.deepEqual(filterMeasurements(entries,'id:b','').map(e=>e.id),['2'])
  assert.deepEqual(filterMeasurements(entries,'text:Same model','none').map(e=>e.id),['3'])
  assert.deepEqual(filterMeasurements(entries,'','1').map(e=>e.id),['2'])
  assert.equal(filterMeasurements(entries,'id:a','1').length,0)
  const devices=[{id:'a',status:'archived',channels:2},{id:'b',status:'active',channels:2}]
  assert.equal(lastDeviceChoice(entries,devices).charger_id,'b')
  assert.equal(lastDeviceChoice(entries,devices).charger_channel,1)
  assert.equal(lastDeviceChoice(entries,[{id:'a',status:'active',channels:1}]).charger_channel,null)
  assert.equal(lastDeviceChoice(entries,[]).id,'3')
  assert.equal(lastDeviceChoice(entries.slice(0,2),[]),null)
  assert.equal(lastDeviceChoice([entries[2],entries[0]],devices).charger_id,null)
})
