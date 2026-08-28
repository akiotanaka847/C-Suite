import type { MetadataRoute } from "next";

/**
 * Web app manifest — served at `/manifest.webmanifest`.
 *
 * NOTE: `middleware.ts` must keep this path out of its auth matcher. The
 * browser fetches the manifest before any session exists, and a gated
 * manifest 302s to /signin, which the browser then rejects as invalid JSON —
 * the install prompt silently never appears, with no error anywhere.
 */
export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "C-Suite — Agent Orchestrator",
    short_name: "C-Suite",
    description: "Your AI-powered virtual executive team",
    // `/today` rather than `/`: opening the installed app should land on the
    // current state, which is what you reach for on a phone.
    start_url: "/today",
    scope: "/",
    display: "standalone",
    orientation: "portrait",
    background_color: "#f7f7f5",
    theme_color: "#f7f7f5",
    categories: ["business", "productivity"],
    icons: [
      {
        src: "/brand/icon-180.png",
        sizes: "180x180",
        type: "image/png",
        purpose: "any",
      },
      {
        src: "/brand/icon-512.png",
        sizes: "512x512",
        type: "image/png",
        purpose: "any",
      },
      // A `maskable` entry lets Android crop the emblem into its adaptive-icon
      // shape without clipping content — the emblem already sits inside a
      // margin, so the same file serves both purposes.
      {
        src: "/brand/icon-512.png",
        sizes: "512x512",
        type: "image/png",
        purpose: "maskable",
      },
    ],
  };
}
