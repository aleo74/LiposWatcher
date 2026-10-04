import { useState } from 'react'
import type { Battery } from './api'
import { ampsFromMahAndC } from './chargeMath'

// Keep existing keys: completion timestamps belong to individual batteries.
const STEPS = [
  { key: 'inspection', title: 'Inspecter le pack', text: 'Examiner l’enveloppe, les câbles et les connecteurs. En cas de gonflement ou de dommage, interrompre l’utilisation et consulter la notice du pack.' },
  { key: 'identity', title: 'Vérifier la chimie et les cellules', text: 'Comparer la chimie et le nombre de cellules indiqués sur l’étiquette à ceux de cette fiche. Corriger la fiche si nécessaire avant de préparer une charge.' },
  { key: 'connections', title: 'Vérifier les branchements', text: 'Contrôler la polarité et la compatibilité du connecteur principal. Utiliser la prise d’équilibrage et une charge avec équilibrage lorsque le pack et le chargeur l’exigent ou le prévoient.' },
  { key: 'program', title: 'Choisir la chimie et le nombre de cellules', text: 'Choisir sur le chargeur un programme correspondant à la chimie réelle et au nombre de cellules. Les noms des programmes varient selon les appareils : suivre la notice du chargeur, sans supposer un menu ou un bouton précis.' },
  { key: 'limits', title: 'Contrôler courant et tension finale', text: 'Lire sur l’étiquette ou dans la notice du pack le courant maximal de charge et la tension finale adaptés à sa chimie. Choisir séparément le courant de cette charge ; le C de décharge ne donne pas le C de charge.' },
  { key: 'monitoring', title: 'Préparer et surveiller la charge', text: 'Placer le pack sur une surface stable adaptée à la charge, éloignée des matières inflammables. Rester présent et surveiller le pack et le chargeur pendant toute la charge.' },
  { key: 'cooling', title: 'Laisser refroidir un pack chaud', text: 'Si le pack est chaud après utilisation, attendre son retour à la température ambiante avant de le recharger, selon sa notice.' },
  { key: 'first_sessions', title: 'Suivre les premières utilisations', text: 'Appliquer, si elle existe, la procédure du fabricant pour cette référence. Noter les conditions et le comportement du pack, sans supposer un nombre de cycles ni prescrire de décharges profondes.' },
  { key: 'reference', title: 'Noter les mesures de référence', text: 'Noter le moment du relevé, l’état de charge et son origine, le chargeur, la température, les conditions, puis si possible les tensions et résistances par cellule.' },
  { key: 'storage', title: 'Prévoir la mise en stockage', text: 'Après utilisation, choisir le stockage adapté à la chimie et à la notice du pack. Vérifier la tension ou l’état de charge visés dans cette notice avant de lancer une tâche de stockage.' },
] as const

const ampsText = (value: number) => value.toLocaleString('fr-FR', { maximumFractionDigits: 3 })

