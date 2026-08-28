import { signIn, auth } from "@/auth";
import Image from "next/image";
import { redirect } from "next/navigation";

// Mirrors the guard in auth.ts. Both must hold or the button is not rendered
// and the underlying provider does not exist.
const DEV_BYPASS_ENABLED =
  process.env.NODE_ENV === "development" && process.env.DEV_AUTH_BYPASS === "true";
const DEV_BYPASS_EMAIL = (process.env.DEV_AUTH_BYPASS_EMAIL ?? "").trim();

type SearchParams = Promise<{ callbackUrl?: string; error?: string }>;

// Only same-origin paths allowed — a leading `/` followed by anything other
// than another `/` (which would be protocol-relative, e.g. `//evil.com`).
function safeCallbackUrl(raw: string | undefined): string {
  if (!raw) return "/";
  if (!raw.startsWith("/") || raw.startsWith("//") || raw.startsWith("/\\")) return "/";
  return raw;
}

export default async function SignInPage({ searchParams }: { searchParams: SearchParams }) {
  const { callbackUrl, error } = await searchParams;
  const safeDest = safeCallbackUrl(callbackUrl);

  // If already signed in, bounce straight to the destination.
  const session = await auth();
  if (session?.user) {
    redirect(safeDest);
  }

  const errorMessage = error ? describeError(error) : null;

  return (
    <main className="min-h-screen flex items-center justify-center px-6">
      <div className="w-full max-w-sm rounded-2xl border border-line bg-surface/60 p-8 shadow-xl">
        <div className="flex flex-col items-center text-center">
          <Image
            src="/brand/emblem-256.png"
            alt=""
            width={220}
            height={256}
            priority
            className="h-20 w-auto"
          />
          <h1 className="mt-4 text-2xl font-bold uppercase tracking-[0.18em] text-fg">
            C&#8209;Suite
          </h1>
          <p className="mt-1 text-[11px] font-medium uppercase tracking-[0.16em] text-fg-muted">
            Agent Orchestrator
          </p>
        </div>

        <p className="mt-6 text-sm text-fg-muted">Sign in to continue.</p>

        {errorMessage && (
          <p className="mt-4 rounded-md border border-red-900/50 bg-red-950/40 px-3 py-2 text-sm text-red-200">
            {errorMessage}
          </p>
        )}

        <form
          action={async () => {
            "use server";
            await signIn("google", { redirectTo: safeDest });
          }}
          className="mt-6"
        >
          <button
            type="submit"
            className="w-full rounded-md bg-white px-4 py-2 text-sm font-medium text-zinc-900 hover:bg-zinc-100 transition"
          >
            Sign in with Google
          </button>
        </form>

        {DEV_BYPASS_ENABLED && DEV_BYPASS_EMAIL && (
          <form
            action={async () => {
              "use server";
              await signIn("dev-bypass", { redirectTo: safeDest });
            }}
            className="mt-3"
          >
            <button
              type="submit"
              className="w-full rounded-md border border-dashed border-amber-500/60 bg-amber-500/10 px-4 py-2 text-sm font-medium text-amber-300 hover:bg-amber-500/20 transition"
            >
              Local dev sign-in — {DEV_BYPASS_EMAIL}
            </button>
            <p className="mt-2 text-center text-xs text-fg-muted">
              Development only. Not present in production builds.
            </p>
          </form>
        )}
      </div>
    </main>
  );
}

function describeError(code: string): string {
  switch (code) {
    case "AccessDenied":
      return "Your Google account is not on the allow-list for this workspace. Ask an admin to add you.";
    case "Configuration":
      return "Authentication is misconfigured. Contact the administrator.";
    default:
      return "Sign-in failed. Try again, or contact the administrator if this keeps happening.";
  }
}
