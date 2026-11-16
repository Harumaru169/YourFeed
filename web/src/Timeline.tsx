import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { api, ApiError } from './api'
import type { Card, Detail, FeedStatus, Run } from './api'
import { Link, navigate } from './router'
import { Banner, Button, IconButton, Label, Text } from '@primer/react'
import { Card as PrimerCard } from '@primer/react/experimental'

const CloseIcon = () => <svg aria-hidden="true" width="16" height="16" viewBox="0 0 16 16" fill="currentColor"><path d="M3.72 3.72a.75.75 0 0 1 1.06 0L8 6.94l3.22-3.22a.75.75 0 1 1 1.06 1.06L9.06 8l3.22 3.22a.75.75 0 1 1-1.06 1.06L8 9.06l-3.22 3.22a.75.75 0 1 1-1.06-1.06L6.94 8 3.72 4.78a.75.75 0 0 1 0-1.06Z" /></svg>

const UPDATING_MS = 5 * 1000
const IDLE_MS = 60 * 1000

const dateFormat = new Intl.DateTimeFormat('ja-JP', { year: 'numeric', month: 'numeric', day: 'numeric' })
const updateFormat = new Intl.DateTimeFormat('ja-JP', {
  year: 'numeric', month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit',
})

function DetailDialog({ id, onClose, onClosed }: { id: string | null; onClose: () => void; onClosed: () => void }) {
  const ref = useRef<HTMLDialogElement>(null)
  const [result, setResult] = useState<{ id: string; detail: Detail | null; missing?: boolean } | null>(null)
  const [attempt, setAttempt] = useState(0)
  const current = result?.id === id ? result : null

  useLayoutEffect(() => {
    const dialog = ref.current
    if (!dialog) return
    if (id && current && !dialog.open) dialog.showModal()
    if (dialog.open && (!id || !current)) {
      dialog.close()
      if (!id) onClosed()
    }
  }, [id, current])

  useEffect(() => {
    setResult(null)
    if (!id) return
    let cancelled = false
    api.item(id).then((item) => {
      if (!cancelled) setResult({ id, detail: item })
    }).catch((error) => { if (!cancelled) setResult({ id, detail: null, missing: error instanceof ApiError && error.status === 404 }) })
    return () => { cancelled = true }
  }, [id, attempt])

  const detail = current?.detail
  return <>
    {id && !current && <p className="detail-loading" role="status">詳細を読み込んでいます…</p>}
    <dialog ref={ref} className="detail" aria-labelledby={detail ? 'detail-title' : undefined}
    aria-label={detail ? undefined : '記事の詳細'}
    onClick={(event) => {
      if (event.target !== event.currentTarget) return
      const { left, right, top, bottom } = event.currentTarget.getBoundingClientRect()
      if (event.clientX < left || event.clientX > right || event.clientY < top || event.clientY > bottom) onClose()
    }}
    onCancel={(event) => { event.preventDefault(); onClose() }}>
    <IconButton className="close" icon={CloseIcon} variant="invisible" onClick={onClose} aria-label="閉じる" />
    {current && !detail && <div><p>{current.missing ? "記事が見つかりません。" : "記事を読み込めませんでした。"}</p>{!current.missing && <Button onClick={() => setAttempt((value) => value + 1)}>再試行</Button>}</div>}
    {detail && <>
      <h2 id="detail-title">{detail.title}</h2>
      <p className="detail-text">{detail.facts}</p>
      {detail.interpretation && <div className="detail-interpretation">
        <p className="detail-label">解釈</p>
        <p className="detail-text">{detail.interpretation}</p>
      </div>}
      <div className="detail-meta">
        {detail.published_at && <p>{dateFormat.format(new Date(detail.published_at))}</p>}
        <p>{detail.original_title}</p>
        <p><a href={detail.url} target="_blank" rel="noopener noreferrer">{detail.source} ↗</a></p>
      </div>
    </>}
    </dialog>
  </>
}


