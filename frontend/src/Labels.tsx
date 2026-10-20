import {useEffect, useState, useSyncExternalStore} from 'react'
import {api, type Battery, type Lot} from './api'
import {FicheroPrinter, packRaster} from './fichero'
import {usePrinter} from './PrinterContext'
import type {LabelJob} from './printQueue'
import {drawThermalText} from './thermalLabel'

type QrContent = 'link' | 'compact'
async function compactLabel(battery: Battery, content: QrContent) {
  const response = await fetch(`/api/batteries/${battery.id}/qr.png?profile=fichero&content=${content}`, {cache: 'no-store'})
  if (!response.ok) {
    if (response.status === 401) throw new Error('Session expirée : reconnectez-vous avant d’imprimer')
    const error = await response.json().catch(() => null)
    throw new Error(typeof error?.detail === 'string' ? error.detail : 'Étiquette inaccessible : vérifiez les droits et la connexion')
  }
  const bitmap = await createImageBitmap(await response.blob())
  try {
    if (bitmap.width > 96 || bitmap.height > 96) throw new Error('QR trop grand pour la Fichero')
    const canvas = document.createElement('canvas')
    canvas.width = 240; canvas.height = 96
    const ctx = canvas.getContext('2d')!
    ctx.fillStyle = '#fff'; ctx.fillRect(0, 0, 240, 96)
    ctx.imageSmoothingEnabled = false
    ctx.drawImage(bitmap, 0, Math.floor((96 - bitmap.height) / 2))
    drawThermalText(ctx, battery.number, battery.cells, battery.nominal_mah)
    return canvas
  } finally {bitmap.close()}
}

