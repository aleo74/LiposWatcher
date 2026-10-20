import {useEffect, useRef, useState} from 'react'
import {api, type Battery, type Lot} from './api'
import {FicheroPrinter, packRaster} from './fichero'

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
    ctx.fillStyle = '#000'
    let fontSize = 20
    do {ctx.font = `bold ${fontSize--}px sans-serif`} while (ctx.measureText(battery.number).width > 140 && fontSize >= 10)
    const split = (text: string, max: number) => {
      const lines: string[] = []; let line = ''
      for (const ch of text) {if (line && ctx.measureText(line + ch).width > max) {lines.push(line); line = ''} line += ch}
      if (line) lines.push(line)
      return lines
    }
    const lines = split(battery.number, 140)
    if (lines.length > 3) throw new Error('Numéro trop long pour une étiquette Fichero : raccourcissez-le')
    lines.forEach((line, i) => ctx.fillText(line, 98, 23 + i * 15))
    ctx.font = '12px sans-serif'
    ctx.fillText(`${battery.cells}S · ${battery.nominal_mah} mAh`, 98, 77)
    ctx.font = '10px sans-serif'; ctx.fillText('LipoWatcher', 98, 91)
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
  return <div className="fichero-preview">{url ? <img src={url} alt={`Étiquette Fichero ${battery.number}`}/> : <small>{error || 'Préparation…'}</small>}</div>
}