export default function Timeline({ path }: { path: string }) {
  const [cards, setCards] = useState<Card[]>([])
  const [busy, setBusy] = useState(false)
  const [done, setDone] = useState(false)
  const [error, setError] = useState(false)
  const [lastRun, setLastRun] = useState<Run | null>()
  const [feedStatus, setFeedStatus] = useState<FeedStatus[]>([])
  const cursor = useRef<string | null>(null)
  const seen = useRef(new Set<string>())
  const inFlight = useRef(false)
  const loaded = useRef(false)
  const updating = useRef(false)
  const keep = useRef<{ element: HTMLElement; top: number } | null>(null)
  const cardList = useRef<HTMLElement>(null)
  const sentinel = useRef<HTMLDivElement>(null)
  const opener = useRef<{ element: HTMLElement | null; top: number; scrollY: number } | null>(null)
  const detailId = path.startsWith('/items/') ? path.split('/')[2] : null

  const load = useCallback(async (fromTop: boolean) => {
    if (inFlight.current) return
    inFlight.current = true
    setBusy(true)
    setError(false)
    if (fromTop || !loaded.current) {
      void api.settings().then((settings) => { setLastRun(settings.last_run); setFeedStatus(settings.feed_status) }).catch(() => {})
    }
    try {
      const page = await api.timeline(fromTop ? undefined : cursor.current ?? undefined)
      const fresh = page.items.filter((item) => !seen.current.has(item.id))
      fresh.forEach((item) => seen.current.add(item.id))
      updating.current = page.updating
      if (fromTop && loaded.current) {
        const incoming = new Set(page.items.map((item) => item.id))
        const visible = window.scrollY > 0 && Array.from(cardList.current?.querySelectorAll<HTMLElement>('[data-card-id]') ?? [])
          .find((element) => incoming.has(element.dataset.cardId ?? '') && element.getBoundingClientRect().bottom > 0
            && element.getBoundingClientRect().top < window.innerHeight)
        keep.current = visible ? { element: visible, top: visible.getBoundingClientRect().top } : null
        seen.current = new Set(page.items.map((item) => item.id))
        setCards(page.items)
        cursor.current = page.next_cursor
        setDone(!page.next_cursor)
      } else {
        cursor.current = page.next_cursor
        setDone(!page.next_cursor)
        if (fresh.length) setCards((current) => [...current, ...fresh])
      }
      loaded.current = true
    } catch {
      setError(true)
    } finally {
      inFlight.current = false
      setBusy(false)
    }
  }, [])

  useEffect(() => { void load(false) }, [load])

  useLayoutEffect(() => {
    const saved = keep.current
    if (!saved) return
    keep.current = null
    if (saved.element.isConnected) {
      window.scrollTo({ top: window.scrollY + saved.element.getBoundingClientRect().top - saved.top, behavior: 'instant' })
    }
  }, [cards])

  useEffect(() => {
    if (!sentinel.current) return
    const observer = new IntersectionObserver((entries) => {
      if (entries[0]?.isIntersecting && !busy && !done && !error && loaded.current) void load(false)
    }, { rootMargin: '500px 0px' })
    observer.observe(sentinel.current)
    return () => observer.disconnect()
  }, [cards.length, busy, done, error, load])

  useEffect(() => {
    let timer = 0
    let stopped = false
    const check = async () => { if (!document.hidden && loaded.current) await load(true) }
    const tick = async () => {
      await check()
      if (!stopped) timer = window.setTimeout(tick, updating.current ? UPDATING_MS : IDLE_MS)
    }
    timer = window.setTimeout(tick, UPDATING_MS)
    const visible = () => { void check() }
    document.addEventListener('visibilitychange', visible)
    return () => { stopped = true; window.clearTimeout(timer); document.removeEventListener('visibilitychange', visible) }
  }, [load])

  function open(id: string, element: HTMLElement) {
    opener.current = { element, top: element.getBoundingClientRect().top, scrollY: window.scrollY }
    navigate(`/items/${id}`)
  }

  function close() {
    if (opener.current) history.back()
    else navigate('/', true)
  }

  function restore() {
    const saved = opener.current
    if (!saved) return
    const element = saved.element
    const delta = element?.isConnected ? element.getBoundingClientRect().top - saved.top : null
    window.scrollTo({ top: delta === null ? saved.scrollY : window.scrollY + delta, behavior: 'instant' })
    element?.focus({ preventScroll: true })
    opener.current = null
  }

  return <main className="page" aria-busy={busy}>
    <nav className="top feed-top">
      <Text size="small" className="last-update">
        {lastRun === undefined ? '' : lastRun === null ? '未更新' : lastRun.status === 'running' ? '更新中'
          : `最終更新：${updateFormat.format(new Date(lastRun.finished_at ?? lastRun.started_at))}`}
      </Text>
      <div className="feed-actions">
        <Link to="/settings" button>設定</Link>
      </div>
    </nav>
    <h1 className="page-title">フィード</h1>
    {lastRun?.status === 'partial' && <Banner variant="warning" title="前回の更新で記事が不足しました" description={`新しいカードを ${lastRun.posted}/${lastRun.target} 件用意できました。`} />}
    {feedStatus.some((genre) => genre.current < genre.target) && <Banner variant="warning" title="フィードに不足があります" description={feedStatus.filter((genre) => genre.current < genre.target).map((genre) => `${genre.name} ${genre.current}/${genre.target}件`).join("、")} />}
    {busy && !cards.length && <p role="status">記事を読み込んでいます…</p>}
    {!busy && !error && loaded.current && cards.length === 0 && <p>記事はまだありません。<Link to="/settings">設定</Link>からジャンルを追加して更新してください。</p>}
    <section className="cards" ref={cardList}>
      {cards.map((card) => <PrimerCard className="feed-card" data-card-id={card.id} key={card.id}>
        <PrimerCard.Heading as="h2"><button className="card-open" onClick={(event) => open(card.id, event.currentTarget)}>{card.title}</button></PrimerCard.Heading>
        <PrimerCard.Description>{card.body}</PrimerCard.Description>
        <PrimerCard.Metadata className="card-genre"><Label>{card.genre}</Label></PrimerCard.Metadata>
      </PrimerCard>)}
    </section>
    {error && <Banner variant="critical" title="接続できません" primaryAction={<Button onClick={() => void load(false)}>再試行</Button>} />}
    {busy && cards.length > 0 && <p role="status">読み込んでいます…</p>}
    {done && cards.length > 0 && <p className="note">すべての記事を表示しました。</p>}
    <div ref={sentinel} className="end" />
    <DetailDialog id={detailId} onClose={close} onClosed={restore} />
  </main>
}
