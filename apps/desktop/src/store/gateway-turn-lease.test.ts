import { afterEach, beforeEach, expect, it, vi } from 'vitest'

const wire = vi.hoisted(() => ({
  closed: vi.fn(),
  request: vi.fn(),
  event: (_event: { type: string; session_id: string; payload?: Record<string, unknown> }) => {}
}))
vi.mock('@/hermes', () => ({
  setApiRequestConnection: vi.fn(),
  HermesGateway: class {
    connectionState = 'closed'
    connect = async () => {
      this.connectionState = 'open'
    }
    close = wire.closed
    request = wire.request
    onState = () => () => {}
    onEvent = (handler: typeof wire.event) => {
      wire.event = handler
      return () => {}
    }
  }
}))
vi.mock('@/store/notify-baseline', () => ({ markNativeNotifyBaseline: vi.fn() }))
const { closeSecondaryGateways, configureGatewayRegistry, retainGatewayForSessionTurn, setPrimaryGateway } =
  await import('./gateway')
const { requestForSessionProfile } = await import('./session-request-router')

beforeEach(() => {
  vi.useFakeTimers()
  configureGatewayRegistry({ onEvent: vi.fn() })
  setPrimaryGateway({ connectionState: 'open' } as never, 'default')
  ;(window as unknown as { hermesDesktop: unknown }).hermesDesktop = {
    getConnectionFor: async () => ({ wsUrl: 'wss://test.invalid/api/ws', token: 'test', profile: 'worker' })
  }
})
afterEach(() => {
  closeSecondaryGateways()
  vi.clearAllMocks()
  vi.useRealTimers()
  delete (window as unknown as { hermesDesktop?: unknown }).hermesDesktop
})
const retain = () => retainGatewayForSessionTurn('source', 'worker', 'session')
const emit = (type: string, payload?: Record<string, unknown>) => wire.event({ type, payload, session_id: 'session' })

it('public lease handoff makes the original release stale before a lazy successor starts', async () => {
  const first = await retain()
  const queued = await retain()
  queued.acknowledge({ status: 'queued' })
  first()
  emit('session.info', { running: false })
  await vi.advanceTimersByTimeAsync(2000)
  expect(wire.closed).not.toHaveBeenCalled()
  // A readiness failure can terminate the successor without message.start.
  emit('message.complete', { status: 'error' })
  emit('session.info', { running: false })
  await vi.advanceTimersByTimeAsync(500)
  expect(wire.closed).toHaveBeenCalledOnce()
})

it('a delayed queued ACK cannot resurrect a completed successor lease', async () => {
  const first = await retain()
  first.acknowledge({ status: 'streaming' })
  emit('message.start')
  emit('message.complete')
  emit('session.info', { running: false })
  const queued = await retain()
  emit('message.start')
  emit('message.complete')
  emit('session.info', { running: false })
  queued.acknowledge({ status: 'queued' })
  await vi.advanceTimersByTimeAsync(500)
  expect(wire.closed).toHaveBeenCalledOnce()
})

it('audit: failed prospective successor restores cancelled idle cleanup', async () => {
  const first = await retain()
  first.acknowledge({ status: 'streaming' })
  emit('message.start')
  emit('message.complete')
  emit('session.info', { running: false })
  const candidate = await retain()
  candidate()
  await vi.advanceTimersByTimeAsync(500)
  expect(wire.closed).toHaveBeenCalledOnce()
})

it('audit: failed prospective successor never closes a live predecessor', async () => {
  const first = await retain()
  first.acknowledge({ status: 'streaming' })
  emit('message.start')
  const candidate = await retain()
  candidate()
  await vi.advanceTimersByTimeAsync(2000)
  expect(wire.closed).not.toHaveBeenCalled()
  emit('message.complete')
  emit('session.info', { running: false })
  await vi.advanceTimersByTimeAsync(500)
  expect(wire.closed).toHaveBeenCalledOnce()
})

it('audit: authoritative pending false reconciles Stop after advertised pending work', async () => {
  await retain()
  emit('message.complete')
  emit('session.info', { running: false, pending_turn: true })
  emit('session.info', { running: false, pending_turn: false })
  await vi.advanceTimersByTimeAsync(500)
  expect(wire.closed).toHaveBeenCalledOnce()
})

