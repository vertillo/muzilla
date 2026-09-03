import { useState } from "react";

export interface InputProps {
        id?: string;
        name?: string;
        value?: string;
        placeholder?: string;
        type?: "text" | "password" | "search" | "number";
        mono?: boolean;
        error?: boolean;
        disabled?: boolean;
        step?: string;
        min?: number;
        max?: number;
        onChange?: (value: string) => void;
}

export function Input({
        id,
        name,
        value = "",
        placeholder = "",
        type = "text",
        mono = false,
        error = false,
        disabled = false,
        step,
        min,
        max,
        onChange,
}: InputProps) {
        const [focused, setFocused] = useState(false);
        const borderColor = error
                ? "var(--diff-removed)"
                : focused
                  ? "var(--accent-solid)"
                  : "var(--border-default)";
        return (
                <input
                        id={id}
                        name={name}
                        type={type}
                        value={value}
                        placeholder={placeholder}
                        disabled={disabled}
                        step={step}
                        min={min}
                        max={max}
                        onChange={(e) => onChange?.(e.target.value)}
                        onFocus={() => setFocused(true)}
                        onBlur={() => setFocused(false)}
                        className={`w-full box-border px-[10px] py-[6px] text-sm rounded-md outline-none bg-surface ${mono ? "font-mono" : "font-sans"} ${disabled ? "cursor-not-allowed" : "cursor-text"}`}
                        style={{
                                color: disabled
                                        ? "var(--text-disabled)"
                                        : "var(--text-primary)",
                                border: `1px solid ${borderColor}`,
                                boxShadow:
                                        focused && !error
                                                ? "0 0 0 3px var(--accent-subtle-bg)"
                                                : "none",
                                transition: "border-color var(--transition-fast), box-shadow var(--transition-fast)",
                        }}
                />
        );
}
