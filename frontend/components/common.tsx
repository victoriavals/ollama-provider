"use client";

import {
  useEffect,
  useId,
  useRef,
  useState,
  type ReactNode,
  type ComponentProps,
} from "react";
import { createPortal } from "react-dom";
import { motion } from "motion/react";
import {
  AlertCircle,
  Check,
  CheckCircle2,
  Circle,
  Copy,
  LoaderCircle,
  RefreshCw,
  TriangleAlert,
  X,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardHeader,
  CardTitle,
  CardDescription,
  CardContent,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import {
  Field,
  FieldLabel,
  FieldError,
  FieldDescription,
} from "@/components/ui/field";
import { Alert, AlertTitle, AlertDescription } from "@/components/ui/alert";
import {
  Empty,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
  EmptyDescription,
  EmptyContent,
} from "@/components/ui/empty";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Dialog,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog";
import {
  Sheet,
  SheetHeader,
  SheetTitle,
  SheetDescription,
} from "@/components/ui/sheet";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { messageFor } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { HealthState } from "@/lib/types";
import { usePreferences } from "./providers";

export { Button };
export function Panel({
  title,
  description,
  action,
  children,
  className,
}: {
  title: string;
  description?: string;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <Card className={cn("panel", className)}>
      <CardHeader className="panel-heading">
        <div>
          <CardTitle>
            <h2>{title}</h2>
          </CardTitle>
          {description && <CardDescription>{description}</CardDescription>}
        </div>
        {action}
      </CardHeader>
      <CardContent>{children}</CardContent>
    </Card>
  );
}
export function StatusPill({
  kind,
  children,
}: {
  kind: HealthState;
  children?: ReactNode;
}) {
  const Icon =
    kind === "ok"
      ? CheckCircle2
      : kind === "warn"
        ? TriangleAlert
        : kind === "down"
          ? AlertCircle
          : Circle;
  return (
    <Badge variant="outline" className={cn("status", `status-${kind}`)}>
      <Icon aria-hidden="true" />
      {children ||
        {
          ok: "Normal",
          warn: "Perlu dicek",
          down: "Bermasalah",
          absent: "Siaga",
        }[kind]}
    </Badge>
  );
}
export function RequestStatus({ code }: { code: number }) {
  return (
    <StatusPill
      kind={
        code < 400
          ? "ok"
          : code === 499
            ? "absent"
            : code < 500
              ? "warn"
              : "down"
      }
    >
      {code}
      {code === 429 ? " dibatasi" : code === 499 ? " client putus" : ""}
    </StatusPill>
  );
}
export function Failure({
  error,
  retry,
  title = "Data belum dapat diperbarui",
}: {
  error: unknown;
  retry?: () => void;
  title?: string;
}) {
  if (!error) return null;
  return (
    <Alert variant="destructive" className="feedback">
      <AlertCircle aria-hidden="true" />
      <AlertTitle>{title}</AlertTitle>
      <AlertDescription>
        <p>{typeof error === "string" ? error : messageFor(error)}</p>
        {retry && (
          <Button variant="outline" onClick={retry}>
            <RefreshCw data-icon="inline-start" />
            Coba lagi
          </Button>
        )}
      </AlertDescription>
    </Alert>
  );
}
export function Loading({ label = "Memuat data" }: { label?: string }) {
  return (
    <div className="loading-grid" role="status" aria-label={label}>
      <span className="sr-only">{label}</span>
      <Skeleton className="loading-block" />
      <Skeleton className="loading-block" />
      <Skeleton className="loading-block" />
    </div>
  );
}
export function NoData({
  title,
  description,
  action,
}: {
  title: string;
  description: string;
  action?: ReactNode;
}) {
  return (
    <Empty>
      <EmptyHeader>
        <EmptyMedia variant="icon">
          <Circle />
        </EmptyMedia>
        <EmptyTitle>{title}</EmptyTitle>
        <EmptyDescription>{description}</EmptyDescription>
      </EmptyHeader>
      {action && <EmptyContent>{action}</EmptyContent>}
    </Empty>
  );
}
export function CopyButton({
  text,
  label = "Salin",
  ...props
}: { text: string; label?: string } & ComponentProps<typeof Button>) {
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState(false);
  async function copy() {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setError(false);
    } catch {
      setError(true);
    }
  }
  return (
    <span className="copy-control">
      <Button variant="outline" {...props} onClick={() => void copy()}>
        {copied ? (
          <Check data-icon="inline-start" />
        ) : (
          <Copy data-icon="inline-start" />
        )}
        {copied ? "Tersalin" : label}
      </Button>
      <span role="status" className={error ? "copy-error" : "sr-only"}>
        {error
          ? "Pilih teksnya, lalu tekan Ctrl+C untuk menyalin."
          : copied
            ? `${label} berhasil disalin`
            : ""}
      </span>
    </span>
  );
}
export function SubmitButton({
  pending,
  children,
  ...props
}: ComponentProps<typeof Button> & { pending: boolean }) {
  return (
    <Button
      {...props}
      type="submit"
      disabled={pending || props.disabled}
      aria-busy={pending}
    >
      {pending && <LoaderCircle className="spin" data-icon="inline-start" />}
      {pending ? "Memproses…" : children}
    </Button>
  );
}
export function TextField({
  label,
  hint,
  error,
  ...props
}: ComponentProps<typeof Input> & {
  label: string;
  hint?: string;
  error?: string;
}) {
  const errorId = props.id ? `${props.id}-error` : undefined;
  return (
    <Field data-invalid={!!error}>
      <FieldLabel htmlFor={props.id}>{label}</FieldLabel>
      <Input
        {...props}
        aria-invalid={!!error}
        aria-describedby={
          error ? errorId : hint && props.id ? `${props.id}-hint` : undefined
        }
      />
      {hint && (
        <FieldDescription id={`${props.id}-hint`}>{hint}</FieldDescription>
      )}
      {error && <FieldError id={errorId}>{error}</FieldError>}
    </Field>
  );
}
export function Overlay({
  open,
  onOpenChange,
  title,
  description,
  children,
  side = false,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description: string;
  children: ReactNode;
  side?: boolean;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const id = useId();
  const { motionEnabled } = usePreferences();
  useEffect(() => {
    const dialog = ref.current;
    if (!open || !dialog) return;
    const opener =
      document.activeElement instanceof HTMLElement
        ? document.activeElement
        : null;
    dialog.showModal();
    return () => {
      if (dialog.open) dialog.close();
      if (opener?.isConnected) opener.focus({ preventScroll: true });
    };
  }, [open]);
  if (!open || typeof document === "undefined") return null;
  const body = (
    <motion.div
      className="overlay-body"
      initial={motionEnabled ? { opacity: 0, y: 6 } : false}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.18 }}
    >
      {children}
    </motion.div>
  );
  // Native modal focus/inert behavior avoids runtime-injected scroll-lock CSS under strict CSP.
  const content = (
    <dialog
      ref={ref}
      aria-labelledby={`${id}-title`}
      aria-describedby={`${id}-description`}
      data-slot={side ? "sheet-content" : "dialog-content"}
      className={cn("native-overlay", side ? "detail-sheet" : "detail-dialog")}
      onCancel={(event) => {
        event.preventDefault();
        onOpenChange(false);
      }}
      onClick={(event) => {
        if (event.target === event.currentTarget) {
          const rect = event.currentTarget.getBoundingClientRect();
          if (
            event.clientX < rect.left ||
            event.clientX > rect.right ||
            event.clientY < rect.top ||
            event.clientY > rect.bottom
          )
            onOpenChange(false);
        }
      }}
    >
      {side ? (
        <SheetHeader>
          <SheetTitle id={`${id}-title`}>{title}</SheetTitle>
          <SheetDescription id={`${id}-description`}>
            {description}
          </SheetDescription>
        </SheetHeader>
      ) : (
        <DialogHeader>
          <DialogTitle id={`${id}-title`}>{title}</DialogTitle>
          <DialogDescription id={`${id}-description`}>
            {description}
          </DialogDescription>
        </DialogHeader>
      )}
      {body}
      <Button
        variant="ghost"
        size="icon"
        className="native-overlay-close"
        type="button"
        aria-label="Tutup panel"
        onClick={() => onOpenChange(false)}
      >
        <X />
      </Button>
    </dialog>
  );
  return createPortal(
    side ? (
      <Sheet open modal={false}>
        {content}
      </Sheet>
    ) : (
      <Dialog open modal={false}>
        {content}
      </Dialog>
    ),
    document.body,
  );
}
export function Choices({
  value,
  onChange,
  options,
  label,
}: {
  value: string;
  onChange: (value: string) => void;
  options: { value: string; label: string }[];
  label: string;
}) {
  return (
    <ToggleGroup
      type="single"
      variant="outline"
      value={value}
      onValueChange={(next) => {
        if (next) onChange(next);
      }}
      aria-label={label}
      className="choices"
    >
      {options.map((option) => (
        <ToggleGroupItem key={option.value} value={option.value}>
          {option.label}
        </ToggleGroupItem>
      ))}
    </ToggleGroup>
  );
}
