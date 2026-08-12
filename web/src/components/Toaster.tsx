import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { Undo2, X } from "lucide-react";

/**
 * Undo toasts.
 *
 * Destructive actions here apply immediately and offer undo, rather than
 * asking "are you sure?" first. Confirmation dialogs are the right pattern for
 * multi-user systems where a mistake affects other people; for a single-user
 * local tool they tax every correct action to guard against the rare wrong
 * one. Undo taxes only the mistake.
 */
export interface Toast {
  id: string;
  message: string;
  onUndo?: () => Promise<void> | void;
  /** Visible countdown, in ms. The server token outlives this deliberately. */
  duration?: number;
  tone?: "default" | "error";
}

interface ToastContextValue {
  push: (t: Omit<Toast, "id">) => string;
  dismiss: (id: string) => void;
}

const ToastContext = createContext<ToastContextValue | null>(null);

const DEFAULT_DURATION = 5000;

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const timers = useRef(new Map<string, number>());

  const dismiss = useCallback((id: string) => {
    setToasts((ts) => ts.filter((t) => t.id !== id));
    const timer = timers.current.get(id);
    if (timer) {
      window.clearTimeout(timer);
      timers.current.delete(id);
    }
  }, []);

  const push = useCallback(
    (t: Omit<Toast, "id">) => {
      const id = Math.random().toString(36).slice(2);
      setToasts((ts) => [...ts, { ...t, id }]);
      const timer = window.setTimeout(() => dismiss(id), t.duration ?? DEFAULT_DURATION);
      timers.current.set(id, timer);
      return id;
    },
    [dismiss],
  );

  useEffect(() => {
    const map = timers.current;
    return () => map.forEach((t) => window.clearTimeout(t));
  }, []);

  const value = useMemo(() => ({ push, dismiss }), [push, dismiss]);

  return (
    <ToastContext.Provider value={value}>
      {children}
      {/* aria-live so the outcome is announced without stealing focus —
          a toast that grabs focus interrupts whatever you were doing next. */}
      <div className="toaster" role="status" aria-live="polite" aria-atomic="false">
        {toasts.map((t) => (
          <ToastRow key={t.id} toast={t} onDismiss={() => dismiss(t.id)} />
        ))}
      </div>
    </ToastContext.Provider>
  );
}

function ToastRow({ toast, onDismiss }: { toast: Toast; onDismiss: () => void }) {
  const [busy, setBusy] = useState(false);
  return (
    <div className={`toast toast-${toast.tone ?? "default"}`}>
      <span className="toast-message">{toast.message}</span>
      {toast.onUndo && (
        <button
          type="button"
          className="toast-undo"
          disabled={busy}
          onClick={async () => {
            setBusy(true);
            try {
              await toast.onUndo?.();
            } finally {
              onDismiss();
            }
          }}
        >
          <Undo2 size={13} aria-hidden />
          Undo
        </button>
      )}
      <button type="button" className="toast-close" onClick={onDismiss} aria-label="Dismiss">
        <X size={13} aria-hidden />
      </button>
    </div>
  );
}

export function useToast(): ToastContextValue {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error("useToast must be used inside <ToastProvider>");
  return ctx;
}
