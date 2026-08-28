# Brand assets

`source-logo.jpeg` is the master artwork (2816×1536). Everything else is
derived from it — regenerate rather than hand-editing the derivatives.

| File | Use |
|---|---|
| `source-logo.jpeg` | Master. Includes the original wordmark, which the app does not use. |
| `emblem.png` | Emblem only, background removed. Source for the sizes below. |
| `emblem-96.png` | `BrandMark` at `sm` / `md` — header, message avatars. |
| `emblem-256.png` | `BrandMark` at `lg` and the sign-in page. |
| `icon-{16,32,180,512}.png` | Favicon sizes. 32 and 180 are copied to `src/app/icon.png` and `src/app/apple-icon.png`, which is how the App Router emits the `<link>` tags. |

The wordmark is **not** baked into any image — it is CSS text on the sign-in
page, so it stays crisp at any size and follows the theme.

## Two things that will bite you

**Background removal needs a noise floor.** The master is a JPEG, so its
"flat" background is not flat: compression noise puts stray pixels far enough
from the sampled background colour to survive a naive threshold, leaving a
visible rectangular halo around the emblem. Measure the noise (99.9th
percentile of the background's own deviation), use that as a hard floor, and
ramp alpha above it.

**`public/` is gated by auth middleware.** `src/middleware.ts` must keep
`brand/` in its matcher exclusions. Without it the emblem 302s to `/signin` —
and since the sign-in page is what renders the emblem, it shows a broken image
on the page doing the redirecting.
