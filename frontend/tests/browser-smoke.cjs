// Real headless browser + app UI; API and BLE fixtures only. Never accesses production.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const {spawn} = require('node:child_process')
const {chromium} = require('playwright')
const {setTimeout: sleep} = require('node:timers/promises')
const root = path.resolve(__dirname, '..')
const base = 'http://127.0.0.1:5183'
const ids = ['11111111-2222-4333-8444-555555555555', '22222222-2222-4333-8444-555555555555']
const owner = {id: 'fixture-owner', username: 'admin', email: null, email_verified_at: null, is_admin: 1}
const bob = {id: 'fixture-bob', username: 'bob', email: 'bob@example.com', email_verified_at: '2026-10-01', is_admin: 0}
const entry = {id: 'fixture-entry', kind: 'test', occurred_at: '2026-10-19T10:00:00Z', discharged_mah: 600, added_mah: null, before_v: [3.8, null], after_v: null, resistance_mohm: [5, null], ambient_c: 22, current_a: .5, cutoff_v: 6.5, charger: 'Chargeur test', charger_id: 'fixture-charger', charger_channel: 1, initial_percent: null, initial_percent_source: '', notes: '', metrics: {measured_ratio: 80, resistance_mean_mohm: 5}}
const batteries = ids.map((id, i) => ({id, number: 'F750-00' + (i + 1), brand: 'Flywoo', model: '750', range: '', connector: '', chemistry: 'LiPo', cells: 2, nominal_mah: 750, condition: 'used', prior_history: 'unknown', prior_cycles: null, status: 'active', lot_id: 'fixture-lot', model_id: null, guide_steps: {}, entries: i === 0 ? [entry] : [], charge_count: 1, total_added_mah: 500, last_used_at: i ? null : '2026-10-19T10:00:00Z', last_measured_at: i ? null : entry.occurred_at}))
const resultDir = path.join(root, 'test-results')
fs.mkdirSync(resultDir, {recursive: true})

