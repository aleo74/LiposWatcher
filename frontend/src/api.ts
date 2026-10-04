export type Battery = { id:string; number:string; brand:string; model:string; chemistry:string; cells:number; nominal_mah:number; c_rating:number|null; acquired_on:string|null; condition:string; prior_history:string; prior_cycles:number|null; status:string; notes:string; charge_c:number|null; charge_rate_source:string; manufacturer_guide_url:string; manufacturer_guide_scope:string; range:string; connector:string; weight_g:number|null; charge_max_a:number|null; charge_final_v:number|null; model_id:string|null; model_revision:number|null; lot_id:string|null; guide_steps?:Record<string,string>; charge_count?:number; total_added_mah?:number; entries?:Entry[]; transfer_chain?:{from_ref:string;to_ref:string;closed_at:string}[] }
export type Entry = { id:string; kind:string; occurred_at:string; added_mah:number|null; discharged_mah:number|null; cutoff_v:number|null; charger:string; charger_id:string|null; charger_channel:number|null; initial_percent:number|null; initial_percent_source:string; final_percent:number|null; before_v:(number|null)[]|null; after_v:(number|null)[]|null; resistance_mohm:(number|null)[]|null; current_a:number|null; ambient_c:number|null; aircraft:string; duration_min:number|null; voltage_sag:string; heat:string; notes:string; metrics:Record<string,number|string>; locked?:number;origin_ref?:string;annotations?:{id:string;origin_ref:string;text:string;created_at:string}[] }
export type User = {id:string; username:string; is_admin:number;email?:string|null;email_verified_at?:string|null}
export type Model = {id:string; author_id:string; brand:string; range:string; model:string; chemistry:string; cells:number; nominal_mah:number; c_rating:number|null; connector:string; weight_g:number|null; charge_c:number|null; charge_max_a:number|null; charge_final_v:number|null; charge_rate_source:string; manufacturer_guide_url:string; manufacturer_guide_scope:string; notes:string; provenance:string; revision:number; status:string; published_revision?:number|null; decision_revision?:number; pending?:boolean; rejection_reason?:string; created_at:string; updated_at:string; origin_model_id?:string; origin_revision?:number}
export type Lot = {id:string; name:string; acquired_on:string|null; seller:string; total_price:number|null; currency:string; condition:string; notes:string; battery_count?:number; batteries?:Battery[]}
export type Charger = {id:string; name:string; brand:string; model:string; channels:number; firmware:string; notes:string; status:string}

export class ApiError extends Error {
  constructor(message:string, readonly status:number) { super(message); this.name='ApiError' }
}

export async function api<T>(path:string, options:RequestInit={}):Promise<T> {
  const headers = new Headers(options.headers)
  headers.set('Content-Type','application/json')
  if (['POST','PUT','PATCH','DELETE'].includes((options.method||'GET').toUpperCase())) {
    const csrf = await fetch('/api/auth/csrf', {credentials:'same-origin',cache:'no-store'})
    if (!csrf.ok) throw new ApiError('Échange CSRF impossible',csrf.status)
    headers.set('X-CSRF-Token',(await csrf.json()).token)
  }
  const response = await fetch('/api'+path, { ...options, credentials:'same-origin', headers })
  if (!response.ok) {
    let error = `Erreur ${response.status}`
    try { const body = await response.json(); error = typeof body.detail === 'string' ? body.detail : (body.detail?.[0]?.msg || error) } catch {}
    throw new ApiError(error, response.status)
  }
  if (response.status === 204) return undefined as T
  return response.json()
}
export function json(method:string, body:unknown):RequestInit { return {method, body:JSON.stringify(body)} }
