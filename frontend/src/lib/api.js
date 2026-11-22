async function request(path, options = {}) {
  const res = await fetch(path, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
    body: options.body ? JSON.stringify(options.body) : undefined,
  })
  let data = null
  try {
    data = await res.json()
  } catch {
    data = null
  }
  if (!res.ok) {
    const msg = data?.detail || `Error ${res.status}`
    throw new Error(typeof msg === 'string' ? msg : 'No se pudo completar la acción')
  }
  return data
}

export const api = {
  vault: () => request('/api/vault'),
  setBankroll: (amount) => request('/api/bankroll', { method: 'POST', body: { amount } }),
  validate: (payload) => request('/api/validate', { method: 'POST', body: payload }),
  takeBet: (payload) => request('/api/bets', { method: 'POST', body: payload }),
  settle: (id, result) => request(`/api/bets/${id}/settle`, { method: 'POST', body: { result } }),
  remove: (id) => request(`/api/bets/${id}`, { method: 'DELETE' }),
  facts: (eventId) => request(`/api/facts/${encodeURIComponent(eventId)}`),
  settings: () => request('/api/settings'),
  setOddsKey: (key) => request('/api/settings/oddsio', { method: 'POST', body: { key } }),
  setOddsBooks: (books) => request('/api/settings/oddsio/books', { method: 'POST', body: { books } }),
  removeOddsKey: () => request('/api/settings/oddsio', { method: 'DELETE' }),
  notifyTest: () => request('/api/notify/test', { method: 'POST' }),
}

export function wsUrl() {
  const proto = window.location.protocol === 'https:' ? 'wss' : 'ws'
  return `${proto}://${window.location.host}/ws/live`
}
