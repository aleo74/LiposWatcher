import type {Charger, Entry} from './api'

export function filterMeasurements(entries:Entry[], device:string, channel:string):Entry[]{
  return entries.filter(e=>(!device||(device.startsWith('id:')?e.charger_id===device.slice(3):!e.charger_id&&e.charger===device.slice(5)))&&(!channel||(channel==='none'?e.charger_channel==null:e.charger_channel===Number(channel))))
}

/** The latest relevant choice is specific to this battery and a physical device. */
export function lastDeviceChoice(entries:Entry[],chargers:Charger[]):Entry|null{
  const latest=entries.find(e=>e.charger_id?chargers.some(c=>c.id===e.charger_id&&c.status==='active'):Boolean(e.charger))
  if(!latest)return null
  if(!latest.charger_id)return {...latest,charger_channel:null}
  const charger=chargers.find(c=>c.id===latest.charger_id)!
  return {...latest,charger_channel:latest.charger_channel!=null&&latest.charger_channel<=charger.channels?latest.charger_channel:null}
}
