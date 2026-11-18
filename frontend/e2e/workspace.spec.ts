import { test, expect, type Page } from "@playwright/test";
test("analyst imports logs and retrieves cited knowledge", async ({page}) => {
  await page.goto("/");
  await login(page,"analyst");
  await page.getByRole("link",{name:"Security events"}).click();
  for (const format of ["csv","json"]) {
    await page.getByLabel("Upload file").setInputFiles("../data/demo/events."+format);
    const uploaded=page.waitForResponse(r=>r.url().endsWith("/events/import"));
    await page.getByRole("button",{name:"Import events"}).click();
    expect((await uploaded).ok()).toBeTruthy();
  }
  await page.getByLabel("username").fill("no-such-local-demo-identity");
  await page.getByRole("button",{name:"Filter"}).click();
  await expect(page.getByText("No matching records.")).toBeVisible();
  await page.getByRole("link",{name:"Knowledge base"}).click();
  await page.getByLabel("Upload file").setInputFiles("../data/demo/session-policy.md");
  await page.getByRole("button",{name:"Import document"}).click();
  await page.getByLabel("Search policies").fill("MFA session policy");
  await page.getByRole("button",{name:"Search knowledge"}).click();
  await expect(page.getByText("chunk id",{exact:true}).first()).toBeVisible();
});
async function login(page: Page, role: string) {
  await page
    .getByLabel("Email", { exact: true })
    .fill(role + "@secops.example");
  await page.getByLabel("Password").fill(process.env.DEMO_PASSWORD!);
  await page.getByRole("button", { name: "Sign in" }).click();
}
test("analyst investigates and reviewer approves cited report", async ({
  page,
}) => {
  await page.goto("/");
  await login(page, "analyst");
  await page
    .getByRole("row")
    .filter({ hasText: "Repeated authentication failures" })
    .first()
    .getByText("Investigate")
    .click();
  const state = page
    .locator("section > p")
    .filter({ hasText: "awaiting_review" });
  await expect(state).toBeVisible();
  const id = (await state.innerText()).split(" ")[0];
  await page.getByText("Sign out").click();
  await login(page, "reviewer");
  await page.getByRole("link", { name: "Investigations", exact: true }).click();
  await page
    .getByRole("row")
    .filter({ hasText: id })
    .getByText("Inspect")
    .click();
  await page.getByRole("button", { name: "approve", exact: true }).click();
  await expect(
    page.locator("section > p").filter({ hasText: id }),
  ).toContainText("approved");
});
