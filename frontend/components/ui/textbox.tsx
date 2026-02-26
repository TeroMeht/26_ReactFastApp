'use client'

import * as React from "react"
import { cn } from "@/lib/utils"

/* -------------------------------------------------------------------------------------------------
 * Root
 * -----------------------------------------------------------------------------------------------*/

type TxtFilesContextType = {
  expanded: Record<string, boolean>
  toggle: (name: string) => void
  editable?: boolean
  savingFile?: string | null
}

const TxtFilesContext = React.createContext<TxtFilesContextType | null>(null)

interface TxtFilesProps
  extends Omit<
    React.HTMLAttributes<HTMLDivElement>,
    "onToggle"
  > {
  expanded: Record<string, boolean>
  onToggle: (filename: string) => void
  editable?: boolean
  savingFile?: string | null
}

const TxtFiles = React.forwardRef<HTMLDivElement, TxtFilesProps>(
  ({ className, expanded, onToggle, editable, savingFile, ...props }, ref) => {
    return (
      <TxtFilesContext.Provider
        value={{
          expanded,
          toggle: onToggle,
          editable,
          savingFile,
        }}
      >
        <div
          ref={ref}
          className={cn("space-y-4", className)}
          {...props}
        />
      </TxtFilesContext.Provider>
    )
  }
)
TxtFiles.displayName = "TxtFiles"

/* -------------------------------------------------------------------------------------------------
 * Item
 * -----------------------------------------------------------------------------------------------*/

interface TxtFilesItemProps
  extends React.HTMLAttributes<HTMLDivElement> {
  name: string
}

const TxtFilesItem = React.forwardRef<
  HTMLDivElement,
  TxtFilesItemProps
>(({ className, ...props }, ref) => (
  <div
    ref={ref}
    className={cn(
      "border rounded-xl bg-background shadow-sm overflow-hidden",
      className
    )}
    {...props}
  />
))
TxtFilesItem.displayName = "TxtFilesItem"

/* -------------------------------------------------------------------------------------------------
 * Trigger
 * -----------------------------------------------------------------------------------------------*/

interface TxtFilesTriggerProps
  extends React.HTMLAttributes<HTMLDivElement> {
  name: string
}

const TxtFilesTrigger = React.forwardRef<
  HTMLDivElement,
  TxtFilesTriggerProps
>(({ className, name, children, ...props }, ref) => {
  const ctx = React.useContext(TxtFilesContext)
  if (!ctx) throw new Error("TxtFilesTrigger must be used inside TxtFiles")

  const isOpen = ctx.expanded[name]

  return (
    <div
      ref={ref}
      onClick={() => ctx.toggle(name)}
      className={cn(
        "flex justify-between items-center px-4 py-3 cursor-pointer hover:bg-muted/50",
        className
      )}
      {...props}
    >
      <span className="text-sm font-medium">{children}</span>
      <span className="text-xs text-muted-foreground">
        {isOpen ? "▼" : "▶"}
      </span>
    </div>
  )
})
TxtFilesTrigger.displayName = "TxtFilesTrigger"

/* -------------------------------------------------------------------------------------------------
 * Content
 * -----------------------------------------------------------------------------------------------*/

interface TxtFilesContentProps
  extends React.HTMLAttributes<HTMLDivElement> {
  name: string
}

const TxtFilesContent = React.forwardRef<
  HTMLDivElement,
  TxtFilesContentProps
>(({ className, name, ...props }, ref) => {
  const ctx = React.useContext(TxtFilesContext)
  if (!ctx) throw new Error("TxtFilesContent must be used inside TxtFiles")

  if (!ctx.expanded[name]) return null

  return (
    <div
      ref={ref}
      className={cn("border-t p-4 bg-muted/30", className)}
      {...props}
    />
  )
})
TxtFilesContent.displayName = "TxtFilesContent"

/* -------------------------------------------------------------------------------------------------
 * Textarea
 * -----------------------------------------------------------------------------------------------*/

const TxtFilesTextarea = React.forwardRef<
  HTMLTextAreaElement,
  React.TextareaHTMLAttributes<HTMLTextAreaElement>
>(({ className, ...props }, ref) => (
  <textarea
    ref={ref}
    className={cn(
      "w-full h-40 p-3 border rounded-md font-mono text-sm bg-background",
      className
    )}
    {...props}
  />
))
TxtFilesTextarea.displayName = "TxtFilesTextarea"

/* -------------------------------------------------------------------------------------------------
 * Save Button
 * -----------------------------------------------------------------------------------------------*/

interface TxtFilesSaveProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  filename: string
}

const TxtFilesSave = React.forwardRef<
  HTMLButtonElement,
  TxtFilesSaveProps
>(({ className, filename, children, ...props }, ref) => {
  const ctx = React.useContext(TxtFilesContext)
  if (!ctx) throw new Error("TxtFilesSave must be used inside TxtFiles")

  const isSaving = ctx.savingFile === filename

  return (
    <button
      ref={ref}
      disabled={isSaving}
      className={cn(
        "mt-3 px-4 py-1.5 text-sm bg-primary text-primary-foreground rounded-md hover:opacity-90 disabled:opacity-50",
        className
      )}
      {...props}
    >
      {isSaving ? "Saving..." : children ?? "Save"}
    </button>
  )
})
TxtFilesSave.displayName = "TxtFilesSave"

/* -------------------------------------------------------------------------------------------------
 * Exports
 * -----------------------------------------------------------------------------------------------*/

export {
  TxtFiles,
  TxtFilesItem,
  TxtFilesTrigger,
  TxtFilesContent,
  TxtFilesTextarea,
  TxtFilesSave,
}