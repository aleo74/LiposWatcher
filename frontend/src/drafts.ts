type StorageLike = Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>

export function draftKey(userId: string, batteryId: string, entryId = 'new'): string {
  return `lipowatcher:draft:v1:${userId}:${batteryId}:${entryId}`
}

export function loadDraft<T>(storage: StorageLike, userId: string, batteryId: string, entryId = 'new'): T | null {
  try {
    const raw = storage.getItem(draftKey(userId, batteryId, entryId))
    if (!raw) return null
    const parsed = JSON.parse(raw) as { userId?: string; batteryId?: string; entryId?: string; value?: T }
    if (parsed.userId !== userId || parsed.batteryId !== batteryId || parsed.entryId !== entryId) return null
    return parsed.value ?? null
  } catch {
    return null
  }
}

export function saveDraft<T>(storage: StorageLike, userId: string, batteryId: string, entryId: string, value: T): void {
  try {
    storage.setItem(draftKey(userId, batteryId, entryId), JSON.stringify({ userId, batteryId, entryId, value }))
  } catch {
    // A private browsing quota may prevent persistence; the open form stays in memory.
  }
}

export function clearDraft(storage: StorageLike, userId: string, batteryId: string, entryId = 'new'): void {
  try { storage.removeItem(draftKey(userId, batteryId, entryId)) } catch {}
}
