import { createContext, useContext, useEffect, useState } from 'react'
import { BaseStyles } from '@primer/react'
import { ThemeProvider } from '@primer/react/next'
import '@primer/primitives/dist/css/functional/themes/light.css'
import '@primer/primitives/dist/css/functional/themes/dark.css'

type Theme = 'auto' | 'light' | 'dark'
const ThemeContext = createContext<{ theme: Theme; setTheme: (theme: Theme) => void }>({
  theme: 'auto', setTheme: () => {},
})

export function useAppearance() { return useContext(ThemeContext) }

export function AppearanceProvider({ children }: { children: React.ReactNode }) {
  const [theme, setTheme] = useState<Theme>(() => {
    try {
      const saved = localStorage.getItem('yourfeed:theme')
      return saved === 'light' || saved === 'dark' ? saved : 'auto'
    } catch { return 'auto' }
  })

  useEffect(() => {
    try { localStorage.setItem('yourfeed:theme', theme) } catch { /* Keep the selection for this session. */ }
    const root = document.documentElement
    root.dataset.colorMode = theme
    root.dataset.lightTheme = 'light'
    root.dataset.darkTheme = 'dark'
    const media = window.matchMedia('(prefers-color-scheme: dark)')
    const update = () => {
      const dark = theme === 'dark' || (theme === 'auto' && media.matches)
      root.style.colorScheme = dark ? 'dark' : 'light'
      document.querySelector('meta[name="theme-color"]')?.setAttribute('content', dark ? '#0d1117' : '#ffffff')
    }
    update()
    media.addEventListener('change', update)
    return () => media.removeEventListener('change', update)
  }, [theme])

  return <ThemeContext.Provider value={{ theme, setTheme }}>
    <ThemeProvider colorMode={theme} dayScheme="light" nightScheme="dark"><BaseStyles>{children}</BaseStyles></ThemeProvider>
  </ThemeContext.Provider>
}
