// Local visual harness only. API fixtures, no account/database, no print job.
import React from 'react'
import {createRoot} from 'react-dom/client'
import {Labels} from '../src/Labels'
import type {Battery} from '../src/api'
import '../src/style.css'

const battery = {id: '11111111-2222-4333-8444-555555555555', number: 'F750-003', chemistry: 'LiPo', cells: 2, nominal_mah: 750, lot_id: 'fixture'} as Battery
const originalFetch = window.fetch.bind(window)
window.fetch = async (input, init) => {
  const url = String(input)
  if (url.includes('/qr.png')) return originalFetch(url.includes('content=compact') ? '/tests/fichero-qr.png' : '/tests/fichero-web-qr.png')
  if (url === '/api/batteries') return Response.json([battery])
  if (url === '/api/lots') return Response.json([{id: 'fixture', name: 'Lot Flywoo — exemple'}])
  return originalFetch(input, init)
}
localStorage.setItem('lipowatcher:printer', 'fichero')
createRoot(document.getElementById('root')!).render(<main className="container"><p className="notice">Recette visuelle — données et API simulées. Aucun compte réel.</p><Labels list={[battery]} go={() => {}}/></main>)
