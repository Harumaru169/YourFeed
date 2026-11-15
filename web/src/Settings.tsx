import { Button, Select, TextInput } from '@primer/react'
import { useAppearance } from './Theme'
import { useEffect, useRef, useState } from 'react'
import { api } from './api'
import type { Backend, GenreRow, Provider, Run, Settings as SettingsData, SettingsValues, Switch } from './api'
import { Link } from './router'

const INTERVALS: [number, string][] = [[15, '15分'], [30, '30分'], [60, '1時間'], [120, '2時間'],
  [180, '3時間'], [360, '6時間'], [720, '12時間'], [1440, '24時間']]
const PROVIDERS: [Provider, string][] = [['openai', 'OpenAI互換API'], ['anthropic', 'Claude API'],
  ['claude_cli', 'Claude Code'], ['codex_cli', 'Codex'], ['gemini_cli', 'Gemini CLI']]
const MAX_BACKENDS = 5
const providerName = (provider: Provider | null) => PROVIDERS.find(([value]) => value === provider)?.[1] ?? provider ?? ''
const ERRORS: Record<string, string> = {
  no_model: 'モデル未設定',
  http_400: 'AIがリクエストを拒否',
  http_401: 'APIキーが無効',
  http_403: 'APIキーが無効',
  http_404: 'モデルが見つかりません',
  http_429: 'AIの利用制限',
  cli_not_found: 'CLIが見つかりません',
  cli_error: 'CLIでエラー',
  cli_login: 'CLIにログインしていません',
  unexpected_response: 'AIの応答を読めません',
  unknown_provider: '接続先が不明',
  connection_error: 'AIに接続できません',
  timeout: 'AIが応答しません',
  search_unavailable: '検索に接続できません',
  search_format: '検索がJSONに未対応',
  search_limited: '検索の回数制限',
  search_timeout: '検索が応答しません',
  search_error: '検索でエラー',
  interrupted: '中断',
  settings_changed: '設定変更のため再更新します',
  search_budget: '検索の上限に達しました',
}
const timeFormat = new Intl.DateTimeFormat('ja-JP', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' })

type Section = 'genres' | 'update' | 'ai'
/** The update settings as typed, and the providers in the order they are tried. */
type Form = { interval_minutes: string; feed_size: string; search_concurrency: string; llm_concurrency: string; backends: Backend[] }
type TextKey = Exclude<keyof Form, 'backends'>

const toForm = (settings: SettingsValues): Form => ({
  interval_minutes: String(settings.interval_minutes), feed_size: String(settings.feed_size),
  search_concurrency: String(settings.search_concurrency), llm_concurrency: String(settings.llm_concurrency),
  backends: [{ provider: settings.llm_provider, model: settings.llm_model }, ...settings.llm_fallbacks],
})

function toValues(form: Form): SettingsValues {
  const [main, ...fallbacks] = form.backends.map((backend) => ({ ...backend, model: backend.model.trim() }))
  return {
    interval_minutes: Number(form.interval_minutes), feed_size: Number(form.feed_size),
    search_concurrency: Number(form.search_concurrency), llm_concurrency: Number(form.llm_concurrency), llm_provider: main.provider, llm_model: main.model,
    llm_fallbacks: fallbacks,
  }
}

/** "Claude Code（AIの利用制限） → Codex": the providers an update gave up on and the one it went on with. */
function switchText(switches: Switch[]): string {
  if (!switches.length) return ''
  const chain = switches.map((step) => `${providerName(step.from)}（${ERRORS[step.error] ?? step.error}）`)
  const last = switches[switches.length - 1].to
  return `　${chain.join(' → ')} → ${last ? providerName(last) : 'AIなし'}`
}

/** Posts per update for each genre: largest remainder over genres that have a weight. */
function planned(genres: GenreRow[], weights: Record<string, string>, total: number): Record<string, number> {
  const active = genres.filter((genre) => Number(weights[genre.id]) > 0)
  const sum = active.reduce((acc, genre) => acc + Number(weights[genre.id]), 0)
  const counts: Record<string, number> = Object.fromEntries(genres.map((genre) => [genre.id, 0]))
  if (!sum || total <= 0) return counts
  const exact = active.map((genre, index) => ({ id: genre.id, index, value: total * Number(weights[genre.id]) / sum }))
  exact.forEach((entry) => { counts[entry.id] = Math.floor(entry.value) })
  let left = total - exact.reduce((acc, entry) => acc + Math.floor(entry.value), 0)
  for (const entry of [...exact].sort((a, b) => (b.value % 1) - (a.value % 1) || a.index - b.index)) {
    if (left-- <= 0) break
    counts[entry.id] += 1
  }
  return counts
}

function runText(run: Run | null): string {
  if (!run) return '未更新'
  if (run.status === 'running') return '更新中'
  const when = timeFormat.format(new Date(run.finished_at ?? run.started_at))
  const switches = run.switches ?? []
  // A switch already names the error that caused it.
  const errors = [run.search_error, switches.length ? null : run.error].filter((code): code is string => Boolean(code))
  const problem = errors.map((code) => `　${ERRORS[code] ?? code}`).join('') + switchText(switches)
  if (run.status === 'error') return `${when}　失敗${problem}`
  const count = run.posted < run.target ? `${run.posted}/${run.target}件` : `${run.posted}件`
  const found = run.found < run.found_target ? `　候補 ${run.found}/${run.found_target}` : ''
  return `${when}　${count}${found}${problem}`
}

function options(list: [number, string][], current: string, unit: string, below = 0) {
  const all = list.some(([value]) => String(value) === current) ? list : [...list, [Number(current), `${current}${unit}`] as [number, string]]
  return all.map(([value, label]) => <option key={value} value={value} disabled={value < below}>{label}</option>)
}

const blurOnEnter = (event: React.KeyboardEvent<HTMLInputElement>) => {
  if (event.key === 'Enter') event.currentTarget.blur()
}

export default function Settings() {
  const { theme, setTheme } = useAppearance()
  const [data, setData] = useState<SettingsData | null>(null)
  const [form, setForm] = useState<Form | null>(null)
  const [weights, setWeights] = useState<Record<string, string>>({})
  const [failed, setFailed] = useState<Section | 'load' | null>(null)
  const [waiting, setWaiting] = useState(false)
  const saved = useRef<Form | null>(null)
  const poll = useRef<number | null>(null)

  useEffect(() => {
    api.settings().then((settings) => {
      setData(settings)
      setForm(toForm(settings))
      saved.current = toForm(settings)
      setWeights(Object.fromEntries(settings.genres.map((genre) => [genre.id, String(genre.weight)])))
    }).catch(() => setFailed('load'))
    return () => { if (poll.current) window.clearInterval(poll.current) }
  }, [])

  useEffect(() => {
    const timer = window.setInterval(() => {
      if (!document.hidden) void api.settings().then(setData).catch(() => {})
    }, 5000)
    return () => window.clearInterval(timer)
  }, [])

  if (failed === 'load') return <main className="page"><p>接続できません。</p></main>
  if (!data || !form) return <main className="page" aria-busy="true" />

  async function commit(next: Form, section: Section) {
    if (saved.current && JSON.stringify(next) === JSON.stringify(saved.current)) return
    const target = document.querySelector<HTMLFormElement>(`form[data-section="${section}"]`)
    if (target && !target.checkValidity()) {
      setFailed(section)
      return
    }
    try {
      const result = await api.saveSettings(toValues(next))
      saved.current = toForm(result)
      setData(result)
      setFailed(null)
      // The server drops a provider listed twice.
      if (section === 'ai') setForm((current) => current && { ...current, backends: toForm(result).backends })
    } catch {
      setFailed(section)
    }
  }

  const change = (key: TextKey, section: Section, now = false) =>
    (event: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => {
      const next = { ...form, [key]: event.target.value }
      setForm(next)
      if (now) void commit(next, section)
    }
  const blur = (section: Section) => () => { if (form) void commit(form, section) }

  const setBackends = (backends: Backend[], now: boolean) => {
    const next = { ...form, backends }
    setForm(next)
    if (now) void commit(next, 'ai')
  }
  const editBackend = (index: number, patch: Partial<Backend>, now: boolean) =>
    setBackends(form.backends.map((backend, position) => position === index ? { ...backend, ...patch } : backend), now)
  const addBackend = () => {
    const unused = PROVIDERS.find(([value]) => !form.backends.some((backend) => backend.provider === value))?.[0]
    setBackends([...form.backends, { provider: unused ?? 'openai', model: '' }], true)
  }
  const raiseBackend = (index: number) => {
    const backends = [...form.backends];
    [backends[index - 1], backends[index]] = [backends[index], backends[index - 1]]
    setBackends(backends, true)
  }
  const removeBackend = (index: number) => setBackends(form.backends.filter((_, position) => position !== index), true)

  async function saveWeight(genre: GenreRow) {
    const value = Number(weights[genre.id])
    if (!Number.isInteger(value) || value < 0 || value > 100) {
      setFailed('genres')
      return
    }
    if (value === genre.weight) return
    try {
      await api.setWeight(genre.id, value)
      setData(await api.settings())
      setFailed(null)
    } catch {
      setFailed('genres')
    }
  }

  async function refresh() {
    setWaiting(true)
    try {
      await api.refresh()
    } catch {
      setWaiting(false)
      return
    }
    const requested = Date.now()
    if (poll.current) window.clearInterval(poll.current)
    poll.current = window.setInterval(async () => {
      try {
        const latest = await api.settings()
        setData((current) => current && { ...current, last_run: latest.last_run, feed_status: latest.feed_status })
        const run = latest.last_run
        const finished = run && run.status !== 'running' && new Date(run.started_at).getTime() >= requested - 60_000
        if (finished || Date.now() - requested > 30 * 60_000) {
          if (poll.current) window.clearInterval(poll.current)
          setWaiting(false)
        }
      } catch { /* keep polling */ }
    }, 5000)
  }

  const uses = (provider: Provider) => form.backends.some((backend) => backend.provider === provider)
  const counts =planned(data.genres, weights, Number(form.feed_size) || 0)
  const heading = (title: string, section: Section) => <div className="group-head">
    <h2>{title}</h2>
    {failed === section && <span className="message" role="alert">保存できません</span>}
  </div>

  return <main className="page settings">
    <nav className="top top-start"><Link to="/" button>戻る</Link></nav>

    <section className="group">
      <div className="group-head"><h2>外観</h2></div>
      <label className="field"><span>テーマ</span>
        <Select value={theme} onChange={(event) => setTheme(event.target.value as 'auto' | 'light' | 'dark')}>
          <option value="auto">自動</option><option value="light">ライト</option><option value="dark">ダーク</option>
        </Select>
      </label>
    </section>

    <section className="group">
      {heading('ジャンル', 'genres')}
      {data.genres.length > 0 && <>
        <div className="genre-columns" aria-hidden="true"><span /><span>比率</span><span>件数</span></div>
        <ul className="genres">
          {data.genres.map((genre) => <li key={genre.id} className={counts[genre.id] ? '' : 'off'}>
            <Link to={`/settings/genres/${genre.id}`} className="genre-name">{genre.name}</Link>
            <TextInput type="number" min={0} max={100} required aria-label={`${genre.name}の比率`}
              value={weights[genre.id] ?? ''} onKeyDown={blurOnEnter}
              onChange={(event) => setWeights({ ...weights, [genre.id]: event.target.value })}
              onBlur={() => void saveWeight(genre)} />
            <span className="count">{counts[genre.id]}件</span>
          </li>)}
        </ul>
      </>}
      <Link to="/settings/genres/new" className="button">ジャンルを追加</Link>
    </section>

    <form className="group" data-section="update" onSubmit={(event) => event.preventDefault()}>
      {heading('更新', 'update')}
      <label className="field"><span>更新間隔</span>
        <Select value={form.interval_minutes} onChange={change('interval_minutes', 'update', true)}>
          {options(INTERVALS, form.interval_minutes, '分')}
        </Select>
      </label>
      <label className="field"><span>フィードの総件数</span>
        <span className="with-unit"><TextInput type="number" min={1} max={50} required value={form.feed_size}
          onChange={change('feed_size', 'update')} onBlur={blur('update')} onKeyDown={blurOnEnter} />件</span>
      </label>
      <label className="field"><span>検索の同時実行数</span>
        <TextInput type="number" min={1} max={20} required value={form.search_concurrency}
          onChange={change('search_concurrency', 'update')} onBlur={blur('update')} onKeyDown={blurOnEnter} />
      </label>
      <label className="field"><span>AIの同時実行数</span>
        <TextInput type="number" min={1} max={20} required value={form.llm_concurrency}
          onChange={change('llm_concurrency', 'update')} onBlur={blur('update')} onKeyDown={blurOnEnter} />
      </label>
    </form>

    <form className="group" data-section="ai" onSubmit={(event) => event.preventDefault()}>
      {heading('AI', 'ai')}
      <ol className="backends">
        {form.backends.map((backend, index) => <li key={index}>
          <label className="field"><span>{index === 0 ? '接続先' : `切り替え先${index}`}</span>
            <Select value={backend.provider}
              onChange={(event) => editBackend(index, { provider: event.target.value as Provider, model: '' }, true)}>
              {PROVIDERS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
            </Select>
          </label>
          <label className="field"><span>モデル</span>
            <TextInput className="text" spellCheck={false} value={backend.model}
              placeholder={data.default_models[backend.provider] ?? ''}
              onChange={(event) => editBackend(index, { model: event.target.value }, false)}
              onBlur={blur('ai')} onKeyDown={blurOnEnter} />
          </label>
          {index > 0 && <div className="backend-actions">
            <Button type="button" onClick={() => raiseBackend(index)}>上へ</Button>
            <Button type="button" onClick={() => removeBackend(index)}>削除</Button>
          </div>}
        </li>)}
      </ol>
      {form.backends.length < MAX_BACKENDS && <Button type="button" className="button" onClick={addBackend}>
        切り替え先を追加</Button>}
      <p className="note">上の接続先が利用の上限やログイン切れなどで使えなくなると、その更新の間は次の切り替え先を使います。</p>
      {uses('openai') && <div className="field"><span>URL</span>
        <span className="value url">{data.openai_base_url}</span>
      </div>}
      {(['openai', 'anthropic'] as const).filter(uses).map((provider) => <div className="field" key={provider}>
        <span>{provider === 'openai' ? 'APIキー' : 'Claude APIキー'}</span>
        <span className="value">{data.keys[provider] ? '設定済み' : '未設定'}</span>
      </div>)}
    </form>

    <section className="group status">
      <span>{waiting ? '更新中' : runText(data.last_run)}</span>
      <p>フィードの充足状況</p>
      <ul>{data.feed_status.map((genre) => <li key={genre.id}>{genre.name}：{genre.current}/{genre.target}件
        {genre.current < genre.target ? '（不足）' : ''}
        {data.last_run?.genres?.[genre.id] && `、前回の新規 ${data.last_run.genres[genre.id].posted}/${data.last_run.genres[genre.id].target}件`}</li>)}</ul>
      <Button type="button" onClick={() => void refresh()} disabled={waiting}>今すぐ更新</Button>
    </section>
  </main>
}
