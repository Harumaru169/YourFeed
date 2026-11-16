import { Button, Checkbox, CheckboxGroup, ConfirmationDialog, FormControl, Radio, RadioGroup, TextInput, Textarea } from '@primer/react'
import { useEffect, useRef, useState } from 'react'
import { api, ApiError } from './api'
import type { Genre } from './api'
import { navigate, setNavigationGuard } from './router'

const STANCES = ['事実のみ', '事実中心', '半々', '解釈多め']
const FRESHNESS: [string, boolean][] = [['最新', true], ['問わない', false]]
const TRANSLATE: [string, boolean][] = [['する', true], ['しない', false]]
const LOCALES: [string, string][] = [['ja-JP', '日本・日本語'], ['en-US', 'アメリカ・英語'], ['en-GB', 'イギリス・英語'],
  ['en', '英語（国と地域は問わない）'], ['zh-CN', '中国・中国語'], ['zh-TW', '台湾・中国語'], ['ko-KR', '韓国・韓国語'],
  ['fr-FR', 'フランス・フランス語'], ['de-DE', 'ドイツ・ドイツ語'], ['all', '指定しない']]

interface Form {
  name: string
  latest: boolean
  locales: string[]
  translate: boolean
  sites: string
  focus_terms: string
  discovery_percent: string
  exclude_terms: string
  min_chars: string
  max_chars: string
  stance: number
}

const EMPTY: Form = {
  name: '', latest: true, locales: ['ja-JP'], translate: true, sites: '', focus_terms: '', discovery_percent: '20', exclude_terms: '',
  min_chars: '100', max_chars: '160', stance: 1,
}

const toForm = (genre: Genre): Form => ({
  name: genre.name, latest: genre.latest, locales: genre.locales, translate: genre.translate, sites: genre.sites.join('\n'), focus_terms: genre.focus_terms.join('\n'),
  discovery_percent: String(genre.discovery_percent), exclude_terms: genre.exclude_terms.join('\n'),
  min_chars: String(genre.min_chars), max_chars: String(genre.max_chars), stance: genre.stance,
})

/** "指定しない" stands alone, and at least one stays chosen. */
function toggleLocale(current: string[], value: string): string[] {
  if (current.includes(value)) return current.length > 1 ? current.filter((locale) => locale !== value) : current
  if (value === 'all') return ['all']
  const chosen = new Set([...current.filter((locale) => locale !== 'all'), value])
  return LOCALES.map(([locale]) => locale).filter((locale) => chosen.has(locale))
}

const lines = (value: string) => value.split('\n').map((line) => line.trim()).filter(Boolean)

/** Same rule as the server: a host with a dot, over http(s), without credentials. */
function validSite(value: string): boolean {
  try {
    const url = new URL(value.includes('://') ? value : `https://${value}`)
    return ['http:', 'https:'].includes(url.protocol) && url.hostname.includes('.') && !url.username
      && !/\s/.test(value)
  } catch {
    return false
  }
}

