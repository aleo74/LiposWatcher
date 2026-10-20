import {useEffect, useState, type ReactNode} from 'react'
import {api, json, type Battery, type Charger, type Entry} from './api'
import {modelKey, modelLabel, latestMeasured} from './fleetUtils'
import {filterMeasurements} from './sessionDevices'
const value = (v: string | number | null | undefined, unit = '') => v == null || v === '' ? '—' : String(v) + unit
const date = (entry?: Entry) => entry ? new Date(entry.occurred_at).toLocaleString('fr-FR') : '—'
function conditions(entry?: Entry) {
  return entry ? <><span>{entry.charger || 'Chargeur non renseigné'}{entry.charger_channel != null ? ' · canal ' + entry.charger_channel : ''}</span><span>{value(entry.ambient_c, ' °C')} · {value(entry.current_a, ' A')} · coupure {value(entry.cutoff_v, ' V')}</span><span>Charge initiale : {value(entry.initial_percent, ' %')} · {entry.initial_percent_source || 'origine non renseignée'}</span>{entry.notes && <span>{entry.notes}</span>}</> : '—'
}
export function Comparison({offline}: {offline: boolean}) {
  const [list, setList] = useState<Battery[]>([]), [chargers, setChargers] = useState<Charger[]>([]), [group, setGroup] = useState(''), [selected, setSelected] = useState<string[]>([])
  const [result, setResult] = useState<Battery[]>([]), [device, setDevice] = useState(''), [channel, setChannel] = useState(''), [error, setError] = useState(''), [busy, setBusy] = useState(false)
  useEffect(() => {Promise.all([api<Battery[]>('/batteries'), api<Charger[]>('/chargers')]).then(([b, c]) => {setList(b); setChargers(c)}).catch(e => setError(e.message))}, [])
  const models = [...new Map(list.map(b => [modelKey(b), modelLabel(b)])).entries()]
  const members = list.filter(b => group && modelKey(b) === group)
  const measures = result.map(b => ({battery: b, entries: filterMeasurements(b.entries || [], device, channel)})).map(b => ({...b, capacity: latestMeasured(b.entries, 'capacity'), resistance: latestMeasured(b.entries, 'resistance')}))
  const rows: [string, (b: typeof measures[number]) => ReactNode][] = [
    ['Capacité nominale', b => b.battery.nominal_mah + ' mAh'],
    ['Dernier test de capacité retenu', b => date(b.capacity)],
    ['Capacité mesurée (décharge)', b => value(b.capacity?.discharged_mah, ' mAh')],
    ['Part de la capacité nominale', b => value(b.capacity?.metrics.measured_ratio, ' %')],
    ['Conditions de ce test', b => <div className="comparison-conditions">{conditions(b.capacity)}</div>],
    ['Dernier relevé de résistance retenu', b => date(b.resistance)],
    ['Résistance moyenne des cellules mesurées', b => value(b.resistance?.metrics.resistance_mean_mohm, ' mΩ')],
    ['Résistance par cellule', b => Array.from({length: b.battery.cells}, (_, i) => 'C' + (i + 1) + ' : ' + value(b.resistance?.resistance_mohm?.[i], ' mΩ')).join(' · ')],
    ['Conditions de ce relevé', b => <div className="comparison-conditions">{conditions(b.resistance)}</div>],
  ]
  return <><h1>Comparer mes batteries</h1><p>Comparer les mesures de batteries ayant les mêmes caractéristiques déclarées. Les différences de chargeur, de température ou de protocole peuvent modifier les résultats.</p>
    <section className="panel"><label>Modèle et caractéristiques<select value={group} onChange={e => {setGroup(e.target.value); setSelected([]); setResult([])}}><option value="">Choisir un groupe…</option>{models.map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label>
      <div className="heading-actions"><button className="btn ghost" disabled={busy} onClick={() => {setSelected(members.slice(0, 20).map(b => b.id)); setResult([])}}>Sélectionner le groupe (20 maximum)</button></div>
      <div className="lot-members">{members.map(b => <label className="check" key={b.id}><input type="checkbox" disabled={busy} checked={selected.includes(b.id)} onChange={e => {setSelected(e.target.checked ? [...selected, b.id] : selected.filter(id => id !== b.id)); setResult([])}}/><span>#{b.number}</span></label>)}</div>
      <button className="btn primary" disabled={offline || busy || selected.length < 2 || selected.length > 20} onClick={async () => {setBusy(true); setError(''); try {setResult((await api<{batteries: Battery[]}>('/batteries/compare', json('POST', {battery_ids: selected}))).batteries)} catch (e) {setResult([]); setError((e as Error).message)} finally {setBusy(false)}}}>Comparer {selected.length} batteries</button>{error && <p className="alert" role="alert">{error}</p>}
    </section>
    {result.length > 0 && <section className="panel"><div className="form-grid"><label>Chargeur<select value={device} onChange={e => {setDevice(e.target.value); setChannel('')}}><option value="">Tous les chargeurs</option>{chargers.map(c => <option value={'id:' + c.id} key={c.id}>{c.name}</option>)}{[...new Set(result.flatMap(b => (b.entries || []).filter(e => !e.charger_id && e.charger).map(e => e.charger)))].map(c => <option key={c} value={'text:' + c}>{c} (texte historique)</option>)}</select></label><label>Canal<select value={channel} onChange={e => setChannel(e.target.value)}><option value="">Tous les canaux</option><option value="none">Non renseigné</option>{[...new Set(result.flatMap(b => (b.entries || []).flatMap(e => e.charger_channel == null ? [] : [e.charger_channel])))].sort((a, b) => a - b).map(c => <option key={c} value={c}>{c}</option>)}</select></label></div>
      {new Set(result.map(modelKey)).size > 1 && <p className="notice">Certaines fiches ont changé depuis votre sélection. Leurs caractéristiques diffèrent ; vérifiez-les avant de comparer.</p>}
      <p className="chart-note">Sur mobile, faites défiler le tableau horizontalement pour voir chaque batterie.</p><div className="cell-table-wrap" tabIndex={0} aria-label="Comparaison des batteries, défilement horizontal"><table className="cell-table comparison-table"><thead><tr><th>Mesure</th>{measures.map(b => <th key={b.battery.id}>#{b.battery.number}</th>)}</tr></thead><tbody>{rows.map(([label, render]) => <tr key={label}><th>{label}</th>{measures.map(b => <td key={b.battery.id}>{render(b)}</td>)}</tr>)}</tbody></table></div><p>— signifie non renseigné. Les moyennes portent uniquement sur les cellules mesurées. Les dernières mesures retenues peuvent provenir de sessions différentes ; leurs dates et conditions sont affichées séparément. Aucun score global de santé ni garantie de sécurité.</p>
    </section>}
  </>
}
