// D11s protocol documented by https://github.com/0xMH/fichero-printer
// Independent, deliberately limited implementation: no firmware/reset commands.
export const HEAD_WIDTH = 96
export const LABEL_ROWS = 240
const SERVICE = '000018f0-0000-1000-8000-00805f9b34fb'
interface Characteristic extends EventTarget {
  value?: DataView
  startNotifications(): Promise<unknown>
  writeValueWithoutResponse(value: Uint8Array): Promise<void>
}
interface Device extends EventTarget {
  name?: string
  gatt: {connected: boolean; connect(): Promise<{getPrimaryService(id: string): Promise<{getCharacteristic(id: string): Promise<Characteristic>}>}>; disconnect(): void}
}
interface Bluetooth {requestDevice(options: unknown): Promise<Device>}
const pause = (ms: number) => new Promise(resolve => setTimeout(resolve, ms))

export function packRaster(rgba: Uint8ClampedArray, width: number, height: number): Uint8Array {
  if (width !== HEAD_WIDTH || height !== LABEL_ROWS || rgba.length !== width * height * 4) throw new Error('Format Fichero incorrect')
  const result = new Uint8Array(width * height / 8)
  for (let pixel = 0; pixel < width * height; pixel++) {
    const i = pixel * 4
    // Transparent pixels remain white; a QR is never dithered or interpolated.
    if (rgba[i + 3] > 127 && rgba[i] * .299 + rgba[i + 1] * .587 + rgba[i + 2] * .114 < 128) result[pixel >> 3] |= 0x80 >> (pixel % 8)
  }
  return result
}

export class FicheroPrinter {
  private device?: Device
  private write?: Characteristic
  private notify?: Characteristic
  private received: number[] = []
  private pending?: {accept: (bytes: number[]) => boolean; resolve: (bytes: number[]) => void; reject: (error: Error) => void}
  private printing = false
  constructor(private changed: (connected: boolean) => void, private bluetooth?: Bluetooth) {}
  get connected() {return Boolean(this.device?.gatt.connected && this.write)}
  static supported() {return window.isSecureContext && 'bluetooth' in navigator}
  private disconnected = () => {
    this.pending?.reject(new Error('Bluetooth déconnecté ; impression éventuellement partielle. Vérifiez les étiquettes avant de recommencer.'))
    this.pending = undefined
    this.write = undefined
    this.changed(false)
  }
  disconnect() {
    this.notify?.removeEventListener('characteristicvaluechanged', this.onNotify)
    this.device?.gatt.disconnect()
    this.disconnected()
  }
  private onNotify = (event: Event) => {
    const value = (event.target as Characteristic).value
    if (!value || !this.pending) return
    const bytes = Array.from(new Uint8Array(value.buffer, value.byteOffset, value.byteLength))
    this.received.push(...bytes)
    const errorIndex = this.received.indexOf(0xff)
    if (errorIndex >= 0 && errorIndex + 1 < this.received.length) {
      const flags = this.received[errorIndex + 1]
      this.pending.reject(new Error(`Erreur imprimante : ${flags & 1 ? 'surchauffe' : flags & 2 ? 'capot ouvert' : flags & 4 ? 'papier absent' : flags & 8 ? 'batterie faible' : 'réponse inconnue'}`))
      return
    }
    if (this.pending.accept(this.received)) this.pending.resolve(this.received)
  }
  private async send(bytes: number[] | Uint8Array) {
    if (!this.write || !this.device?.gatt.connected) throw new Error('Connectez la Fichero avant d’imprimer')
    await this.write.writeValueWithoutResponse(new Uint8Array(bytes))
  }
  private async query(bytes: number[], accept: (bytes: number[]) => boolean, timeout = 5000) {
    this.received = []
    let timer: ReturnType<typeof setTimeout> | undefined
    const response = new Promise<number[]>((resolve, reject) => {
      this.pending = {accept, resolve, reject}
      timer = setTimeout(() => reject(new Error('Aucune confirmation de l’imprimante. Impression incertaine : vérifiez avant de recommencer.')), timeout)
    })
    // Attach the rejection handler before the write, including immediate failures.
    const writing = this.send(bytes).catch(error => {this.pending?.reject(error)})
    try {const [, result] = await Promise.all([writing, response]); return result} finally {clearTimeout(timer); this.pending = undefined}
  }
  private ack(bytes: number[], timeout = 5000) {
    return this.query(bytes, b => b.includes(0xaa) || new TextDecoder().decode(new Uint8Array(b)).includes('OK'), timeout)
  }
  async connect() {
    const bluetooth = this.bluetooth || (navigator as Navigator & {bluetooth?: Bluetooth}).bluetooth
    if (!bluetooth) throw new Error('Bluetooth web indisponible : utilisez Chrome Android en HTTPS')
    this.disconnect()
    const device = await bluetooth.requestDevice({filters: [{namePrefix: 'FICHERO'}, {namePrefix: 'D11s_'}], optionalServices: [SERVICE]})
    this.device = device
    device.addEventListener('gattserverdisconnected', this.disconnected)
    try {
      const server = await device.gatt.connect()
      const service = await server.getPrimaryService(SERVICE)
      this.write = await service.getCharacteristic('00002af1-0000-1000-8000-00805f9b34fb')
      this.notify = await service.getCharacteristic('00002af0-0000-1000-8000-00805f9b34fb')
      this.notify.addEventListener('characteristicvaluechanged', this.onNotify)
      await this.notify.startNotifications()
      const model = await this.query([0x10, 0xff, 0x20, 0xf0], b => new TextDecoder().decode(new Uint8Array(b)).includes('D11s'))
      if (!new TextDecoder().decode(new Uint8Array(model)).includes('D11s')) throw new Error('Modèle Fichero non compatible')
      this.changed(true)
    } catch (error) {this.disconnect(); throw error}
  }
  async print(raster: Uint8Array) {
    if (this.printing) throw new Error('Une impression est déjà en cours')
    if (raster.length !== HEAD_WIDTH * LABEL_ROWS / 8) throw new Error('Format Fichero incorrect')
    this.printing = true
    try {
      const status = await this.query([0x10, 0xff, 0x40], b => b.length > 0)
      if (status[0] & 0x5f) throw new Error('Imprimante occupée, capot ouvert, papier absent, batterie faible ou surchauffe')
      await this.ack([0x10, 0xff, 0x10, 0, 1])
      await pause(100)
      await this.ack([0x10, 0xff, 0x84, 0])
      await pause(50)
      await this.send(new Uint8Array(12)); await pause(50)
      await this.send([0x10, 0xff, 0xfe, 1]); await pause(50)
      const payload = new Uint8Array(8 + raster.length)
      payload.set([0x1d, 0x76, 0x30, 0, 12, 0, LABEL_ROWS & 255, LABEL_ROWS >> 8])
      payload.set(raster, 8)
      for (let i = 0; i < payload.length; i += 200) {await this.send(payload.slice(i, i + 200)); await pause(20)}
      await pause(500)
      await this.send([0x1d, 0x0c]); await pause(300)
      await this.ack([0x10, 0xff, 0xfe, 0x45], 60000)
    } finally {this.printing = false}
  }
}
