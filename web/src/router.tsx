import { useEffect, useState } from 'react'
import { LinkButton } from '@primer/react'

const EVENT = 'yourfeed:navigate'

export function navigate(path: string, replace = false) {
  if (replace) history.replaceState({}, '', path)
  else history.pushState({}, '', path)
  window.dispatchEvent(new Event(EVENT))
}

export function usePath() {
  const [path, setPath] = useState(location.pathname)
  useEffect(() => {
    const update = () => setPath(location.pathname)
    window.addEventListener('popstate', update)
    window.addEventListener(EVENT, update)
    return () => {
      window.removeEventListener('popstate', update)
      window.removeEventListener(EVENT, update)
    }
  }, [])
  return path
}

export function Link({ to, className, children, button = false }: {
  to: string; className?: string; children: React.ReactNode; button?: boolean
}) {
  const onClick = (event: React.MouseEvent<HTMLElement>) => {
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.button !== 0) return
    event.preventDefault()
    navigate(to)
  }
  if (button) return <LinkButton href={to} className={className} onClick={onClick}>{children}</LinkButton>
  return <a href={to} className={className} onClick={onClick}>{children}</a>
}
