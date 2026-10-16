/**
 * One detection presentation for every camera type.
 *
 * Colours mirror CATEGORY_BOX_COLOR_BGR in ai-server/app/core/config.py
 * (converted from BGR to RGB), so a box looks the same whether the server drew
 * it on an IP/USB stream or the browser drew it on a browser camera.
 */
export const CATEGORY_RGB = {
  Human: '#00ff00',
  Animals: '#ffa500',
  Vehicles: '#0000ff',
  Others: '#808080',
}

export const ZERO_COUNTS = Object.freeze({ Human: 0, Animals: 0, Vehicles: 0, Others: 0 })

export function normalizeCounts(counts) {
  return {
    Human: Number(counts?.Human) || 0,
    Animals: Number(counts?.Animals) || 0,
    Vehicles: Number(counts?.Vehicles) || 0,
    Others: Number(counts?.Others) || 0,
  }
}

export function totalCount(counts) {
  return Object.values(counts || {}).reduce((sum, value) => sum + (Number(value) || 0), 0)
}

/**
 * Draws YOLO boxes onto a canvas whose pixel size equals the frame size.
 * `sourceWidth`/`sourceHeight` are the size the detections were computed on.
 */
export function drawDetections(ctx, detections, sourceWidth, sourceHeight) {
  const canvas = ctx.canvas
  const sx = canvas.width / (sourceWidth || canvas.width)
  const sy = canvas.height / (sourceHeight || canvas.height)
  const lineWidth = Math.max(2, canvas.width / 320)
  ctx.lineWidth = lineWidth
  ctx.font = `${Math.max(12, Math.round(canvas.width / 48))}px sans-serif`
  ctx.textBaseline = 'alphabetic'

  for (const item of detections || []) {
    const [x1, y1, x2, y2] = item.bbox || []
    if (![x1, y1, x2, y2].every(Number.isFinite)) continue
    const color = CATEGORY_RGB[item.category] || CATEGORY_RGB.Others
    const left = x1 * sx
    const top = y1 * sy
    ctx.strokeStyle = color
    ctx.strokeRect(left, top, (x2 - x1) * sx, (y2 - y1) * sy)

    const label = `${item.name} ${Math.round((item.confidence || 0) * 100)}% (${item.category})`
    const textWidth = ctx.measureText(label).width
    const labelHeight = Math.round(parseInt(ctx.font, 10) * 1.5)
    const labelTop = Math.max(0, top - labelHeight)
    ctx.fillStyle = color
    ctx.fillRect(left, labelTop, textWidth + 8, labelHeight)
    ctx.fillStyle = '#000000'
    ctx.fillText(label, left + 4, labelTop + labelHeight - 6)
  }
}