export function FirstUseGuide({ battery, offline, busy, onClose, onToggle, onReference }: {
  battery: Battery; offline: boolean; busy: boolean; onClose: () => void;
  onToggle: (key: string, done: boolean) => void; onReference: () => void;
}) {
  const [chosenCurrent, setChosenCurrent] = useState('')
  const hasDocumentedRate = battery.charge_c !== null && Boolean(battery.charge_rate_source.trim())
  const calculatedAmps = hasDocumentedRate ? ampsFromMahAndC(battery.nominal_mah, battery.charge_c) : null
  const directMax = battery.charge_max_a != null && Boolean(battery.charge_rate_source.trim()) ? battery.charge_max_a : null
  const maxAmps = directMax ?? calculatedAmps
  const chosenAmps = chosenCurrent.trim() ? Number(chosenCurrent) : null
  const chosenValid = chosenAmps !== null && Number.isFinite(chosenAmps) && chosenAmps > 0

  return <div className="modal-backdrop guide-backdrop" onMouseDown={event => { if (event.target === event.currentTarget) onClose() }}>
    <div className="modal guide-modal" role="dialog" aria-modal="true" aria-label="Première utilisation / rodage selon le fabricant">
      <div className="modal-head"><div><div className="eyebrow">BATTERIE NEUVE #{battery.number}</div><h2>Première utilisation / rodage selon le fabricant</h2></div><button className="iconbtn" onClick={onClose} aria-label="Fermer">×</button></div>
      <p>Guide facultatif, valable comme liste de vérifications générales. Un éventuel « rodage » dépend de la notice de cette référence ; il n’est pas obligatoire pour toutes les LiPo et ne répare pas un pack vieilli ou mal stocké. Ce guide ne commande aucun chargeur.</p>

      {battery.manufacturer_guide_url ? <div className="guide-source"><strong>Source fabricant renseignée pour ce pack</strong><div>Champ d’application déclaré : {battery.manufacturer_guide_scope}</div><a href={battery.manufacturer_guide_url} target="_blank" rel="noopener noreferrer">Consulter cette source ↗</a><small>Vérifiez que la référence et la révision correspondent réellement au pack. Cette procédure ne s’applique pas automatiquement à d’autres batteries.</small></div> : <div className="notice">Aucune procédure de premiers cycles n’est renseignée pour cette référence. Consultez son étiquette et sa notice ; aucun nombre de cycles n’est supposé.</div>}

      <div className="guide-specs"><span>Chimie inscrite dans la fiche <strong>{battery.chemistry}</strong></span><span>Cellules inscrites dans la fiche <strong>{battery.cells}S</strong></span><span>Capacité nominale inscrite <strong>{battery.nominal_mah} mAh</strong></span></div>
      <p className="guide-check">Comparez ces trois valeurs à l’étiquette. Leurs limites de charge ne sont pas déduites de la seule chimie ni du nombre de cellules.</p>

      <section className="guide-current" aria-label="Courant de charge">
        <strong>Courant maximal du pack et courant choisi</strong>
        <p>Calcul en ampères à partir des valeurs renseignées : capacité (mAh) ÷ 1 000 × taux de charge (C). Exemple purement arithmétique : 1 300 mAh à 1C = 1,3 A. Ce calcul ne vérifie ni la notice ni l’autorisation de charger à ce courant.</p>
        {maxAmps !== null ? <p><strong>Maximum déclaré dans la fiche : {ampsText(maxAmps)} A</strong> ({directMax!==null?'valeur renseignée directement en A':`${battery.charge_c}C × ${battery.nominal_mah} mAh`} ; source indiquée : {battery.charge_rate_source}). Vérifiez que cette source désigne bien la limite du pack. Ce maximum n’est pas un réglage conseillé pour sa première utilisation.</p> : <p><strong>Maximum non calculable.</strong> Consultez sur l’étiquette ou dans la notice le taux ou courant <em>maximal de charge</em> propre au pack et sa source. La capacité et le C de décharge ne suffisent pas.</p>}
        {directMax!==null&&calculatedAmps!==null&&Math.abs(directMax-calculatedAmps)>0.001&&<p className="guide-alert">Les limites renseignées diffèrent : {ampsText(directMax)} A directement et {ampsText(calculatedAmps)} A calculés depuis les C. Consultez leur source et corrigez la fiche avant de choisir un courant.</p>}
        {battery.charge_final_v!=null&&<p>Tension finale du pack déclarée : <strong>{battery.charge_final_v} V</strong> · source : {battery.charge_rate_source}. Vérifiez sa référence et son champ d’application.</p>}
        <label className="guide-choice">Courant choisi pour cette charge (A), facultatif<input type="number" inputMode="decimal" min="0.001" step="any" value={chosenCurrent} onChange={event => setChosenCurrent(event.target.value)} placeholder="À saisir après lecture des notices"/></label>
        {chosenCurrent && !chosenValid && <p className="guide-alert">Saisissez un courant positif et fini en ampères.</p>}
        {chosenValid && <p>Courant choisi ici : <strong>{ampsText(chosenAmps!)} A</strong>. Cette valeur n’est pas enregistrée par le guide et doit être réglée séparément sur le chargeur, après vérification de ses propres limites.</p>}
        {chosenValid && maxAmps !== null && chosenAmps! > maxAmps && <p className="guide-alert">Le courant choisi dépasse le maximum déclaré dans la fiche : vérifiez la valeur, sa source et la notice avant toute charge.</p>}
        {chosenValid && maxAmps !== null && chosenAmps! <= maxAmps && <p>Le courant choisi ne dépasse pas le maximum déclaré. Cette comparaison arithmétique ne confirme pas que le réglage convient à cette charge.</p>}
        {chosenValid && maxAmps === null && <p>Comparaison impossible : le maximum de charge documenté pour ce pack manque dans la fiche.</p>}
      </section>

      <div className="guide-info"><strong>Avant de lancer la charge</strong><p>Vérifiez sur l’étiquette ou dans la notice du pack sa chimie réelle, son nombre de cellules, sa tension finale et son courant maximal de charge. Vérifiez dans la notice du chargeur la compatibilité, les connexions, l’équilibrage lorsqu’il s’applique et ses limites de courant. Les intitulés des programmes varient selon les chargeurs ; aucun menu ni bouton précis n’est présumé ici.</p><p>Surveillez la charge sur une surface adaptée, stable et éloignée des matières inflammables. Laissez refroidir un pack chaud avant recharge.</p></div>

      <div className="guide-steps">{STEPS.map(step => <label className="guide-step" key={step.key}><input type="checkbox" checked={Boolean(battery.guide_steps?.[step.key])} disabled={offline || busy} onChange={event => onToggle(step.key, event.target.checked)} /><span><strong>{step.title}</strong><small>{step.text}</small></span></label>)}</div>
      <p className="guide-reference">Sources identifiées, propres à leurs produits : <a href="https://gensace.de/pages/lipo-battery-guide" target="_blank" rel="noopener noreferrer">consignes Gens Ace / Tattu</a> et <a href="https://www.spektrumrc.com/on/demandware.static/Sites-spektrum-us-Site/Sites-horizon-master/default/Manuals/LiPo_Charge_Manual_Low_Resolution.pdf" target="_blank" rel="noopener noreferrer">manuel de charge Spektrum</a>. Elles étayent les précautions générales ; leurs réglages et procédures ne sont pas universels.</p>
      <div className="modal-actions"><button className="btn secondary" onClick={onClose}>Fermer</button><button className="btn primary" onClick={onReference} disabled={offline || busy}>Enregistrer des mesures de référence</button></div>
      <p className="guide-disclaimer">Cocher toutes les étapes ne change pas le statut de la batterie et ne signifie pas qu’elle est sûre pour le vol.</p>
    </div>
  </div>
}
