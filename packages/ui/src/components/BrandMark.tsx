import Image from "next/image";

type BrandMarkSize = "sm" | "md" | "lg";

// Rendered pixel size per slot. The emblem is served from `public/brand/`,
// which middleware.ts must keep ungated — the sign-in page renders it before
// any session exists.
const BOX_CLASSES: Record<BrandMarkSize, string> = {
  sm: "h-7 w-7",
  md: "h-7 w-7",
  lg: "h-12 w-12",
};

// Two source files rather than one: the 96px asset is 18 KB and covers the
// sm/md slots, while lg would visibly soften if upscaled from it.
const SOURCES: Record<BrandMarkSize, { src: string; w: number; h: number }> = {
  sm: { src: "/brand/emblem-96.png", w: 82, h: 96 },
  md: { src: "/brand/emblem-96.png", w: 82, h: 96 },
  lg: { src: "/brand/emblem-256.png", w: 220, h: 256 },
};

interface BrandMarkProps {
  size?: BrandMarkSize;
}

export default function BrandMark({ size = "sm" }: BrandMarkProps) {
  const { src, w, h } = SOURCES[size];
  return (
    <Image
      src={src}
      alt=""
      width={w}
      height={h}
      aria-hidden
      priority={size === "lg"}
      className={`${BOX_CLASSES[size]} object-contain`}
    />
  );
}
