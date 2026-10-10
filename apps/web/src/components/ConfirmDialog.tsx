'use client';

/**
 * A modal question with a confirm and a cancel, on the native <dialog>
 * element: focus is trapped and Escape cancels without any of it written here.
 */

import { useEffect, useRef } from 'react';
import type { ReactNode } from 'react';

import { Button } from '@/components/ui';

export function ConfirmDialog({
  open,
  title,
  children,
  confirmLabel,
  onConfirm,
  onCancel,
}: {
  open: boolean;
  title: string;
  children: ReactNode;
  confirmLabel: string;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    const dialog = ref.current;
    if (dialog === null) return;
    if (open && !dialog.open) dialog.showModal();
    if (!open && dialog.open) dialog.close();
  }, [open]);

  return (
    <dialog
      ref={ref}
      className="confirm-dialog"
      aria-labelledby="confirm-dialog-title"
      onCancel={(e) => {
        e.preventDefault();
        onCancel();
      }}
    >
      <div className="flex flex-col gap-4 p-6">
        <h2 id="confirm-dialog-title" className="text-[18px] font-semibold tracking-[-0.01em]">
          {title}
        </h2>
        <div className="flex flex-col gap-3 text-[14.5px] leading-relaxed text-fg-secondary">
          {children}
        </div>
        <div className="flex flex-wrap justify-end gap-3 pt-2">
          <Button variant="secondary" onClick={onCancel}>
            Cancel
          </Button>
          <Button onClick={onConfirm} autoFocus>
            {confirmLabel}
          </Button>
        </div>
      </div>
    </dialog>
  );
}
