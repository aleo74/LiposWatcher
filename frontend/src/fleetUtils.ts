import type {Battery, Entry} from './api'

export function modelKey(battery: Pick<Battery, 'brand' | 'model' | 'chemistry' | 'cells' | 'nominal_mah'>) {
  return JSON.stringify([battery.brand.trim().toLocaleLowerCase('fr'), battery.model.trim().toLocaleLowerCase('fr'), battery.chemistry, battery.cells, battery.nominal_mah])
}
export function modelLabel(battery: Battery) {return [battery.brand, battery.model, battery.chemistry, battery.cells + 'S', battery.nominal_mah + ' mAh'].filter(Boolean).join(' · ')}
export function latestMeasured(entries: Entry[], metric: 'capacity' | 'resistance') {
  return [...entries].sort((a, b) => Date.parse(b.occurred_at) - Date.parse(a.occurred_at)).find(e => metric === 'capacity' ? e.kind === 'test' && e.discharged_mah != null : e.resistance_mohm?.some(v => v !== null))
}
export function sortFleet(batteries: Battery[], sort: string) {
  const numberOrder = (a: Battery, b: Battery) => a.number.localeCompare(b.number, 'fr', {numeric: true})
  return [...batteries].sort((a, b) => {
    if (sort === 'capacity') return a.nominal_mah - b.nominal_mah || numberOrder(a, b)
    if (sort === 'charges') return (b.charge_count || 0) - (a.charge_count || 0) || numberOrder(a, b)
    if (sort === 'last_used_at' || sort === 'last_measured_at') {
      const x = a[sort], y = b[sort]
      return x && y ? Date.parse(y) - Date.parse(x) || numberOrder(a, b) : x ? -1 : y ? 1 : numberOrder(a, b)
    }
    if (sort === 'model') return modelLabel(a).localeCompare(modelLabel(b), 'fr', {numeric: true}) || numberOrder(a, b)
    return numberOrder(a, b)
  })
}
