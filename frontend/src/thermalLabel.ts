// Native printer dots: never scale this canvas before packing the raster.
export const TEXT_WIDTH = 142
const font = (size: number) => `900 ${size}px Arial, sans-serif`
export function thermalTextLayout(number: string, cells: number, mah: number, measure: (text: string, font: string) => number) {
  const details = `${cells}S ${mah}mAh`
  let detailSize = 20
  while (detailSize > 16 && measure(details, font(detailSize)) > TEXT_WIDTH) detailSize--
  if (measure(details, font(detailSize)) > TEXT_WIDTH) throw new Error('Caractéristiques trop longues pour une étiquette Fichero')
  const candidates = []
  for (const size of [24, 22, 20]) {
    const lines: string[] = []; let line = ''
    for (const ch of number) {
      if (measure(ch, font(size)) > TEXT_WIDTH) throw new Error('Caractère trop large pour une étiquette Fichero')
      if (line && measure(line + ch, font(size)) > TEXT_WIDTH) {lines.push(line); line = ''}
      line += ch
    }
    if (line) lines.push(line)
    const lineHeight = size + 2, gap = 6, height = lines.length * lineHeight + gap + detailSize + 2
    if (height <= 96) candidates.push({lines, size, lineHeight, top: Math.floor((96 - height) / 2), gap, details, detailSize})
  }
  // Prefer keeping a short identifier on one line over a larger wrapped font.
  candidates.sort((a, b) => a.lines.length - b.lines.length || b.size - a.size)
  if (candidates.length) return candidates[0]
  throw new Error('Numéro trop long pour une étiquette Fichero lisible : raccourcissez-le ou utilisez le format standard')
}

export function drawThermalText(ctx: CanvasRenderingContext2D, number: string, cells: number, mah: number) {
  const text = document.createElement('canvas')
  text.width = TEXT_WIDTH; text.height = 96
  const ink = text.getContext('2d')!
  const layout = thermalTextLayout(number, cells, mah, (value, face) => {ink.font = face; return ink.measureText(value).width})
  ink.fillStyle = '#000'; ink.textBaseline = 'top'; ink.font = font(layout.size)
  layout.lines.forEach((line, i) => ink.fillText(line, 0, layout.top + i * layout.lineHeight))
  ink.font = font(layout.detailSize)
  ink.fillText(layout.details, 0, layout.top + layout.lines.length * layout.lineHeight + layout.gap)
  const pixels = ink.getImageData(0, 0, TEXT_WIDTH, 96)
  // Keep partially covered edge dots as solid ink instead of losing thin strokes
  // at the printer's usual 50% threshold. Only the text region is affected.
  for (let i = 0; i < pixels.data.length; i += 4) {
    const value = pixels.data[i + 3] >= 48 ? 0 : 255
    pixels.data[i] = pixels.data[i + 1] = pixels.data[i + 2] = value
    pixels.data[i + 3] = 255
  }
  ctx.putImageData(pixels, 98, 0)
}
