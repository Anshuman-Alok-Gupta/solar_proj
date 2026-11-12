import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { AccountCard, inviteLink } from "@/components/settings/account-sharing";
import { EmptyState, ErrorState, SourceBadge } from "@/components/state";
import { endpoints } from "@/lib/api";

describe("state components", () => {
  it("ErrorState announces the error and retries", async () => {
    const retry = vi.fn();
    render(<ErrorState message="Backend is waking up" onRetry={retry} />);
    expect(screen.getByRole("alert")).toHaveTextContent("Backend is waking up");
    await userEvent.click(screen.getByRole("button", { name: /retry/i }));
    expect(retry).toHaveBeenCalledOnce();
  });

  it("EmptyState shows title, description and action", () => {
    render(<EmptyState title="No saved reports yet" description="Pick a period" action={<button>Go</button>} />);
    expect(screen.getByText("No saved reports yet")).toBeInTheDocument();
    expect(screen.getByText("Pick a period")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Go" })).toBeInTheDocument();
  });

  it("SourceBadge labels provenance and hides when unknown", () => {
    const { container, rerender } = render(<SourceBadge source={null} />);
    expect(container).toBeEmptyDOMElement();
    rerender(<SourceBadge source="synthetic" />);
    expect(screen.getByText(/synthetic|demo/i)).toBeInTheDocument();
  });
});

describe("sharing & account", () => {
  it("builds invite links on the current origin and encodes the token", () => {
    expect(inviteLink("a b", "https://solar.example.app/")).toBe("https://solar.example.app/invite/a%20b");
  });

  it("AccountCard blocks mismatched passwords and submits matching ones", async () => {
    const change = vi.spyOn(endpoints, "changePassword").mockResolvedValue(undefined);
    const user = userEvent.setup();
    render(<AccountCard />);
    await user.type(screen.getByLabelText("Current password"), "old-password");
    await user.type(screen.getByLabelText("New password"), "new-password-1");
    await user.type(screen.getByLabelText("Confirm new password"), "new-password-2");
    expect(screen.getByRole("alert")).toHaveTextContent("do not match");
    expect(screen.getByRole("button", { name: "Change password" })).toBeDisabled();

    await user.clear(screen.getByLabelText("Confirm new password"));
    await user.type(screen.getByLabelText("Confirm new password"), "new-password-1");
    await user.click(screen.getByRole("button", { name: "Change password" }));
    expect(change).toHaveBeenCalledWith("old-password", "new-password-1");
  });
});
