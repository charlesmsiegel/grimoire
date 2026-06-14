import type { ReactNode, SVGProps } from "react";

export interface IconProps extends Omit<SVGProps<SVGSVGElement>, "children"> {
  /**
   * Square edge length. Defaults to `1em` so icons inherit the surrounding
   * font-size (a `card-icon-button` icon is ~0.95rem, a `provider-card-icon`
   * ~1.4rem) without per-call sizing.
   */
  size?: number | string;
}

/**
 * Base wrapper for the shared SVG icon set (#516). Every icon is a 24×24
 * stroke glyph that paints with `currentColor`, so it themes from the text
 * color of whatever renders it. Decorative by default (`aria-hidden`): icon
 * buttons carry their own `aria-label`/`title`, so the glyph must not add a
 * second accessible name. Pass `aria-hidden={false}` + `role="img"` +
 * `aria-label` for a standalone, meaningful icon.
 */
export function Icon({ size = "1em", children, ...rest }: IconProps & { children: ReactNode }) {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
      {...rest}
    >
      {children}
    </svg>
  );
}
