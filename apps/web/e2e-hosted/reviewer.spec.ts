import { expect, test, type APIRequestContext } from "@playwright/test";

import type { Proposal, RunView } from "../src/lib/api/client";

const jobId = "job_ai_rag";
const runPath = "/api/v1/jobs/job_ai_rag/run?persona=ai_ml";
const pipelinePath = "/api/v1/pipeline?persona=ai_ml";
const workspacePath = "/?persona=ai_ml&job=job_ai_rag";

async function getWithReviewer(request: APIRequestContext, path: string) {
  try {
    return await request.get(path, { maxRedirects: 0, timeout: 15_000 });
  } catch {
    throw new Error("The authenticated hosted request did not complete.");
  }
}

async function readReviewerJson<T>(request: APIRequestContext, path: string): Promise<T> {
  const response = await getWithReviewer(request, path);
  expect(response.status(), "Authenticated API response").toBe(200);
  try {
    return (await response.json()) as T;
  } catch {
    throw new Error("The authenticated endpoint returned invalid JSON.");
  }
}

async function jobProposals(request: APIRequestContext): Promise<Proposal[]> {
  return (await readReviewerJson<Proposal[]>(request, pipelinePath)).filter((proposal) => proposal.job_id === jobId);
}

test("reviewer signs in, resumes the synthetic RAG review, and submits once", async ({ baseURL, page, request }) => {
  if (!baseURL) throw new Error("The hosted origin is required.");

  await test.step("check access at the public HTTPS entry point", async () => {
    const health = await fetch(new URL("/healthz", baseURL), {
      redirect: "manual",
      signal: AbortSignal.timeout(15_000),
    });
    expect(health.status).toBe(200);

    for (const path of ["/", "/api/v1/personas"]) {
      const anonymous = await fetch(new URL(path, baseURL), {
        redirect: "manual",
        signal: AbortSignal.timeout(15_000),
      });
      expect(anonymous.status).toBe(401);
      expect(/^Basic\b/i.test(anonymous.headers.get("www-authenticate") ?? "")).toBe(true);
    }

    const admin = await getWithReviewer(request, "/admin/state");
    expect([403, 404]).toContain(admin.status());
  });

  const initial = await readReviewerJson<RunView>(request, runPath);
  expect(initial.status, "The AI/RAG fixture must be unused before this smoke test").toBe("idle");
  expect(await jobProposals(request)).toHaveLength(0);

  const response = await page.goto(workspacePath);
  expect(response?.status()).toBe(200);
  await expect(page.getByRole("combobox", { name: "Active persona" })).toHaveValue("ai_ml");
  await expect(page.getByRole("heading", { name: "RAG chatbot over internal policy PDFs" })).toBeVisible();

  await page.getByRole("button", { name: "Analyze job" }).click();
  const review = page.getByRole("heading", { name: "Review proposal" });
  await expect(review).toBeVisible();
  const pendingRun = await readReviewerJson<RunView>(request, runPath);
  expect(pendingRun.status).toBe("awaiting_approval");
  const pending = pendingRun.pending;
  if (!pending) throw new Error("The RAG run did not produce a review checkpoint.");
  const { preview, proposal_digest: digest } = pending;
  expect(pending.submission.job_id).toBe(jobId);
  expect(preview.account_id).toBe("persona_ai_ml");
  expect(preview.connects_cost).toBeGreaterThan(0);
  expect(preview.balance_after).toBe(preview.balance - preview.connects_cost);
  await expect(page.getByLabel("Submission quote")).toContainText(String(preview.connects_cost));
  expect(await jobProposals(request), "No proposal exists before approval").toHaveLength(0);

  await page.reload();
  await expect(review).toBeVisible();
  const saved = await readReviewerJson<RunView>(request, runPath);
  expect(saved.pending?.proposal_digest).toBe(digest);
  expect(saved.pending?.preview).toEqual(preview);

  await page.getByRole("button", { name: "Approve and submit" }).click();
  const submitted = page.getByText("Proposal submitted", { exact: true });
  await expect(submitted).toBeVisible();
  await expect(page.getByText(preview.connects_cost + " Connects spent in GigSim")).toBeVisible();

  const finished = await readReviewerJson<RunView>(request, runPath);
  expect(finished.status).toBe("done");
  expect(finished.approval_status).toBe("approved");
  expect(finished.pending).toBeNull();
  const receipt = finished.outcome;
  if (!receipt) throw new Error("The completed review did not produce a simulator receipt.");
  expect(receipt.job_id).toBe(jobId);
  expect(receipt.status).toBe("submitted");
  expect(receipt.connects_spent).toBe(preview.connects_cost);

  const proposals = await jobProposals(request);
  expect(proposals).toHaveLength(1);
  expect(proposals[0]?.proposal_id).toBe(receipt.proposal_id);
  expect(proposals[0]?.connects_spent).toBe(preview.connects_cost);

  await page.reload();
  await expect(submitted).toBeVisible();
  const persisted = await jobProposals(request);
  expect(persisted).toHaveLength(1);
  expect(persisted[0]?.proposal_id).toBe(receipt.proposal_id);
  expect(persisted[0]?.connects_spent).toBe(preview.connects_cost);
});
