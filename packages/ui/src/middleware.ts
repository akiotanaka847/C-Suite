import { auth } from "@/auth";

// Gate every page + non-auth API route. `auth` from NextAuth v5 wraps a
// handler that injects req.auth; here we use it directly as middleware, which
// makes unauthenticated requests redirect to the configured sign-in page.
export default auth((req) => {
  if (req.auth) return;

  // For API routes, return JSON 401 instead of an HTML redirect so the
  // browser doesn't follow it and break fetch() callers.
  if (req.nextUrl.pathname.startsWith("/api/")) {
    return Response.json({ error: "unauthorized" }, { status: 401 });
  }

  const signInUrl = new URL("/signin", req.nextUrl.origin);
  signInUrl.searchParams.set("callbackUrl", req.nextUrl.pathname + req.nextUrl.search);
  return Response.redirect(signInUrl);
});

// Exclude Auth.js's own routes, Next internals, static assets, and exactly
// `/signin` (with optional trailing slash). Using `signin/?` rather than the
// looser `signin` keeps unrelated paths like `/signin-help` gated.
//
// `brand/` holds the logo files under `public/`. They must be reachable
// unauthenticated because the sign-in page itself renders the emblem — gate
// them and the logo 302s to /signin, which renders as a broken image on the
// very page doing the redirecting. Public branding assets carry nothing
// sensitive; anything under `public/` that does should not live there.
export const config = {
  matcher: [
    "/((?!api/auth|_next/static|_next/image|favicon.ico|icon.png|apple-icon.png|brand/|signin/?$).*)",
  ],
};