async function main() {
  const server = spawn(process.execPath, [path.join(path.dirname(require.resolve('vite/package.json')), 'bin/vite.js'), '--host', '127.0.0.1', '--port', '5183', '--strictPort'], {cwd: root, stdio: 'pipe', windowsHide: true})
  let output = ''
  server.stdout.on('data', data => {output = (output + data).slice(-5000)})
  server.stderr.on('data', data => {output = (output + data).slice(-5000)})
  let browser, page
  try {
    let ready = false
    for (let i = 0; i < 100; i++) {if (server.exitCode !== null) throw new Error(output); try {if ((await fetch(base)).ok) {ready = true; break}} catch {} await sleep(100)}
    assert.ok(ready, 'Vite did not start')
    browser = await chromium.launch({headless: true, ...(process.env.BROWSER_CHANNEL || process.platform === 'win32' ? {channel: process.env.BROWSER_CHANNEL || 'msedge'} : {})})
    const context = await browser.newContext({viewport: {width: 390, height: 844}, isMobile: true, hasTouch: true})
    await context.addInitScript(() => {
      const metrics = window.__ble = {choosers: 0, connects: 0, rasters: 0, failAt: 0, connected: false}
      const notify = new EventTarget(), device = new EventTarget()
      const emit = bytes => {notify.value = new DataView(Uint8Array.from(bytes).buffer); notify.dispatchEvent(new Event('characteristicvaluechanged'))}
      notify.startNotifications = async () => {}
      const write = {async writeValueWithoutResponse(bytes) {
        const key = [...bytes].join(',')
        if (key === '16,255,32,240') queueMicrotask(() => emit([68, 49, 49, 115]))
        else if (key === '16,255,64') queueMicrotask(() => emit([0]))
        else if (key.startsWith('16,255,16,') || key === '16,255,132,0' || key === '16,255,254,69') queueMicrotask(() => emit([79, 75]))
        else if (bytes[0] === 29 && bytes[1] === 118) {metrics.rasters++; if (metrics.rasters === metrics.failAt) throw new Error('Liaison interrompue — simulation')}
      }}
      device.name = 'FICHERO_5836'
      device.gatt = {connected: false, async connect() {this.connected = true; metrics.connected = true; metrics.connects++; return {getPrimaryService: async () => ({getCharacteristic: async id => id.includes('2af1') ? write : notify})}}, disconnect() {this.connected = false; metrics.connected = false; device.dispatchEvent(new Event('gattserverdisconnected'))}}
      Object.defineProperty(navigator, 'bluetooth', {configurable: true, value: {requestDevice: async () => {metrics.choosers++; return device}}})
      if (navigator.mediaDevices) navigator.mediaDevices.getUserMedia = async () => {throw new Error('Camera unavailable in fixture')}
    })
    let currentUser = owner, chargePosts = 0, passwordPosts = 0, expireCharge = false, holdFleet = false, releaseFleet
    await context.route('**/api/**', async route => {
      const request = route.request(), url = new URL(request.url()), p = url.pathname
      const json = (body, status = 200) => route.fulfill({status, json: body})
      if (p === '/api/auth/me') return json(currentUser || {detail: 'Session expirée'}, currentUser ? 200 : 401)
      if (p === '/api/auth/setup') return json({needed: false})
      if (p === '/api/auth/options') return json({registration: false, mail: false})
      if (p === '/api/auth/csrf') return json({token: 'fixture-csrf-token'})
      if (p === '/api/auth/logout') {currentUser = null; return route.fulfill({status: 204})}
      if (p === '/api/auth/login') {currentUser = request.postDataJSON().username === 'bob' ? bob : owner; return json(currentUser)}
      if (p === '/api/auth/change-password') {passwordPosts++; return json({message: 'Mot de passe modifié. Autres sessions invalidées.'})}
      if (p === '/api/batteries') {
        const requestOwner = currentUser
        if (holdFleet && requestOwner?.id === owner.id) {holdFleet = false; await new Promise(resolve => {releaseFleet = resolve})}
        return json(requestOwner?.id === owner.id ? batteries.filter(b => !url.searchParams.get('q') || b.number.includes(url.searchParams.get('q'))) : [])
      }
      if (p === '/api/lots') return json(currentUser?.id === owner.id ? [{id: 'fixture-lot', name: 'Flywoo — stock'}] : [])
      if (p === '/api/chargers') return json([{id: 'fixture-charger', name: 'Chargeur test', status: 'active', channels: 2}])
      if (p === '/api/batteries/compare') return json({batteries: batteries.filter(b => request.postDataJSON().battery_ids.includes(b.id))})
      const battery = batteries.find(b => p.startsWith('/api/batteries/' + b.id))
      if (battery && currentUser?.id === owner.id) {
        if (p.endsWith('/qr.png')) return route.fulfill({contentType: 'image/png', body: fs.readFileSync(path.join(__dirname, url.searchParams.get('content') === 'compact' ? 'fichero-qr.png' : 'fichero-web-qr.png'))})
        if (p.endsWith('/entries')) {if (expireCharge) {expireCharge = false; return json({detail: 'Session expirée'}, 401)} chargePosts++; return json({id: 'fixture-charge', ...request.postDataJSON()}, 201)}
        return json(battery)
      }
      return json({detail: 'Introuvable'}, 404)
    })
    page = await context.newPage()
    const errors = []
    page.on('pageerror', error => {errors.push(error.message); console.error('PAGE ERROR:', error.message)})
    const screenshot = name => page.screenshot({path: path.join(resultDir, name + '.png'), fullPage: true})
    const noOverflow = async () => assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), 'Mobile layout overflows horizontally')
    const nav = name => page.getByRole('navigation', {name: 'Atelier'}).getByRole('button', {name, exact: true}).click()
    const queueState = async (confirmed, uncertain = 0) => page.waitForFunction(({confirmed, uncertain}) => {
      const rows = [...document.querySelectorAll('.queue-row')]
      return rows.filter(r => [...r.querySelectorAll('span')].some(s => s.textContent === 'Confirmée')).length === confirmed && rows.filter(r => [...r.querySelectorAll('span')].some(s => s.textContent === 'À vérifier sur le papier')).length === uncertain
    }, {confirmed, uncertain})
    await page.goto(base)
    const thermal = await page.evaluate(async () => {
      const {drawThermalText, thermalTextLayout} = await import('/src/thermalLabel.ts')
      const source = await createImageBitmap(await (await fetch('/tests/fichero-qr.png')).blob())
      const canvas = document.createElement('canvas'); canvas.width = 240; canvas.height = 96
      const ctx = canvas.getContext('2d')
      ctx.fillStyle = '#fff'; ctx.fillRect(0, 0, 240, 96); ctx.drawImage(source, 0, 4)
      const before = ctx.getImageData(0, 0, 96, 96).data
      drawThermalText(ctx, 'FW750-005', 2, 750)
      const after = ctx.getImageData(0, 0, 96, 96).data
      const text = ctx.getImageData(98, 0, 142, 96).data
      const results = {qrUnchanged: before.every((v, i) => v === after[i]), monochrome: true, blackDots: 0, png: canvas.toDataURL(), layouts: [], overflowRejected: false}
      for (let i = 0; i < text.length; i += 4) {
        if (![0, 255].includes(text[i]) || text[i] !== text[i + 1] || text[i] !== text[i + 2] || text[i + 3] !== 255) results.monochrome = false
        if (!text[i]) results.blackDots++
      }
      for (const number of ['FW750-005', '001', 'abc-Été-01', 'A'.repeat(24)]) {
        const layout = thermalTextLayout(number, 16, 100000, (value, face) => {ctx.font = face; return ctx.measureText(value).width})
        results.layouts.push({preserved: layout.lines.join('') === number, fits: layout.top >= 0 && layout.top + layout.lines.length * layout.lineHeight + layout.gap + layout.detailSize + 2 <= 96})
        drawThermalText(ctx, number, 16, 100000)
      }
      try {drawThermalText(ctx, 'W'.repeat(100), 2, 750)} catch (error) {results.overflowRejected = /trop long/.test(error.message)}
      source.close()
      return results
    })
    assert.equal(thermal.qrUnchanged, true)
    assert.equal(thermal.monochrome, true)
    assert.ok(thermal.blackDots > 700)
    assert.ok(thermal.layouts.every(l => l.preserved && l.fits))
    assert.equal(thermal.overflowRejected, true)
    fs.writeFileSync(path.join(resultDir, 'fichero-readable.png'), Buffer.from(thermal.png.split(',')[1], 'base64'))
    console.log('PASS: thermal text is monochrome, readable sizes, long identifiers fit, QR pixels unchanged')
    await page.getByRole('heading', {name: 'Mes batteries', exact: true}).waitFor()
    await noOverflow(); await screenshot('fleet-mobile')
    // Print from two separate battery cards; the provider stays mounted.
    for (let i = 0; i < 2; i++) {
      await page.getByRole('button', {name: new RegExp('#' + batteries[i].number)}).click()
      await page.getByRole('button', {name: 'Étiquette', exact: true}).click()
      await page.getByLabel('Imprimante', {exact: true}).selectOption('fichero')
      await page.getByLabel('Contenu du QR').selectOption('compact')
      if (i === 0) await page.getByRole('button', {name: 'Connecter / reconnecter la Fichero', exact: true}).click()
      await page.getByRole('button', {name: 'Imprimer 1 étiquette(s) sur Fichero', exact: true}).click()
      await queueState(i + 1)
      assert.equal(await page.evaluate(() => __ble.choosers), 1)
      assert.equal(await page.evaluate(() => __ble.connects), 1)
      if (i === 0) await nav('Batteries')
    }
    console.log('PASS: two battery cards, one chooser and one persistent BLE connection')
    await page.locator('.printer-status').getByRole('button', {name: /Étiquettes/}).click()
    await page.getByRole('button', {name: 'Tout désélectionner', exact: true}).click()
    const selectedRow = page.locator('.label-selection-row').filter({hasText: batteries[0].number})
    await selectedRow.getByRole('checkbox').check()
    await selectedRow.getByLabel('Copies').fill('3')
    await page.evaluate(() => {__ble.failAt = 4})
    await page.getByRole('button', {name: 'Imprimer 3 étiquette(s) sur Fichero', exact: true}).click()
    await queueState(3, 1)
    assert.equal(await page.getByRole('button', {name: 'Reprendre les étiquettes restantes', exact: true}).isDisabled(), true)
    await page.getByRole('button', {name: 'Elle est bien imprimée', exact: true}).click()
    await page.getByRole('button', {name: 'Reprendre les étiquettes restantes', exact: true}).click()
    await queueState(5)
    assert.equal(await page.evaluate(() => __ble.rasters), 5)
    await noOverflow(); await screenshot('labels-mobile')
    await page.getByLabel('Imprimante', {exact: true}).selectOption('standard')
    assert.equal(await page.evaluate(() => __ble.connected), true)
    await page.emulateMedia({media: 'print'})
    assert.equal(await page.locator('.labels-sheet .label').count(), 3)
    assert.ok(Math.abs((await page.locator('.labels-sheet .label img').first().boundingBox()).width - 28 * 96 / 25.4) < 1)
    assert.equal(await page.getByRole('navigation', {name: 'Atelier'}).isVisible(), false)
    await page.emulateMedia({media: 'screen'})
    console.log('PASS: selection, copy counts, interruption, explicit review, resume without duplicates')
    holdFleet = true
    await nav('Batteries')
    for (let i = 0; !releaseFleet && i < 50; i++) await sleep(50)
    assert.ok(releaseFleet, 'Expected pending first-owner request')
    // Logout disposes the printer; the second account never sees the first queue.
    await page.getByRole('button', {name: 'Menu', exact: true}).click()
    await page.getByRole('button', {name: 'Déconnexion', exact: true}).click()
    await page.getByRole('button', {name: 'Se connecter', exact: true}).waitFor()
    assert.equal(await page.evaluate(() => __ble.connected), false)
    async function login(name) {await page.getByLabel('E-mail ou identifiant', {exact: true}).fill(name); await page.getByLabel('Mot de passe', {exact: true}).fill('fixture_password_123'); await page.getByRole('button', {name: 'Se connecter', exact: true}).click(); await page.getByRole('heading', {name: 'Mes batteries', exact: true}).waitFor()}
    await login('bob')
    const delayed = page.waitForResponse(r => new URL(r.url()).pathname === '/api/batteries' && r.request().method() === 'GET')
    releaseFleet()
    await delayed
    await sleep(100)
    assert.ok(!(await page.locator('body').innerText()).includes('F750-001'), 'A stale first-owner response leaked into the second account')
    await page.getByRole('button', {name: 'Menu', exact: true}).click(); await page.getByRole('button', {name: /Planche d’étiquettes/}).click()
    await page.getByRole('heading', {name: 'Étiquettes QR', exact: true}).waitFor()
    assert.equal(await page.locator('.queue-row').count(), 0)
    assert.ok(!(await page.locator('body').innerText()).includes('F750-001'))
    await page.getByRole('button', {name: 'Menu', exact: true}).click(); await page.getByRole('button', {name: 'Déconnexion', exact: true}).click(); await login('admin')
    console.log('PASS: logout disconnects BLE; print queues are isolated between accounts')
    await nav('Mode atelier')
    await page.getByLabel('Ou chercher par numéro').fill('F750-001')
    await page.getByRole('button', {name: '#F750-001', exact: true}).click()
    await page.getByLabel('mAh ajoutés', {exact: true}).fill('450')
    expireCharge = true
    await page.getByRole('button', {name: 'Enregistrer et scanner la suivante', exact: true}).click()
    await page.getByRole('dialog', {name: 'Session expirée', exact: true}).waitFor()
    await page.getByRole('dialog', {name: 'Session expirée', exact: true}).getByLabel('Mot de passe', {exact: true}).fill('fixture_password_123')
    await page.getByRole('button', {name: 'Reprendre', exact: true}).click()
    await page.getByRole('dialog', {name: 'Session expirée', exact: true}).waitFor({state: 'hidden'})
    assert.equal(await page.getByLabel('mAh ajoutés', {exact: true}).inputValue(), '450')
    assert.equal(await page.getByText('Session expirée. Votre saisie est conservée pour ce compte et cette batterie.', {exact: true}).count(), 0)
    await noOverflow(); await screenshot('workshop-mobile')
    await page.getByRole('button', {name: 'Enregistrer et scanner la suivante', exact: true}).click()
    await page.getByRole('dialog', {name: 'Scanner une batterie', exact: true}).waitFor()
    assert.equal(chargePosts, 1)
    await page.getByRole('button', {name: 'Fermer', exact: true}).click()
    console.log('PASS: workshop draft retained across expiration/reconnection, one successful save then next scanner')
    await nav('Comparer')
    const group = await page.getByLabel('Modèle et caractéristiques').locator('option').nth(1).getAttribute('value')
    await page.getByLabel('Modèle et caractéristiques').selectOption(group)
    await page.getByRole('button', {name: 'Sélectionner le groupe (20 maximum)', exact: true}).click()
    await page.getByRole('button', {name: 'Comparer 2 batteries', exact: true}).click()
    await page.getByRole('table').waitFor()
    assert.ok((await page.getByRole('table').innerText()).includes('600 mAh'))
    assert.ok((await page.getByRole('table').innerText()).includes('22 °C'))
    assert.ok((await page.getByRole('table').innerText()).includes('C2 : —'))
    await noOverflow(); await screenshot('comparison-mobile')
    await nav('Mon compte')
    const passwordForm = page.locator('section').filter({has: page.getByRole('heading', {name: 'Changer mon mot de passe', exact: true})})
    await passwordForm.getByLabel('Mot de passe actuel', {exact: true}).fill('fixture_password_123')
    await passwordForm.getByLabel('Nouveau mot de passe', {exact: true}).fill('fixture_replacement_456')
    await passwordForm.getByLabel('Confirmer le nouveau mot de passe', {exact: true}).fill('fixture_mismatch_789')
    await passwordForm.getByRole('button', {name: 'Modifier mon mot de passe', exact: true}).click()
    await page.getByText('Les deux nouveaux mots de passe ne correspondent pas.', {exact: true}).waitFor()
    assert.equal(passwordPosts, 0)
    await passwordForm.getByLabel('Confirmer le nouveau mot de passe', {exact: true}).fill('fixture_replacement_456')
    await passwordForm.getByRole('button', {name: 'Modifier mon mot de passe', exact: true}).click()
    await page.getByText('Mot de passe modifié. Autres sessions invalidées.', {exact: true}).waitFor()
    assert.equal(passwordPosts, 1)
    assert.equal(await passwordForm.getByLabel('Nouveau mot de passe', {exact: true}).inputValue(), '')
    await noOverflow(); await screenshot('account-mobile')
    assert.deepEqual(errors, [])
    console.log('PASS: comparison with missing cells/conditions; password form validation and recovery reminder; 390px layouts')
  } catch (error) {
    if (page) {console.error('UI at failure:', (await page.locator('body').innerText()).slice(0, 5000)); await page.screenshot({path: path.join(resultDir, 'failure-mobile.png'), fullPage: true})}
    throw error
  } finally {await browser?.close(); server.kill('SIGTERM')}
}
main().catch(error => {console.error(error); process.exitCode = 1})
