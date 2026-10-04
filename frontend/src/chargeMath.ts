/** Arithmetic only: this does not validate a manufacturer's charging limit. */
export function ampsFromMahAndC(capacityMah: number, chargeRateC: number | null): number | null {
  if (!Number.isFinite(capacityMah) || capacityMah <= 0 || chargeRateC === null || !Number.isFinite(chargeRateC) || chargeRateC <= 0) return null
  const amps = capacityMah / 1000 * chargeRateC
  return Number.isFinite(amps) && amps > 0 ? amps : null
}
