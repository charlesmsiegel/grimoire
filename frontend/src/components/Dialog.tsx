/**
 * App-standard modal dialog built on Radix Dialog: Escape to dismiss,
 * backdrop-click to dismiss, focus trapping, focus restoration, and
 * aria-labelledby wiring all come from the primitive.
 *
 * Renders into the existing `.modal-backdrop` / `.modal` styling (the panel
 * nests inside the overlay so the backdrop's flex centering applies). Pass
 * `panelClassName` for wider variants (e.g. the composition diff modal).
 * Action rows keep using the `.modal-actions` class. Dialogs that want a
 * corner × can render `<DialogClose />` themselves.
 */

import * as RadixDialog from "@radix-ui/react-dialog";

interface DialogProps {
  open: boolean;
  /** Called on every dismissal: Escape, backdrop click, or a DialogClose. */
  onClose: () => void;
  title: React.ReactNode;
  children: React.ReactNode;
  panelClassName?: string;
}

export function Dialog({ open, onClose, title, children, panelClassName }: DialogProps) {
  return (
    <RadixDialog.Root
      open={open}
      onOpenChange={(next) => {
        if (!next) onClose();
      }}
    >
      <RadixDialog.Portal>
        <RadixDialog.Overlay className="modal-backdrop">
          <RadixDialog.Content
            className={panelClassName ? `modal ${panelClassName}` : "modal"}
            aria-describedby={undefined}
          >
            <RadixDialog.Title asChild>
              <h4>{title}</h4>
            </RadixDialog.Title>
            {children}
          </RadixDialog.Content>
        </RadixDialog.Overlay>
      </RadixDialog.Portal>
    </RadixDialog.Root>
  );
}

/** Corner close button for dialogs that want one (e.g. preview modals). */
export function DialogClose() {
  return (
    <RadixDialog.Close asChild>
      <button type="button" className="modal-close" aria-label="Close">
        ×
      </button>
    </RadixDialog.Close>
  );
}
