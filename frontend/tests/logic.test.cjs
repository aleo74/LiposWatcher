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
const {PrintQueue} = load('printQueue.ts')
const {sortFleet, modelKey, latestMeasured} = load('fleetUtils.ts')
const {thermalTextLayout} = load('thermalLabel.ts')

test('thermal labels preserve identifiers, readable sizes and bounded layout', () => {
  const measure = (text, font) => [...text].length * Number(font.match(/(\d+)px/)[1]) * .6
  for (const number of ['FW750-005', '001', 'abc-Été-01', 'A'.repeat(30)]) {
    const result = thermalTextLayout(number, 16, 100000, measure)
    assert.equal(result.lines.join(''), number)
    assert.ok(result.size >= 20)
    assert.ok(result.detailSize >= 16)
    assert.ok(result.top >= 0)
    assert.ok(result.top + result.lines.length * result.lineHeight + result.gap + result.detailSize + 2 <= 96)
    for (const line of result.lines) assert.ok(measure(line, `900 ${result.size}px Arial`) <= 142)
  }
  assert.throws(() => thermalTextLayout('W'.repeat(100), 2, 750, measure), /trop long/)
})

function fakePrinter({paperError = false, disconnectDuringRaster = false, endError = false, statuses = [0], lateStatusAck = false, disconnectOnBusy = false} = {}) {
  const notifications = new EventTarget(), device = new EventTarget(), sent = []
  let statusIndex = 0
  const counts = {requests: 0, connections: 0}
  device.gatt = {
    connected: false,
    async connect() {counts.connections++; this.connected = true; return {getPrimaryService: async () => ({getCharacteristic: async uuid => uuid.includes('2af1') ? writer : notifications})}},
    disconnect() {this.connected = false; device.dispatchEvent(new Event('gattserverdisconnected'))},
  }
  function emit(bytes) {notifications.value = new DataView(Uint8Array.from(bytes).buffer); notifications.dispatchEvent(new Event('characteristicvaluechanged'))}
  notifications.startNotifications = async () => {}
  const writer = {async writeValueWithoutResponse(bytes) {
    sent.push([...bytes])
    const key = [...bytes].join(',')
    if (key === '16,255,32,240') {queueMicrotask(() => {emit([68,49]); emit([49,115])})}
    else if (key === '16,255,64') queueMicrotask(() => {
      const status = paperError ? 4 : statuses[Math.min(statusIndex++, statuses.length - 1)]
      if (disconnectOnBusy && (status & 1)) {device.gatt.disconnect(); return}
      if (lateStatusAck) {emit([0xaa]); emit([79]); emit([75])}
      emit([status])
    })
    else if (key === '16,255,254,69' && endError) queueMicrotask(() => emit([255, 2]))
    else if (key.startsWith('16,255,16,') || key === '16,255,132,0' || key === '16,255,254,69') queueMicrotask(() => {emit([79]); emit([75])})
    else if (bytes[0] === 29 && bytes[1] === 118 && disconnectDuringRaster) device.gatt.disconnect()
  }}
  return {sent, device, counts, bluetooth: {requestDevice: async () => {counts.requests++; return device}}}
}

test('Fichero reuses an active connection and reconnects the known device without another chooser', async () => {
  const fake = fakePrinter()
  const printer = new FicheroPrinter(() => {}, fake.bluetooth)
  await printer.connect(); await printer.connect()
  assert.deepEqual(fake.counts, {requests: 1, connections: 1})
  fake.device.gatt.disconnect()
  assert.equal(printer.connected, false)
  await printer.connect()
  assert.equal(printer.connected, true)
  assert.deepEqual(fake.counts, {requests: 1, connections: 2})
  await printer.connect(true)
  assert.deepEqual(fake.counts, {requests: 2, connections: 3})
  printer.disconnect()
})

test('Fichero discards a chooser response arriving after logout/disposal', async () => {
  const fake = fakePrinter()
  let selected
  const printer = new FicheroPrinter(() => {}, {requestDevice: () => new Promise(resolve => {selected = resolve})})
  const connecting = printer.connect()
  printer.disconnect()
  selected(fake.device)
  await assert.rejects(connecting, /annulée/)
  assert.equal(fake.device.gatt.connected, false)
})

test('print queue requires review after an uncertain label and resumes only remaining copies', async () => {
  const queue = new PrintQueue('alice')
  queue.enqueue([{id, number: 'F750-001', copies: 3}], 'compact')
  let sent = 0
  const prepare = async () => new Uint8Array(2880)
  await assert.rejects(queue.run(prepare, async () => {if (++sent === 2) throw new Error('Link lost after send')}))
  assert.deepEqual(queue.snapshot().map(j => j.state), ['confirmed', 'uncertain', 'queued'])
  await assert.rejects(queue.run(prepare, async () => {sent++}), /Vérifiez/)
  assert.equal(sent, 2)
  queue.resolve(queue.snapshot()[1].id, 'confirmed')
  await queue.run(prepare, async () => {sent++})
  assert.equal(sent, 3)
  assert.deepEqual(queue.snapshot().map(j => j.state), ['confirmed', 'confirmed', 'confirmed'])
})

