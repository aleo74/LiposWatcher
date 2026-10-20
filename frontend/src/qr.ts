const UUID = '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'
const VERSIONED = new RegExp(`^lipowatcher:v1:(${UUID})$`, 'i')
const LEGACY_PATH = new RegExp(`^/b/(${UUID})/?$`, 'i')
const BASE45 = '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ $%*+-./:'

/** Return only the technical identifier; never navigate to a scanned origin. */
export function parseQrIdentifier(raw: string): string | null {
  const value = raw.trim()
  // Version L: 16 UUID bytes in Base45; 25 QR-alphanumeric characters total.
  if (value.startsWith('L') && value.length === 25) {
    const bytes: number[] = []
    for (let i = 1; i < value.length; i += 3) {
      const digits = [value[i], value[i + 1], value[i + 2]].map(c => BASE45.indexOf(c))
      if (digits.some(n => n < 0)) return null
      const number = digits[0] + digits[1] * 45 + digits[2] * 2025
      if (number > 65535) return null
      bytes.push(number >> 8, number & 255)
    }
    const hex = bytes.map(n => n.toString(16).padStart(2, '0')).join('')
    return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`
  }
  const versioned = VERSIONED.exec(value)
  if (versioned) return versioned[1].toLowerCase()
  try {
    const url = new URL(value)
    if (url.protocol !== 'https:' && url.protocol !== 'http:') return null
    const legacy = LEGACY_PATH.exec(url.pathname)
    return legacy ? legacy[1].toLowerCase() : null
  } catch {
    return null
  }
}
