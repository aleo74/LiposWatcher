import {useEffect, useRef, useState} from 'react'
import {api, json, ApiError, type Battery, type Charger, type Entry} from './api'
import {Scanner} from './Scanner'
import {clearDraft, loadDraft, saveDraft} from './drafts'
import {lastDeviceChoice} from './sessionDevices'

const localTime = () => {const d = new Date(); return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 16)}
type Draft = {mah: string; date: string; chargerId: string; channel: string}
function QuickCharge({battery, chargers, userId, offline, onExpired, done, onBusy, sessionVersion}: {battery: Battery; chargers: Charger[]; userId: string; offline: boolean; onExpired: () => void; done: (battery: Battery, mah: number) => void; onBusy: (busy: boolean) => void; sessionVersion: number}) {
  const previous = lastDeviceChoice(battery.entries || [], chargers)
  const [draft, setDraft] = useState<Draft>(() => loadDraft<Draft>(sessionStorage, userId, battery.id, 'atelier-charge') || {mah: '', date: localTime(), chargerId: previous?.charger_id || '', channel: previous?.charger_channel?.toString() || ''})
  const [busy, setBusy] = useState(false), [error, setError] = useState('')
  const submitting = useRef(false)
  useEffect(() => setError(''), [sessionVersion])
  useEffect(() => saveDraft(sessionStorage, userId, battery.id, 'atelier-charge', draft), [userId, battery.id, draft])
  const charger = chargers.find(c => c.id === draft.chargerId && c.status === 'active')
  async function save() {
    if (submitting.current || offline) return
    const mah = Number(draft.mah)
    if (!draft.mah.trim() || !Number.isInteger(mah) || mah <= 0) {setError('Renseignez les mAh ajoutés, en nombre entier positif'); return}
    submitting.current = true; setBusy(true); onBusy(true); setError('')
    try {
      await api<Entry>('/batteries/' + battery.id + '/entries', json('POST', {kind: 'charge', added_mah: mah, occurred_at: new Date(draft.date).toISOString(), charger_id: charger?.id || null, charger_channel: charger && draft.channel ? Number(draft.channel) : null}))
      clearDraft(sessionStorage, userId, battery.id, 'atelier-charge')
      done(battery, mah)
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) {setError('Session expirée. Votre saisie est conservée pour ce compte et cette batterie.'); onExpired()}
      else setError((e as Error).message + (!(e instanceof ApiError) || e.status >= 500 ? ' Enregistrement non confirmé : vérifiez l’historique avant de réessayer.' : ''))
    } finally {submitting.current = false; setBusy(false); onBusy(false)}
  }
  return <form className="panel quick-charge" onSubmit={e => {e.preventDefault(); save()}}><h2>#{battery.number}</h2><p>{battery.brand} {battery.model} · {battery.cells}S · {battery.nominal_mah} mAh</p><label>mAh ajoutés<input autoFocus required type="number" min="1" step="1" inputMode="numeric" disabled={busy || offline} value={draft.mah} onChange={e => setDraft({...draft, mah: e.target.value})}/></label>
    <details className="advanced"><summary>Date et chargeur facultatif</summary><div className="form-grid"><label>Date et heure<input type="datetime-local" required disabled={busy} value={draft.date} onChange={e => setDraft({...draft, date: e.target.value})}/></label><label>Chargeur<select disabled={busy} value={charger?.id || ''} onChange={e => setDraft({...draft, chargerId: e.target.value, channel: ''})}><option value="">Non renseigné</option>{chargers.filter(c => c.status === 'active').map(c => <option key={c.id} value={c.id}>{c.name}</option>)}</select></label>{charger && <label>Canal<select disabled={busy} value={draft.channel} onChange={e => setDraft({...draft, channel: e.target.value})}><option value="">Non renseigné</option>{Array.from({length: charger.channels}, (_, i) => <option key={i} value={i + 1}>{i + 1}</option>)}</select></label>}</div></details>
    {error && <p className="alert" role="alert">{error}</p>}<button className="btn primary" disabled={busy || offline}>{busy ? 'Enregistrement…' : 'Enregistrer et scanner la suivante'}</button><p>Seuls les mAh ajoutés et la date sont nécessaires. Aucun appareil n’est piloté.</p>
  </form>
}

export function Workshop({userId, offline, onExpired, sessionVersion}: {userId: string; offline: boolean; onExpired: () => void; sessionVersion: number}) {
  const [scan, setScan] = useState(false), [id, setId] = useState(''), [battery, setBattery] = useState<Battery | null>(null), [chargers, setChargers] = useState<Charger[]>([])
  const [query, setQuery] = useState(''), [found, setFound] = useState<Battery[]>([]), [error, setError] = useState(''), [last, setLast] = useState('')
  const [saving, setSaving] = useState(false)
  useEffect(() => {api<Charger[]>('/chargers').then(setChargers).catch(e => setError(e.message))}, [])
  useEffect(() => {let active = true; setBattery(null); setError(''); if (id) api<Battery>('/batteries/' + id).then(b => {if (active) setBattery(b)}).catch(e => {if (active) {setError(e.message); if (e instanceof ApiError && e.status === 401) onExpired()}}); return () => {active = false}}, [id])
  useEffect(() => {let active = true; if (!query.trim()) {setFound([]); return} api<Battery[]>('/batteries?q=' + encodeURIComponent(query)).then(b => {if (active) setFound(b)}).catch(e => {if (active) setError(e.message)}); return () => {active = false}}, [query])
  return <><h1>Mode atelier</h1><p>Scanner une batterie, noter les mAh ajoutés, enregistrer puis passer à la suivante.</p>{last && <p className="notice" role="status">{last}</p>}{error && <p className="alert" role="alert">{error}</p>}
    {battery ? <QuickCharge sessionVersion={sessionVersion} onBusy={setSaving} key={userId + battery.id} battery={battery} chargers={chargers} userId={userId} offline={offline} onExpired={onExpired} done={(b, mah) => {setLast('#' + b.number + ' : ' + mah + ' mAh enregistrés'); setId(''); setBattery(null); setQuery(''); setScan(true)}}/> : <section className="panel"><button className="btn primary" disabled={offline} onClick={() => setScan(true)}>Scanner la batterie</button>{id && !error && <p>Chargement de la fiche…</p>}<label>Ou chercher par numéro<input disabled={offline} value={query} onChange={e => setQuery(e.target.value)} placeholder="F750-003"/></label><div className="heading-actions">{found.map(b => <button className="btn secondary" key={b.id} onClick={() => setId(b.id)}>#{b.number}</button>)}</div></section>}
    {battery && <button className="btn secondary" disabled={saving} onClick={() => {setId(''); setBattery(null)}}>Choisir une autre batterie — conserver le brouillon</button>}
    {scan && <Scanner close={() => setScan(false)} found={identifier => {setScan(false); setId(identifier)}}/>}
  </>
}