export function Labels({list, go}: {list: Battery[]; go: (path: string) => void}) {
  const one = new URLSearchParams(location.search).get('one')
  const [lot, setLot] = useState(new URLSearchParams(location.search).get('lot') || '')
  const [batteries, setBatteries] = useState(list), [lots, setLots] = useState<Lot[]>([]), [ready, setReady] = useState(false)
  const [single, setSingle] = useState<Battery | null>(null)
  const [mode, setMode] = useState(() => {try {return localStorage.getItem('lipowatcher:printer') === 'fichero' ? 'fichero' : 'standard'} catch {return 'standard'}})
  const [content, setContent] = useState<QrContent>(() => {try {return localStorage.getItem('lipowatcher:fichero-qr') === 'compact' ? 'compact' : 'link'} catch {return 'link'}})
  const [connected, setConnected] = useState(false), [busy, setBusy] = useState(false), [message, setMessage] = useState(''), [error, setError] = useState('')
  const printer = useRef<FicheroPrinter | null>(null)
  const cancel = useRef(false)
  if (!printer.current) printer.current = new FicheroPrinter(setConnected)
  useEffect(() => () => {cancel.current = true; printer.current?.disconnect()}, [])
  useEffect(() => {
    let active = true
    setReady(false)
    const load = one ? api<Battery>('/batteries/' + one).then(b => {if (active) setSingle(b)}) :
      Promise.all([api<Battery[]>('/batteries'), api<Lot[]>('/lots')]).then(([b, l]) => {if (active) {setBatteries(b); setLots(l)}})
    load.then(() => {if (active) setReady(true)}).catch(() => {if (active) setError('Fiches inaccessibles : vérifiez votre connexion')})
    return () => {active = false}
  }, [one])
  const selected = ready ? one ? (single ? [single] : []) : batteries.filter(b => !lot || b.lot_id === lot) : []
  const supported = FicheroPrinter.supported()
  async function print() {
    if (mode === 'standard') {window.print(); return}
    setBusy(true); setError(''); setMessage(''); cancel.current = false
    let count = 0
    try {
      for (const battery of selected) {
        if (cancel.current) break
        // Re-fetch with current authentication immediately before each label.
        const landscape = await compactLabel(battery, content)
        if (cancel.current) break
        const canvas = document.createElement('canvas'); canvas.width = 96; canvas.height = 240
        const ctx = canvas.getContext('2d')!
        ctx.imageSmoothingEnabled = false; ctx.translate(96, 0); ctx.rotate(Math.PI / 2); ctx.drawImage(landscape, 0, 0)
        setMessage(`Envoi de l’étiquette ${count + 1}/${selected.length} : ${battery.number}`)
        await printer.current!.print(packRaster(ctx.getImageData(0, 0, 96, 240).data, 96, 240))
        count++
      }
      setMessage(`${count} étiquette(s) confirmée(s) par l’imprimante.${cancel.current ? ' Série arrêtée.' : ''} Vérifiez le papier et la lecture du QR.`)
    } catch (e) {setError(`${(e as Error).message} ${count} étiquette(s) confirmée(s) avant l’arrêt. Aucune relance automatique.`); setMessage('')}
    finally {setBusy(false)}
  }
  return <>
    <button className="back no-print" disabled={busy} onClick={() => go(one ? '/b/' + one : '/')} >← Retour</button>
    <div className="page-heading no-print"><div><div className="eyebrow">IDENTIFICATION</div><h1>Étiquettes QR</h1><p>{mode === 'fichero' ? 'Format papier 30 × 14 mm, zone imprimable 30 × 12 mm. Choisissez un code compact ou un lien web.' : 'Scannez le code avec LipoWatcher pour ouvrir la fiche après connexion. Il ne contient aucun secret.'}</p></div></div>
    <section className="panel no-print printer-controls">
      <label>Imprimante<select aria-label="Imprimante" value={mode} disabled={busy} onChange={e => {setMode(e.target.value); setError(''); setMessage(''); printer.current?.disconnect(); try {localStorage.setItem('lipowatcher:printer', e.target.value)} catch { /* preference only */ }}}><option value="standard">Standard — impression A4</option><option value="fichero">Fichero 5836 / D11s — Bluetooth, 30 × 14 mm</option></select></label>
      {!one && <label>Lot à imprimer<select value={lot} disabled={busy || !ready} onChange={e => setLot(e.target.value)}><option value="">Toutes mes batteries</option>{lots.map(l => <option key={l.id} value={l.id}>{l.name}</option>)}</select></label>}
      {mode === 'fichero' && <><label>Contenu du QR<select value={content} disabled={busy} onChange={e => {const value = e.target.value as QrContent; setContent(value); setMessage(''); try {localStorage.setItem('lipowatcher:fichero-qr', value)} catch { /* preference only */ }}}><option value="link">Lien web — appareil photo, QR plus fin</option><option value="compact">Compact — scanner LipoWatcher, recommandé sur Fichero</option></select></label><p>{content === 'compact' ? 'Modules de 3 points au lieu de 2, QR simplifié. À lire avec le scanner intégré à LipoWatcher après connexion ; l’appareil photo seul n’ouvre pas de lien. Le code reste valable si le domaine change.' : 'Le lien complet ouvre la fiche après connexion depuis l’appareil photo. Ses modules de 2 points sont plus difficiles à lire sur cette petite imprimante ; le lien dépend du domaine actuel.'}</p></>}
      {mode === 'fichero' && <><p>Fermez Fichero Printer avant de connecter l’appareil. Imprimez d’abord une seule étiquette et testez le QR.</p>{!supported ? <p role="alert">Bluetooth web indisponible. Ouvrez LipoWatcher en HTTPS dans Chrome sur Android, ou utilisez l’impression standard.</p> : <button className="btn secondary" disabled={busy} onClick={async () => {if (connected) {printer.current?.disconnect(); return} setBusy(true); setError(''); try {await printer.current!.connect()} catch(e) {setError((e as Error).message)} finally {setBusy(false)}}}>{connected ? 'Déconnecter la Fichero' : 'Connecter la Fichero'}</button>}<span role="status">{connected ? ' Fichero connectée' : ' Non connectée'}</span></>}
      <div className="heading-actions"><button className="btn primary" disabled={busy || !selected.length || (mode === 'fichero' && (!supported || !connected))} onClick={print}>{mode === 'fichero' ? `Imprimer ${selected.length} étiquette(s) sur Fichero` : 'Imprimer'}</button>{busy && mode === 'fichero' && <button className="btn secondary" onClick={() => {cancel.current = true}}>Arrêter après l’étiquette en cours</button>}</div>
      {message && <p role="status">{message}</p>}{error && <p className="alert" role="alert">{error}</p>}
    </section>
    {selected.length ? mode === 'fichero' ? <div className="fichero-previews">{selected.map(b => <Preview battery={b} content={content} key={b.id}/>)}</div> : <div className="labels-sheet">{selected.map(b => <div className="label" key={b.id}><img src={`/api/batteries/${b.id}/qr.png`} alt={`QR batterie ${b.number}`}/><div><strong>#{b.number}</strong><span>{b.chemistry} {b.cells}S · {b.nominal_mah} mAh</span><small>LipoWatcher</small></div></div>)}</div> : <div className="empty">Aucune batterie à imprimer.</div>}
  </>
}
