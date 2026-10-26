import { useState } from "react"
import { toast } from "sonner"

import { AccountDialog } from "@/components/account-dialog"
import { useAuth } from "@/contexts/auth-context"
import { userLabel } from "@/lib/api"
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import {
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  useSidebar,
} from "@/components/ui/sidebar"
import {
  EllipsisVerticalIcon,
  CircleUserRoundIcon,
  LogOutIcon,
  SettingsIcon,
  ShieldIcon,
} from "lucide-react"

function getInitials(name: string): string {
  const parts = name.split(/[\s._-]+/).filter(Boolean)
  if (parts.length === 0) {
    return "?"
  }
  if (parts.length === 1) {
    return parts[0].slice(0, 2).toUpperCase()
  }
  return `${parts[0][0]}${parts[1][0]}`.toUpperCase()
}

export function NavUser() {
  const { isMobile } = useSidebar()
  const { user, logout, availableRoles, setRole } = useAuth()
  const [switching, setSwitching] = useState(false)
  const [accountOpen, setAccountOpen] = useState(false)

  if (!user) {
    return null
  }

  const displayName = userLabel(user)
  const subtitle = user.role ?? user.email ?? user.username
  const initials = getInitials(displayName)

  async function handleRoleChange(role: string) {
    if (!role || role === user?.role || switching) {
      return
    }
    setSwitching(true)
    try {
      await setRole(role)
      toast.success(`Switched to ${role}`)
    } catch (error) {
      let message =
        error instanceof Error ? error.message : "Could not switch role"
      try {
        const parsed = JSON.parse(message) as { detail?: string }
        if (parsed.detail) message = parsed.detail
      } catch {
        // keep raw message
      }
      toast.error(message)
    } finally {
      setSwitching(false)
    }
  }

  return (
    <SidebarMenu>
      <SidebarMenuItem>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <SidebarMenuButton
              size="lg"
              className="data-[state=open]:bg-sidebar-accent data-[state=open]:text-sidebar-accent-foreground"
            >
              <Avatar className="h-8 w-8 rounded-lg grayscale">
                <AvatarImage src="" alt={displayName} />
                <AvatarFallback className="rounded-lg">{initials}</AvatarFallback>
              </Avatar>
              <div className="grid flex-1 text-left text-sm leading-tight">
                <span className="truncate font-medium">{displayName}</span>
                <span className="truncate text-xs text-muted-foreground">
                  {subtitle}
                </span>
              </div>
              <EllipsisVerticalIcon className="ml-auto size-4" />
            </SidebarMenuButton>
          </DropdownMenuTrigger>
          <DropdownMenuContent
            className="w-(--radix-dropdown-menu-trigger-width) min-w-56 rounded-lg"
            side={isMobile ? "bottom" : "right"}
            align="end"
            sideOffset={4}
          >
            <DropdownMenuLabel className="p-0 font-normal">
              <div className="flex items-center gap-2 px-1 py-1.5 text-left text-sm">
                <Avatar className="h-8 w-8 rounded-lg">
                  <AvatarImage src="" alt={displayName} />
                  <AvatarFallback className="rounded-lg">{initials}</AvatarFallback>
                </Avatar>
                <div className="grid flex-1 text-left text-sm leading-tight">
                  <span className="truncate font-medium">{displayName}</span>
                  <span className="truncate text-xs text-muted-foreground">
                    {subtitle}
                  </span>
                </div>
              </div>
            </DropdownMenuLabel>
            <DropdownMenuSeparator />
            <DropdownMenuGroup>
              <DropdownMenuItem onSelect={() => setAccountOpen(true)}>
                <CircleUserRoundIcon />
                Account
              </DropdownMenuItem>
              {user.is_admin ? (
                <DropdownMenuItem asChild>
                  <a href="/admin/users" target="_blank" rel="noopener noreferrer">
                    <SettingsIcon />
                    Administration
                  </a>
                </DropdownMenuItem>
              ) : null}
              {availableRoles.length > 0 && user.snowflake_linked ? (
                <DropdownMenuSub>
                  <DropdownMenuSubTrigger disabled={switching}>
                    <ShieldIcon />
                    Role
                  </DropdownMenuSubTrigger>
                  <DropdownMenuSubContent className="min-w-48">
                    <DropdownMenuRadioGroup
                      value={user.role ?? undefined}
                      onValueChange={(value) => void handleRoleChange(value)}
                    >
                      {availableRoles.map((role) => (
                        <DropdownMenuRadioItem key={role} value={role}>
                          {role}
                        </DropdownMenuRadioItem>
                      ))}
                    </DropdownMenuRadioGroup>
                  </DropdownMenuSubContent>
                </DropdownMenuSub>
              ) : null}
            </DropdownMenuGroup>
            <DropdownMenuSeparator />
            <DropdownMenuItem onClick={() => void logout()}>
              <LogOutIcon />
              Log out
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
        <AccountDialog open={accountOpen} onOpenChange={setAccountOpen} />
      </SidebarMenuItem>
    </SidebarMenu>
  )
}
