const UUID = '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'
const VERSIONED = new RegExp(`^lipowatcher:v1:(${UUID})$`, 'i')
const LEGACY_PATH = new RegExp(`^/b/(${UUID})/?$`, 'i')

/** Return only the technical identifier; never navigate to a scanned origin. */
export function parseQrIdentifier(raw: string): string | null {
  const value = raw.trim()
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
