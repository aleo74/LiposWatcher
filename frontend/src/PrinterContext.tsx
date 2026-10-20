import {createContext, useContext, useEffect, useMemo, useState, useSyncExternalStore, type ReactNode} from 'react'
import {FicheroPrinter} from './fichero'
import {PrintQueue} from './printQueue'

type PrinterState = {printer: FicheroPrinter; connected: boolean; connecting: boolean; connect: (reselect?: boolean) => Promise<void>; queue: PrintQueue}
const Context = createContext<PrinterState | null>(null)
export function PrinterProvider({userId, children}: {userId: string; children: ReactNode}) {
  const [connected, setConnected] = useState(false), [connecting, setConnecting] = useState(false)
  const printer = useMemo(() => new FicheroPrinter(setConnected), [])
  const queue = useMemo(() => {let storage: Storage | undefined; try {storage = sessionStorage} catch { /* optional */ } return new PrintQueue(userId, storage)}, [userId])
  useEffect(() => () => {queue.pause(); printer.disconnect()}, [printer, queue])
  async function connect(reselect = false) {setConnecting(true); try {await printer.connect(reselect)} finally {setConnecting(false)}}
  return <Context.Provider value={{printer, connected, connecting, connect, queue}}>{children}</Context.Provider>
}
export function usePrinter() {const context = useContext(Context); if (!context) throw new Error('PrinterProvider requis'); return context}
export function PrinterStatus({go}: {go: (path: string) => void}) {
  const {printer, connected, connecting, queue} = usePrinter()
  const jobs = useSyncExternalStore(queue.subscribe, queue.snapshot)
  if (!connected && !connecting && !queue.running && !jobs.some(j => !['confirmed', 'skipped'].includes(j.state))) return null
  return <div className="printer-status no-print" role="status"><span>{connecting ? 'Connexion Fichero…' : connected ? `${printer.name} connectée` : 'Fichero déconnectée'}{queue.running ? ' · Impression en cours' : ''}</span><button onClick={() => go('/labels')}>Étiquettes ({jobs.filter(j => j.state === 'confirmed').length}/{jobs.length})</button>{connected && <button disabled={queue.running} onClick={() => printer.disconnect()}>Déconnecter</button>}</div>
}
