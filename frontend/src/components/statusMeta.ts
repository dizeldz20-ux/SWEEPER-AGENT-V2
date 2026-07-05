// One place for the machine-status visual language, so the cards grid and the
// machine detail modal stay in lock-step (colors, labels, LED behavior).

import type {Machine} from '../types';

export interface StatusMeta {
  label: string;
  /** Status chip: text + tinted bg + border. */
  chip: string;
  /** LED dot color. */
  dot: string;
  /** Extra LED classes (pulse on warning). */
  dotExtra: string;
  /** Middle color stop of the machined top-edge hairline. */
  hairline: string;
  /** Soft glow behind the server-icon housing. */
  glow: string;
  /** Icon tint inside the housing. */
  icon: string;
}

export const STATUS_META: Record<Machine['status'], StatusMeta> = {
  online: {
    label: 'פעיל',
    chip: 'text-emerald-400 bg-emerald-400/10 border-emerald-400/20',
    dot: 'bg-emerald-400',
    dotExtra: '',
    hairline: 'via-emerald-400/60',
    glow: 'shadow-[0_0_18px_rgba(52,211,153,0.25)]',
    icon: 'text-emerald-400',
  },
  warning: {
    label: 'שגיאה',
    chip: 'text-amber-400 bg-amber-400/10 border-amber-400/20',
    dot: 'bg-amber-400',
    dotExtra: 'animate-pulse',
    hairline: 'via-amber-400/60',
    glow: 'shadow-[0_0_18px_rgba(251,191,36,0.22)]',
    icon: 'text-amber-400',
  },
  offline: {
    label: 'לא פעיל',
    chip: 'text-rose-400 bg-rose-400/10 border-rose-400/20',
    dot: 'bg-rose-400',
    dotExtra: '',
    hairline: 'via-rose-400/60',
    glow: 'shadow-[0_0_18px_rgba(251,113,133,0.22)]',
    icon: 'text-rose-400',
  },
};