it('audit: queued ACK after pre-start failure cannot revive cleanup', async () => {
  const first = await retain()
  first.acknowledge({ status: 'streaming' })
  emit('message.start')
  emit('message.complete')
  emit('session.info', { running: false })
  const candidate = await retain()
  emit('message.complete', { status: 'error' })
  emit('session.info', { running: false })
  candidate.acknowledge({ status: 'queued' })
  await vi.advanceTimersByTimeAsync(500)
  expect(wire.closed).toHaveBeenCalledOnce()
})

it.each([false, true])('correction: rejected candidate with live predecessor %s reconciles only its reservation', async live => {
  const first = await retain()
  first.acknowledge({ status: 'streaming' })
  emit('message.start')
  if (!live) {
    emit('message.complete')
    emit('session.info', { running: false })
    await vi.advanceTimersByTimeAsync(250)
  }
  wire.request.mockResolvedValueOnce({ status: 'rejected' })
  await expect(requestForSessionProfile(
    { connectionId: 'source', profile: 'worker' }, vi.fn(), 'prompt.submit', { session_id: 'session' }
  )).resolves.toEqual({ status: 'rejected' })
  await vi.advanceTimersByTimeAsync(2000)
  if (live) {
    expect(wire.closed).not.toHaveBeenCalled()
    emit('message.complete')
    emit('session.info', { running: false })
    await vi.advanceTimersByTimeAsync(500)
  }
  expect(wire.closed).toHaveBeenCalledOnce()
})

it.each([false, true])('correction: candidate across predecessor idle with failure before ACK %s', async failBeforeAck => {
  const first = await retain()
  first.acknowledge({ status: 'streaming' })
  emit('message.start')
  const candidate = await retain()
  emit('message.complete')
  emit('session.info', { running: false })
  // The outstanding reservation itself must bridge the predecessor's idle.
  await vi.advanceTimersByTimeAsync(1000)
  expect(wire.closed).not.toHaveBeenCalled()
  if (!failBeforeAck) {
    candidate.acknowledge({ status: 'queued' })
    await vi.advanceTimersByTimeAsync(2000)
    expect(wire.closed).not.toHaveBeenCalled()
  }
  // B can fail readiness without ever emitting message.start.
  emit('message.complete', { status: 'error' })
  emit('session.info', { running: false })
  candidate.acknowledge({ status: 'queued' })
  await vi.advanceTimersByTimeAsync(500)
  expect(wire.closed).toHaveBeenCalledOnce()
})

it.each([false, true])('P2: B pre-start failure preserves C delayed queued ACK (B late ACK: %s)', async lateB => {
  const a = await retain()
  a.acknowledge({ status: 'streaming' })
  emit('message.start')
  const b = await retain()
  const c = await retain()
  emit('message.complete')
  emit('session.info', { running: false })
  emit('message.complete', { status: 'error' })
  emit('session.info', { running: false })
  if (lateB) b.acknowledge({ status: 'queued' })
  c.acknowledge({ status: 'queued' })
  await vi.advanceTimersByTimeAsync(2000)
  expect(wire.closed).not.toHaveBeenCalled()
  emit('message.start')
  emit('message.complete')
  emit('session.info', { running: false })
  await vi.advanceTimersByTimeAsync(500)
  expect(wire.closed).toHaveBeenCalledOnce()
  b.acknowledge({ status: 'queued' })
  c()
  emit('session.info', { running: false })
  await vi.advanceTimersByTimeAsync(2000)
  expect(wire.closed).toHaveBeenCalledOnce()
})

it('P2: B start preserves C in-flight reservation and queued ACK eligibility', async () => {
  const a = await retain()
  a.acknowledge({ status: 'streaming' })
  emit('message.start')
  const b = await retain()
  const c = await retain()
  b.acknowledge({ status: 'queued' })
  emit('message.complete')
  emit('session.info', { running: false })
  emit('message.start')
  emit('message.complete')
  emit('session.info', { running: false })
  await vi.advanceTimersByTimeAsync(1000)
  expect(wire.closed).not.toHaveBeenCalled()
  c.acknowledge({ status: 'queued' })
  await vi.advanceTimersByTimeAsync(2000)
  expect(wire.closed).not.toHaveBeenCalled()
  emit('message.start')
  emit('message.complete')
  emit('session.info', { running: false })
  await vi.advanceTimersByTimeAsync(500)
  expect(wire.closed).toHaveBeenCalledOnce()
})