export default function GenreEdit({ id }: { id: string }) {
  const isNew = id === 'new'
  const [form, setForm] = useState<Form | null>(isNew ? EMPTY : null)
  const [invalid, setInvalid] = useState<string[]>([])
  const [error, setError] = useState<{ field?: 'name' | 'length'; message: string } | null>(null)
  const [saving, setSaving] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [missing, setMissing] = useState(false)
  const [loadError, setLoadError] = useState(false)
  const [loadAttempt, setLoadAttempt] = useState(0)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const nameRef = useRef<HTMLInputElement>(null)
  const siteRef = useRef<HTMLTextAreaElement>(null)
  const maxRef = useRef<HTMLInputElement>(null)
  const initial = useRef<Form | null>(isNew ? EMPTY : null)
  const dirty = Boolean(form && initial.current && JSON.stringify(form) !== JSON.stringify(initial.current))
  useEffect(() => {
    if (!dirty) return
    const warn = (event: BeforeUnloadEvent) => { event.preventDefault() }
    window.addEventListener('beforeunload', warn)
    setNavigationGuard(() => confirm('保存していない変更を破棄しますか？'))
    return () => {
      window.removeEventListener('beforeunload', warn)
      setNavigationGuard(null)
    }
  }, [dirty])

  useEffect(() => {
    if (isNew) { setForm(EMPTY); return }
    api.genre(id).then((genre) => { const value = toForm(genre); initial.current = value; setForm(value) }).catch((error) => { if (error instanceof ApiError && error.status === 404) setMissing(true); else setLoadError(true) })
  }, [id, loadAttempt])

  const back = <nav className="top top-start"><Button type="button" onClick={() => { if (!dirty || confirm("保存していない変更を破棄しますか？")) navigate("/settings") }}>戻る</Button></nav>
  if (missing) return <main className="page settings">{back}<p>見つかりません。</p></main>
  if (loadError) return <main className="page settings">{back}<p>読み込めませんでした。</p><Button onClick={() => { setLoadError(false); setLoadAttempt((n) => n + 1) }}>再試行</Button></main>
  if (!form) return <main className="page settings" aria-busy="true">{back}</main>

  const set = (key: keyof Form) => (event: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) =>
    setForm({ ...form, [key]: event.target.value })

  async function save(event: React.FormEvent) {
    event.preventDefault()
    if (!form) return
    const sites = lines(form.sites)
    const wrong = sites.filter((site) => !validSite(site))
    setInvalid(wrong)
    const minChars = Number(form.min_chars)
    const maxChars = Number(form.max_chars)
    if (wrong.length) {
      setError(null)
      siteRef.current?.focus()
      return
    }
    if (maxChars < minChars + 20) {
      setError({ field: 'length', message: '文字数の幅は20字以上にしてください' })
      maxRef.current?.focus()
      return
    }
    const values = {
      name: form.name.trim(), latest: form.latest, locales: form.locales, translate: form.translate, sites, focus_terms: lines(form.focus_terms),
      discovery_percent: Number(form.discovery_percent), exclude_terms: lines(form.exclude_terms),
      min_chars: minChars, max_chars: maxChars, stance: form.stance,
    }
    setSaving(true)
    setError(null)
    try {
      if (isNew) await api.createGenre(values)
      else await api.saveGenre(id, values)
      navigate('/settings', true)
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) {
        setError({ field: 'name', message: '同じ名前のジャンルがあります' })
        nameRef.current?.focus()
      } else {
        setError({ message: '保存できません' })
      }
    } finally {
      setSaving(false)
    }
  }

  async function remove() {
    if (!form || saving || deleting) return
    setDeleting(true)
    try {
      await api.deleteGenre(id)
      navigate('/settings', true)
    } catch {
      setConfirmDelete(false)
      setError({ message: '削除できません' })
    } finally {
      setDeleting(false)
    }
  }

  const hasTerms = lines(form.focus_terms).length > 0

  return <main className="page settings">
    {back}
    <h1 className="page-title">{isNew ? "ジャンルを追加" : "ジャンルを編集"}</h1>
    <form onSubmit={save}>
      <div className="group">
        <label className="field"><span>名前</span>
          <TextInput ref={nameRef} className="text" required maxLength={40} value={form.name} onChange={set('name')} />
        </label>
        {error?.field === "name" && <p className="message" role="alert">{error.message}</p>}
        <RadioGroup name="latest" className="choice-group"><RadioGroup.Label>新しさ</RadioGroup.Label>
          <span className="segments">
            {FRESHNESS.map(([label, value]) => <label key={label} className={form.latest === value ? 'selected' : ''}>
              <Radio value={String(value)} name="latest" checked={form.latest === value}
                onChange={() => setForm({ ...form, latest: value })} />
              {label}
            </label>)}
          </span>
        </RadioGroup>
        <CheckboxGroup className="choice-group">
          <CheckboxGroup.Label>国と地域・言語</CheckboxGroup.Label>
          <span className="checks">
            {LOCALES.map(([value, label]) => <label key={value}>
              <Checkbox checked={form.locales.includes(value)}
                onChange={() => setForm({ ...form, locales: toggleLocale(form.locales, value) })} />
              {label}
            </label>)}
          </span>
        </CheckboxGroup>
        <RadioGroup name="translate" className="choice-group"><RadioGroup.Label>翻訳</RadioGroup.Label>
          <span className="segments">
            {TRANSLATE.map(([label, value]) => <label key={label} className={form.translate === value ? 'selected' : ''}>
              <Radio value={String(value)} name="translate" checked={form.translate === value}
                onChange={() => setForm({ ...form, translate: value })} />
              {label}
            </label>)}
          </span>
        </RadioGroup>
      </div>

      <div className="group">
        <FormControl className="group-control"><FormControl.Label>サイト</FormControl.Label>
          <Textarea ref={siteRef} rows={Math.max(2, lines(form.sites).length + 1)} spellCheck={false} wrap="off"
            value={form.sites} onChange={set('sites')} aria-invalid={invalid.length > 0} />
          <FormControl.Caption>1行に1件入力してください（例：example.com）。</FormControl.Caption>
          {invalid.length > 0 && <FormControl.Validation variant="error">読み取れません: {invalid.join("、")}</FormControl.Validation>}
        </FormControl>
      </div>

      <div className="group">
        <FormControl className="group-control"><FormControl.Label>注目する語</FormControl.Label>
          <Textarea rows={3} value={form.focus_terms} onChange={set('focus_terms')} />
          <FormControl.Caption>1行に1件入力してください。</FormControl.Caption>
        </FormControl>
        {hasTerms && <label className="field"><span>注目語以外</span>
          <span className="with-unit"><TextInput type="number" min={0} max={100} required value={form.discovery_percent}
            onChange={set('discovery_percent')} />%</span>
        </label>}
        <FormControl className="group-control"><FormControl.Label>除外する語</FormControl.Label>
          <Textarea rows={2} value={form.exclude_terms} onChange={set('exclude_terms')} />
          <FormControl.Caption>1行に1件入力してください。</FormControl.Caption>
        </FormControl>
      </div>

      <div className="group">
        <div className="field"><span>文字数</span>
          <span className="with-unit">
            <TextInput type="number" min={40} max={800} required aria-label="最小の文字数" value={form.min_chars}
              onChange={set('min_chars')} />〜
            <TextInput ref={maxRef} type="number" min={40} max={800} required aria-label="最大の文字数" value={form.max_chars}
              onChange={set('max_chars')} />字
          </span>
        </div>
        {error?.field === "length" && <p className="message" role="alert">{error.message}</p>}
        <RadioGroup name="stance" className="choice-group"><RadioGroup.Label>事実と解釈</RadioGroup.Label>
          <span className="segments">
            {STANCES.map((label, index) => <label key={label} className={form.stance === index ? 'selected' : ''}>
              <Radio value={String(index)} name="stance" checked={form.stance === index}
                onChange={() => setForm({ ...form, stance: index })} />
              {label}
            </label>)}
          </span>
        </RadioGroup>
      </div>

      <div className="actions">
        <Button type="submit" variant="primary" disabled={saving || deleting}>{saving ? '保存中…' : '保存'}</Button>
        <span className="message" role="status">{!error?.field && error?.message}</span>
        {!isNew && <Button type="button" variant="danger" className="danger" disabled={saving || deleting} onClick={() => setConfirmDelete(true)}>削除</Button>}
      </div>
    </form>
    {confirmDelete && <ConfirmationDialog
      title={`「${form.name}」を削除しますか？`}
      confirmButtonContent="削除" cancelButtonContent="キャンセル" confirmButtonType="danger"
      confirmButtonLoading={deleting}
      onClose={(gesture) => {
        if (deleting) return
        if (gesture === 'confirm') void remove()
        else setConfirmDelete(false)
      }}>
      ジャンルの設定を削除します。
    </ConfirmationDialog>}
  </main>
}
