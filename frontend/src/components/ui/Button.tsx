import { useState, type ReactNode, type MouseEventHandler } from "react";

export type ButtonVariant = "primary" | "secondary" | "ghost" | "destructive";
export type ButtonSize = "sm" | "md";

export interface ButtonProps
  extends Omit<React.ButtonHTMLAttributes<HTMLButtonElement>, "type"> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  disabled?: boolean;
  children?: ReactNode;
  onClick?: MouseEventHandler<HTMLButtonElement>;
  type?: "button" | "submit" | "reset";
}

const SIZE_CLASSES: Record<ButtonSize, string> = {
  // vertical padding (3px, 5px) and gap (5px, 6px) don't land exactly
  // on the --space-N scale — kept as arbitrary Tailwind values rather
  // than rounding to the nearest mapped step, which would silently
  // change the rendered size. Horizontal padding (8px, 12px) does map
  // (--space-3, --space-4).
  sm: "gap-[5px] px-3 py-[3px] text-xs",
  md: "gap-[6px] px-4 py-[5px] text-sm",
};

export function Button({
  variant = "primary",
  size = "md",
  disabled = false,
  children,
  onClick,
  type = "button",
  ...rest
}: ButtonProps) {
  const [hover, setHover] = useState(false);
  const [pressed, setPressed] = useState(false);
  const [focused, setFocused] = useState(false);

  let background = "transparent";
  let color = "var(--text-primary)";
  let border = "1px solid transparent";

  if (variant === "primary") {
    background = pressed
      ? "var(--accent-active)"
      : hover
        ? "var(--accent-hover)"
        : "var(--accent-solid)";
    color = "var(--text-on-accent)";
  } else if (variant === "secondary") {
    background = hover ? "var(--bg-surface-hover)" : "var(--bg-surface-raised)";
    color = "var(--text-primary)";
    border = "1px solid var(--border-default)";
  } else if (variant === "ghost") {
    background = hover ? "var(--bg-surface-hover)" : "transparent";
    color = "var(--text-secondary)";
  } else if (variant === "destructive") {
    background = hover ? "var(--diff-removed-bg)" : "transparent";
    color = "var(--diff-removed)";
    border = "1px solid var(--diff-removed)";
  }

  return (
    <button
      {...rest}
      type={type}
      disabled={disabled}
      onClick={onClick}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => {
        setHover(false);
        setPressed(false);
      }}
      onMouseDown={() => setPressed(true)}
      onMouseUp={() => setPressed(false)}
      onFocus={() => setFocused(true)}
      onBlur={() => setFocused(false)}
      className={`inline-flex items-center justify-center font-sans font-medium rounded-md outline-none ${SIZE_CLASSES[size]} ${disabled ? "cursor-not-allowed opacity-45" : "cursor-pointer"}`}
      style={{
        border,
        background,
        color,
        // transition-fast is a combined "<duration> <timing-function>"
        // shorthand (effects.css) — stays an inline `transition` value
        // rather than a Tailwind ease-* class, which expects a bare
        // timing-function (see styles/index.css's @theme comment).
        transition:
          "background var(--transition-fast), border-color var(--transition-fast)",
        boxShadow:
          focused && !disabled
            ? "0 0 0 2px var(--bg-canvas), 0 0 0 4px var(--focus-ring)"
            : "none",
      }}
    >
      {children}
    </button>
  );
}