function Preview({battery, content}: {battery: Battery; content: QrContent}) {
  const [url, setUrl] = useState(''), [error, setError] = useState('')
  useEffect(() => {
    let cancelled = false
    setUrl(''); setError('')
    compactLabel(battery, content).then(canvas => {if (!cancelled) setUrl(canvas.toDataURL())}).catch(e => {if (!cancelled) setError(e.message)})
    return () => {cancelled = true}
  }, [battery, content])
  return <div className="fichero-preview">{url ? <img src={url} alt={'Étiquette Fichero ' + battery.number}/> : <small>{error || 'Préparation…'}</small>}</div>
}
const jobText: Record<LabelJob['state'], string> = {queued: 'À imprimer', preparing: 'Préparation', printing: 'En cours', confirmed: 'Confirmée', uncertain: 'À vérifier sur le papier', failed: 'Non envoyée — erreur', skipped: 'Ignorée'}
export function Labels({list, go}: {list: Battery[]; go: (path: string) => void}) {
  const one = new URLSearchParams(location.search).get('one')
  const [lot, setLot] = useState(new URLSearchParams(location.search).get('lot') || '')
  const [batteries, setBatteries] = useState(list), [lots, setLots] = useState<Lot[]>([]), [ready, setReady] = useState(false)
  const [checked, setChecked] = useState<Record<string, boolean>>({}), [copies, setCopies] = useState<Record<string, string>>({})
  const [mode, setMode] = useState(() => {try {return localStorage.getItem('lipowatcher:printer') === 'fichero' ? 'fichero' : 'standard'} catch {return 'standard'}})
  const [content, setContent] = useState<QrContent>(() => {try {return localStorage.getItem('lipowatcher:fichero-qr') === 'compact' ? 'compact' : 'link'} catch {return 'link'}})
  const [message, setMessage] = useState(''), [error, setError] = useState('')
  const {printer, connected, connecting, connect, queue} = usePrinter()
  const jobs = useSyncExternalStore(queue.subscribe, queue.snapshot)
  const busy = queue.running || connecting
  useEffect(() => {
    let active = true
    setReady(false)
    const load = one ? api<Battery>('/batteries/' + one).then(b => {if (active) setBatteries([b])}) :
      Promise.all([api<Battery[]>('/batteries'), api<Lot[]>('/lots')]).then(([b, l]) => {if (active) {setBatteries(b); setLots(l)}})
    load.then(() => {if (active) setReady(true)}).catch(() => {if (active) setError('Fiches inaccessibles : vérifiez votre connexion')})
    return () => {active = false}
  }, [one])
  const visible = ready ? batteries.filter(b => !lot || b.lot_id === lot) : []
  const selected = visible.filter(b => checked[b.id] !== false)
  const validCopies = selected.every(b => {const n = Number(copies[b.id] || '1'); return Number.isInteger(n) && n >= 1 && n <= 20})
  const copiesFor = (b: Battery) => Math.min(20, Math.max(1, Math.floor(Number(copies[b.id]) || 1)))
  const count = selected.reduce((sum, b) => sum + copiesFor(b), 0)
  async function resume() {
    setError(''); setMessage('')
    try {
      await queue.run(async job => {
        // Re-read current owner and number before every copy, including resumed jobs.
        const battery = await api<Battery>('/batteries/' + job.batteryId)
        job.number = battery.number
        const landscape = await compactLabel(battery, job.content)
        const canvas = document.createElement('canvas'); canvas.width = 96; canvas.height = 240
        const ctx = canvas.getContext('2d')!
        ctx.imageSmoothingEnabled = false; ctx.translate(96, 0); ctx.rotate(Math.PI / 2); ctx.drawImage(landscape, 0, 0)
        return packRaster(ctx.getImageData(0, 0, 96, 240).data, 96, 240)
      }, raster => printer.print(raster))
      setMessage('Série terminée ou mise en pause. Vérifiez le papier et la lecture des QR.')
    } catch (e) {setError((e as Error).message)}
  }
  async function print() {
    if (mode === 'standard') {window.print(); return}
    setError('')
    try {queue.enqueue(selected.map(b => ({id: b.id, number: b.number, copies: copiesFor(b)})), content); await resume()}
    catch (e) {setError((e as Error).message)}
  }
  const unresolved = jobs.some(j => j.state === 'uncertain' || j.state === 'failed')
  return <>
    <button className="back no-print" onClick={() => go(one ? '/b/' + one : '/')}>← Retour</button>
    <div className="page-heading no-print"><div><div className="eyebrow">IDENTIFICATION</div><h1>Étiquettes QR</h1><p>{mode === 'fichero' ? 'Papier 30 × 14 mm. La connexion BLE reste active en naviguant entre les fiches.' : 'Impression standard A4. Scannez les codes dans LipoWatcher après connexion.'}</p></div></div>
    <section className="panel no-print printer-controls">
      <label>Imprimante<select aria-label="Imprimante" value={mode} disabled={busy} onChange={e => {setMode(e.target.value); setError(''); try {localStorage.setItem('lipowatcher:printer', e.target.value)} catch { /* optional */ }}}><option value="standard">Standard — impression A4</option><option value="fichero">Fichero 5836 / D11s — Bluetooth, 30 × 14 mm</option></select></label>
      {!one && <label>Lot à imprimer<select value={lot} disabled={busy || !ready} onChange={e => setLot(e.target.value)}><option value="">Toutes mes batteries</option>{lots.map(l => <option key={l.id} value={l.id}>{l.name}</option>)}</select></label>}
      {mode === 'fichero' && <>
        <label>Contenu du QR<select value={content} disabled={busy} onChange={e => {const value = e.target.value as QrContent; setContent(value); try {localStorage.setItem('lipowatcher:fichero-qr', value)} catch { /* optional */ }}}><option value="link">Lien web — appareil photo, QR plus fin</option><option value="compact">Compact — scanner LipoWatcher, recommandé sur Fichero</option></select></label>
        <p>{content === 'compact' ? 'Modules de 3 points. À lire avec le scanner LipoWatcher ; la caméra seule n’ouvre pas de lien.' : 'Lien complet, modules de 2 points : testez la lecture après impression. Le lien dépend du domaine.'}</p>
        <p>Fermez Fichero Printer. La connexion reste active dans cet onglet jusqu’à déconnexion, extinction de l’appareil ou interruption par le navigateur. Un rechargement de page demande une reconnexion.</p>
        {!FicheroPrinter.supported() ? <p role="alert">Bluetooth web indisponible : utilisez Chrome Android en HTTPS ou l’impression standard.</p> :
          <div className="heading-actions"><button className="btn secondary" disabled={busy} onClick={async () => {setError(''); try {if (connected) printer.disconnect(); else await connect()} catch (e) {setError((e as Error).message)}}}>{connected ? 'Déconnecter la Fichero' : 'Connecter / reconnecter la Fichero'}</button><button className="btn ghost" disabled={busy} onClick={async () => {try {await connect(true)} catch (e) {setError((e as Error).message)}}}>Choisir une autre imprimante</button></div>}
        <p role="status">{connecting ? 'Connexion…' : connected ? printer.name + ' connectée' : 'Non connectée'}</p>
      </>}
      <div className="label-selection">
        <div className="heading-actions"><button className="btn ghost" disabled={busy} onClick={() => setChecked({...checked, ...Object.fromEntries(visible.map(b => [b.id, true]))})}>Tout sélectionner</button><button className="btn ghost" disabled={busy} onClick={() => setChecked({...checked, ...Object.fromEntries(visible.map(b => [b.id, false]))})}>Tout désélectionner</button></div>
        {visible.map(b => <div className="label-selection-row" key={b.id}><label className="check"><input type="checkbox" disabled={busy} checked={checked[b.id] !== false} onChange={e => setChecked({...checked, [b.id]: e.target.checked})}/><span>#{b.number} · {b.cells}S · {b.nominal_mah} mAh</span></label><label>Copies<input type="number" min="1" max="20" disabled={busy} value={copies[b.id] ?? '1'} onChange={e => setCopies({...copies, [b.id]: e.target.value})}/></label></div>)}
      </div>
      {!validCopies && <p role="alert">Choisissez de 1 à 20 copies par batterie.</p>}
      <div className="heading-actions"><button className="btn primary" disabled={busy || !count || !validCopies || (mode === 'fichero' && (!connected || unresolved || jobs.some(j => j.state === 'queued')))} onClick={print}>{mode === 'fichero' ? 'Imprimer ' + count + ' étiquette(s) sur Fichero' : 'Imprimer ' + count + ' étiquette(s)'}</button>{queue.running && <button className="btn secondary" onClick={() => queue.pause()}>Arrêter après l’étiquette en cours</button>}</div>
      {message && <p role="status">{message}</p>}{error && <p className="alert" role="alert">{error}</p>}
    </section>
    {jobs.length > 0 && <section className="panel no-print"><h2>File d’impression</h2><p>Les confirmations et les étiquettes restantes sont conservées dans cet onglet pour votre compte. Vérifiez une étiquette incertaine avant de choisir sa suite.</p>
      <div className="heading-actions"><button className="btn primary" disabled={busy || !connected || unresolved || !jobs.some(j => j.state === 'queued')} onClick={resume}>Reprendre les étiquettes restantes</button><button className="btn ghost" disabled={busy} onClick={() => queue.clear()}>Vider la file</button></div>
      {jobs.map(j => <div className="queue-row" key={j.id}><strong>#{j.number} · copie {j.copy}</strong><span>{jobText[j.state]}</span>{j.error && <small>{j.error}</small>}{['uncertain', 'failed'].includes(j.state) && <div className="heading-actions">{j.state === 'uncertain' && <button disabled={busy} onClick={() => queue.resolve(j.id, 'confirmed')}>Elle est bien imprimée</button>}<button disabled={busy} onClick={() => queue.resolve(j.id, 'queued')}>{j.state === 'uncertain' ? 'La réimprimer' : 'Réessayer'}</button><button disabled={busy} onClick={() => queue.resolve(j.id, 'skipped')}>Ignorer cette copie</button></div>}</div>)}
    </section>}
    {selected.length ? mode === 'fichero' ? <div className="fichero-previews">{selected.map(b => <Preview battery={b} content={content} key={b.id}/>)}</div> : <div className="labels-sheet">{selected.flatMap(b => Array.from({length: copiesFor(b)}, (_, i) => <div className="label" key={b.id + ':' + i}><img src={'/api/batteries/' + b.id + '/qr.png'} alt={'QR batterie ' + b.number}/><div><strong>#{b.number}</strong><span>{b.chemistry} {b.cells}S · {b.nominal_mah} mAh</span><small>LipoWatcher</small></div></div>))}</div> : <div className="empty">Aucune batterie sélectionnée.</div>}
  </>
}
