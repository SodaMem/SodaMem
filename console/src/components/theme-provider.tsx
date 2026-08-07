import { ThemeProvider as NextThemesProvider } from 'next-themes'
import type * as React from 'react'

/** Matches the inline pre-paint script in index.html (same storageKey), so
 * there is no light->dark flash on load and no divergence between the two. */
export function ThemeProvider({ children }: { children: React.ReactNode }) {
  return (
    <NextThemesProvider
      attribute="class"
      defaultTheme="system"
      enableSystem
      storageKey="sodamem-console-theme"
      disableTransitionOnChange
    >
      {children}
    </NextThemesProvider>
  )
}
