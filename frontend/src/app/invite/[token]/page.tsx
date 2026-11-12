import { Suspense } from "react";

import { InviteClient } from "./invite-client";

export default function InvitePage() {
  // useParams() reads the dynamic token on the client; Suspense lets the shell prerender.
  return (
    <Suspense fallback={<div className="flex min-h-screen items-center justify-center text-sm text-muted-foreground">Loading invite…</div>}>
      <InviteClient />
    </Suspense>
  );
}
