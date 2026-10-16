/**
 * Camera helpers shared by Live Monitoring, Cameras and the Add Camera form.
 * Connection-type values must match CONNECTION_TYPES in ai-server/app/core/config.py.
 */
import { AI_SERVER_URL } from './api'

export const CONNECTION_BROWSER = 'browser_webcam'
export const CONNECTION_LOCAL = 'local_webcam'
export const NETWORK_CONNECTIONS = ['ip_camera', 'rtsp', 'http_mjpeg']

export const CONNECTION_LABELS = {
  browser_webcam: 'Browser Webcam / USB',
  local_webcam: 'USB (AI server host)',
  ip_camera: 'IP Camera',
  rtsp: 'RTSP Stream',
  http_mjpeg: 'HTTP/MJPEG Stream',
}

export const isBrowserCamera = (camera) => camera?.connection_type === CONNECTION_BROWSER
export const isNetworkCamera = (camera) => NETWORK_CONNECTIONS.includes(camera?.connection_type)

/** The browser's camera choice is per browser, so it lives in localStorage, not the database. */
export const browserDeviceStorageKey = (cameraId) => `sas.browserCamera.device.${cameraId}`

function hostOf(url) {
  try {
    return new URL(String(url).trim()).hostname
  } catch {
    return ''
  }
}

/** True for loopback, RFC1918 and link-local hosts that a cloud server cannot reach. */
export function isPrivateHost(host) {
  if (!host) return false
  if (host === 'localhost' || host.endsWith('.local')) return true
  if (/^127\./.test(host) || /^10\./.test(host) || /^192\.168\./.test(host)) return true
  if (/^169\.254\./.test(host)) return true
  const m = host.match(/^172\.(\d+)\./)
  return !!m && Number(m[1]) >= 16 && Number(m[1]) <= 31
}

/**
 * Warns when a network camera is on a private address but the AI server is a
 * remote host: the server (e.g. on Render) cannot open a LAN-only camera.
 */
export function privateCameraWarning(streamUrl) {
  const cameraHost = hostOf(streamUrl)
  if (!cameraHost || !isPrivateHost(cameraHost)) return ''
  if (isPrivateHost(hostOf(AI_SERVER_URL))) return ''
  return `${cameraHost} is a private network address. The AI server at ${hostOf(AI_SERVER_URL) || 'the configured URL'} cannot reach it. Run the AI server on the same network as this camera, or use a public, port-forwarded or VPN address.`
}
