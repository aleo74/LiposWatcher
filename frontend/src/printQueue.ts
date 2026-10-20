export type LabelContent = 'link' | 'compact'
export type LabelJob = {
  id: string; batteryId: string; number: string; copy: number; content: LabelContent;
  state: 'queued' | 'preparing' | 'printing' | 'confirmed' | 'uncertain' | 'failed' | 'skipped'; error?: string
}
type StorageLike = Pick<Storage, 'getItem' | 'setItem'>

/** Owner-scoped queue, never an automatic replay after a lost acknowledgement. */
export class PrintQueue {
  private jobs: LabelJob[] = []
  private listeners = new Set<() => void>()
  private busy = false
  private paused = false
  private key: string
  constructor(private owner: string, private storage?: StorageLike) {
    this.key = `lipowatcher:print-queue:v1:${owner}`
    try {
      const saved = JSON.parse(storage?.getItem(this.key) || 'null')
      const states = ['queued', 'preparing', 'printing', 'confirmed', 'uncertain', 'failed', 'skipped']
      if (saved?.owner === owner && Array.isArray(saved.jobs) && saved.jobs.length <= 1000 && saved.jobs.every((j: LabelJob) =>
        typeof j.id === 'string' && typeof j.batteryId === 'string' && typeof j.number === 'string' &&
        Number.isInteger(j.copy) && j.copy >= 1 && states.includes(j.state) && ['link', 'compact'].includes(j.content))) {
        this.jobs = saved.jobs.map((j: LabelJob) => ({...j, state: j.state === 'printing' ? 'uncertain' : j.state === 'preparing' ? 'queued' : j.state}))
      }
    } catch { /* Corrupt/unavailable storage must not cross owners or replay jobs. */ }
  }
  subscribe = (listener: () => void) => {this.listeners.add(listener); return () => {this.listeners.delete(listener)}}
  snapshot = () => this.jobs
  get running() {return this.busy}
  private publish() {
    // New array for React subscribers; retain active job references across awaits.
    this.jobs = [...this.jobs]
    try {this.storage?.setItem(this.key, JSON.stringify({owner: this.owner, jobs: this.jobs}))} catch { /* In-memory queue still works. */ }
    this.listeners.forEach(listener => listener())
  }
  enqueue(batteries: {id: string; number: string; copies: number}[], content: LabelContent) {
    if (this.busy) throw new Error('Une série est déjà en cours')
    const count = batteries.reduce((sum, b) => sum + b.copies, 0)
    if (!count || batteries.some(b => !Number.isInteger(b.copies) || b.copies < 1 || b.copies > 20) || count + this.jobs.length > 1000) throw new Error('Choisissez 1 à 20 copies par batterie, au plus 1 000 étiquettes dans la file')
    for (const battery of batteries) for (let copy = 1; copy <= battery.copies; copy++) this.jobs.push({id: crypto.randomUUID(), batteryId: battery.id, number: battery.number, copy, content, state: 'queued'})
    this.publish()
  }
  resolve(id: string, decision: 'confirmed' | 'queued' | 'skipped') {
    if (this.busy) return
    const job = this.jobs.find(j => j.id === id)
    if (!job || !['uncertain', 'failed'].includes(job.state)) return
    job.state = decision; delete job.error; this.publish()
  }
  clear() {if (!this.busy) {this.jobs = []; this.publish()}}
  pause() {this.paused = true}
  async run(prepare: (job: LabelJob) => Promise<Uint8Array>, send: (raster: Uint8Array) => Promise<void>) {
    if (this.busy) throw new Error('Une série est déjà en cours')
    if (this.jobs.some(j => j.state === 'uncertain' || j.state === 'failed')) throw new Error('Vérifiez les étiquettes incertaines ou en erreur avant de reprendre')
    this.busy = true; this.paused = false; this.publish()
    try {
      for (const job of this.jobs) {
        if (this.paused) break
        if (job.state !== 'queued') continue
        job.state = 'preparing'; this.publish()
        try {
          const raster = await prepare(job)
          if (this.paused) {job.state = 'queued'; this.publish(); break}
          job.state = 'printing'; this.publish()
          await send(raster)
          job.state = 'confirmed'; this.publish()
        } catch (error) {
          job.state = job.state === 'printing' ? 'uncertain' : 'failed'
          job.error = (error as Error).message; this.publish(); throw error
        }
      }
    } finally {this.busy = false; this.publish()}
  }
}
