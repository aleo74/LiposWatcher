import { useState } from 'react'
import type { Entry } from './api'

const COLORS = ['#167b59', '#d1784a', '#6176bc', '#a6639d', '#aa842c', '#258e9d', '#b55d61', '#687e40', '#735faf', '#bc6e91', '#388379', '#9d713d', '#4872a4', '#b36748', '#718d54', '#815f83']
const metrics = [
  { key: 'before_v', label: 'Tension avant', unit: 'V' },
  { key: 'after_v', label: 'Tension après', unit: 'V' },
  { key: 'resistance_mohm', label: 'Résistance interne', unit: 'mΩ' },
] as const

export function CellTrends({ entries, cells }: { entries: Entry[]; cells: number }) {
  const [selected, setSelected] = useState<(typeof metrics)[number]['key']>('before_v')
  const metric = metrics.find(item => item.key === selected)!
  const sessions = entries.slice().reverse().filter(entry => entry.before_v !== null || entry.after_v !== null || entry.resistance_mohm !== null)
  const all = sessions.flatMap(entry => (entry[selected] || []).filter((value): value is number => value !== null))
  const low = Math.min(...all)
  const high = Math.max(...all)
  const x = (index: number) => 42 + (sessions.length < 2 ? 220 : index * 440 / (sessions.length - 1))
  const y = (value: number) => 138 - (high === low ? 55 : (value - low) * 108 / (high - low))
  const paths = Array.from({ length: cells }, (_, cell) => {
    const segments: string[][] = []
    let segment: string[] = []
    sessions.forEach((entry, index) => {
      const value = entry[selected]?.[cell]
      if (value === null || value === undefined) {
        if (segment.length) segments.push(segment)
        segment = []
      } else segment.push(`${x(index)},${y(value)}`)
    })
    if (segment.length) segments.push(segment)
    return segments.filter(points => points.length > 1).map(points => points.join(' '))
  })

  return <section className="panel cell-panel">
    <div className="section-heading"><div><div className="eyebrow">CELLULES</div><h2>Suivi par cellule</h2></div></div>
    <div className="chart-tabs">{metrics.map(item => <button type="button" key={item.key} className={selected === item.key ? 'selected' : ''} onClick={() => setSelected(item.key)}>{item.label}</button>)}</div>
    {all.length ? <>
      <div className="cell-chart" role="img" aria-label={`${metric.label} de chaque cellule, en ${metric.unit}`}>
        <svg viewBox="0 0 520 165" preserveAspectRatio="none">
          <line x1="42" x2="482" y1="143" y2="143" stroke="#dce3df" />
          {paths.map((parts, cell) => parts.map((points, part) => <polyline key={`${cell}-${part}`} points={points} fill="none" stroke={COLORS[cell % COLORS.length]} strokeWidth="2.5" />))}
          {sessions.flatMap((entry, index) => Array.from({ length: cells }, (_, cell) => {
            const value = entry[selected]?.[cell]
            return value === null || value === undefined ? null : <circle key={`${index}-${cell}`} cx={x(index)} cy={y(value)} r="4" fill={COLORS[cell % COLORS.length]}><title>{`Cellule ${cell + 1} · ${new Date(entry.occurred_at).toLocaleDateString('fr-FR')} · ${value} ${metric.unit}`}</title></circle>
          }))}
          <text x="2" y="34" fontSize="11" fill="#73857a">{high.toFixed(metric.unit === 'V' ? 2 : 1)}</text>
          <text x="2" y="141" fontSize="11" fill="#73857a">{low.toFixed(metric.unit === 'V' ? 2 : 1)}</text>
        </svg>
      </div>
      <div className="cell-legend">{Array.from({ length: cells }, (_, cell) => <span key={cell}><i style={{ background: COLORS[cell % COLORS.length] }} /> Cellule {cell + 1}</span>)}</div>
      <div className="cell-table-wrap"><table className="cell-table"><thead><tr><th>Session</th>{Array.from({ length: cells }, (_, cell) => <th key={cell}>C{cell + 1}</th>)}</tr></thead><tbody>{sessions.slice().reverse().map(entry => <tr key={entry.id}><td>{new Date(entry.occurred_at).toLocaleDateString('fr-FR')}</td>{Array.from({ length: cells }, (_, cell) => <td key={cell}>{entry[selected]?.[cell] ?? '—'}</td>)}</tr>)}</tbody></table></div>
      <p className="chart-note">— signifie « non mesuré ». Aucun zéro n’est ajouté et les lignes sont interrompues lorsqu’une cellule n’a pas de relevé. Comparez des sessions prises dans des conditions similaires.</p>
    </> : <div className="chart-empty">Aucun relevé {metric.label.toLowerCase()} enregistré.</div>}
  </section>
}