test('print queue handles permission/preparation failure before sending, isolates users, and recovers interrupted states', async () => {
  const values = new Map()
  const storage = {getItem: key => values.get(key) || null, setItem: (key, value) => values.set(key, value)}
  const queue = new PrintQueue('alice', storage)
  queue.enqueue([{id, number: '001', copies: 1}], 'link')
  await assert.rejects(queue.run(async () => {throw new Error('404')}, async () => assert.fail('must not print inaccessible battery')))
  assert.equal(queue.snapshot()[0].state, 'failed')
  assert.equal(new PrintQueue('bob', storage).snapshot().length, 0)
  values.set('lipowatcher:print-queue:v1:bob', values.get('lipowatcher:print-queue:v1:alice'))
  assert.equal(new PrintQueue('bob', storage).snapshot().length, 0)
  const saved = JSON.parse(values.get('lipowatcher:print-queue:v1:alice'))
  saved.jobs[0].state = 'printing'
  values.set('lipowatcher:print-queue:v1:alice', JSON.stringify(saved))
  assert.equal(new PrintQueue('alice', storage).snapshot()[0].state, 'uncertain')
  saved.jobs[0].state = 'preparing'
  values.set('lipowatcher:print-queue:v1:alice', JSON.stringify(saved))
  assert.equal(new PrintQueue('alice', storage).snapshot()[0].state, 'queued')
})

test('print queue prevents overlapping runs and pauses without duplicating a confirmed copy', async () => {
  const queue = new PrintQueue('alice')
  queue.enqueue([{id, number: '001', copies: 2}], 'compact')
  let finish
  const running = queue.run(async () => new Uint8Array(2880), () => new Promise(resolve => {finish = resolve}))
  await new Promise(resolve => setImmediate(resolve))
  await assert.rejects(queue.run(async () => new Uint8Array(2880), async () => {}), /déjà/)
  queue.pause(); finish(); await running
  assert.deepEqual(queue.snapshot().map(j => j.state), ['confirmed', 'queued'])
  assert.throws(() => queue.enqueue([{id, number: '001', copies: 21}], 'link'))
})

test('fleet sorting leaves missing dates last, groups snapshots, and distinguishes measured data', () => {
  const common = {brand: 'Flywoo', model: '750', chemistry: 'LiPo', cells: 2, nominal_mah: 750}
  const list = [{...common, number: 'F750-10'}, {...common, number: 'F750-2', last_used_at: '2026-10-18'}, {...common, number: 'F750-1', last_used_at: '2026-10-20'}]
  assert.deepEqual(sortFleet(list, 'number').map(b => b.number), ['F750-1', 'F750-2', 'F750-10'])
  assert.deepEqual(sortFleet(list, 'last_used_at').map(b => b.number), ['F750-1', 'F750-2', 'F750-10'])
  assert.equal(modelKey(common), modelKey({...common, brand: ' FLYWOO '}))
  assert.notEqual(modelKey(common), modelKey({...common, nominal_mah: 850}))
  const entries = [{kind: 'baseline', occurred_at: '2026-10-20', resistance_mohm: [null, null]}, {kind: 'test', occurred_at: '2026-10-19', discharged_mah: 700, resistance_mohm: [null, 7]}]
  assert.equal(latestMeasured(entries, 'capacity'), entries[1])
  assert.equal(latestMeasured(entries, 'resistance'), entries[1])
})

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

test('Fichero batch waits for readiness after the first label and ignores late ACKs', async () => {
  const fake = fakePrinter({statuses: [0, 1, 1, 0], lateStatusAck: true})
  const printer = new FicheroPrinter(() => {}, fake.bluetooth)
  await printer.connect()
  await printer.print(new Uint8Array(2880))
  const afterFirst = fake.sent.length
  await printer.print(new Uint8Array(2880))
  assert.deepEqual(fake.sent.slice(afterFirst, afterFirst + 3), Array.from({length: 3}, () => [16, 255, 64]))
  assert.equal(fake.sent.filter(b => b[0] === 29 && b[1] === 118).length, 2)
  assert.equal(fake.sent.filter(b => b.join(',') === '29,12').length, 2)
  assert.equal(fake.sent.filter(b => b.join(',') === '16,255,254,69').length, 2)
  printer.disconnect()
})

test('Fichero distinguishes busy from actual faults, including faults while busy', async () => {
  for (const [status, message] of [[3, /capot ouvert/], [5, /papier absent/], [9, /batterie faible/], [17, /surchauffe/], [65, /surchauffe/]]) {
    const fake = fakePrinter({statuses: [status]})
    const printer = new FicheroPrinter(() => {}, fake.bluetooth)
    await printer.connect()
    await assert.rejects(printer.print(new Uint8Array(2880)), message)
    assert.equal(fake.sent.filter(b => b[0] === 29 && b[1] === 118).length, 0)
    printer.disconnect()
  }
})

test('Fichero stops on a permanent busy state or a between-label disconnection without duplicates', async () => {
  for (const disconnectOnBusy of [false, true]) {
    const fake = fakePrinter({statuses: [0, 1], disconnectOnBusy})
    const printer = new FicheroPrinter(() => {}, fake.bluetooth, 20)
    await printer.connect()
    await printer.print(new Uint8Array(2880))
    await assert.rejects(printer.print(new Uint8Array(2880)), disconnectOnBusy ? /déconnecté/ : /reste occupée/)
    assert.equal(fake.sent.filter(b => b[0] === 29 && b[1] === 118).length, 1)
    assert.equal(fake.sent.filter(b => b.join(',') === '29,12').length, 1)
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
