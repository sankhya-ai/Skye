// Tura's thinking orb: a dotted outline morphing circle → triangle → square,
// with a crest sweeping it so a held shape still reads as working.
//
// Ported from Plank (`apps/dashboard/src/components/thinking-orb/ThinkingOrb.tsx`);
// `morph.ts` is copied unchanged. One change: the ink follows root's theme
// (`<html data-theme>`) frame by frame instead of a fixed `onPaper` prop, so
// switching Light/Dark mid-turn recolours the orb without remounting it.

import { useEffect, useRef } from "react";

import { drawMorph } from "./morph.ts";

/** Tura's four resource hues, tuned for a dark ground. */
const PASTEL_INKS = ["#e9c6da", "#dce9a8", "#c9e0f4", "#f8e7a5"] as const;
/** The same hues at the depth a near-white page needs — and not orange. */
const INKS_ON_PAPER = ["#c48aa8", "#8fa86a", "#6f9dc4", "#b9a05f"] as const;

const HOLD_SECONDS = 0.72;

function channels(hex: string): readonly [number, number, number] {
  const value = Number.parseInt(hex.slice(1), 16);
  return [(value >> 16) & 255, (value >> 8) & 255, value & 255];
}

function inkAt(seconds: number, onPaper: boolean): string {
  const inks = onPaper ? INKS_ON_PAPER : PASTEL_INKS;
  // rAF can hand back a frame time just before the loop was armed; a negative
  // position would index inks[-1] and kill the loop.
  const position = Math.max(0, seconds) / HOLD_SECONDS;
  const from = channels(inks[Math.floor(position) % inks.length]!);
  const to = channels(inks[(Math.floor(position) + 1) % inks.length]!);
  const local = position - Math.floor(position);
  const blend = local * local * (3 - 2 * local);
  const channel = (i: number) => Math.round(from[i]! + (to[i]! - from[i]!) * blend);
  return `rgb(${channel(0)} ${channel(1)} ${channel(2)})`;
}

const onPaper = () => document.documentElement.dataset.theme !== "dark";

export function ThinkingOrb({ size = 18, speed = 1.8 }: { size?: number; speed?: number }) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    const ctx = canvas?.getContext("2d");
    if (!canvas || !ctx) return undefined;

    // Device-resolution backing store, drawn in CSS pixels, so the dots stay sharp.
    const ratio = Math.min(3, window.devicePixelRatio || 1);
    canvas.width = Math.round(size * ratio);
    canvas.height = Math.round(size * ratio);
    ctx.scale(ratio, ratio);

    // Tuned for a status-row size: fewer, larger hollow dots, and a sweeping crest.
    const opts = {
      spread: 1.55,
      iconD: 0.42,
      rDot: 0.038,
      rMin: 0.6,
      ink: INKS_ON_PAPER[0] as string,
      hollow: true,
      lineWidth: Math.max(1, size / 20),
      highlight: 1,
      highlightSpeed: 0.62,
    };
    const paintAt = (seconds: number) => {
      ctx.clearRect(0, 0, size, size);
      opts.ink = inkAt(seconds, onPaper());
      drawMorph(ctx, size, seconds * speed, false, opts);
    };

    if (window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) {
      // A formed circle, not a shape caught mid-morph.
      paintAt(0.7 / speed);
      return undefined;
    }
    let raf = 0;
    const started = performance.now();
    const frame = (now: number) => {
      paintAt((now - started) / 1000);
      raf = requestAnimationFrame(frame);
    };
    raf = requestAnimationFrame(frame);
    return () => cancelAnimationFrame(raf);
  }, [size, speed]);

  return <canvas ref={canvasRef} aria-hidden="true" style={{ width: size, height: size, display: "block" }} />;
}
