import { File as FileIcon, FileText, FileType, X } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import * as React from "react";

import type { ResumeResponse } from "@matchlayer/shared-types";

import { Button } from "@/components/ui/button";
import { cn, formatBytes } from "@/lib/utils";

/**
 * Props for {@link FilePreviewCard} (design Section 7.2 "FilePreviewCard").
 *
 * Accepts either of two shapes so a caller can hand the card data straight
 * from the API contract **or** from local client state before a server
 * response exists:
 *
 *   - `Pick<ResumeResponse, …>` — the canonical, generated
 *     `@matchlayer/shared-types` fields (`original_filename`, `byte_size`,
 *     `content_type`). Components never redefine these (Req 16.3, 20.1, 21.11).
 *   - A camelCase `{ filename, byteSize, contentType }` — for the brief window
 *     where the `UploadWidget` has a local `File` selected but has not yet
 *     received the `ResumeResponse` back from `POST /api/v1/resumes`.
 *
 * The two shapes are discriminated at runtime by the presence of the
 * snake_case `original_filename` key, then normalized to one internal shape.
 */
export interface FilePreviewCardProps {
  /** File metadata, from the API `ResumeResponse` or local pre-upload state. */
  resume:
    | Pick<ResumeResponse, "original_filename" | "byte_size" | "content_type">
    | { filename: string; byteSize: number; contentType: string };
  /** Invoked when the remove button is activated (Req 9.10). */
  onRemove: () => void;
  /** Composition hook — extends (never replaces) the base layout. */
  className?: string;
}

/** Normalized internal view of the union prop. */
interface NormalizedFile {
  filename: string;
  byteSize: number;
  contentType: string;
}

/** The two media types accepted by the upload flow (Req 9.1). */
const PDF_CONTENT_TYPE = "application/pdf";
const DOCX_CONTENT_TYPE =
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document";

/**
 * Collapse the union prop into one `{ filename, byteSize, contentType }` view.
 *
 * Discriminates on the snake_case `original_filename` key — present only on the
 * generated `ResumeResponse` shape — so the camelCase pre-upload shape is the
 * fallthrough.
 */
function normalize(resume: FilePreviewCardProps["resume"]): NormalizedFile {
  if ("original_filename" in resume) {
    return {
      filename: resume.original_filename,
      byteSize: resume.byte_size,
      contentType: resume.content_type,
    };
  }
  return {
    filename: resume.filename,
    byteSize: resume.byteSize,
    contentType: resume.contentType,
  };
}

/**
 * Map the validated `content_type` to a Lucide glyph and a short visible label
 * (Req 9.3, 16.3).
 *
 * PDF and DOCX get distinct icons so the file type is recognizable at a glance;
 * any other value (which the upload validation should already have rejected)
 * falls back to a generic file glyph rather than rendering nothing.
 */
function fileTypeMeta(contentType: string): {
  Icon: LucideIcon;
  label: string;
} {
  if (contentType === PDF_CONTENT_TYPE) {
    return { Icon: FileText, label: "PDF" };
  }
  if (contentType === DOCX_CONTENT_TYPE) {
    return { Icon: FileType, label: "DOCX" };
  }
  return { Icon: FileIcon, label: "File" };
}

/**
 * FilePreviewCard — the selected-file summary shown on the Upload_Page once a
 * **valid** file is chosen (design Section 7.2; Req 9.3, 9.10, 16.3).
 *
 * Renders the filename, a human-readable size (via {@link formatBytes}), a
 * Lucide file-type icon derived from `content_type`, and a remove button. It is
 * purely presentational — the parent `UploadWidget` decides *when* to render it
 * (only for valid files — never for an invalid type / oversize file, Req 9.4)
 * and supplies the `onRemove` handler that clears the selection and disables
 * the "Analyze Match" button (Req 9.10).
 *
 * ## Layout
 * A token-styled card (`bg-bg-elevated` + `border-border-strong`,
 * `rounded-card`, `shadow-resting`) laid out as: type-icon well · filename +
 * `"<TYPE> · <size>"` subline · remove button. The filename is allowed to
 * `truncate` (with a `title` for the full value) so a long name never forces
 * horizontal scroll, and the size uses `tabular-nums` per the design type scale.
 *
 * ## Accessibility
 *   - The remove control is the shared {@link Button} (`ghost`/`icon`), so it
 *     inherits the **2px branded focus ring** (`focus-visible:ring-2
 *     ring-brand` + offset). As an icon-only button it carries an explicit
 *     `aria-label` naming the file it removes; the `X` glyph is `aria-hidden`.
 *   - The type icon is decorative (`aria-hidden`) — the type is also conveyed
 *     by the visible `"PDF"`/`"DOCX"` text, so meaning never rests on the icon
 *     alone.
 *
 * No client-only features (no state/effects); `onRemove` is a passed-in
 * handler, so the component needs no `"use client"` directive and renders in a
 * Server or Client context alike.
 */
export function FilePreviewCard({
  resume,
  onRemove,
  className,
}: FilePreviewCardProps): React.JSX.Element {
  const { filename, byteSize, contentType } = normalize(resume);
  const { Icon, label } = fileTypeMeta(contentType);

  return (
    <div
      data-slot="file-preview-card"
      className={cn(
        "flex items-center gap-3 rounded-card border border-border-strong bg-bg-elevated p-4 shadow-resting",
        className,
      )}
    >
      <span
        aria-hidden="true"
        className="flex size-10 shrink-0 items-center justify-center rounded-card border border-border bg-bg text-text-muted"
      >
        <Icon className="size-5" />
      </span>

      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-medium text-text" title={filename}>
          {filename}
        </p>
        <p className="text-xs tabular-nums text-text-muted">
          {label} · {formatBytes(byteSize)}
        </p>
      </div>

      <Button
        type="button"
        variant="ghost"
        size="icon"
        onClick={onRemove}
        aria-label={`Remove ${filename}`}
        className="shrink-0 text-text-muted hover:text-text"
      >
        <X aria-hidden="true" />
      </Button>
    </div>
  );
}
