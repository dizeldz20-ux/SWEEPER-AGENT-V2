import {useEffect, type ReactNode} from 'react';
import {createPortal} from 'react-dom';
import {X} from 'lucide-react';

interface ModalProps {
  open: boolean;
  onClose: () => void;
  /** Header title shown top-right (RTL). */
  title?: ReactNode;
  /** Small uppercase kicker above the title. */
  kicker?: ReactNode;
  /** Rich content under the title (e.g. a stepper). */
  subtitle?: ReactNode;
  /** Body content. Scrolls internally when taller than the viewport. */
  children: ReactNode;
  /** Sticky footer (wizard buttons). */
  footer?: ReactNode;
  /** Max width class for the panel (default: max-w-3xl). */
  widthClass?: string;
  /** When false, clicking the backdrop / pressing Esc does nothing (e.g. mid-save). */
  dismissable?: boolean;
}

/**
 * A centered, portal-rendered "control console" dialog. Rendered on
 * document.body so it escapes ancestor clipping / stacking contexts, over a
 * tinted, blurred backdrop, with Esc-to-close and a body-scroll lock while
 * open. A hairline top-edge highlight gives the panel a machined, instrument
 * feel rather than a generic rounded card. RTL by default.
 */
export function Modal({
  open,
  onClose,
  title,
  kicker,
  subtitle,
  children,
  footer,
  widthClass = 'max-w-3xl',
  dismissable = true,
}: ModalProps) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && dismissable) onClose();
    };
    window.addEventListener('keydown', onKey);
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      window.removeEventListener('keydown', onKey);
      document.body.style.overflow = prevOverflow;
    };
  }, [open, onClose, dismissable]);

  if (!open) return null;

  return createPortal(
    <div
      dir="rtl"
      className="fixed inset-0 z-[200] flex items-center justify-center p-4 sm:p-6"
      role="dialog"
      aria-modal="true"
    >
      <div
        className="wizard-overlay-in absolute inset-0 bg-[#070a12]/85 backdrop-blur-md"
        onClick={() => dismissable && onClose()}
      />
      <div
        className={`wizard-panel-in relative z-10 flex max-h-[92vh] w-full ${widthClass} flex-col overflow-hidden rounded-2xl bg-slate-900 shadow-2xl shadow-black/60 ring-1 ring-white/10`}
      >
        {/* Machined top-edge highlight. */}
        <div className="pointer-events-none absolute inset-x-0 top-0 h-px bg-gradient-to-l from-transparent via-indigo-400/60 to-transparent" />

        {(title || subtitle || kicker) && (
          <header className="flex items-start justify-between gap-4 px-7 pt-6 pb-5">
            <div className="min-w-0">
              {kicker ? (
                <div className="mb-1.5 font-mono text-[11px] uppercase tracking-[0.2em] text-indigo-300/80">
                  {kicker}
                </div>
              ) : null}
              {title ? (
                <h2 className="font-display text-[1.35rem] font-semibold leading-tight text-white">{title}</h2>
              ) : null}
              {subtitle ? <div className="mt-3">{subtitle}</div> : null}
            </div>
            <button
              type="button"
              onClick={onClose}
              aria-label="סגור"
              className="shrink-0 rounded-lg p-2 text-slate-500 transition-colors hover:bg-white/5 hover:text-slate-200"
            >
              <X className="h-5 w-5" />
            </button>
          </header>
        )}

        <div className="min-h-0 flex-1 overflow-y-auto px-7 py-5">{children}</div>

        {footer ? (
          <footer className="border-t border-white/5 bg-slate-900/60 px-7 py-4">{footer}</footer>
        ) : null}
      </div>
    </div>,
    document.body,
  );
}
