import { useState } from 'react'
import { ConfirmationDialog } from '@primer/react'
import { api, ApiError } from './api'

export default function ClearDialog({ onClose }: { onClose: () => void }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  async function clear() {
    setBusy(true)
    setError('')
    try {
      await api.clearTimeline()
      window.location.reload()
    } catch (cause) {
      setError(cause instanceof ApiError && cause.status === 409
        ? '更新中はクリアできません。更新が終わってからお試しください。'
        : 'クリアできませんでした。もう一度お試しください。')
      setBusy(false)
    }
  }

  return <ConfirmationDialog title="キャッシュをクリアしますか？" confirmButtonContent="クリアする"
    cancelButtonContent="キャンセル" confirmButtonType="danger" confirmButtonLoading={busy}
    onClose={(gesture) => { if (busy) return; if (gesture === 'confirm') void clear(); else onClose() }}>
    <p>記事候補、要約、表示中のカードを削除します。設定とジャンルは残ります。</p>
    <p>次の更新で同じ記事が再び表示されることがあります。</p>
    {error && <p className="message" role="alert">{error}</p>}
  </ConfirmationDialog>
}
