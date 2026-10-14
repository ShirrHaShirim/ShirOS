// Browser smoke test. Explicitly run against the local synthetic demonstration workspace.
const { chromium } = require(process.env.SHIROS_PLAYWRIGHT || "playwright");
const fs = require("node:fs");
const path = require("node:path");
const assert = require("node:assert/strict");

(async () => {
  const root = path.resolve(__dirname, "..");
  const session = JSON.parse(
    fs.readFileSync(path.join(root, ".local/browser-session/session.json")),
  );
  const browser = await chromium.launch({ channel: "msedge", headless: true });
  try {
    const page = await browser.newPage({
      viewport: { width: 1440, height: 1000 },
    });
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.goto(`http://127.0.0.1:${session.port}/#token=${session.token}`);
    await page.waitForResponse((r) => r.url().endsWith("/ui-api/memories"));
    if (
      (await page
        .locator("#memory-list .list-item")
        .filter({ hasText: "Synthetic demo - Reading notes" })
        .count()) === 0
    ) {
      await page.locator('#go-import').click();
      await page
        .locator("#import-title")
        .fill("Synthetic demo - Reading notes");
      await page
        .locator("#import-text")
        .fill(
          "Synthetic demonstration only. A reading note separates a claim, its evidence, and open questions. This is not personal information about ShirHaShirim.",
        );
      await page.locator("#preview-button").click();
      await page.waitForFunction(
        () => !document.querySelector("#stage-button").disabled,
      );
      await page.locator("#stage-button").click();
      await page
        .locator("#review-list .list-item")
        .filter({ hasText: "Synthetic demo - Reading notes" })
        .first()
        .click();
      await page.locator("#review-detail .primary-button").click();
      await page.locator("#dialog-confirm").click();
      await page.locator("#confirm-dialog").waitFor({ state: "hidden" });
    }
    const qaTitle = 'Synthetic QA ' + String(Date.now()).replace(/[0-9]/g, d => String.fromCharCode(97 + Number(d)));
    await page.locator('#go-import').click();
    await page.locator('#import-title').fill(qaTitle);
    await page.locator('#import-text').fill(qaTitle + ' - synthetic browser review test.');
    await page.locator('#preview-button').click();
    await page.waitForFunction(() => !document.querySelector('#stage-button').disabled);
    await page.locator('#stage-button').click();
    await page.locator('#review-list .list-item').filter({hasText: qaTitle}).click();
    await page.locator('#candidate-edit').fill(qaTitle + ' - edited synthetic review.');
    assert.equal(await page.locator('#review-detail .primary-button').isDisabled(), true);
    await page.locator('.review-commands .secondary-button').click();
    await page.waitForFunction(() => !document.querySelector('#review-detail .primary-button')?.disabled);
    await page.locator('#review-detail .primary-button').click();
    await page.locator('#dialog-confirm').click();
    await page.locator('#confirm-dialog').waitFor({state:'hidden'});
    await page.locator('[data-view="memory"]').click();
    await page.locator('#memory-list .list-item').filter({hasText:qaTitle}).click();
    await page.locator('#memory-detail .danger-button').click();
    await page.locator('#dialog-confirm').click();
    await page.locator('#confirm-dialog').waitFor({state:'hidden'});
    assert.equal(await page.locator('#memory-list .list-item').filter({hasText:qaTitle}).count(), 0);
    await page.locator('[data-view="memory"]').click();
    await page
      .locator("#memory-list .list-item")
      .filter({ hasText: "Synthetic demo - Reading notes" })
      .first()
      .click();
    await page.waitForFunction(() =>
      document
        .querySelector("#memory-detail")
        .textContent.includes("Synthetic demonstration only"),
    );
    assert.equal(
      await page
        .locator("#memory-detail")
        .innerText()
        .then((t) => t.includes("[object Object]")),
      false,
    );
    await page.screenshot({
      path: path.join(root, ".local/workbench-desktop.png"),
      fullPage: true,
    });
    await page.locator("#memory-search").fill("reading");
    await page.waitForResponse((r) => r.url().endsWith("/ui-api/memories"));
    assert.equal(await page.locator("#memory-list .list-item").count(), 1);
    await page.locator("#memory-search").fill("zzzz-no-synthetic-result");
    await page.waitForResponse((r) => r.url().endsWith("/ui-api/memories"));
    await page.waitForFunction(
      () => document.querySelectorAll("#memory-list .list-item").length === 0,
    );
    await page.locator("#memory-search").fill("");
    await page.waitForResponse((r) => r.url().endsWith("/ui-api/memories"));
    await page.locator("#language-toggle").click();
    assert.equal(await page.locator("html").getAttribute("lang"), "en");
    await page.locator("#language-toggle").click();
    await page.locator('#memory-list .list-item').first().click();
    await page.waitForFunction(() => document.querySelector('#memory-detail').textContent.includes('Synthetic demonstration only'));
    await page.setViewportSize({ width: 390, height: 844 });
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await page.waitForTimeout(350);
    await page.screenshot({
      path: path.join(root, ".local/workbench-mobile.png"),
      fullPage: true,
    });
    assert.equal(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
      true,
    );
    assert.deepEqual(errors, []);
    const locked = await browser.newPage();
    await locked.goto(`http://127.0.0.1:${session.port}/`);
    assert.equal(await locked.locator("#locked-screen").isVisible(), true);
    console.log(
      "Browser smoke passed: preview, stage, approve, detail, search, language, mobile, session lock.",
    );
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error.message);
  process.exitCode = 1;
});
