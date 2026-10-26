import { CheckIcon, MonitorIcon, MoonIcon, SunIcon } from "lucide-react"

import { useTheme } from "@/components/theme-provider"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { cn } from "@/lib/utils"

const OPTIONS = [
  { value: "light", label: "Light", Icon: SunIcon },
  { value: "dark", label: "Dark", Icon: MoonIcon },
  { value: "system", label: "System", Icon: MonitorIcon },
] as const

/**
 * Light, dark, or follow the computer. The button shows a sun or a moon for the
 * theme actually on screen (CSS decides, from the `dark` class, so there is no
 * flash before React knows), and the menu says which choice is saved.
 */
export function ThemeToggle({
  variant = "ghost",
  className,
}: {
  variant?: "ghost" | "outline"
  className?: string
}) {
  const { theme, setTheme } = useTheme()

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          variant={variant}
          size="icon"
          aria-label="Change theme"
          className={cn("relative", className)}
        >
          <SunIcon className="size-4 scale-100 rotate-0 transition-transform duration-300 ease-(--ease-soft) dark:scale-0 dark:-rotate-90" />
          <MoonIcon className="absolute size-4 scale-0 rotate-90 transition-transform duration-300 ease-(--ease-soft) dark:scale-100 dark:rotate-0" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-36">
        {OPTIONS.map(({ value, label, Icon }) => (
          <DropdownMenuItem key={value} onSelect={() => setTheme(value)}>
            <Icon />
            {label}
            {theme === value ? (
              <CheckIcon className="ml-auto text-brand-strong" aria-label="Selected" />
            ) : null}
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
