type PendingReviewCardProps = {
  pendingSampleCases: number;
};

export default function PendingReviewCard({ pendingSampleCases }: PendingReviewCardProps) {
  return (
    <div className="relative overflow-hidden rounded-2xl border border-primary/30 bg-gradient-to-br from-primary/10 via-card/90 to-background/90 px-5 pb-10 pt-6 text-center shadow-[0_0_0_1px_hsl(var(--primary)/0.18),0_28px_72px_-32px_hsl(var(--foreground)/0.45)] backdrop-blur-sm sm:px-8 sm:pb-14 sm:pt-8">
      <div className="pointer-events-none absolute -right-16 -top-16 h-36 w-36 rounded-full bg-primary/20 blur-3xl sm:-right-14 sm:-top-14 sm:h-40 sm:w-40" />
      <div className="pointer-events-none absolute -left-20 bottom-0 h-32 w-32 rounded-full bg-foreground/10 blur-3xl sm:h-36 sm:w-36" />

      <div className="relative mx-auto mb-8 inline-flex items-center rounded-full border border-primary/40 bg-primary/15 px-3 py-1 text-xs font-semibold tracking-[0.12em] text-foreground sm:text-sm sm:tracking-[0.16em]">
        ⏳ PIPELINE PENDING REVIEW
      </div>

      <p className="relative mx-auto max-w-2xl px-4 text-center text-lg font-semibold tracking-wide text-foreground sm:px-5 sm:text-xl">
        {pendingSampleCases} sample cases generated — pending review
      </p>

      <p className="relative mx-auto mt-2 max-w-2xl px-4 text-left text-sm leading-relaxed text-muted-foreground sm:px-5">
        Your sample cases are ready. Before moving forward, we do a quick review together to make sure everything looks right and aligns with real-world usage.
      </p>

      <div className="relative mx-auto mt-8 max-w-2xl rounded-xl bg-primary/10 px-4 py-4 text-left sm:px-5">
        <p className="text-base font-semibold text-foreground sm:text-lg text-center">
          Next step: join Slack for review
        </p>
        <p className="mt-1 text-sm leading-relaxed text-muted-foreground">
          We&apos;ve sent you a Slack invitation to your registered email with Zealt. Please join and we&apos;ll walk through the next step together.
        </p>
        <p className="mt-3 text-xs text-muted-foreground">
          If you don&apos;t see it, just reach out at{" "}
          <a
            href="mailto:zealtdev@tabbyml.com"
            className="font-normal text-foreground underline decoration-primary/60 underline-offset-4 hover:text-primary transition-colors"
          >
            zealtdev@tabbyml.com
          </a>
          .
        </p>
      </div>

    </div>
  );
}
