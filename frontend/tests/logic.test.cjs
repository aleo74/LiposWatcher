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
const {FicheroPrinter, packRaster} = load('fichero.ts')

function fakePrinter({paperError = false, disconnectDuringRaster = false, endError = false} = {}) {
  const notifications = new EventTarget(), device = new EventTarget(), sent = []
  device.gatt = {
    connected: false,
    async connect() {this.connected = true; return {getPrimaryService: async () => ({getCharacteristic: async uuid => uuid.includes('2af1') ? writer : notifications})}},
    disconnect() {this.connected = false; device.dispatchEvent(new Event('gattserverdisconnected'))},
  }
  function emit(bytes) {notifications.value = new DataView(Uint8Array.from(bytes).buffer); notifications.dispatchEvent(new Event('characteristicvaluechanged'))}
  notifications.startNotifications = async () => {}
  const writer = {async writeValueWithoutResponse(bytes) {
    sent.push([...bytes])
    const key = [...bytes].join(',')
    if (key === '16,255,32,240') {queueMicrotask(() => {emit([68,49]); emit([49,115])})}
    else if (key === '16,255,64') queueMicrotask(() => emit([paperError ? 4 : 0]))
    else if (key === '16,255,254,69' && endError) queueMicrotask(() => emit([255, 2]))
    else if (key.startsWith('16,255,16,') || key === '16,255,132,0' || key === '16,255,254,69') queueMicrotask(() => {emit([79]); emit([75])})
    else if (bytes[0] === 29 && bytes[1] === 118 && disconnectDuringRaster) device.gatt.disconnect()
  }}
  return {sent, device, bluetooth: {requestDevice: async () => device}}
}

test('Fichero raster uses 96 x 240 dots, MSB-first, no transparent black', () => {
  const rgba = new Uint8ClampedArray(96 * 240 * 4).fill(255)
  rgba[0] = rgba[1] = rgba[2] = 0
  rgba[7 * 4] = rgba[7 * 4 + 1] = rgba[7 * 4 + 2] = 0
  rgba[8 * 4] = rgba[8 * 4 + 1] = rgba[8 * 4 + 2] = 0
  rgba[8 * 4 + 3] = 0
  const raster = packRaster(rgba, 96, 240)
  assert.equal(raster.length, 2880)
  assert.equal(raster[0], 129)
  assert.equal(raster[1], 0)
  assert.throws(() => packRaster(rgba, 112, 240))
})

test('Fichero sends the D11s sequence and waits for fragmented acknowledgements', async () => {
  const fake = fakePrinter()
  const printer = new FicheroPrinter(() => {}, fake.bluetooth)
  await printer.connect()
  await printer.print(new Uint8Array(2880))
  const raster = fake.sent.filter(b => b.length > 12).flat()
  assert.equal(raster.length, 2888)
  assert.deepEqual(raster.slice(0, 8), [29, 118, 48, 0, 12, 0, 240, 0])
  assert.ok(fake.sent.every(b => b.length <= 200))
  assert.deepEqual(fake.sent.at(-2), [29, 12])
  assert.deepEqual(fake.sent.at(-1), [16, 255, 254, 69])
  printer.disconnect()
})

test('Fichero rejects paper errors and disconnections without retrying the label', async () => {
  for (const options of [{paperError: true}, {disconnectDuringRaster: true}, {endError: true}]) {
    const fake = fakePrinter(options)
    const printer = new FicheroPrinter(() => {}, fake.bluetooth)
    await printer.connect()
    await assert.rejects(printer.print(new Uint8Array(2880)))
    assert.ok(fake.sent.filter(b => b[0] === 29 && b[1] === 118).length <= 1)
    printer.disconnect()
  }
})

test('versioned and legacy QR yield only a UUID', () => {
  assert.equal(parseQrIdentifier(`lipowatcher:v1:${id}`), id)
  assert.equal(parseQrIdentifier(`https://old.example/b/${id}`), id)
  assert.equal(parseQrIdentifier(`http://previous.local/b/${id}/?foo=bar`), id)
  assert.equal(parseQrIdentifier(`https://evil.example/b/${id}/edit`), null)
  assert.equal(parseQrIdentifier('javascript:alert(1)'), null)
  assert.equal(parseQrIdentifier('lipowatcher:v2:' + id), null)
  assert.equal(parseQrIdentifier('https://old.example/b/../../admin'), null)
})

test('compact version-L QR yields the same UUID and rejects malformed Base45', () => {
  assert.equal(parseQrIdentifier('L4724728E4DM8KWGKZAKZAKZA'), id)
  assert.equal(parseQrIdentifier('L' + '000'.repeat(8)), '00000000-0000-0000-0000-000000000000')
  assert.equal(parseQrIdentifier('L' + 'FGW'.repeat(8)), 'ffffffff-ffff-ffff-ffff-ffffffffffff')
  assert.equal(parseQrIdentifier('L' + ':::'.repeat(8)), null)
  assert.equal(parseQrIdentifier('L' + 'abc'.repeat(8)), null)
  assert.equal(parseQrIdentifier('L4724728E4DM8KWGKZAKZAKZ'), null)
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
